"""
RMM index (Real-time Multivariate MJO, after Wheeler and Hendon 2004) for one
ECMWF extended-range forecast, and a phase diagram of every ensemble member,
the ensemble mean and the recent observed track. It needs no
wavenumber-frequency filter: the MJO is picked out by combining OLR with the
850 and 200 hPa zonal wind.

  1. Fields: OLR, u850 and u200 averaged over 15S-15N, as anomalies from the
     annual cycle (olr_wave_anomalies.fit_annual_cycle, ERA5), the forecasts
     also minus the lead-dependent reforecast bias (olr_wave_anomalies.lead_bias).
     The OLR anomalies are the ones olr_wave_anomalies.py wrote; the winds come
     from download_rmm_winds.py and get the same treatment here.
  2. Interannual variability: minus the mean of the previous MEAN_DAYS days.
     For a forecast day that window is the observations up to the start plus
     the member's own forecast since (Gottschalck et al. 2010).
  3. Patterns: each field is divided by its standard deviation over all
     longitudes and EOF days, and the two leading EOFs of the three combined
     are taken from ERA5 over EOF_YEARS, all seasons. WH04's own EOF file is
     no longer online; these explain the same share of the variance.
  4. Axes: the two PCs are scaled to unit standard deviation and rotated
     (orthogonal Procrustes) onto the Bureau of Meteorology's RMM1/RMM2 over
     the EOF days, so RMM1, RMM2 and phases 1-8 mean what they do everywhere
     else: 2-3 Indian Ocean, 4-5 Maritime Continent, 6-7 western Pacific,
     8-1 Western Hemisphere and Africa.
  5. Check: the observed index over the EOF days, and the recent observed
     track, against BoM's RMM. BoM uses NOAA satellite OLR and NCEP/ACCESS
     winds (and removed ENSO as well before 2014), so close but not identical
     agreement is expected: bivariate correlation ~0.95.

Writes data/<date>/mjo_rmm_index_<date>.nc and
plots/olr_waves/<date>/mjo_rmm_phase_diagram.png.

Run after olr_wave_anomalies.py and download_rmm_winds.py.

Env vars:
    DATE_STR      forecast init date, defaults to today-2
    OLR_OBS_DIR   ERA5 archive folder (default olr_obs/); BoM's RMM file is
                  cached there too, for the days bom.gov.au can't be reached
"""

import glob
import io
import os
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patheffects
import numpy as np
import pandas as pd
import xarray as xr

import olr_wave_anomalies as wa
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

print(f"RMM index for: {date_str}")

path = f'data/{date_str}/'
plot_path = f'plots/olr_waves/{date_str}/'

INIT = pd.Timestamp(date_str)
FIELDS = ("olr", "u850", "u200")
UNITS = {"olr": "W m-2", "u850": "m s-1", "u200": "m s-1"}
WH04_STD = {"olr": 15.1, "u850": 1.81, "u200": 4.81}    # WH04's normalisation factors, for comparison
LAT_MAX = 15
EOF_YEARS = ("2006", "2022")    # the years both ERA5 archives cover in full
MEAN_DAYS = 120                 # running mean removed as interannual variability
OBS_DAYS = 40                   # observed days drawn before the forecast
MEMBER_DAYS = {1: "red", 5: "hotpink", 10: "darkorange", 15: "tab:blue", 20: "tab:green"}   # forecast day: colour of the members' circles
MEAN_COLOR = "purple"

BOM = "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"
BOM_CACHE = os.path.join(OBS_DIR, "rmm.74toRealtime.txt")


# ============================================================
# 1. Fields
# ============================================================

def with_lat(x):
    """Add a one-point latitude (the equator) in front of longitude, so the
    15S-15N means go through olr_wave_anomalies' (.., latitude, longitude) functions."""
    return x.expand_dims(latitude=[0.0]).transpose(..., "latitude", "longitude")


def olr_fields():
    """Observed 15S-15N OLR anomalies over the EOF years, and the lead-in and
    forecast anomalies of olr_wave_anomalies.py."""
    files = sorted(glob.glob(os.path.join(OBS_DIR, "era5_olr_*.nc")))
    obs = xr.open_mfdataset(files, combine="by_coords").olr.sel(latitude=slice(LAT_MAX, -LAT_MAX))
    obs = with_lat(obs.sel(time=slice(*EOF_YEARS)).mean("latitude").load())
    clim = obs - wa.fit_annual_cycle(obs)(obs.time.values)
    an = xr.open_zarr(f"{path}olr_anom_{date_str}.zarr").sel(latitude=slice(LAT_MAX, -LAT_MAX))
    an = an[["obs_anom", "fc_anom"]].mean("latitude").load()
    return clim.squeeze("latitude"), an.obs_anom, an.fc_anom


def wind_fields(name):
    """The same three for a wind: ERA5 anomalies over the EOF years, lead-in
    anomalies and bias-corrected forecast anomalies."""
    files = sorted(glob.glob(os.path.join(OBS_DIR, "era5_u_15S15N_*.nc")))
    obs = with_lat(xr.open_mfdataset(files, combine="by_coords")[name].load())
    fc = with_lat(xr.open_dataset(f"{path}rmm_u_forecast_{date_str}.nc")[name].load())
    rf = with_lat(xr.open_dataset(f"{path}rmm_u_reforecast_{date_str}.nc")[name].load())
    leadin = with_lat(xr.open_dataset(f"{path}rmm_u_obs_leadin_{date_str}.nc")[name].load())

    print(f"{name}:")
    annual_cycle = wa.fit_annual_cycle(obs)
    bias, _, _ = wa.lead_bias(rf, obs, annual_cycle, units=UNITS[name])
    bias = bias.reindex(step=fc.step, method="ffill")       # the reforecasts are a day shorter
    clim = obs.sel(time=slice(*EOF_YEARS))
    clim = clim - annual_cycle(clim.time.values)
    return (clim.squeeze("latitude"), wa.obs_anomalies(leadin, annual_cycle, bias).squeeze("latitude"),
            wa.forecast_anomalies(fc, annual_cycle, bias).squeeze("latitude"))


# ============================================================
# 2. Interannual variability
# ============================================================

def minus_running_mean(x):
    """x (time, ...) minus the mean of the MEAN_DAYS days before each day.
    The first MEAN_DAYS days have no full window and are dropped."""
    c = np.cumsum(np.concatenate([np.zeros_like(x[:1]), x]), axis=0)
    return x[MEAN_DAYS:] - (c[MEAN_DAYS:-1] - c[:-MEAN_DAYS - 1]) / MEAN_DAYS


# ============================================================
# 3-4. EOFs and axes
# ============================================================

def eof_axes(clim):
    """clim: {field: ERA5 anomalies (time, longitude)} -> the per-field
    normalisation, the combined field's mean, the two EOFs (2, 3 x longitude)
    scaled so that projections have unit standard deviation, and the observed
    index on the EOF days."""
    times = clim["olr"].time.to_index()
    assert len(times) == (times[-1] - times[0]).days + 1
    x = {f: minus_running_mean(clim[f].values.astype(float)) for f in FIELDS}
    norm = {f: float(x[f].std()) for f in FIELDS}
    print("Normalisation (std over all longitudes and EOF days; WH04's in brackets): "
          + ", ".join(f"{f} {norm[f]:.2f} ({WH04_STD[f]}) {UNITS[f]}" for f in FIELDS))
    X = np.hstack([x[f] / norm[f] for f in FIELDS])
    centre = X.mean(axis=0)
    _, s, vt = np.linalg.svd(X - centre, full_matrices=False)
    var = s ** 2 / (s ** 2).sum()
    print(f"EOFs of the combined fields from {len(X)} ERA5 days ({EOF_YEARS[0]}-{EOF_YEARS[1]}): "
          f"explained variance {var[0]:.1%}, {var[1]:.1%} (WH04: 12.8%, 12.2%), then {var[2]:.1%}")
    eofs = vt[:2] / ((X - centre) @ vt[:2].T).std(axis=0)[:, None]
    index = pd.DataFrame((X - centre) @ eofs.T, index=times[MEAN_DAYS:], columns=["x", "y"])
    return norm, centre, eofs, index


def read_bom():
    """BoM's RMM1/RMM2 as (x, y), 1974 to a few days ago. bom.gov.au turns
    away requests without a browser user agent (and some networks altogether),
    so the last good download is kept in the archive folder."""
    try:
        text = urlopen(Request(BOM, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read().decode()
        os.makedirs(OBS_DIR, exist_ok=True)
        with open(BOM_CACHE, "w") as f:
            f.write(text)
    except Exception as e:
        if not os.path.exists(BOM_CACHE):
            raise RuntimeError(f"can't download BoM's RMM ({e}) and there is no earlier copy at {BOM_CACHE}")
        print(f"  can't download BoM's RMM ({e}): using the copy at {BOM_CACHE}")
        with open(BOM_CACHE) as f:
            text = f.read()
    df = pd.read_csv(io.StringIO(text), skiprows=2, sep=r"\s+", header=None, usecols=range(5),
                     names=["year", "month", "day", "x", "y"])
    df = df.set_index(pd.to_datetime(df[["year", "month", "day"]]))[["x", "y"]]
    return df[(df.abs() < 100).all(axis=1)]                 # missing days are 1e36 or 999


def align_to(ours, bom):
    """2x2 orthogonal matrix R (rotation, or rotation + reflection) that best
    maps our (x, y) onto BoM's (RMM1, RMM2) over the days both have
    (orthogonal Procrustes). Our EOF pair is only defined up to such a
    transform; this pins the axes and phases to WH04's."""
    both = ours.join(bom, how="inner", rsuffix="_bom")
    u, _, vt = np.linalg.svd(both[["x", "y"]].values.T @ both[["x_bom", "y_bom"]].values)
    r = u @ vt
    kind = "rotation" if np.linalg.det(r) > 0 else "rotation + reflection"
    print(f"  aligned to BoM's RMM over {len(both)} days: {kind} by "
          f"{np.degrees(np.arctan2(r[1, 0], r[0, 0])):+.0f} deg")
    return r


def phase(x, y):
    """MJO phase 1-8 of (RMM1, RMM2): 45-degree sectors, phase 1 from 180 degrees anticlockwise."""
    return (np.floor((np.degrees(np.arctan2(y, x)) + 180) / 45).astype(int) % 8) + 1


def compare(name, ours, bom, min_amp=1.0):
    both = ours.join(bom, how="inner", rsuffix="_bom")
    if both.empty:
        print(f"  vs {name}: no days in common")
        return
    a, b = both[["x", "y"]].values, both[["x_bom", "y_bom"]].values
    amp_a, amp_b = np.hypot(*a.T), np.hypot(*b.T)
    strong = (amp_a > min_amp) & (amp_b > min_amp)
    dphase = (phase(*a.T) - phase(*b.T) + 4) % 8 - 4
    print(f"  vs {name} ({len(both)} days): bivariate correlation "
          f"{(a * b).sum() / np.sqrt((a ** 2).sum() * (b ** 2).sum()):.2f}, RMSE {np.sqrt(((a - b) ** 2).sum(axis=1).mean()):.2f}, "
          f"mean amplitude {amp_a.mean():.2f} vs {amp_b.mean():.2f}; on the {strong.sum()} days both > {min_amp}: "
          f"same phase {(dphase[strong] == 0).mean():.0%}, within one phase {(np.abs(dphase[strong]) <= 1).mean():.0%}")


# ============================================================
# Run
# ============================================================

def main():
    clim, leadin, fc = {}, {}, {}
    clim["olr"], leadin["olr"], fc["olr"] = olr_fields()
    for name in ("u850", "u200"):
        clim[name], leadin[name], fc[name] = wind_fields(name)

    norm, centre, eofs, obs_index = eof_axes(clim)
    bom = read_bom()
    print("Observed index check:")
    align = align_to(obs_index, bom)
    obs_index[["x", "y"]] = obs_index[["x", "y"]].values @ align
    compare("BoM RMM", obs_index, bom)

    # forecast: the observed lead-in, then each member, as one series per member
    lead_times = leadin["u850"].time.to_index()
    n_obs = len(lead_times)
    if n_obs < MEAN_DAYS + 1:
        raise RuntimeError(f"the wind lead-in has {n_obs} days, the running mean alone needs {MEAN_DAYS}")
    numbers = fc["olr"].number.values
    X = []
    for f in FIELDS:
        o = leadin[f].sel(time=lead_times).values                                        # (time, lon)
        m = fc[f].sel(number=numbers).transpose("step", "number", "longitude").values    # (step, member, lon)
        o = np.broadcast_to(o[:, None], (n_obs,) + m.shape[1:])
        X.append(minus_running_mean(np.concatenate([o, m]).astype(float)) / norm[f])
    members = (np.concatenate(X, axis=-1) - centre) @ eofs.T @ align                     # (time, member, 2)
    times = pd.date_range(lead_times[MEAN_DAYS], periods=members.shape[0], freq="D")
    keep = times >= INIT - pd.Timedelta(days=OBS_DAYS)
    members, times = members[keep], times[keep]
    ens_mean = members.mean(axis=1)
    is_fc = times >= INIT
    assert is_fc.sum() == fc["olr"].sizes["step"]

    recent = pd.DataFrame(ens_mean[~is_fc], index=times[~is_fc], columns=["x", "y"])
    print("Recent observed track check:")
    compare("BoM RMM", recent, bom, min_amp=0.5)
    bom_xy = bom.reindex(times[~is_fc]).dropna().values

    ds = xr.Dataset(
        {"rmm1": (("time", "number"), members[..., 0].astype("float32")),
         "rmm2": (("time", "number"), members[..., 1].astype("float32")),
         "rmm1_mean": ("time", ens_mean[:, 0]), "rmm2_mean": ("time", ens_mean[:, 1])},
        coords={"time": times, "number": numbers, "is_forecast": ("time", is_fc)},
        attrs={"description": "RMM index (Wheeler and Hendon 2004): ERA5-based EOFs of 15S-15N OLR, u850 and u200, "
                              "rotated onto BoM's RMM1/RMM2; observed days are the same for every member",
               "eof_years": "-".join(EOF_YEARS)})
    ds.to_netcdf(f"{path}mjo_rmm_index_{date_str}.nc")
    print(f"Wrote {path}mjo_rmm_index_{date_str}.nc")

    plot_phase(members[is_fc], ens_mean, is_fc, times, bom_xy, f"{plot_path}mjo_rmm_phase_diagram.png")


# ============================================================
# Plot
# ============================================================

def phase_axes(ax, lim):
    """Square RMM diagram: unit circle, the eight phase sectors and the regions of convection."""
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.add_patch(plt.Circle((0, 0), 1, fill=False, color="grey", lw=1))
    for a in (0, 45, 90, 135):
        r = np.radians(a)
        ax.plot([-lim * np.cos(r) * 2, lim * np.cos(r) * 2], [-lim * np.sin(r) * 2, lim * np.sin(r) * 2],
                color="grey", lw=0.6, zorder=0)
    for p in range(1, 9):
        ang = np.radians(-180 + 45 * (p - 0.5))
        ax.text(0.85 * lim * np.cos(ang) / max(abs(np.cos(ang)), abs(np.sin(ang))),
                0.85 * lim * np.sin(ang) / max(abs(np.cos(ang)), abs(np.sin(ang))),
                str(p), ha="center", va="center", fontsize=16, color="grey")
    kw = dict(ha="center", va="center", fontsize=13, color="dimgrey")
    ax.text(0, -lim * 0.95, "Indian Ocean", **kw)
    ax.text(lim * 0.95, 0, "Maritime Continent", rotation=-90, **kw)
    ax.text(0, lim * 0.95, "Western Pacific", **kw)
    ax.text(-lim * 0.95, 0, "West. Hem. and Africa", rotation=90, **kw)


def plot_phase(members_fc, ens_mean, is_fc, times, bom_xy, out):
    obs_xy = ens_mean[~is_fc]
    fc_mean = np.vstack([obs_xy[-1:], ens_mean[is_fc]])       # join the mean onto the last observed day
    lim = max(3.0, float(np.ceil(np.abs(np.r_[members_fc.ravel(), obs_xy.ravel()]).max() * 2) / 2))

    fig, ax = plt.subplots(figsize=(9, 9), layout="constrained")
    phase_axes(ax, lim)
    ax.set_xlabel("RMM1", fontsize=13)
    ax.set_ylabel("RMM2", fontsize=13)
    ax.tick_params(labelsize=12)
    fc_times = times[is_fc]                                   # fc_mean[d] is valid on fc_times[d - 1]
    for day, color in MEMBER_DAYS.items():
        ax.scatter(*members_fc[day - 1].T, s=24, color=color, linewidths=0, zorder=2,
                   label=f"members, day {day} ({fc_times[day - 1]:%d %b})")
    ax.plot(fc_mean[:, 0], fc_mean[:, 1], color=MEAN_COLOR, lw=2.5, zorder=3, label="ensemble mean")
    for d in range(8, len(fc_mean), 7):                       # a week, two weeks, ... after the start
        ax.plot(*fc_mean[d], "o", color=MEAN_COLOR, ms=6, zorder=3)
        ax.annotate(f"{fc_times[d - 1]:%d %b}", fc_mean[d], textcoords="offset points", xytext=(5, 5),
                    fontsize=11, color="w", zorder=6,
                    path_effects=[patheffects.withStroke(linewidth=2, foreground="k")])
    ax.plot(obs_xy[:, 0], obs_xy[:, 1], color="k", lw=2, label=f"ERA5, last {len(obs_xy)} days")
    ax.plot(*obs_xy[0], "s", color="k", ms=6)
    ax.annotate(times[0].strftime("%d %b"), obs_xy[0], textcoords="offset points", xytext=(5, -12), fontsize=11)
    if len(bom_xy):
        ax.plot(bom_xy[:, 0], bom_xy[:, 1], color="grey", lw=1.5, ls="--", label="BoM RMM, same days")
    ax.plot(*obs_xy[-1], "o", color="k", ms=7, zorder=5)
    ax.legend(loc="lower left", fontsize=11, framealpha=0.9)
    ax.set_title(f"RMM index (Wheeler-Hendon), ECMWF extended range from {date_str}\n"
                 f"ensemble-mean dots every 7 days; EOFs of ERA5 OLR, u850 and u200, {EOF_YEARS[0]}-{EOF_YEARS[1]}",
                 fontsize=13)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
