"""
Wavenumber-frequency filtering of the OLR anomalies of one ECMWF
extended-range forecast into MJO, Kelvin, equatorial Rossby (ER) and mixed
Rossby-gravity / tropical depression (MRG/TD) signals, following the padded
filtering method of Janiga et al. (2018, MWR), from what olr_wave_anomalies.py
wrote to data/<date>/olr_anom_<date>.zarr.

For the ensemble mean and the control member, at every latitude, a SERIES_DAYS
(4 year) series is built: the 730 observed lead-in days, the 46 forecast days,
then zeros. Then per wave band:
  1. high-pass the non-zero part (lead-in + forecast): subtract a centred
     running mean of the band's prefilter length (100 days before MJO/ER, 20
     before Kelvin/MRG), which damps the ringing from the jump to the zeros;
  2. taper the first year of the lead-in to zero with a half cosine bell (the
     start of the series then joins smoothly onto the zeros at the end, which
     the FFT treats as periodic);
  3. 2-D FFT over time and longitude, keep only the band's box of zonal
     wavenumber (positive = eastward) and period, inverse FFT.
The high-pass comes before the taper here, so the tapered start stays at zero.

The same is done once with the forecast replaced by zeros ("obs_only"): the
purely observational extrapolation of Wheeler and Weickmann (2001), a
baseline for what the forecast adds.

Writes data/<date>/olr_waves_<date>.zarr holding, per wave, the filtered OLR
anomaly from WINDOW_BEFORE days before the forecast to its end:
    <wave>_mean     ensemble mean (= the filtered ensemble-mean anomaly, the
                    filtering being linear)
    <wave>_control  member 0
    <wave>_obs_only observations + zeros
    <wave>_prob_enhanced / _suppressed
                    share of members with an active wave at 5S-5N
                    (Janiga et al. 2018 amplitude > AMP_THRESHOLD) in the enhanced /
                    suppressed half of its cycle (time, longitude)
and in plots/olr_waves/<date>/: waves_hovmoeller.png, waves_hovmoeller_ncics_style.png
(Carl Schreck's NCICS monitor look), waves_probability.png (ensemble
agreement on each wave) kenya_wave_phase_<wave>.png (local phase-amplitude
diagram at Kenya) and kenya_wave_rose_<wave>.png (its weekly phase roses; the per-member <wave>_kenya_A / _B are stored for it).

Env vars:
    DATE_STR      forecast init date, defaults to today-2
"""

import glob
import os
import shutil
import time
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.fft
import xarray as xr

from olr_obs_archive import OUT_DIR as OBS_DIR
from olr_wave_anomalies import CLIM_YEARS, equatorial_mean, fit_annual_cycle, hovmoeller, lon_0_360

# ============================================================
# CONFIG
# ============================================================

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

print(f"Tropical wave filtering of the OLR forecast: {date_str}")

path = f'data/{date_str}/'
plot_path = f'plots/olr_waves/{date_str}/'

SERIES_DAYS = 1461       # 4 years: 2 of observations, the forecast, then zeros
TAPER_DAYS = 365         # the first year of observations is tapered to zero
WINDOW_BEFORE = 90       # observed days kept in the output before the forecast starts

# Janiga et al. (2018) bands: zonal wavenumber (positive = eastward), period
# (days), and the high-pass (days) applied before filtering (None: none, for
# the low-frequency / large-scale band, which is what that high-pass removes).
# Kelvin and ER are further limited to between the shallow-water dispersion
# curves of equivalent depth h (m), as in Wheeler and Kiladis (1999) and the
# NCICS monitor: Janiga's plain boxes also pass fast eastward signals that
# aren't Kelvin waves, e.g. near-horizontal ringing from the jump where the
# observations meet the forecast.
WAVES = {
    "Low":    {"k": (-10, 10), "period": (100, SERIES_DAYS), "highpass": None},
    "MJO":    {"k": (0, 9),    "period": (20, 100), "highpass": 100},
    "Kelvin": {"k": (1, 14),   "period": (2.5, 20), "highpass": 20, "h": (8, 90), "dispersion": "kelvin"},
    "ER":     {"k": (-10, -1), "period": (10, 100), "highpass": 100, "h": (8, 90), "dispersion": "er"},
    "MRG_TD": {"k": (-20, 0),  "period": (2.5, 10), "highpass": 20},
}


# ============================================================
# Filtering
# ============================================================

def running_mean_highpass(x, n_valid, window):
    """x minus its centred `window`-day running mean, over the first n_valid
    days of axis 0 (the windows shrink at both ends); the rest stays zero."""
    v = x[:n_valid]
    c = np.concatenate([np.zeros((1,) + v.shape[1:]), np.cumsum(v, axis=0)])
    i = np.arange(n_valid)
    lo = np.clip(i - window // 2, 0, n_valid)
    hi = np.clip(i + window // 2 + 1, 0, n_valid)
    mean = (c[hi] - c[lo]) / (hi - lo).reshape((-1,) + (1,) * (v.ndim - 1))
    out = np.zeros_like(x)
    out[:n_valid] = v - mean
    return out


def taper(x):
    """Half cosine bell from 0 to 1 over the first TAPER_DAYS of axis 0."""
    w = np.ones(x.shape[0])
    w[:TAPER_DAYS] = 0.5 * (1 - np.cos(np.pi * np.arange(TAPER_DAYS) / TAPER_DAYS))
    return x * w.reshape((-1,) + (1,) * (x.ndim - 1))


EARTH_RADIUS = 6.371e6                     # m
BETA = 2 * 7.292e-5 / EARTH_RADIUS        # m-1 s-1, at the equator
GRAVITY = 9.81


def dispersion_frequency(k_east, h, wave):
    """Shallow-water frequency (cycles per day) of a Kelvin or n=1 equatorial
    Rossby wave of zonal wavenumber k_east and equivalent depth h (m)."""
    c = np.sqrt(GRAVITY * h)
    k = k_east / EARTH_RADIUS
    if wave == "kelvin":
        omega = c * k
    elif wave == "er":
        omega = -BETA * k / (k ** 2 + 3 * BETA / c)
    else:
        raise ValueError(wave)
    return omega * 86400 / (2 * np.pi)


def band_mask(n_time, n_lon, k, period, h=None, dispersion=None):
    """Boolean (frequency, wavenumber) mask for np.fft.fft2 over (time, lon).

    numpy's coefficient at frequency f > 0 and wavenumber index m is a wave
    exp(i(m*lon + 2*pi*f*t)), which moves westward for m > 0 -- so the eastward
    wavenumber is -m * sign(f). The mask is symmetric under (f, m) -> (-f, -m),
    which keeps the filtered field real. With h and dispersion, only
    frequencies between the curves for h[0] and h[1] are kept as well.
    """
    f = np.fft.fftfreq(n_time)[:, None]                  # cycles per day
    m = np.fft.fftfreq(n_lon, 1 / n_lon)[None, :]        # integer wavenumber
    k_east = -m * np.sign(f)
    mask = ((np.abs(f) >= 1 / period[1]) & (np.abs(f) <= 1 / period[0])
            & (k_east >= k[0]) & (k_east <= k[1]))
    if h is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            lo = dispersion_frequency(k_east, h[0], dispersion)
            hi = dispersion_frequency(k_east, h[1], dispersion)
        mask &= (np.abs(f) >= lo) & (np.abs(f) <= hi)
    return mask


def filter_series(series, n_valid):
    """series: (time, lat, lon) padded anomaly series, non-zero for the first
    n_valid days. Returns {wave: filtered (time, lat, lon)}."""
    n_time, _, n_lon = series.shape
    spectra = {}
    out = {}
    for name, band in WAVES.items():
        hp = band["highpass"]
        if hp not in spectra:
            prefiltered = series if hp is None else running_mean_highpass(series, n_valid, hp)
            spectra[hp] = scipy.fft.fft2(taper(prefiltered), axes=(0, 2), workers=-1)
        mask = band_mask(n_time, n_lon, band["k"], band["period"], band.get("h"), band.get("dispersion"))
        out[name] = scipy.fft.ifft2(spectra[hp] * mask[:, None, :], axes=(0, 2), workers=-1).real
    return out


# ============================================================
# Run
# ============================================================

def padded(obs, fc=None):
    """(SERIES_DAYS, lat, lon): lead-in, then the forecast (or nothing), then zeros."""
    n_obs = obs.shape[0]
    s = np.zeros((SERIES_DAYS,) + obs.shape[1:])
    s[:n_obs] = obs
    if fc is not None:
        s[n_obs:n_obs + fc.shape[0]] = fc
    return s


def wave_std():
    """Per wave, one observed standard deviation (all longitudes together) of the 5S-5N
    (NCICS_LAT) filtered OLR anomaly A and of its time derivative B, over
    STD_YEARS days within +-STD_HALF_WINDOW calendar days of the middle of the
    forecast, from the ERA5 archive. The archive is filtered as one long
    series (tapered at the start, zeros after the end), so a year is left off
    at each end. The filtering and the annual cycle fit act the same at every
    latitude, so averaging over latitude first gives the same result."""
    files = sorted(glob.glob(os.path.join(OBS_DIR, "era5_olr_*.nc")))
    obs = xr.open_mfdataset(files, combine="by_coords").olr.sel(time=slice(*CLIM_YEARS)).load()
    obs = equatorial_mean(obs.drop_vars("source"), NCICS_LAT).expand_dims(latitude=[0.0], axis=1)
    anom = (obs - fit_annual_cycle(obs)(obs.time.values)).values
    n = anom.shape[0]
    filtered = filter_series(np.concatenate([anom, np.zeros_like(anom)]), n)

    times = obs.get_index("time")
    mid_doy = (pd.Timestamp(date_str) + pd.Timedelta(days=23)).dayofyear
    gap = np.abs(times.dayofyear - mid_doy)
    use = (np.minimum(gap, 365 - gap) <= STD_HALF_WINDOW) & (times >= STD_YEARS[0]) & (times < str(int(STD_YEARS[1]) + 1))
    ik = nearest_lon(obs.longitude.values, KENYA_POINT)
    std, local = {}, {}
    for w, x in filtered.items():
        x = x[:n, 0]
        b = np.gradient(x, axis=0)
        std[w] = (x[use].std(), b[use].std())     # one value per wave: the same strength counts everywhere
        local[w] = (x[use, ik], b[use, ik])       # the season's days at the Kenya point, for plot_local_phase
    print(f"Wave amplitude scale: ERA5 {STD_YEARS[0]}-{STD_YEARS[1]}, {use.sum()} days within "
          f"+-{STD_HALF_WINDOW} days of day {mid_doy}; std of A: "
          + ", ".join(f"{w} {a:.1f}" for w, (a, _) in std.items()) + " W m-2")
    return std, local


def nearest_lon(lon, target):
    """Index of the longitude nearest target, whether lon runs -180..180 or 0..360."""
    return int(np.argmin(np.abs((np.asarray(lon) - target + 180) % 360 - 180)))


def member_probabilities(an, n_obs, n_valid, keep, std, threshold, batch=25):
    """Per wave, the share of members with an active wave (Janiga et al. 2018
    amplitude sqrt(A^2 + B^2) > threshold, A = filtered 5S-5N OLR anomaly
    and B = its time derivative, each over its observed std for that wave) in the enhanced
    half of the cycle (A < 0) and in the suppressed half (A > 0), each (time,
    lon) over the output window. The filtering acts the same at every
    latitude, so averaging over latitude first gives the same 5S-5N filtered
    field at a fraction of the cost."""
    obs = equatorial_mean(an.obs_anom.drop_vars("source"), NCICS_LAT).values               # (time, lon)
    fc = equatorial_mean(an.fc_anom, NCICS_LAT).transpose("step", "number", "longitude").values
    ik = nearest_lon(an.longitude.values, KENYA_POINT)
    counts = {w: [0, 0] for w in WAVES}
    local = {w: ([], []) for w in WAVES}         # per member, A and B at the Kenya point (time, member)
    for b in range(0, fc.shape[1], batch):
        s = np.zeros((SERIES_DAYS, min(batch, fc.shape[1] - b), fc.shape[2]))
        s[:n_obs] = obs[:, None, :]
        s[n_obs:n_valid] = fc[:, b:b + batch]
        for w, x in filter_series(s, n_valid).items():
            a = x / std[w][0]
            b = np.gradient(x, axis=0)
            local[w][0].append(x[keep, :, ik])
            local[w][1].append(b[keep, :, ik])
            active = np.hypot(a, b / std[w][1])[keep] > threshold
            counts[w][0] = counts[w][0] + (active & (a[keep] < 0)).sum(axis=1)
            counts[w][1] = counts[w][1] + (active & (a[keep] > 0)).sum(axis=1)
    return ({w: (enh / fc.shape[1], sup / fc.shape[1]) for w, (enh, sup) in counts.items()},
            {w: (np.concatenate(a, axis=1), np.concatenate(b, axis=1)) for w, (a, b) in local.items()})


def main():
    an = xr.open_zarr(f"{path}olr_anom_{date_str}.zarr").load()
    obs = an.obs_anom.values.astype(np.float64)                 # (730, lat, lon)
    fc = an.fc_anom.transpose("number", "step", "latitude", "longitude")
    n_obs, n_fc = obs.shape[0], fc.sizes["step"]
    n_valid = n_obs + n_fc
    keep = slice(n_obs - WINDOW_BEFORE, n_valid)                # output window

    # Every step is linear, so the ensemble mean of the filtered members is
    # the filtered ensemble mean: one run instead of one per member.
    t0 = time.time()
    runs = {
        "mean": filter_series(padded(obs, fc.mean("number").values), n_valid),
        "control": filter_series(padded(obs, fc.sel(number=0).values), n_valid),
        "obs_only": filter_series(padded(obs), n_obs),
    }
    print(f"Filtered ensemble mean, control and observations only ({time.time() - t0:.0f}s)")
    t0 = time.time()
    std, clim_local = wave_std()
    probs = {}
    for thr in (AMP_THRESHOLD, OBS_AMP_THRESHOLD):
        probs[thr], kenya = member_probabilities(an, n_obs, n_valid, keep, std, thr)
    print(f"Filtered all {an.sizes['number']} members at {NCICS_LAT}S-{NCICS_LAT}N ({time.time() - t0:.0f}s)")

    times = pd.date_range(an.time.values[n_obs - WINDOW_BEFORE], periods=keep.stop - keep.start, freq="D")
    coords = {"time": times, "latitude": an.latitude, "longitude": an.longitude}
    dims = ("time", "latitude", "longitude")
    data = {}
    for w, band in WAVES.items():
        attrs = {"units": "W m-2", "zonal_wavenumber": str(band["k"]), "period_days": str(band["period"]),
                 "highpass_days": str(band["highpass"]), "equivalent_depth_m": str(band.get("h"))}
        for run, res in runs.items():
            data[f"{w}_{run}"] = xr.DataArray(res[w][keep].astype("float32"), coords, dims, attrs=attrs)
        for thr, suffix in ((AMP_THRESHOLD, ""), (OBS_AMP_THRESHOLD, OBS_SUFFIX)):
            for kind, p in zip(("enhanced", "suppressed"), probs[thr][w]):
                data[f"{w}_prob_{kind}{suffix}"] = xr.DataArray(
                    p.astype("float32"), {"time": times, "longitude": an.longitude}, ("time", "longitude"),
                    attrs={"units": "1", "long_name": f"share of members with {NCICS_LAT}S-{NCICS_LAT}N wave amplitude "
                                                      f"> {thr:g} in the {kind} half of the cycle"})
        for name, v, units in (("A", kenya[w][0], "W m-2"), ("B", kenya[w][1], "W m-2 day-1")):
            data[f"{w}_kenya_{name}"] = xr.DataArray(
                v.T.astype("float32"), {"number": an.number.values, "time": times}, ("number", "time"),
                attrs={"units": units, "longitude": KENYA_POINT,
                       "long_name": f"{NCICS_LAT}S-{NCICS_LAT}N filtered OLR anomaly"
                                    + (" time derivative" if name == "B" else "") + " per member at the Kenya point"})
    ds = xr.Dataset(data, attrs={"init": date_str, "method": "Janiga et al. (2018) padded filtering"})
    ds = ds.assign_coords(is_forecast=("time", times >= pd.Timestamp(date_str)))

    out = f"{path}olr_waves_{date_str}.zarr"
    if os.path.exists(out):
        shutil.rmtree(out)
    ds.to_zarr(out, mode="w")
    print(f"Wrote {out}")

    unfiltered = xr.concat([an.obs_anom.isel(time=slice(-WINDOW_BEFORE, None)).drop_vars("source"),
                            an.fc_anom.swap_dims(step="valid_date").drop_vars("step").rename(valid_date="time")],
                           dim="time")
    plot_waves(ds, unfiltered, f"{plot_path}waves_hovmoeller.png")
    plot_ncics_style(ds, unfiltered, f"{plot_path}waves_hovmoeller_ncics_style.png")
    plot_ncics_style(ds, unfiltered, f"{plot_path}waves_hovmoeller_ncics_style_amplitude.png", std=std)
    plot_probability(ds, unfiltered, f"{plot_path}waves_probability.png")
    plot_local_phase(ds, clim_local, f"{plot_path}kenya_wave_phase_{{wave}}.png")
    plot_phase_roses(ds, clim_local, f"{plot_path}kenya_wave_rose_{{wave}}.png")


# ============================================================
# Plot
# ============================================================

CONTOURS = {"MJO": ("k", 5), "Kelvin": ("tab:green", 10), "ER": ("tab:purple", 5), "MRG_TD": ("tab:orange", 10)}


def plot_waves(ds, unfiltered, out):
    init = pd.Timestamp(date_str)
    fig, axes = plt.subplots(2, 2, figsize=(16, 14), layout="constrained", sharey=True)
    rows = (("mean", unfiltered.mean("number"), "ensemble mean"),
            ("control", unfiltered.sel(number=0), "member 0 (control)"))
    for r, (suffix, shade, label) in enumerate(rows):
        shade = lon_0_360(equatorial_mean(shade.drop_vars("number", errors="ignore"), 10))
        for c, pair in enumerate((("MJO", "Kelvin"), ("ER", "MRG_TD"))):
            ax = axes[r, c]
            pc = hovmoeller(ax, shade, vmax=40)
            pc.set_alpha(0.6)
            for w in pair:
                color, step = CONTOURS[w]
                field = lon_0_360(equatorial_mean(ds[f"{w}_{suffix}"], 10))
                vmax = max(step, float(np.abs(field).max()))
                levels = np.arange(step, vmax + step, step)
                # enhanced convection (negative OLR) solid, suppressed dashed
                ax.contour(field.longitude, field.time, field, levels=-levels[::-1], colors=color, linewidths=1.3,
                           linestyles="solid")
                ax.contour(field.longitude, field.time, field, levels=levels, colors=color, linewidths=0.8,
                           linestyles="dashed")
                ax.plot([], [], color=color, label=f"{w.replace('_', '/')} (every {step} W m$^{{-2}}$)")
            ax.axhline(init, color="k", lw=1.5)
            ax.legend(loc="lower right", fontsize=9, framealpha=0.9)
            ax.set_title(f"{' + '.join(p.replace('_', '/') for p in pair)}: {label}", fontsize=11)
    fig.colorbar(pc, ax=axes, shrink=0.6, label="unfiltered OLR anomaly (W m$^{-2}$), negative = enhanced convection")
    fig.suptitle(f"10S-10N filtered OLR anomalies, ERA5 then ECMWF forecast from {date_str} (black line); "
                 f"solid = enhanced convection, dashed = suppressed", fontsize=12)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"Wrote {out}")


# Carl Schreck's NCICS monitor (ncics.org/mjo) look, for side-by-side
# comparison: 5S-5N, discrete 16 W m-2 shading with -8..8 left white, one
# +-16 W m-2 contour per wave, 12 weeks before and 4 weeks after the start.
NCICS_LAT = 5
NCICS_LEVEL = 16
NCICS_WEEKS = (12, 4)
NCICS_CONTOURS = {"Kelvin": "blue", "ER": "red", "MJO": "black", "Low": "darkviolet"}


def ncics_colormap():
    """Brown (dry) / green (wet) steps of 16 W m-2, -8..8 white: (cmap, norm, ticks)."""
    from matplotlib.colors import BoundaryNorm, ListedColormap
    bounds = np.array([-88, -72, -56, -40, -24, -8, 8, 24, 40, 56, 72, 88])
    colors = plt.get_cmap("BrBG_r")(np.linspace(0, 1, len(bounds) - 1))
    colors[len(colors) // 2] = (1, 1, 1, 1)
    cmap = ListedColormap(colors[1:-1])
    cmap.set_under(colors[0])
    cmap.set_over(colors[-1])
    return cmap, BoundaryNorm(bounds[1:-1], cmap.N), bounds[1:-1]


def begin_forecast(ax, init, fontsize=10):
    ax.axhline(init, color="k", lw=1.5)
    ax.text(150, init, "Begin forecast", ha="center", va="bottom", fontsize=fontsize,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1})


def wave_amplitude(field, std):
    """Janiga et al. (2018) amplitude sqrt(A^2 + B^2) of a (time, lon) filtered
    field, signed by A so that negative = enhanced half of the cycle."""
    b = field.differentiate("time", datetime_unit="D")
    return np.sign(field) * np.hypot(field / std[0], b / std[1])


def plot_ncics_style(ds, unfiltered, out, std=None):
    """With std (from wave_std), contour the wave amplitude at +-OBS_AMP_THRESHOLD
    instead of the filtered OLR at +-NCICS_LEVEL W m-2."""
    init = pd.Timestamp(date_str)
    window = slice(init - pd.Timedelta(weeks=NCICS_WEEKS[0]), init + pd.Timedelta(weeks=NCICS_WEEKS[1]))
    cmap, norm, ticks = ncics_colormap()

    fig, axes = plt.subplots(1, 2, figsize=(15, 9.5), layout="constrained", sharey=True)
    for ax, (suffix, shade, label) in zip(axes, (("mean", unfiltered.mean("number"), "ensemble mean"),
                                                 ("control", unfiltered.sel(number=0), "member 0 (control)"))):
        shade = lon_0_360(equatorial_mean(shade.drop_vars("number", errors="ignore"), NCICS_LAT)).sel(time=window)
        pc = hovmoeller(ax, shade, cmap=cmap, norm=norm)
        for w, color in NCICS_CONTOURS.items():
            field = lon_0_360(equatorial_mean(ds[f"{w}_{suffix}"], NCICS_LAT))
            cut = NCICS_LEVEL
            if std is not None:
                field, cut = wave_amplitude(field, std[w]), OBS_AMP_THRESHOLD
            field = field.sel(time=window)
            for level, ls in ((-cut, "solid"), (cut, "dashed")):
                if (np.sign(level) * field).max() > cut:
                    ax.contour(field.longitude, field.time, field, levels=[level], colors=color,
                               linewidths=1.6, linestyles=ls)
            ax.plot([], [], color=color, lw=1.6, label=w)
        begin_forecast(ax, init)
        ax.legend(loc="lower right", fontsize=9, framealpha=0.9, title_fontsize=9,
                  title=f"contours at {NCICS_LEVEL} W m$^{{-2}}$" if std is None
                  else f"contours at wave amplitude {OBS_AMP_THRESHOLD:g}")
        ax.set_title(f"OLR with ECMWF forecasts ({label}), {NCICS_LAT}S-{NCICS_LAT}N", fontsize=12)
    fig.colorbar(pc, ax=axes, orientation="horizontal", shrink=0.6, extend="both", ticks=ticks,
                 label="OLR anomaly (W m$^{-2}$)")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"Wrote {out}")


# Ensemble agreement on wave activity: per wave, contours of the share of
# members in which it is active (amplitude > AMP_THRESHOLD, see
# member_probabilities), split into the enhanced and suppressed half of the
# cycle, over the ensemble-mean anomaly. Waves whose timing differs between
# members cancel in the ensemble mean, but still show up here where members
# agree. The amplitude, unlike the filtered OLR itself, stays large through the
# whole wave rather than only at its crest, and is scaled by each wave's own
# observed variability (one value per wave, so the same strength counts at every longitude).
PROB_LEVELS = (0.50, 0.66, 0.80)       # at amplitude > 1 a wave is active in its enhanced half on ~30% of days, so 33% would be ~normal
PROB_WIDTHS = (0.8, 1.7, 2.8)
PROB_WEEKS_BEFORE = 12      # observed weeks shown, as on the NCICS monitor
AMP_THRESHOLD = 1.0               # forecast agreement
OBS_AMP_THRESHOLD = 2.0           # observed line: ~16 W m-2 for Kelvin, close to the NCICS +-16 contour
OBS_SUFFIX = f"_amp{OBS_AMP_THRESHOLD:g}".replace(".", "p")   # dataset variable suffix
PROB_WAVES = ("Kelvin", "ER", "MJO")   # Low left out: no high-pass, so the drop to zero padding after
                                       # day 46 makes B (and the amplitude) large at the end of the forecast
STD_YEARS = ("2007", "2021")     # ERA5 years for the amplitude scale (a year off each archive end)
STD_HALF_WINDOW = 60             # calendar days either side of the forecast's middle


def plot_probability(ds, unfiltered, out):
    init = pd.Timestamp(date_str)
    window = slice(init - pd.Timedelta(weeks=PROB_WEEKS_BEFORE), None)
    cmap, norm, ticks = ncics_colormap()
    shade = lon_0_360(equatorial_mean(unfiltered.mean("number"), NCICS_LAT)).sel(time=window)

    fig, axes = plt.subplots(1, len(PROB_WAVES), figsize=(7 * len(PROB_WAVES), 12), layout="constrained", sharey=True)
    for ax, w in zip(axes.flat, PROB_WAVES):
        color = NCICS_CONTOURS[w]
        pc = hovmoeller(ax, shade, cmap=cmap, norm=norm, label_size=13)
        for kind, ls in (("enhanced", "solid"), ("suppressed", "dashed")):
            p = lon_0_360(ds[f"{w}_prob_{kind}"]).sel(time=window)
            p_obs = lon_0_360(ds[f"{w}_prob_{kind}{OBS_SUFFIX}"]).sel(time=window)
            # observed days: every member shares them, so the share is ~0 or ~1
            # there and all levels would stack -- one line marks where it was active
            obs, fc = p_obs.where(p_obs.time < init), p.where(p.time >= init)
            if float(obs.max()) >= 0.5:
                ax.contour(p.longitude, p.time, obs, levels=[0.5], colors=color, linewidths=1.4, linestyles=ls)
            if float(fc.max()) >= PROB_LEVELS[0]:
                ax.contour(p.longitude, p.time, fc, levels=list(PROB_LEVELS), colors=color,
                           linewidths=list(PROB_WIDTHS), linestyles=ls)
        begin_forecast(ax, init, fontsize=14)
        handles = [plt.Line2D([], [], color=color, lw=1.7, ls="solid", label="active, enhanced half"),
                   plt.Line2D([], [], color=color, lw=1.7, ls="dashed", label="active, suppressed half"),
                   plt.Line2D([], [], color="k", lw=1.4, label=f"observed: amplitude > {OBS_AMP_THRESHOLD:g}")]
        handles += [plt.Line2D([], [], color="k", lw=lw, label=f"forecast: {lvl:.0%} of members > {AMP_THRESHOLD:g}")
                    for lvl, lw in zip(PROB_LEVELS, PROB_WIDTHS)]
        ax.legend(handles=handles, loc="lower right", fontsize=12, framealpha=0.9)
        ax.set_title(f"{w}: wave activity", fontsize=18)
        ax.tick_params(labelsize=14)
        ax.xaxis.label.set_size(15)
    cb = fig.colorbar(pc, ax=axes, orientation="horizontal", shrink=0.6, extend="both", ticks=ticks)
    cb.set_label("ensemble-mean OLR anomaly (W m$^{-2}$)", fontsize=15)
    cb.ax.tick_params(labelsize=14)
    fig.suptitle(f"ECMWF extended range from {date_str}: ensemble agreement on wave activity, "
                 f"{NCICS_LAT}S-{NCICS_LAT}N (ERA5 before the line)", fontsize=19)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"Wrote {out}")


# Local wave phase-amplitude diagram at one point (van der Linden et al. 2016;
# Schlueter et al. 2019; Nicholson et al. 2022, Fig. 14): x = -A, y = -B (the
# 5S-5N filtered OLR anomaly and its time derivative, sign flipped so that wet
# is to the right and the rise towards it on top), each over its local std for
# the season. A passing wave turns clockwise: P1 dry (left), P3, P5 wet
# (right), P7. Shading: per phase, the season's 90th and 99th percentile of the
# amplitude in ERA5. Members are dots, one per member on the middle day of each
# forecast week (daily dots would ring the whole circle for a Kelvin wave,
# weekly means would collapse it to the centre).
KENYA_POINT = 37.5                   # middle of KENYA_LON (33-42E) on the 1.5 degree grid
PHASE_WAVES = ("Kelvin", "ER", "MJO")
PHASE_OBS_DAYS = 30                  # observed days drawn before the forecast
PHASE_WEEK_DAYS = (3, 10, 17, 24, 31, 38)   # days after init: lead days 4, 11, ..., 39, mid-week
PHASE_LIM = 4.5
# one distinct colour per week (as in run_rainfall_onset.py), kept off the red
# observed line and the tan / lavender percentile shading
PHASE_WEEK_COLORS = ("#1B5E20", "#00A6B8", "#F57C00", "#C2185B", "#1A237E", "#5D4037")


def phase_of(x, y):
    """Phase 1..8 of points (x, y): P5 centred on +x, P1 on -x, numbered clockwise."""
    theta = np.degrees(np.arctan2(y, x))
    return (np.round(((180 - theta) % 360) / 45).astype(int) % 8) + 1


def plot_local_phase(ds, clim, out):
    """One figure per wave: out is a pattern with {wave}."""
    from matplotlib import patheffects
    from matplotlib.patches import Circle, Wedge
    init = pd.Timestamp(date_str)
    times = ds.get_index("time")
    fc_days = np.flatnonzero(times >= init)
    obs_days = np.flatnonzero((times < init) & (times >= init - pd.Timedelta(days=PHASE_OBS_DAYS)))
    weeks = [fc_days[d] for d in PHASE_WEEK_DAYS if d < len(fc_days)]
    colors = PHASE_WEEK_COLORS[:len(weeks)]
    lim = PHASE_LIM

    for w in PHASE_WAVES:
        fig, ax = plt.subplots(figsize=(15, 10.5), layout="constrained")
        a_hist, b_hist = clim[w]
        sa, sb = a_hist.std(), b_hist.std()                 # local std at the Kenya point
        xh, yh = -a_hist / sa, -b_hist / sb
        amp_h, ph_h = np.hypot(xh, yh), phase_of(xh, yh)
        x = -ds[f"{w}_kenya_A"].values / sa                  # (number, time)
        y = -ds[f"{w}_kenya_B"].values / sb
        xm, ym = x.mean(0), y.mean(0)                        # = the filtered ensemble mean (linear)

        for p in range(1, 9):
            centre = 180 - 45 * (p - 1)
            p90, p99 = np.percentile(amp_h[ph_h == p], [90, 99])
            for r, c in ((p99, "#8c8cc4"), (p90, "#d8c9a0")):
                if r > 1:
                    ax.add_patch(Wedge((0, 0), r, centre - 22.5, centre + 22.5, width=r - 1, color=c, lw=0, zorder=0))
            edge = np.radians(centre - 22.5)
            ax.plot([np.cos(edge), 2 * lim * np.cos(edge)], [np.sin(edge), 2 * lim * np.sin(edge)],
                    color="#4060a0", lw=0.8, ls="--", zorder=1)
            label = f"P{p}" + {1: "\ndry", 5: "\nwet"}.get(p, "")
            ax.text(0.87 * lim * np.cos(np.radians(centre)), 0.87 * lim * np.sin(np.radians(centre)), label,
                    ha="center", va="center", fontsize=16, zorder=6)
        ax.add_patch(Circle((0, 0), 1, facecolor="white", edgecolor="k", lw=1, zorder=1))

        # observed lead-in, then the members and the ensemble mean
        ax.plot(xm[obs_days], ym[obs_days], "o-", color="tab:red", ms=3, lw=1.5, zorder=4)
        for i in obs_days[::7]:
            ax.annotate(times[i].strftime("%d %b"), (xm[i], ym[i]), fontsize=15, fontweight="bold", color="tab:red",
                        xytext=(5, 5), textcoords="offset points", zorder=7,
                        path_effects=[patheffects.withStroke(linewidth=3, foreground="white")])
        for i, c in zip(weeks, colors):
            ax.scatter(x[:, i], y[:, i], s=45, color=c, alpha=0.7, edgecolors="none", zorder=3)
        ax.plot(np.r_[xm[obs_days[-1:]], xm[fc_days]], np.r_[ym[obs_days[-1:]], ym[fc_days]],
                color="k", lw=2.5, zorder=5)
        for i, c in zip(weeks, colors):
            ax.scatter(xm[i], ym[i], s=200, color=c, edgecolors="k", linewidths=1.5, zorder=6)

        i0 = fc_days[0]
        amp0 = np.hypot(xm[i0], ym[i0])
        state = f"P{phase_of(xm[i0], ym[i0])}, amplitude {amp0:.1f}" if amp0 > 1 else f"weak (amplitude {amp0:.1f})"
        ax.set_title(f"{w} at Kenya ({KENYA_POINT:g}E, {NCICS_LAT}S-{NCICS_LAT}N OLR): {state} on {init:%d %b}\n"
                     f"ECMWF extended range from {date_str}", fontsize=18)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_aspect("equal")
        ax.tick_params(labelsize=14)
        ax.set_xlabel(r"filtered OLR anomaly, sign flipped (std)   wet $\rightarrow$", fontsize=15)
        ax.set_ylabel("its time derivative, sign flipped (std)", fontsize=15)

        handles = [plt.Line2D([], [], color="tab:red", marker="o", ms=4, lw=1.5, label=f"ERA5, last {PHASE_OBS_DAYS} days"),
                   plt.Line2D([], [], color="k", lw=2.5, label="ensemble mean")]
        handles += [plt.Line2D([], [], ls="", marker="o", ms=9, color=c, alpha=0.8,
                               label=f"week {k + 1}: members on {times[i]:%d %b}")
                    for k, (i, c) in enumerate(zip(weeks, colors))]
        handles += [plt.Rectangle((0, 0), 1, 1, color="#d8c9a0", label="ERA5, amplitude 1 to 90th pct"),
                    plt.Rectangle((0, 0), 1, 1, color="#8c8cc4", label="ERA5, 90th to 99th pct")]
        fig.legend(handles=handles, loc="outside right center", fontsize=15, frameon=False)
        path_w = out.format(wave=w)
        os.makedirs(os.path.dirname(path_w), exist_ok=True)
        fig.savefig(path_w, dpi=110)
        plt.close(fig)
        print(f"Wrote {path_w}")


# Phase rose: a wind rose of the local wave phase at Kenya, one per forecast
# week. Each bar points to a phase (P5 wet right, P1 dry left, P3 top, as in
# plot_local_phase); its length is the share of all members x days of that
# week in that phase, stacked by amplitude class. Days with amplitude < 1 (no
# active wave) are the "calm" share written in the centre.
ROSE_AMP_BINS = (1, 1.5, 2, 3, np.inf)
ROSE_COLORS = ("#FFD54F", "#FB8C00", "#D84315", "#6D1B7B")


def local_xy(ds, clim, w):
    """Members' (x, y) = (-A, -B) over the local ERA5 std at the Kenya point,
    plus the same for the ERA5 days of the season."""
    a_hist, b_hist = clim[w]
    sa, sb = a_hist.std(), b_hist.std()
    return (-ds[f"{w}_kenya_A"].values / sa, -ds[f"{w}_kenya_B"].values / sb,
            -a_hist / sa, -b_hist / sb)


def plot_phase_roses(ds, clim, out):
    """One figure per wave (out is a pattern with {wave}), one rose per week."""
    init = pd.Timestamp(date_str)
    times = ds.get_index("time")
    fc_days = np.flatnonzero(times >= init)
    weeks = [fc_days[7 * k:7 * k + 7] for k in range(len(fc_days) // 7)]
    centres = np.radians([(180 - 45 * (p - 1)) % 360 for p in range(1, 9)])    # P1..P8, kept in 0..360

    for w in PHASE_WAVES:
        x, y, _, _ = local_xy(ds, clim, w)
        amp, ph = np.hypot(x, y), phase_of(x, y)

        shares = []         # per week: (amplitude class, phase) in % of members x days
        for days in weeks:
            a, p = amp[:, days].ravel(), ph[:, days].ravel()
            shares.append(np.array([[np.mean((a > lo) & (a <= hi) & (p == k)) * 100 for k in range(1, 9)]
                                    for lo, hi in zip(ROSE_AMP_BINS[:-1], ROSE_AMP_BINS[1:])]))
        rmax = max(sh.sum(0).max() for sh in shares) * 1.1

        fig, axes = plt.subplots(2, 3, figsize=(17, 12.5), subplot_kw={"projection": "polar"}, layout="constrained")
        for k, (ax, days, sh) in enumerate(zip(axes.flat, weeks, shares)):
            bottom = np.zeros(8)
            for c, part in zip(ROSE_COLORS, sh):
                ax.bar(centres, part, width=np.radians(40), bottom=bottom, color=c, edgecolor="k", lw=0.6, zorder=3)
                bottom += part
            calm = 100 - sh.sum()
            ax.text(0, 0, f"weak\n{calm:.0f}%", ha="center", va="center", fontsize=13, zorder=5,
                    bbox={"boxstyle": "circle", "facecolor": "white", "edgecolor": "k"})
            ax.set_ylim(0, rmax)
            ax.set_thetalim(0, 2 * np.pi)
            ax.set_xticks(centres)
            ax.set_xticklabels([f"P{p}" + {1: "\ndry", 5: "\nwet"}.get(p, "") for p in range(1, 9)], fontsize=14)
            ax.tick_params(axis="y", labelsize=11)
            ax.set_rlabel_position(112.5)
            ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(decimals=0))
            ax.set_title(f"Week {k + 1}: {times[days[0]]:%d %b} - {times[days[-1]]:%d %b}", fontsize=16, pad=14)

        handles = [plt.Rectangle((0, 0), 1, 1, facecolor=c, edgecolor="k",
                                 label=f"amplitude {lo:g}-{hi:g}" if np.isfinite(hi) else f"amplitude > {lo:g}")
                   for c, lo, hi in zip(ROSE_COLORS, ROSE_AMP_BINS[:-1], ROSE_AMP_BINS[1:])]
        fig.legend(handles=handles, loc="outside lower center", ncol=4, fontsize=14, frameon=False)
        fig.suptitle(f"{w} phase at Kenya ({KENYA_POINT:g}E, {NCICS_LAT}S-{NCICS_LAT}N OLR) per forecast week: "
                     f"share of all {ds.sizes['number']} members x 7 days, ECMWF from {date_str}", fontsize=17)
        path_w = out.format(wave=w)
        os.makedirs(os.path.dirname(path_w), exist_ok=True)
        fig.savefig(path_w, dpi=110)
        plt.close(fig)
        print(f"Wrote {path_w}")


if __name__ == "__main__":
    main()
