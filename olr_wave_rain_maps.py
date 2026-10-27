"""
Maps of the ECMWF extended-range rainfall anomaly over East Africa and the
western Indian Ocean, with the filtered OLR of a tropical wave contoured on
top, from what olr_wave_filter.py wrote to data/<date>/olr_waves_<date>.zarr:

  plots/olr_waves/<date>/rain_anomaly_mjo_weekly.png
      weeks 1-4, weekly mean rainfall anomaly and MJO-filtered OLR
  plots/olr_waves/<date>/rain_anomaly_kelvin_daily.png
      days 1-10, daily rainfall anomaly and Kelvin-filtered OLR (a Kelvin
      wave passes in a few days, so weekly means would average it out)
  plots/olr_waves/<date>/rain_anomaly_mjo_daily.gif
      an animation, one frame per forecast day (1-46), of the daily rainfall
      anomaly and MJO-filtered OLR, over a wider 20-120E so the MJO can be
      followed across the Indian Ocean

Both are the ensemble mean, which is also what the filtered OLR is (the
filtering being linear). The rainfall anomaly is the forecast minus the
model's own climate for the same lead day and time of year: the mean of the
Planette reforecasts 2006-2024 nearest this calendar day (gef.load_reforecast),
which also takes out the model's drift with lead time. The forecast rainfall
comes from the 1.5 degree stores download_s2s.py writes,
ECMWF_s2s_precip_alt_<date>.zarr (20S-20N, 30-120E) filled in west of 30E
from ECMWF_s2s_precip_<date>.zarr.

Ensemble agreement, on every panel:
  dots        at least AGREEMENT of the members are on the same side of the
              model's median (the reforecast median for that lead day or
              week) as the ensemble-mean anomaly. The median rather than the
              mean, as daily rainfall is skewed: most members fall a little
              below the mean on most days, so agreement against the mean
              would show dry nearly everywhere. Only on shaded cells
              (|anomaly| >= 1 mm day-1).
  red line    the dots and the wave agree: wetter than the median in at least
              AGREEMENT of the members where the ensemble-mean wave OLR is
              enhanced (<= -middle contour), solid; drier where it is
              suppressed (>= +middle contour), dashed.
  strip       under each map, the share of members with an active wave
              (Janiga et al. 2018 amplitude > 1) at 5S-5N in its enhanced
              (purple) or suppressed (orange) half, whichever is larger,
              against longitude (olr_wave_filter.py's <wave>_prob_*). The
              members are only filtered in that equatorial band.

Lead time convention: day N is 00-24 UTC on init + N - 1, for the forecast,
the reforecasts and the filtered OLR alike.

Env vars:
    DATE_STR      forecast init date, defaults to today-2
"""

import os
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import numpy as np
import pandas as pd
import xarray as xr

import get_ECMWF_functions as gef

# ============================================================
# CONFIG
# ============================================================

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

print(f"Rainfall anomaly maps with tropical wave OLR: {date_str}")

path = f'data/{date_str}/'
plot_path = f'plots/olr_waves/{date_str}/'

INIT = pd.Timestamp(date_str)
MAP_EXTENT = (20, 80, -19.5, 19.5)          # lon W, lon E, lat S, lat N: the static maps
ANIM_EXTENT = (20, 120, -19.5, 19.5)        # the animation, wide enough to follow the MJO east
DATA_EXTENT = ANIM_EXTENT                   # rainfall is read once over both
N_WEEKS = 4
N_DAYS = 10
AGREEMENT = 0.66                            # share of members for the dots and the red line
ANIM_FPS = 2                                # animation frames (forecast days) per second

# filtered OLR anomaly contours (W m-2): negative = enhanced convection
WAVE_LEVELS = {"MJO": (5, 10, 20), "Kelvin": (4, 8, 16)}

# rainfall anomaly shading (mm day-1), white between -1 and 1
RAIN_LEVELS = [-12, -8, -4, -2, -1, 1, 2, 4, 8, 12]

# wave strip: share of members active in the enhanced (+) / suppressed (-) half
STRIP_LEVELS = [-1, -AGREEMENT, -0.5, -0.33, 0.33, 0.5, AGREEMENT, 1]


# ============================================================
# Data
# ============================================================

def forecast_rain_members():
    """Daily rainfall (mm day-1) of every member as (number, step, latitude,
    longitude), step N = day N, over DATA_EXTENT."""
    lon_w, lon_e, lat_s, lat_n = DATA_EXTENT
    parts = []
    for store in (f"ECMWF_s2s_precip_alt_{date_str}.zarr", f"ECMWF_s2s_precip_{date_str}.zarr"):
        tp = xr.open_zarr(f"{path}{store}").tp
        tp = tp.sel(latitude=slice(lat_n, lat_s), longitude=slice(lon_w, lon_e)).load()
        parts.append(tp.diff("step", label="upper"))      # accumulated mm -> mm over each day
    rain = parts[0].combine_first(parts[1]).transpose("number", "step", "latitude", "longitude")
    return rain.drop_vars([c for c in rain.coords if c not in rain.dims])


def reforecast_rain_runs():
    """Daily rainfall (mm day-1) of every reforecast run nearest this calendar
    day in each year, as (run, step, latitude, longitude)."""
    lon_w, lon_e, lat_s, lat_n = DATA_EXTENT
    rf = gef.load_reforecast(date_str, "single", "pr",
                             bbox={"lat1": lat_n, "lon1": lon_w, "lat2": lat_s, "lon2": lon_e},
                             time_range=slice(0, 46), all_years=True)
    print(f"Reforecast climate from {rf.sizes['init_time']} years x {rf.sizes['number']} members")
    rf = rf.stack(run=("init_time", "number")).transpose("run", "step", "latitude", "longitude") * 86400.0
    rf = rf.drop_vars(["run", "init_time", "number"]).assign_coords(run=np.arange(rf.sizes["run"]))
    return rf.drop_vars([c for c in rf.coords if c not in rf.dims])


def agreement(fc, rf):
    """For forecast members fc (number, ...) and reforecast runs rf (run, ...)
    on the same grid: the ensemble-mean anomaly against the reforecast mean,
    and the shares of members above / below the reforecast median."""
    median = rf.median("run")
    return xr.Dataset({"anom": fc.mean("number") - rf.mean("run"),
                       "above": (fc > median).mean("number"),
                       "below": (fc < median).mean("number")})


def rain_fields():
    fc, rf = xr.align(forecast_rain_members(), reforecast_rain_runs(), join="inner")
    valid = (INIT + fc.step.to_index() - pd.Timedelta(days=1)).values
    print(f"Rainfall: {fc.sizes['number']} members, {fc.sizes['step']} days on "
          f"{fc.sizes['latitude']}x{fc.sizes['longitude']} points")
    daily = agreement(fc, rf).assign_coords(valid_date=("step", valid))
    weekly = []
    for week in range(N_WEEKS):
        days = slice(7 * week, 7 * week + 7)
        w = agreement(fc.isel(step=days).mean("step"), rf.isel(step=days).mean("step"))
        weekly.append(w.assign_attrs(dates=pd.DatetimeIndex(valid[days])))
    return daily, weekly


def wave_fields(wave, valid_dates, extent):
    """Ensemble-mean filtered OLR over (a bit more than) extent, and the 5S-5N
    member shares active in the enhanced / suppressed half, for valid_dates."""
    lon_w, lon_e, lat_s, lat_n = extent
    ds = xr.open_zarr(f"{path}olr_waves_{date_str}.zarr").sel(time=valid_dates)
    olr = ds[f"{wave}_mean"].sel(latitude=slice(lat_n + 3, lat_s - 3), longitude=slice(lon_w - 3, lon_e + 3))
    lon = slice(lon_w - 1.5, lon_e + 1.5)
    prob = xr.Dataset({"enhanced": ds[f"{wave}_prob_enhanced"].sel(longitude=lon),
                       "suppressed": ds[f"{wave}_prob_suppressed"].sel(longitude=lon)})
    return olr.load(), prob.load()


# ============================================================
# Plot
# ============================================================

def clip(field, extent):
    lon_w, lon_e, lat_s, lat_n = extent
    return field.sel(latitude=slice(lat_n, lat_s), longitude=slice(lon_w, lon_e))


def basemap(ax, left_labels, extent, fs=1.0):
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.coastlines(resolution="50m", linewidth=0.7, color="#333333")
    ax.add_feature(cfeature.BORDERS, linestyle=":", linewidth=0.8, color="#333333")
    gl = ax.gridlines(draw_labels=True, linewidth=0.4, color="gray", alpha=0.6, linestyle="--")
    gl.top_labels = gl.right_labels = gl.bottom_labels = False   # longitudes go on the strip
    gl.left_labels = left_labels
    gl.ylabel_style = {"size": 8 * fs}


def rain_norm():
    cmap = plt.get_cmap("BrBG")
    return cmap, mcolors.BoundaryNorm(RAIN_LEVELS, ncolors=cmap.N, extend="both")


def strip_norm():
    cmap = plt.get_cmap("PuOr")
    return cmap, mcolors.BoundaryNorm(STRIP_LEVELS, ncolors=cmap.N)


def draw_rain(ax, rain):
    cmap, norm = rain_norm()
    pc = ax.pcolormesh(rain.longitude, rain.latitude, rain.anom, cmap=cmap, norm=norm,
                       shading="nearest", transform=ccrs.PlateCarree())
    shaded = RAIN_LEVELS[len(RAIN_LEVELS) // 2]             # edge of the white bin
    agree = (((rain.anom >= shaded) & (rain.above >= AGREEMENT))
             | ((rain.anom <= -shaded) & (rain.below >= AGREEMENT)))
    lat, lon = xr.broadcast(rain.latitude, rain.longitude)
    ax.scatter(lon.values[agree.values], lat.values[agree.values], s=2.5, c="k", marker=".",
               linewidths=0, transform=ccrs.PlateCarree())
    return pc


def draw_wave(ax, olr, wave, fs=1.0):
    levels = WAVE_LEVELS[wave]
    widths = [0.9, 1.5, 2.2]                 # thicker for a stronger wave
    for sign, ls in ((-1, "solid"), (1, "dashed")):
        if (sign * olr).max() < levels[0]:
            continue
        lv, lw = (sorted(-np.array(levels)), widths[::-1]) if sign < 0 else (list(levels), widths)
        cs = ax.contour(olr.longitude, olr.latitude, olr, levels=lv, colors="k", linestyles=ls,
                        linewidths=lw, transform=ccrs.PlateCarree())
        ax.clabel(cs, fmt="%d", fontsize=7 * fs, inline=True)


def combined_cut(wave):
    """The wave OLR the red line needs: its middle contour, as the lowest one
    covers most of the map on most days."""
    return WAVE_LEVELS[wave][1]


def draw_combined(ax, rain, olr, wave):
    """Red outline where the rainfall agreement and the wave phase coincide."""
    cut = combined_cut(wave)
    olr = olr.sel(latitude=rain.latitude, longitude=rain.longitude)
    for mask, ls in (((rain.above >= AGREEMENT) & (olr <= -cut), "solid"),
                     ((rain.below >= AGREEMENT) & (olr >= cut), "dashed")):
        if mask.any():
            ax.contour(rain.longitude, rain.latitude, mask.astype(float), levels=[0.5], colors="red",
                       linewidths=1.8, linestyles=ls, transform=ccrs.PlateCarree())


def draw_strip(ax, prob, bottom_labels, side_label, extent, fs=1.0):
    """Signed share of members active in the dominant half of the wave cycle:
    + enhanced, - suppressed, along longitude."""
    signed = xr.where(prob.enhanced >= prob.suppressed, prob.enhanced, -prob.suppressed)
    cmap, norm = strip_norm()
    lon = signed.longitude.values
    edges = np.append(lon - 0.75, lon[-1] + 0.75)
    pc = ax.pcolormesh(edges, [0, 1], signed.values[None, :], cmap=cmap, norm=norm)
    ax.set_xlim(extent[0], extent[1])
    ax.set_yticks([])
    ticks = range(extent[0] + 10, extent[1], 10)
    ax.set_xticks(ticks)
    ax.tick_params(labelsize=8 * fs, labelbottom=bottom_labels, length=2)
    if bottom_labels:
        ax.set_xticklabels([f"{x}°E" for x in ticks])
    if side_label:
        ax.text(-0.01, 0.5, "5S-5N", transform=ax.transAxes, ha="right", va="center", fontsize=7 * fs)
    return pc


def make_figure(panels, wave, nrows, ncols, title, out, figsize, margins, fs=1.0):
    """panels: (rain Dataset, wave OLR, 5S-5N member shares, title) per panel.
    margins: (top, bottom, hspace) of the map grid, in figure fractions.
    fs scales every font but the suptitle's."""
    top, bottom, hspace = margins
    fig = plt.figure(figsize=figsize)
    grid = fig.add_gridspec(nrows, ncols, left=0.05, right=0.98, top=top, bottom=bottom,
                            wspace=0.1, hspace=hspace)
    for i, (rain, olr, prob, label) in enumerate(panels):
        ax = fig.add_subplot(grid[i // ncols, i % ncols], projection=ccrs.PlateCarree())
        basemap(ax, left_labels=i % ncols == 0, extent=MAP_EXTENT, fs=fs)
        rain = clip(rain, MAP_EXTENT)
        pc = draw_rain(ax, rain)
        draw_wave(ax, olr, wave, fs=fs)
        draw_combined(ax, rain, olr, wave)
        ax.set_title(label, fontsize=10 * fs)
        # the map keeps its aspect inside the grid cell: put the strip under
        # where it actually ended up, at the same width
        ax.apply_aspect()
        pos = ax.get_position()
        strip_h = 0.06 * pos.height
        sax = fig.add_axes([pos.x0, pos.y0 - strip_h - 0.003, pos.width, strip_h])
        strip = draw_strip(sax, prob, bottom_labels=i // ncols == nrows - 1, side_label=i % ncols == 0,
                           extent=MAP_EXTENT, fs=fs)

    cax = fig.add_axes([0.06, 0.07, 0.42, 0.012])
    cb = fig.colorbar(pc, cax=cax, orientation="horizontal", ticks=RAIN_LEVELS, extend="both")
    cb.set_label("rainfall anomaly (mm day$^{-1}$), ensemble mean\n"
                 "vs. the model's 2006-2024 reforecast climate", fontsize=9 * fs)
    cb.ax.tick_params(labelsize=8 * fs)
    sax = fig.add_axes([0.54, 0.07, 0.42, 0.012])
    cs = fig.colorbar(strip, cax=sax, orientation="horizontal", ticks=STRIP_LEVELS,
                      format=lambda x, _: f"{abs(x):.0%}")
    cs.set_label(f"strip: share of members with an active {wave} (amplitude > 1)\n"
                 "at 5S-5N, suppressed half (orange)  |  enhanced half (purple)", fontsize=9 * fs)
    cs.ax.tick_params(labelsize=8 * fs)

    lv = ", ".join(str(v) for v in WAVE_LEVELS[wave])
    cut = combined_cut(wave)
    fig.suptitle(f"{title}\n"
                 f"Black: {wave}-filtered OLR anomaly at $\\pm${lv} W m$^{{-2}}$; solid = enhanced convection, "
                 f"dashed = suppressed.\n"
                 f"Dots: $\\geq${AGREEMENT:.0%} of members on the same side of the model median as the mean anomaly (shaded cells only).\n"
                 f"Red: $\\geq${AGREEMENT:.0%} of members wetter than the median where the {wave} OLR is "
                 f"$\\leq$-{cut} W m$^{{-2}}$ (solid), or drier where it is $\\geq$+{cut} (dashed)",
                 fontsize=10, y=0.995, va="top")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"Wrote {out}")


def plot_weekly_mjo(weekly):
    panels = []
    for week, rain in enumerate(weekly):
        dates = rain.attrs["dates"]
        olr, prob = wave_fields("MJO", dates, MAP_EXTENT)
        label = f"Week {week + 1}: {dates[0]:%d %b} - {dates[-1]:%d %b}"
        panels.append((rain, olr.mean("time"), prob.mean("time"), label))
    make_figure(panels, "MJO", 2, 2, f"ECMWF weekly rainfall anomaly and the MJO, init {date_str}",
                f"{plot_path}rain_anomaly_mjo_weekly.png", figsize=(12, 12), margins=(0.86, 0.17, 0.3), fs=1.4)


def plot_daily_kelvin(daily):
    daily = daily.isel(step=slice(0, N_DAYS))
    dates = pd.DatetimeIndex(daily.valid_date.values)
    olr, prob = wave_fields("Kelvin", dates, MAP_EXTENT)
    panels = [(daily.isel(step=i), olr.isel(time=i), prob.isel(time=i), f"Day {i + 1}: {d:%a %d %b}")
              for i, d in enumerate(dates)]
    make_figure(panels, "Kelvin", 2, 5, f"ECMWF daily rainfall anomaly and Kelvin waves, init {date_str}",
                f"{plot_path}rain_anomaly_kelvin_daily.png", figsize=(20, 9.5), margins=(0.86, 0.2, 0.35))


def plot_mjo_animation(daily):
    """One frame per forecast day: the daily rainfall anomaly with the MJO OLR,
    agreement dots, red outlines and the 5S-5N strip, over ANIM_EXTENT."""
    from matplotlib.animation import FuncAnimation, PillowWriter

    rain = clip(daily, ANIM_EXTENT)
    dates = pd.DatetimeIndex(rain.valid_date.values)
    olr, prob = wave_fields("MJO", dates, ANIM_EXTENT)
    n = len(dates)

    fig = plt.figure(figsize=(13, 7.6))
    ax = fig.add_axes([0.05, 0.22, 0.92, 0.6], projection=ccrs.PlateCarree())
    basemap(ax, left_labels=True, extent=ANIM_EXTENT)
    ax.apply_aspect()
    pos = ax.get_position()
    strip_h = 0.05 * pos.height
    sax = fig.add_axes([pos.x0, pos.y0 - strip_h - 0.004, pos.width, strip_h])

    cmap, norm = rain_norm()
    cax = fig.add_axes([0.08, 0.1, 0.38, 0.015])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal",
                      ticks=RAIN_LEVELS, extend="both")
    cb.set_label("rainfall anomaly (mm day$^{-1}$), ensemble mean\n"
                 "vs. the model's 2006-2024 reforecast climate", fontsize=9)
    cb.ax.tick_params(labelsize=8)
    cmap, norm = strip_norm()
    cax = fig.add_axes([0.56, 0.1, 0.38, 0.015])
    cs = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal",
                      ticks=STRIP_LEVELS, format=lambda x, _: f"{abs(x):.0%}")
    cs.set_label("strip: share of members with an active MJO (amplitude > 1) at 5S-5N\n"
                 "suppressed half (orange)  |  enhanced half (purple)", fontsize=9)
    cs.ax.tick_params(labelsize=8)

    lv = ", ".join(str(v) for v in WAVE_LEVELS["MJO"])
    cut = combined_cut("MJO")
    fig.text(0.5, 0.985, f"ECMWF daily rainfall anomaly and the MJO, init {date_str}", ha="center", va="top",
             fontsize=13)
    fig.text(0.5, 0.945,
             f"Black: MJO-filtered OLR anomaly at $\\pm${lv} W m$^{{-2}}$; solid = enhanced convection, "
             f"dashed = suppressed.\n"
             f"Dots: $\\geq${AGREEMENT:.0%} of members on the same side of the model median as the mean anomaly "
             f"(shaded cells only).\n"
             f"Red: $\\geq${AGREEMENT:.0%} of members wetter than the median where the MJO OLR is "
             f"$\\leq$-{cut} W m$^{{-2}}$ (solid), or drier where it is $\\geq$+{cut} (dashed)",
             ha="center", va="top", fontsize=9)

    def frame(i):
        ax.cla()
        basemap(ax, left_labels=True, extent=ANIM_EXTENT)
        day = rain.isel(step=i)
        draw_rain(ax, day)
        draw_wave(ax, olr.isel(time=i), "MJO")
        draw_combined(ax, day, olr.isel(time=i), "MJO")
        ax.set_title(f"Day {i + 1} of {n}: {dates[i]:%a %d %b %Y}", fontsize=12, fontweight="bold")
        sax.cla()
        draw_strip(sax, prob.isel(time=i), bottom_labels=True, side_label=True, extent=ANIM_EXTENT)

    out = f"{plot_path}rain_anomaly_mjo_daily.gif"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    FuncAnimation(fig, frame, frames=n).save(out, writer=PillowWriter(fps=ANIM_FPS), dpi=90)
    plt.close(fig)
    print(f"Wrote {out} ({n} frames, {os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    daily, weekly = rain_fields()
    plot_weekly_mjo(weekly)
    plot_daily_kelvin(daily)
    plot_mjo_animation(daily)
