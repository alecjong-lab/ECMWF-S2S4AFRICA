import os
from datetime import datetime, timedelta

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.colors import ListedColormap, BoundaryNorm, LinearSegmentedColormap
import numpy as np
import pandas as pd
import rioxarray
import xarray as xr

import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix = os.environ.get("MAIN_PATH", os.getcwd())
data_path = f'{prefix}/data/{date_str}'

country = os.environ.get("COUNTRY", "Kenya")

# same bbox convention (and duplication) as plot_s2s.py / plot_gefs.py / fetch_dynamical.py
bboxes = {
    "Namibia":    {"lat1": -16.5, "lon1": 11.5, "lat2": -30,   "lon2": 25.5},
    "Botswana":   {"lat1": -17.5, "lon1": 19.5, "lat2": -27,   "lon2": 30},
    "Kenya":      {"lat1": 6,     "lon1": 33,   "lat2": -5,    "lon2": 42},
    "Zambia":     {"lat1": -8,    "lon1": 21,   "lat2": -18.5, "lon2": 34},
    "Madagascar": {"lat1": -10.5, "lon1": 42,   "lat2": -27,   "lon2": 51},
    "Angola":     {"lat1": -5.5,  "lon1": 11.5, "lat2": -18,   "lon2": 24.5},
    "Ghana":      {"lat1": 12,    "lon1": -3.5, "lat2": 4,     "lon2": 1.5},
    "Senegal":    {"lat1": 17,    "lon1": -18,  "lat2": 12,    "lon2": -11.25},
    "Ethiopia":   {"lat1": 16.5,  "lon1": 31.5, "lat2": 1.5,   "lon2": 49.5},
    "Great_Horn": {"lat1": 25.5,  "lon1": 19.5, "lat2": -9,    "lon2": 57},
    "Zimbabwe":   {"lat1": -15,   "lon1": 25,   "lat2": -22.5, "lon2": 33.5},
    "Malawi":     {"lat1": -9,    "lon1": 31.5, "lat2": -18,   "lon2": 37.5},
}
bbox = bboxes[country]

# same Kenya county shapefile dowscale_dekade.py clips the downscaled forecast to
kenya_shapefile = "downscale_data/Kenya_Counties_KNSDI.shp"

plot_dir = f'plots/{country}/{date_str}/monthly'
os.makedirs(plot_dir, exist_ok=True)


def clip_to_kenya(ds):
    """
    Clip to Kenya's actual land shape (union of counties), not just its bbox.
    all_touched=True keeps every cell the shape touches rather than only cells
    whose center falls inside it -- on the coarse S2S 1.5deg grid, center-only
    clipping drops most border/coastal cells to NaN.
    """
    return gef.clip_to_shapefile(ds.rio.write_crs("EPSG:4326"), kenya_shapefile, all_touched=True)


def summarize(name, onset):
    found = onset.notnull()
    n_found, n_total = int(found.sum()), int(found.size)
    print(f"{name}: onset found for {n_found}/{n_total} points/members")
    if n_found:
        print(f"{name}: earliest onset {onset.min(skipna=True).values}, latest onset {onset.max(skipna=True).values}")


def clean_for_netcdf(da):
    """Strip attrs netCDF can't serialize (e.g. dict-valued 'statistics_approximate'
    on dynamical.org catalog coords) so .to_netcdf() doesn't blow up."""
    def safe(attrs):
        return {k: v for k, v in attrs.items()
                 if isinstance(v, (str, bytes, int, float, np.integer, np.floating, np.ndarray, list, tuple))}

    da = da.copy()
    da.attrs = safe(da.attrs)
    for name in da.coords:
        da.coords[name].attrs = safe(da.coords[name].attrs)
    return da


def build_discrete_cmap(vmin, vmax, n_shades=4):
    """Discrete colormap: 5 main color bands (sand, green, cyan, pink-purple, gray),
    each split into n_shades discrete light->dark steps.
    Returns (cmap, norm, boundaries, segment_edges)."""
    segments = [
        ("#EFDFC0", "#8B5A2B"),  # sand
        ("#B9E3A8", "#1B5E20"),  # green
        ("#A9F0EC", "#00838F"),  # cyan
        ("#F3BEDE", "#7B2D8E"),  # pink-purple
        ("#E3E3E3", "#4D4D4D"),  # gray
    ]

    # 6 edges marking where one main color band switches to the next
    segment_edges = np.linspace(vmin, vmax, len(segments) + 1)

    colors = []
    boundaries = [segment_edges[0]]
    for i, (c_light, c_dark) in enumerate(segments):
        seg_cmap = LinearSegmentedColormap.from_list("", [c_light, c_dark])
        # discrete shades within this band, sampled at bin centers for even spacing
        shade_positions = (np.arange(n_shades) + 0.5) / n_shades
        colors.extend(seg_cmap(shade_positions))

        # sub-boundaries within this band
        sub_edges = np.linspace(segment_edges[i], segment_edges[i + 1], n_shades + 1)[1:]
        boundaries.extend(sub_edges)

    cmap = ListedColormap(colors, name="onset_bands_discrete")
    norm = BoundaryNorm(boundaries, ncolors=cmap.N)
    return cmap, norm, np.array(boundaries), segment_edges


def plot_onset_map(onset, bbox, year, title, save_path, forecast_start, n_time, search_days=21):
    """
    Map of ensemble-mean onset day-of-year (deterministic sources plot their
    single onset field directly), with % of members finding a valid onset
    annotated per grid cell where an ensemble dimension is present.

    The color scale runs from the forecast's first day (forecast_start) to
    the last day that still leaves a full search_days window for the
    dry-spell check (see _rainfall_onset_nd's t_max) -- i.e. the actual
    achievable onset range for this forecast, not just whichever onset dates
    happened to occur. Fixing it to the forecast window (rather than the
    data's own min/max) puts every source on a comparable "days since
    forecast start" scale regardless of which onsets it actually found.
    """
    onset_doy = onset.dt.dayofyear  # NaT -> NaN

    has_ensemble = 'number' in onset_doy.dims
    if has_ensemble:
        mean_doy = onset_doy.mean(dim='number', skipna=True)
        pct_valid = onset_doy.notnull().mean(dim='number') * 100
    else:
        mean_doy = onset_doy
        pct_valid = None

    mean_doy = mean_doy.sel(longitude=slice(bbox['lon1'], bbox['lon2']), latitude=slice(bbox['lat1'], bbox['lat2']))
    # per-cell "% of members" text only stays legible on a coarse grid (e.g. S2S);
    # a fine grid (e.g. GEFS's ~0.25deg) draws contour lines of that field instead
    show_text = show_contour = False
    if pct_valid is not None:
        pct_valid = pct_valid.sel(longitude=slice(bbox['lon1'], bbox['lon2']), latitude=slice(bbox['lat1'], bbox['lat2']))
        is_coarse = pct_valid.sizes['latitude'] * pct_valid.sizes['longitude'] <= 200
        show_text = is_coarse
        show_contour = not is_coarse

    if bool(mean_doy.isnull().all()):
        print(f"{title}: no onset found anywhere, skipping plot")
        return

    vmin = float(forecast_start.dayofyear)
    vmax = float((forecast_start + pd.Timedelta(days=n_time - search_days)).dayofyear)
    if vmin == vmax:
        vmax = vmin + 1  # BoundaryNorm needs a non-degenerate range
    onset_cmap, onset_norm, boundaries, segment_edges = build_discrete_cmap(vmin, vmax, n_shades=4)

    fig, ax = plt.subplots(figsize=(9, 7), subplot_kw={'projection': ccrs.PlateCarree()})

    mesh = mean_doy.plot.pcolormesh(
        x='longitude', y='latitude', ax=ax, cmap=onset_cmap, norm=onset_norm,
        transform=ccrs.PlateCarree(), add_colorbar=False,
    )

    ax.coastlines(resolution='10m', linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, linewidth=0.6)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color='gray', alpha=0.5, linestyle='--')
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {'size': 11}
    gl.ylabel_style = {'size': 11}
    ax.set_title(title, fontsize=15, fontweight='bold', pad=10)

    if show_text or show_contour:
        lon2d, lat2d = np.meshgrid(pct_valid.longitude.values, pct_valid.latitude.values)
        pct_vals = pct_valid.values

        if show_contour and np.isfinite(pct_vals).sum() >= 4:  # contour needs a few real points to work with
            # levels fit to this source's own % range rather than a fixed
            # 25/50/75 -- a source whose members rarely agree (e.g. GEFS
            # topping out well under 50%) would otherwise show no lines at all
            finite = pct_vals[np.isfinite(pct_vals)]
            levels = np.linspace(finite.min(), finite.max(), 5)[1:-1]
            if len(levels) >= 2 and levels[-1] > levels[0]:
                cs = ax.contour(
                    lon2d, lat2d, pct_vals, levels=levels,
                    colors='red', linewidths=1.5, transform=ccrs.PlateCarree(),
                )
                # one label per level, placed on its longest segment -- every
                # level always gets a label here, even if that puts it close
                # to a neighboring level's label (e.g. a steep gradient); the
                # per-level pick already keeps a single disconnected-patch
                # level from producing multiple cluttered labels. The first
                # and last levels are pinned to the top (max-latitude) of
                # their segment and the level(s) in between to the bottom
                # (min-latitude), alternating vertical position to cut down
                # on overlap between adjacent levels' labels. Horizontally,
                # the first level is pushed to the rightmost point of that
                # band and every other level to the leftmost, so a typical
                # 3-level set spreads across top-right / bottom-left /
                # top-left corners instead of clustering on one side.
                n_levels = len(cs.allsegs)
                label_pos = []
                for i, segs in enumerate(cs.allsegs):
                    longest = max((s for s in segs if len(s) >= 2), key=len, default=None)
                    if longest is None:
                        continue
                    is_edge_level = i == 0 or i == n_levels - 1
                    lat = longest[:, 1]
                    target_lat = lat.max() if is_edge_level else lat.min()
                    near_target = np.isclose(lat, target_lat, atol=(lat.max() - lat.min()) * 0.1 or 1e-6)
                    candidates = longest[near_target]
                    lon_idx = np.argmax(candidates[:, 0]) if i == 0 else np.argmin(candidates[:, 0])
                    label_pos.append(tuple(candidates[lon_idx]))

                if label_pos:
                    clabels = ax.clabel(cs, inline=True, fontsize=12, fmt='%d%%', colors='white', manual=label_pos)
                    # nudge each label a little off its exact line position so
                    # text doesn't sit flush on the contour, alternating left/
                    # right by index for a bit of extra separation; clamped to
                    # the axes' actual rendered extent (not the nominal bbox,
                    # which can be wider than the data actually drawn) so a
                    # label can never land outside the visible plot.
                    xlim, ylim = ax.get_xlim(), ax.get_ylim()
                    shift = 0.02 * (xlim[1] - xlim[0])
                    # inset margin so a clamped label's text box doesn't get
                    # cut off by the axes frame
                    margin_x = 0.06 * (xlim[1] - xlim[0])
                    margin_y = 0.06 * (ylim[1] - ylim[0])
                    for i, lbl in enumerate(clabels):
                        lbl.set_bbox(dict(facecolor='black', edgecolor='none', pad=1))
                        lbl.set_rotation(0)
                        x, y = lbl.get_position()
                        dx = shift if i % 2 == 0 else -shift
                        x = min(max(x + dx, xlim[0] + margin_x), xlim[1] - margin_x)
                        y = min(max(y, ylim[0] + margin_y), ylim[1] - margin_y)
                        lbl.set_position((x, y))

        if show_text:
            for i in range(lat2d.shape[0]):
                for j in range(lat2d.shape[1]):
                    val = pct_vals[i, j]
                    if not np.isnan(val):
                        ax.text(
                            lon2d[i, j], lat2d[i, j], f'{val:.0f}%',
                            transform=ccrs.PlateCarree(), ha='center', va='center',
                            fontsize=18, color='black',
                            path_effects=[pe.withStroke(linewidth=2, foreground='white')],
                        )

    cbar = fig.colorbar(
        mesh, ax=ax, orientation='vertical', pad=0.03, shrink=0.85, aspect=25,
        boundaries=boundaries, ticks=segment_edges,
    )

    # day-of-year has no year attached, so pick the year the forecast was issued in
    labels = [
        (pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(days=t - 1)).strftime('%b %d')
        for t in segment_edges
    ]
    cbar.set_ticklabels(labels)
    cbar.set_label('Onset date', fontsize=13)
    cbar.ax.tick_params(labelsize=11)

    fig.tight_layout()
    plt.savefig(save_path, bbox_inches='tight')
    plt.close(fig)


# S2S has the longest forecast window (~46 days) of the three sources, so its
# window is used as the universal colorbar scale for all three plots -- giving
# every plot the same length scale instead of each being cut to its own
# (shorter) forecast horizon. Falls back to a source's own window if the S2S
# computation itself fails.
universal_forecast_start = None
universal_n_time = None

# ---- S2S ECMWF forecast ---------------------------------------------------
s2s_path = f'{data_path}/ECMWF_s2s_precip_{date_str}.zarr'
try:
    s2s = xr.open_zarr(s2s_path, consolidated=True).compute()
    s2s = s2s.sel(latitude=slice(bbox['lat1'], bbox['lat2']), longitude=slice(bbox['lon1'], bbox['lon2']))
    # left un-clipped: at S2S's coarse 1.5deg resolution, shapefile clipping
    # is too blocky to be meaningful (a cell easily spans well past the border)

    s2s_daily = s2s.diff('step').tp
    s2s_daily.attrs = s2s.tp.attrs
    valid_time = s2s.time + s2s_daily.step

    onset_s2s = gef.rainfall_onset_date(s2s_daily, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_s2s).to_netcdf(f'{data_path}/rainfall_onset_s2s_{country}.nc')
    summarize('S2S', onset_s2s)

    title = (
        f'S2S rainy season onset — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    universal_forecast_start = pd.Timestamp(valid_time.min().values)
    universal_n_time = s2s_daily.sizes['step']
    plot_onset_map(onset_s2s, bbox, pd.Timestamp(s2s.time.values).year, title, f'{plot_dir}/onset_s2s.png',
                   forecast_start=universal_forecast_start, n_time=universal_n_time)

    onset_s2s_accum = gef.rainfall_onset_date_accum(s2s_daily, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_s2s_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_s2s_{country}.nc')
    summarize('S2S (accum)', onset_s2s_accum)

    title_accum = (
        f'S2S start of growing season — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_s2s_accum, bbox, pd.Timestamp(s2s.time.values).year, title_accum,
                   f'{plot_dir}/onset_s2s_accum.png',
                   forecast_start=universal_forecast_start, n_time=universal_n_time, search_days=30)
except Exception as e:
    print(f"S2S: could not compute onset from {s2s_path} ({e}), skipping")

# ---- GEFS forecast ----------------------------------------------------------
gefs_path = f'{data_path}/gefs/gefs_{country.lower()}.zarr'
try:
    gefs = xr.open_zarr(gefs_path).compute()
    if country == 'Kenya':
        gefs = clip_to_kenya(gefs)
    valid_time = gefs.time + gefs.step

    onset_gefs = gef.rainfall_onset_date(gefs.tp, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_gefs).to_netcdf(f'{data_path}/rainfall_onset_gefs_{country}.nc')
    summarize('GEFS', onset_gefs)

    title = (
        f'GEFS rainy season onset — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_gefs, bbox, pd.Timestamp(gefs.time.values).year, title, f'{plot_dir}/onset_gefs.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                   n_time=universal_n_time or gefs.tp.sizes['step'])

    onset_gefs_accum = gef.rainfall_onset_date_accum(gefs.tp, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_gefs_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_gefs_{country}.nc')
    summarize('GEFS (accum)', onset_gefs_accum)

    title_accum = (
        f'GEFS start of growing season — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_gefs_accum, bbox, pd.Timestamp(gefs.time.values).year, title_accum,
                   f'{plot_dir}/onset_gefs_accum.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                   n_time=universal_n_time or gefs.tp.sizes['step'], search_days=30)
except Exception as e:
    print(f"GEFS: could not compute onset from {gefs_path} ({e}), skipping")

# ---- daily disaggregated downscaled forecast (Kenya only) -------------------
if country == 'Kenya':
    downscaled_path = f'{data_path}/daily_downscaled_kenya.tif'
    try:
        da = rioxarray.open_rasterio(downscaled_path, masked=True)
        da = da.rename({'y': 'latitude', 'x': 'longitude', 'band': 'step'})
        da = clip_to_kenya(da)

        # each band is one disaggregated lead day (band 1 = lead day 1, ...)
        init_date = np.datetime64(date_str)
        valid_time = xr.DataArray(
            init_date + da.step.values.astype('timedelta64[D]'),
            dims='step', coords={'step': da.step}
        )

        onset_downscaled = gef.rainfall_onset_date(da, time_dim='step', valid_time=valid_time)
        clean_for_netcdf(onset_downscaled).to_netcdf(f'{data_path}/rainfall_onset_downscaled_{country}.nc')
        summarize('downscaled', onset_downscaled)

        title = (
            f'Downscaled rainy season onset — {country}\n'
            f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
            f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
        )
        plot_onset_map(onset_downscaled, bbox, pd.Timestamp(init_date).year, title, f'{plot_dir}/onset_downscaled.png',
                       forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                       n_time=universal_n_time or da.sizes['step'])

        onset_downscaled_accum = gef.rainfall_onset_date_accum(da, time_dim='step', valid_time=valid_time)
        clean_for_netcdf(onset_downscaled_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_downscaled_{country}.nc')
        summarize('downscaled (accum)', onset_downscaled_accum)

        title_accum = (
            f'Downscaled start of growing season — {country}\n'
            f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
            f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
        )
        plot_onset_map(onset_downscaled_accum, bbox, pd.Timestamp(init_date).year, title_accum,
                       f'{plot_dir}/onset_downscaled_accum.png',
                       forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                       n_time=universal_n_time or da.sizes['step'], search_days=30)
    except Exception as e:
        print(f"downscaled: could not compute onset from {downscaled_path} ({e}), skipping")
else:
    print("downscaled: only available for Kenya, skipping")
