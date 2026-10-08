"""
OLR anomalies for the tropical wave filtering of one ECMWF extended-range
forecast -- steps 1-2 of Janiga et al. (2018, MWR) -- from what
download_olr_waves.py wrote to data/<date>/ and the ERA5 archive built by
olr_obs_archive.py:

  1. Annual cycle: the mean plus the first 4 harmonics of ERA5 OLR over
     CLIM_YEARS, at every grid point. Subtracted from the observed lead-in and
     from the forecast alike.
  2. Lead-dependent model bias, from the reforecasts started near this calendar
     day in each year that ERA5 covers: per lead, the reforecast ensemble-mean
     anomaly averaged over the years. With ~18 years the plain average is
     mostly sampling noise beyond week 1 (standard error ~6-7 W m-2 against a
     ~5 W m-2 bias), so two things cut it down:
       * the same years' observed anomalies are used as a control variate:
         bias = mean(rf_anom - beta * obs_anom), beta = how much the reforecast
         still tracks the observations at that lead (~1 on day 1, ~0 by week 4,
         fitted over 15S-15N). On day 1 that is the paired reforecast-minus-ERA5
         difference; at long leads it is the reforecast's own climate minus the
         observed annual cycle, free of the observed weather in those years.
       * the bias is smoothed over neighbouring leads, not at all on day 1
         and widening to +-7 days by day 15 (it changes slowly after week 1).
     That leaves ~2-2.5 W m-2 standard error, and maps from odd vs even years
     correlate 0.6-0.8 (checked for the 2026-09-30 forecast).
  3. Anomalies: lead-in minus annual cycle, with the few days ERA5T hasn't
     reached (ECMWF lead-N forecasts, see download_olr_waves.py) also minus the
     lead-N bias; forecast members minus annual cycle minus the lead bias.

Writes data/<date>/olr_anom_<date>.zarr with
    obs_anom (time, latitude, longitude)          the 730-day lead-in
    fc_anom  (number, step, latitude, longitude)  every member
    bias     (step, latitude, longitude), beta (step)
and a check plot plots/olr_waves/<date>/anomaly_join.png of the equatorial
anomalies across the observation -> forecast join.

Env vars:
    DATE_STR      forecast init date, defaults to today-2
    OLR_OBS_DIR   ERA5 OLR archive folder (default olr_obs/)
"""

import glob
import os
import shutil
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.transforms import blended_transform_factory
import numpy as np
import pandas as pd
import xarray as xr

from olr_obs_archive import OUT_DIR as OBS_DIR

# ============================================================
# CONFIG
# ============================================================

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

print(f"OLR anomalies for the tropical wave filtering: {date_str}")

path = f'data/{date_str}/'
plot_path = f'plots/olr_waves/{date_str}/'

CLIM_YEARS = ("2006", "2022")   # annual cycle fit period (reforecast years ERA5 fully covers)
N_HARMONICS = 4
BETA_LAT = 15                   # beta is fitted over 15S-15N
MAX_LEAD_SMOOTHING = 7          # +- days, reached by day 15
KENYA_LON = (33, 42)            # boxed on the Hovmoellers; Kenya's bbox in plot_s2s.py / fetch_dynamical.py


# ============================================================
# 1. Annual cycle
# ============================================================

def harmonics(times):
    """Design matrix: 1, cos(k*phase), sin(k*phase) for k = 1..N_HARMONICS."""
    days = (pd.DatetimeIndex(times) - pd.Timestamp("2000-01-01")).days.values
    phase = 2 * np.pi * days / 365.25
    cols = [np.ones_like(phase)]
    for k in range(1, N_HARMONICS + 1):
        cols += [np.cos(k * phase), np.sin(k * phase)]
    return np.column_stack(cols)


def fit_annual_cycle(obs):
    """Least-squares harmonic fit at every grid point; returns a function
    times -> annual cycle DataArray (time, latitude, longitude)."""
    fit = obs.sel(time=slice(*CLIM_YEARS))
    coef, *_ = np.linalg.lstsq(harmonics(fit.time.values),
                               fit.values.reshape(fit.sizes["time"], -1), rcond=None)
    shape = (obs.sizes["latitude"], obs.sizes["longitude"])

    def annual_cycle(times):
        times = pd.DatetimeIndex(times, name="time")
        return xr.DataArray((harmonics(times) @ coef).reshape(len(times), *shape),
                            coords={"time": times, "latitude": obs.latitude, "longitude": obs.longitude},
                            dims=("time", "latitude", "longitude"))
    print(f"Annual cycle: mean + {N_HARMONICS} harmonics of ERA5 {CLIM_YEARS[0]}-{CLIM_YEARS[1]} "
          f"({fit.sizes['time']} days)")
    return annual_cycle


# ============================================================
# 2. Lead-dependent bias
# ============================================================

def lead_half_width(lead):
    """Smoothing half-width in days for 1-based lead: 0 on day 1, +-7 by day 15."""
    return min(MAX_LEAD_SMOOTHING, (lead - 1) // 2)


def smooth_over_leads(x):
    """Running mean over the leading 'step' axis with a lead-dependent width."""
    out = np.empty_like(x)
    n = x.shape[0]
    for s in range(n):
        h = lead_half_width(s + 1)
        out[s] = x[max(0, s - h):min(n, s + h + 1)].mean(axis=0)
    return out


def lead_bias(rf, obs, annual_cycle):
    rf_anom, obs_anom, years = [], [], []
    for init in rf.init_time.values:
        valid = pd.Timestamp(init) + rf.step.to_index() - pd.Timedelta(days=1)
        o = obs.reindex(time=valid)
        if o.isnull().any():
            print(f"  reforecast {str(init)[:10]}: ERA5 archive doesn't cover it, left out")
            continue
        ac = annual_cycle(valid).values
        rf_anom.append(rf.sel(init_time=init).values - ac)
        obs_anom.append(o.values - ac)
        years.append(pd.Timestamp(init).year)
    r, o = np.array(rf_anom), np.array(obs_anom)            # (year, step, lat, lon)
    if len(years) < 10:
        raise RuntimeError(f"only {len(years)} reforecast years have ERA5 to compare with")

    band = np.abs(rf.latitude.values) <= BETA_LAT
    w = np.cos(np.deg2rad(rf.latitude.values))[band][:, None]
    beta = np.array([(w * r[:, s][:, band] * o[:, s][:, band]).sum() /
                     (w * o[:, s][:, band] ** 2).sum() for s in range(r.shape[1])])
    cv = r - beta[None, :, None, None] * o
    bias = smooth_over_leads(cv.mean(axis=0))
    se = np.std([smooth_over_leads(c) for c in cv], axis=0, ddof=1) / np.sqrt(len(years))

    def eq_rms(x):
        return float(np.sqrt((w * x[band] ** 2).sum() / (w.sum() * x.shape[-1])))
    print(f"Lead bias from {len(years)} reforecast years ({years[0]}-{years[-1]}), 15S-15N:")
    for s in (0, 2, 6, 13, 27, 45):
        print(f"  lead {s + 1:2d}: beta {beta[s]:.2f}  bias RMS {eq_rms(bias[s]):.1f}  "
              f"mean {float((w * bias[s][band]).sum() / (w.sum() * bias.shape[-1])):+.1f}  "
              f"~SE {eq_rms(se[s]):.1f} W m-2")

    coords = {"step": rf.step, "latitude": rf.latitude, "longitude": rf.longitude}
    return (xr.DataArray(bias, coords=coords, dims=("step", "latitude", "longitude")),
            xr.DataArray(beta, coords={"step": rf.step}, dims="step"), years)


# ============================================================
# 3. Anomalies
# ============================================================

def obs_anomalies(leadin, annual_cycle, bias):
    anom = leadin - annual_cycle(leadin.time.values)
    for i, source in enumerate(leadin.source.values):
        source = str(source)
        if source.startswith("ecmwf_lead"):
            lead = int(source.removeprefix("ecmwf_lead"))
            anom[i] = anom[i] - bias.sel(step=pd.Timedelta(days=lead))
    return anom


def forecast_anomalies(fc, annual_cycle, bias):
    ac = annual_cycle(fc.valid_date.values).rename(time="step").assign_coords(step=fc.step)
    return fc - ac - bias


# ============================================================
# Check plot
# ============================================================

def equatorial_mean(x, lat=10):
    x = x.sel(latitude=slice(lat, -lat))
    return x.weighted(np.cos(np.deg2rad(x.latitude))).mean("latitude")


def lon_0_360(x):
    """Longitudes -180..178.5 -> 0..358.5, so Hovmoellers run 0E on the left
    through 180 in the middle."""
    return x.assign_coords(longitude=x.longitude % 360).sortby("longitude")


def hovmoeller(ax, hov, vmax=50, cmap="RdBu_r", norm=None, label_size=8):
    """Shaded time-longitude plot of a lon_0_360 DataArray (time, longitude),
    time running downward, with Kenya's longitudes boxed. A given norm
    replaces the symmetric -vmax..vmax range."""
    limits = {} if norm is not None else {"vmin": -vmax, "vmax": vmax}
    pc = ax.pcolormesh(hov.longitude, hov.time, hov, cmap=cmap, norm=norm, shading="auto", **limits)
    # explicit limits rather than invert_yaxis(), which toggles and so cancels
    # out on axes that share y
    ax.set_ylim(hov.time.values[-1], hov.time.values[0])
    ax.axvspan(*KENYA_LON, facecolor="none", edgecolor="k", lw=1.5, zorder=5)
    ax.text(sum(KENYA_LON) / 2, 1.005, "Kenya", ha="center", va="bottom", fontsize=label_size,
            transform=blended_transform_factory(ax.transData, ax.transAxes))
    ax.set_xlim(0, 360)
    ax.set_xticks(range(0, 361, 60))
    ax.set_xticklabels(["0", "60E", "120E", "180", "120W", "60W", "0"])
    ax.set_xlabel("longitude")
    return pc


def plot_join(obs_anom, fc_anom, fc_raw_anom, out, show_days=90):
    obs = obs_anom.isel(time=slice(-show_days, None))
    t_obs = obs.time.to_index()
    t_fc = pd.DatetimeIndex(fc_anom.valid_date.values)
    gap = np.array([str(s).startswith("ecmwf") for s in obs.source.values])

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(21, 7), gridspec_kw={"width_ratios": [1.1, 1, 1]},
                                        layout="constrained")

    # (a) 15S-15N, all longitudes
    o = equatorial_mean(obs, 15).mean("longitude")
    f = equatorial_mean(fc_anom, 15).mean("longitude")
    f_raw = equatorial_mean(fc_raw_anom, 15).mean("longitude").mean("number")
    ax1.plot(t_obs, o, color="k", label="ERA5")
    ax1.plot(t_obs[gap], o[gap], "o", color="tab:orange", ms=4, label="ECMWF day 1 (ERA5T gap), bias-corrected")
    ax1.fill_between(t_fc, f.quantile(0.1, "number"), f.quantile(0.9, "number"), color="tab:blue", alpha=0.2,
                     label="forecast members 10-90%")
    ax1.plot(t_fc, f.mean("number"), color="tab:blue", label="forecast ensemble mean, bias-corrected")
    ax1.plot(t_fc, f_raw, color="tab:blue", ls="--", lw=1, label="... without bias correction")
    ax1.axvline(t_fc[0], color="grey", lw=0.8)
    ax1.axhline(0, color="grey", lw=0.5)
    ax1.set_ylabel("OLR anomaly (W m$^{-2}$), 15S-15N mean")
    ax1.set_title("(a) Tropical-mean OLR anomaly across the join")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.tick_params(axis="x", rotation=30)

    # (b), (c) Hovmoellers, 10S-10N: ensemble mean, and one member for the
    # day-to-day detail the ensemble mean averages away
    obs_hov = equatorial_mean(obs, 10).drop_vars("source")
    for ax, fc, label in ((ax2, fc_anom.mean("number"), "(b) ... then forecast ensemble mean"),
                          (ax3, fc_anom.sel(number=0), "(c) ... then forecast member 0 (control)")):
        fc_hov = equatorial_mean(fc, 10).swap_dims(step="valid_date").drop_vars("step").rename(valid_date="time")
        hov = lon_0_360(xr.concat([obs_hov, fc_hov.drop_vars("number", errors="ignore")], dim="time"))
        pc = hovmoeller(ax, hov)
        ax.axhline(t_fc[0], color="k", lw=1.2)
        ax.text(3, t_fc[0], " forecast starts", va="bottom", fontsize=8)
        ax.set_title(f"{label}\n10S-10N OLR anomaly, ERA5 before the line", fontsize=10)
    fig.colorbar(pc, ax=[ax2, ax3], label="W m$^{-2}$ (negative = enhanced convection)", shrink=0.9)

    fig.suptitle(f"ECMWF extended-range OLR, init {date_str}: anomalies vs ERA5 "
                 f"{CLIM_YEARS[0]}-{CLIM_YEARS[1]} annual cycle, lead-dependent bias removed")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"Wrote {out}")


def main():
    files = sorted(glob.glob(os.path.join(OBS_DIR, "era5_olr_*.nc")))
    fc = xr.open_zarr(f"{path}ECMWF_s2s_olr_{date_str}.zarr").olr.load()
    rf = xr.open_zarr(f"{path}ECMWF_s2s_olr_reforecast_{date_str}.zarr").olr.load()
    leadin = xr.open_zarr(f"{path}olr_obs_leadin_{date_str}.zarr").olr.load()
    lat = slice(float(fc.latitude.max()), float(fc.latitude.min()))
    obs = xr.open_mfdataset(files, combine="by_coords").olr.sel(latitude=lat).load()

    annual_cycle = fit_annual_cycle(obs)
    bias, beta, years = lead_bias(rf, obs, annual_cycle)
    obs_anom = obs_anomalies(leadin, annual_cycle, bias)
    fc_anom = forecast_anomalies(fc, annual_cycle, bias)

    # the join: observed anomaly just before vs forecast just after, 15S-15N
    eq = lambda x: float(equatorial_mean(x, 15).mean())
    is_gap = np.array([str(s).startswith("ecmwf") for s in obs_anom.source.values])
    print(f"15S-15N mean anomaly: last 7 ERA5 days {eq(obs_anom.isel(time=np.where(~is_gap)[0][-7:])):+.1f}, "
          f"gap days {eq(obs_anom.isel(time=is_gap)):+.1f}, "
          f"forecast days 1-7 {eq(fc_anom.isel(step=slice(0, 7))):+.1f} "
          f"(without bias correction {eq((fc_anom + bias).isel(step=slice(0, 7))):+.1f}) W m-2")

    out = f"{path}olr_anom_{date_str}.zarr"
    ds = xr.Dataset({
        "obs_anom": obs_anom.astype("float32"),
        "fc_anom": fc_anom.astype("float32"),
        "bias": bias.astype("float32"),
        "beta": beta,
    })
    for v in ("obs_anom", "fc_anom", "bias"):
        ds[v].attrs = {"units": "W m-2"}
    ds.attrs = {"annual_cycle": f"mean + {N_HARMONICS} harmonics, ERA5 {CLIM_YEARS[0]}-{CLIM_YEARS[1]}",
                "bias_years": ", ".join(map(str, years))}
    if os.path.exists(out):
        shutil.rmtree(out)
    ds.to_zarr(out, mode="w")
    print(f"Wrote {out}")

    plot_join(obs_anom, fc_anom, fc_anom + bias, f"{plot_path}anomaly_join.png")


if __name__ == "__main__":
    main()
