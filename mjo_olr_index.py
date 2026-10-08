"""
OLR-based MJO index (OMI-style, after Kiladis et al. 2014) for one ECMWF
extended-range forecast, and a phase diagram of every ensemble member, the
ensemble mean and the recent observed track -- mainly to check the OLR wave
pipeline against the MJO forecasts other centres publish.

  1. Patterns: the two leading EOFs of MJO-filtered ERA5 OLR (the Janiga MJO
     band of olr_wave_filter.py: eastward wavenumbers 0-9, 20-100 days) over
     20S-20N, on days within +-EOF_HALF_WINDOW days (by calendar day) of the
     middle of the forecast, in EOF_YEARS -- seasonally varying patterns like
     OMI's, but one pair per forecast rather than one per day.
  2. Axes: each EOF's index is scaled to unit standard deviation over the EOF
     days (amplitude 1 = typical MJO), then the pair is rotated (orthogonal
     Procrustes) onto PSL's OMI as plotted, (PC2, -PC1), over those days, so
     phases 1-8 mean what they do on OMI/RMM diagrams: 2-3 Indian Ocean,
     4-5 Maritime Continent, 6-7 western Pacific, 8-1 Western Hemisphere and
     Africa. (Before that, a first guess puts x > 0 at Maritime Continent
     convection and y > 0 at western Pacific convection.)
  3. Forecast: each member's padded series (data/<date>/olr_anom_<date>.zarr)
     is MJO-filtered exactly as in olr_wave_filter.py and projected onto the
     axes. The observed track (last OBS_DAYS days) is the ensemble-mean-padded
     filtering, like a real-time analysis.
  4. Check: the observed index over the EOF days, and the recent observed
     track, against PSL's OMI / real-time OMI (ROMI), which are computed
     differently (NOAA OLR, per-day EOFs, ROMI's 40-day mean removal), so
     close but not identical agreement is expected.

Writes data/<date>/mjo_olr_index_<date>.nc and
plots/olr_waves/<date>/mjo_phase_diagram.png.

Env vars:
    DATE_STR      forecast init date, defaults to today-2
    OLR_OBS_DIR   ERA5 OLR archive folder (default olr_obs/)
"""

import glob
import io
import os
from datetime import datetime, timedelta
from urllib.request import urlopen

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.fft
import xarray as xr

import olr_wave_anomalies as wa
import olr_wave_filter as wf
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

print(f"OLR MJO index for: {date_str}")

path = f'data/{date_str}/'
plot_path = f'plots/olr_waves/{date_str}/'

INIT = pd.Timestamp(date_str)
EOF_LAT = 20
EOF_YEARS = ("2007", "2021")    # ERA5 archive minus a year at each end (filter edge effects)
EOF_HALF_WINDOW = 60            # days either side of the forecast's middle, every year
OBS_DAYS = 40                   # observed days drawn before the forecast
MC_LON, WP_LON = 120, 165       # Maritime Continent / western Pacific reference longitudes
MEMBER_BATCH = 5
MJO = wf.WAVES["MJO"]

PSL = "https://psl.noaa.gov/mjo/mjoindex/"


# ============================================================
# MJO filtering of many series at once
# ============================================================

def mjo_filter(series, n_valid):
    """MJO band of olr_wave_filter.filter_series for series (time, ..., lat, lon)
    with any dims in between (e.g. members): high-pass, taper, then the band
    mask. The MJO band only keeps |wavenumber| <= 9, so the longitude FFT is
    truncated to those before the (expensive) time FFT -- same result."""
    x = wf.taper(wf.running_mean_highpass(series, n_valid, MJO["highpass"]))
    n_time, n_lon = x.shape[0], x.shape[-1]
    kmax = max(abs(k) for k in MJO["k"])
    cols = np.r_[0:kmax + 1, n_lon - kmax:n_lon]
    spec = scipy.fft.fft(x, axis=-1, workers=-1)[..., cols]
    spec = scipy.fft.fft(spec, axis=0, workers=-1)
    mask = wf.band_mask(n_time, n_lon, MJO["k"], MJO["period"])[:, cols]
    spec *= mask.reshape((n_time,) + (1,) * (x.ndim - 2) + (len(cols),))
    spec = scipy.fft.ifft(spec, axis=0, workers=-1)
    full = np.zeros(spec.shape[:-1] + (n_lon,), dtype=complex)
    full[..., cols] = spec
    return scipy.fft.ifft(full, axis=-1, workers=-1).real


# ============================================================
# 1-2. EOFs and axes
# ============================================================

def eof_axes(obs_anom, lat_w):
    """(e_x, e_y) unit patterns (lat, lon) and the observed index on the EOF days."""
    n = obs_anom.sizes["time"]
    series = obs_anom.values.astype(np.float32)
    filt = mjo_filter(np.concatenate([series, np.zeros_like(series)]), n)[:n]
    filt = xr.DataArray(filt, coords=obs_anom.coords, dims=obs_anom.dims).sel(time=slice(*EOF_YEARS))

    mid_doy = (INIT + pd.Timedelta(days=23)).dayofyear
    gap = np.abs(filt.time.dt.dayofyear.values - mid_doy)
    gap = np.minimum(gap, 365 - gap)
    train = filt.isel(time=gap <= EOF_HALF_WINDOW)

    X = (train.values * lat_w[:, None]).reshape(train.sizes["time"], -1)
    _, s, vt = np.linalg.svd(X - X.mean(axis=0), full_matrices=False)
    var = s ** 2 / (s ** 2).sum()
    e1, e2 = (v.reshape(train.shape[1:]) for v in vt[:2])
    print(f"EOFs from {train.sizes['time']} MJO-filtered ERA5 days ({EOF_YEARS[0]}-{EOF_YEARS[1]}, "
          f"day of year {mid_doy} +- {EOF_HALF_WINDOW}): explained variance {var[0]:.1%}, {var[1]:.1%}")

    # rotate within the EOF plane: e_x most negative (enhanced convection) at
    # the Maritime Continent, e_y negative over the western Pacific
    lon = train.longitude.values
    profile = lambda e, at: float((e * lat_w[:, None]).mean(axis=0)[np.argmin(np.abs((lon % 360) - at))])
    theta = np.arctan2(-profile(e2, MC_LON), -profile(e1, MC_LON))
    e_x = np.cos(theta) * e1 + np.sin(theta) * e2
    e_y = -np.sin(theta) * e1 + np.cos(theta) * e2
    if profile(e_y, WP_LON) > 0:
        e_y = -e_y

    obs_xy = project(train.values, lat_w, e_x, e_y)
    scale = obs_xy.std(axis=0)
    obs_xy = obs_xy / scale
    return e_x, e_y, scale, pd.DataFrame(obs_xy, index=train.time.to_index(), columns=["x", "y"])


def project(fields, lat_w, e_x, e_y):
    """fields (..., lat, lon) -> (..., 2) projections onto the axes. e_x is
    negative (enhanced convection) over the Maritime Continent, so a field
    with convection there projects positively: x > 0."""
    f = fields * lat_w[:, None]
    return np.stack([(f * e_x).sum(axis=(-2, -1)), (f * e_y).sum(axis=(-2, -1))], axis=-1)


# ============================================================
# 4. PSL indices
# ============================================================

def read_psl(name):
    text = urlopen(PSL + name, timeout=120).read().decode()
    df = pd.read_csv(io.StringIO(text), sep=r"\s+", header=None)
    if df.shape[1] == 7:            # ROMI: year month day hour PC1 PC2 amplitude
        df = df.drop(columns=3)
    df.columns = ["year", "month", "day", "pc1", "pc2", "amp"]
    return df.set_index(pd.to_datetime(df[["year", "month", "day"]]))[["pc1", "pc2", "amp"]]


def omi_xy(psl):
    """PSL's OMI/ROMI on the phase diagram's axes: (PC2, -PC1)."""
    return pd.DataFrame({"x": psl.pc2, "y": -psl.pc1, "amp": psl.amp})


def align_to(ours, psl):
    """2x2 orthogonal matrix R (rotation, or rotation + reflection) that best
    maps our (x, y) onto PSL's (PC2, -PC1) over the days both have
    (orthogonal Procrustes). Our EOF pair is only defined up to such a
    transform; this pins the phases to the convention other centres use."""
    both = ours.join(omi_xy(psl), how="inner", rsuffix="_psl")
    u, _, vt = np.linalg.svd(both[["x", "y"]].values.T @ both[["x_psl", "y_psl"]].values)
    r = u @ vt
    kind = "rotation" if np.linalg.det(r) > 0 else "rotation + reflection"
    print(f"  aligned to PSL OMI over {len(both)} days: {kind} by "
          f"{np.degrees(np.arctan2(r[1, 0], r[0, 0])):+.0f} deg")
    return r


def phase(x, y):
    return (np.floor((np.degrees(np.arctan2(y, x)) + 180) / 45).astype(int) % 8) + 1


def compare(name, ours, psl, min_amp=1.0):
    both = ours.join(omi_xy(psl), how="inner", rsuffix="_psl")
    strong = (np.hypot(both.x, both.y) > min_amp) & (both.amp > min_amp)
    dphase = (phase(both.x, both.y) - phase(both.x_psl, both.y_psl) + 4) % 8 - 4
    print(f"  vs {name} ({len(both)} days): r(x)={both.x.corr(both.x_psl):.2f}, r(y)={both.y.corr(both.y_psl):.2f}, "
          f"r(amplitude)={np.hypot(both.x, both.y).corr(both.amp):.2f}; on the {strong.sum()} days both > {min_amp}: "
          f"same phase {(dphase[strong] == 0).mean():.0%}, within one phase {(np.abs(dphase[strong]) <= 1).mean():.0%}")


# ============================================================
# Run
# ============================================================

def main():
    files = sorted(glob.glob(os.path.join(OBS_DIR, "era5_olr_*.nc")))
    obs = xr.open_mfdataset(files, combine="by_coords").olr.sel(latitude=slice(EOF_LAT, -EOF_LAT)).load()
    obs = obs.sel(time=slice("2006", "2022"))
    lat_w = np.sqrt(np.cos(np.deg2rad(obs.latitude.values)))
    annual_cycle = wa.fit_annual_cycle(obs)
    e_x, e_y, scale, obs_index = eof_axes(obs - annual_cycle(obs.time.values), lat_w)

    omi = read_psl("omi.1x.txt")
    print("Observed index check:")
    compare("PSL OMI (NOAA OLR), before alignment", obs_index, omi)
    align = align_to(obs_index, omi)
    obs_index[["x", "y"]] = obs_index[["x", "y"]].values @ align
    compare("PSL OMI (NOAA OLR)", obs_index, omi)

    # forecast: every member's padded series, MJO-filtered
    an = xr.open_zarr(f"{path}olr_anom_{date_str}.zarr").sel(latitude=slice(EOF_LAT, -EOF_LAT)).load()
    n_obs, n_fc = an.sizes["time"], an.sizes["step"]
    n_valid = n_obs + n_fc
    keep = slice(n_obs - OBS_DAYS, n_valid)
    fc = an.fc_anom.transpose("step", "number", "latitude", "longitude").values
    members = []
    for b in range(0, fc.shape[1], MEMBER_BATCH):
        batch = fc[:, b:b + MEMBER_BATCH]
        s = np.zeros((wf.SERIES_DAYS,) + batch.shape[1:])
        s[:n_obs] = an.obs_anom.values[:, None]
        s[n_obs:n_valid] = batch
        members.append(project(mjo_filter(s, n_valid)[keep], lat_w, e_x, e_y) / scale @ align)
    members = np.concatenate(members, axis=1)                    # (time, member, 2)
    ens_mean = members.mean(axis=1)                              # = filtering the ensemble mean (linear)
    times = pd.date_range(an.time.values[n_obs - OBS_DAYS], periods=keep.stop - keep.start, freq="D")
    is_fc = times >= INIT

    recent = pd.DataFrame(ens_mean[~is_fc], index=times[~is_fc], columns=["x", "y"])
    print("Recent observed track check:")
    romi = read_psl("romi.cpcolr.1x.txt")
    compare("PSL real-time OMI (ROMI)", recent, romi, min_amp=0.5)
    romi_xy = omi_xy(romi.reindex(times[~is_fc]).dropna())[["x", "y"]].values

    ds = xr.Dataset(
        {"x": (("time", "number"), members[..., 0].astype("float32")),
         "y": (("time", "number"), members[..., 1].astype("float32")),
         "x_mean": ("time", ens_mean[:, 0]), "y_mean": ("time", ens_mean[:, 1])},
        coords={"time": times, "number": an.number.values, "is_forecast": ("time", is_fc)},
        attrs={"description": "OLR MJO index: x > 0 Maritime Continent, y > 0 western Pacific convection; "
                              "unit std over the EOF days", "eof_years": "-".join(EOF_YEARS)})
    ds.to_netcdf(f"{path}mjo_olr_index_{date_str}.nc")
    print(f"Wrote {path}mjo_olr_index_{date_str}.nc")

    plot_phase(members[is_fc], ens_mean, is_fc, times, romi_xy, f"{plot_path}mjo_phase_diagram.png")


# ============================================================
# Plot
# ============================================================

def phase_axes(ax, lim):
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
                str(p), ha="center", va="center", fontsize=13, color="grey")
    kw = dict(ha="center", va="center", fontsize=10, color="dimgrey")
    ax.text(0, -lim * 0.95, "Indian Ocean", **kw)
    ax.text(lim * 0.95, 0, "Maritime Continent", rotation=-90, **kw)
    ax.text(0, lim * 0.95, "Western Pacific", **kw)
    ax.text(-lim * 0.95, 0, "West. Hem. and Africa", rotation=90, **kw)
    ax.set_xlabel("x  (+ = Maritime Continent convection)")
    ax.set_ylabel("y  (+ = western Pacific convection)")


def plot_phase(members_fc, ens_mean, is_fc, times, romi_xy, out):
    obs_xy = ens_mean[~is_fc]
    fc_mean = np.vstack([obs_xy[-1:], ens_mean[is_fc]])       # join the mean onto the last observed day
    lim = max(3.0, float(np.ceil(np.abs(np.r_[members_fc.ravel(), obs_xy.ravel()]).max() * 2) / 2))

    fig, ax = plt.subplots(figsize=(9, 9), layout="constrained")
    phase_axes(ax, lim)
    for m in range(members_fc.shape[1]):
        track = np.vstack([obs_xy[-1:], members_fc[:, m]])
        ax.plot(track[:, 0], track[:, 1], color="tab:blue", lw=0.5, alpha=0.25)
    ax.plot([], [], color="tab:blue", lw=0.8, alpha=0.5, label=f"{members_fc.shape[1]} members, days 1-{len(fc_mean) - 1}")
    ax.plot(fc_mean[:, 0], fc_mean[:, 1], color="tab:red", lw=2.5, label="ensemble mean")
    for d in range(7, len(fc_mean), 7):
        ax.plot(*fc_mean[d], "o", color="tab:red", ms=6)
        ax.annotate(f"day {d}", fc_mean[d], textcoords="offset points", xytext=(5, 5), fontsize=8, color="tab:red")
    ax.plot(obs_xy[:, 0], obs_xy[:, 1], color="k", lw=2, label=f"ERA5, last {len(obs_xy)} days")
    ax.plot(*obs_xy[0], "s", color="k", ms=6)
    ax.annotate(times[0].strftime("%d %b"), obs_xy[0], textcoords="offset points", xytext=(5, -10), fontsize=8)
    if len(romi_xy):
        ax.plot(romi_xy[:, 0], romi_xy[:, 1], color="grey", lw=1.5, ls="--",
                label="PSL real-time OMI (ROMI), same days")
    ax.plot(*obs_xy[-1], "o", color="k", ms=7, zorder=5)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.9)
    ax.set_title(f"OLR-based MJO index (OMI-style), ECMWF extended range from {date_str}\n"
                 f"red dots every 7 days; EOFs of MJO-filtered ERA5 OLR {EOF_YEARS[0]}-{EOF_YEARS[1]}",
                 fontsize=11)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
