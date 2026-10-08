"""
Builds, and keeps extending, a daily ERA5 outgoing longwave radiation (OLR)
archive on the 1.5 degree S2S grid: the observed record that the tropical wave
filtering needs, both for the climatology / reforecast bias and as the ~2 years
of lead-in padded in front of each forecast (see download_olr_waves.py).

Two public, credential-free sources, picked per day:

  * up to 2023-01-10: WeatherBench2's daily ERA5 on the 1.5 degree grid
    (gs://weatherbench2/datasets/era5_daily/), mean_top_net_long_wave_radiation_flux.
    Already daily and already regridded, so 2006-2022 is ~0.5 GB.
  * after that: ARCO-ERA5's hourly 0.25 degree top_net_thermal_radiation
    (gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3), which
    runs up to ~5 days behind real time (ERA5T). Stored one global hour per
    chunk, so even a tropical crop costs ~25 GB per year of record -- only the
    lead-in period and the daily top-up should come from here.

The ARCO path reproduces the WeatherBench2 daily mean to ~0.01 W m-2 (checked
on 2022-06-01): both average the 24 hourly fields valid at 00..23 UTC of the
day, i.e. the hours ending 00..23 UTC, which is one hour earlier than the
forecast's 00-24 UTC daily mean. The hour shift does not bias the mean.

    python olr_obs_archive.py --start 2006-01-01 --end 2023-01-10   # climatology period (WeatherBench2, minutes)
    python olr_obs_archive.py --start 2024-09-01                    # lead-in period (ARCO, ~25 GB per year)
    python olr_obs_archive.py                                       # daily: extend to the latest ERA5T day

Writes one file per year, <OLR_OBS_DIR>/era5_olr_<YYYY>.nc (default olr_obs/),
holding `olr` (time, latitude, longitude) in W m-2, positive upward, global, on
the same grid as the dynamical forecast and the Planette reforecasts
(lat 90..-90, lon -180..178.5), plus a `source` (time) coordinate. Days already
in the archive are skipped, so an interrupted build picks up where it stopped.
ERA5T days are not re-fetched once ECMWF replaces them with final ERA5.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import xarray as xr
import zarr

WB2 = "weatherbench2/datasets/era5_daily/1959-2023_01_10-1h-240x121_equiangular_with_poles_conservative.zarr"
WB2_VAR = "mean_top_net_long_wave_radiation_flux"
WB2_LAST_DAY = date(2023, 1, 10)

ARCO = "gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
ARCO_VAR = "top_net_thermal_radiation"
HOUR0 = np.datetime64("1900-01-01T00", "h")   # ARCO's time index 0

OUT_DIR = os.environ.get("OLR_OBS_DIR", "olr_obs")

# the 1.5 degree S2S grid (dynamical forecast and Planette reforecasts)
LATS = np.linspace(90, -90, 121)
LONS = np.arange(-180, 180, 1.5)

# a 1.5 degree cell centred on a target point spans 7 source points 0.25
# degree apart; the two edge ones are shared with the neighbouring cell
_OFFSETS = np.arange(-3, 4)
_WEIGHTS = np.array([0.5, 1, 1, 1, 1, 1, 0.5])


# ============================================================
# WeatherBench2 (daily, 1.5 degree, up to 2023-01-10)
# ============================================================

def fetch_wb2(days):
    """Daily-mean OLR (W m-2, positive up), shape (len(days), 121, 240), for
    consecutive days. WB2 is (time, longitude, latitude) with latitude
    ascending, longitude 0..358.5 and the flux negative upward."""
    ds = xr.open_zarr(f"gs://{WB2}", storage_options={"token": "anon"}, chunks=None)
    da = ds[WB2_VAR].sel(time=slice(str(days[0]), str(days[-1])))
    if da.sizes["time"] != len(days):
        raise RuntimeError(f"WeatherBench2 has {da.sizes['time']} of the {len(days)} days {days[0]} .. {days[-1]}")
    da = da.assign_coords(longitude=((da.longitude + 180) % 360) - 180)
    da = da.sortby("longitude").sortby("latitude", ascending=False).transpose("time", "latitude", "longitude")
    np.testing.assert_allclose(da.latitude, LATS)
    np.testing.assert_allclose(da.longitude, LONS)
    return -da.values.astype(np.float32)


# ============================================================
# ARCO-ERA5 (hourly, 0.25 degree, up to ~5 days ago)
# ============================================================

def hour_index(day):
    """ARCO time index of 00 UTC on `day`."""
    return int((np.datetime64(day, "h") - HOUR0) / np.timedelta64(1, "h"))


def to_s2s_grid(x):
    """Box-average (..., 721, 1440) 0.25 degree fields onto the 1.5 degree S2S
    grid. Longitude wraps; at the poles the box is cut off and renormalised."""
    src_lon = np.round((LONS % 360) / 0.25).astype(int)
    lon_idx = (src_lon[:, None] + _OFFSETS) % 1440
    x = x[..., lon_idx] @ (_WEIGHTS / _WEIGHTS.sum())           # (..., 721, 240)

    src_lat = np.round((90 - LATS) / 0.25).astype(int)
    lat_idx = src_lat[:, None] + _OFFSETS
    w = np.where((lat_idx >= 0) & (lat_idx <= 720), _WEIGHTS, 0.0)
    w /= w.sum(axis=1, keepdims=True)
    x = x[..., np.clip(lat_idx, 0, 720), :]                     # (..., 121, 7, 240)
    return np.einsum("...jkl,jk->...jl", x, w).astype(np.float32)


def open_arco(concurrency):
    zarr.config.set({"async.concurrency": concurrency})
    return zarr.open_array(f"gs://{ARCO}/{ARCO_VAR}", mode="r",
                           storage_options={"token": "anon"}, zarr_format=2)


def fetch_arco(arr, days, stride=1):
    """Daily-mean OLR (W m-2, positive up) on the S2S grid, shape (len(days), 121, 240).

    ARCO's TTR at hour h is the accumulation (J m-2, negative upward) over the
    hour ending at h. Day D averages the hours valid 00..23 UTC on D, as
    WeatherBench2 does. stride > 1 averages every Nth hour only: every 3rd hour
    is off by ~1.7 W m-2 RMS over 30S-30N (vs ~37 W m-2 day-to-day variability).
    """
    per_day = list(range(0, 24, stride))
    hours = [hour_index(d) + h for d in days for h in per_day]
    raw = arr.oindex[hours, :, :].reshape(len(days), len(per_day), 721, 1440)
    missing = [str(d) for d, r in zip(days, raw) if np.isnan(r).any()]
    if missing:
        raise RuntimeError(f"ARCO has no (or incomplete) TTR for {missing}")
    return to_s2s_grid(-raw.mean(axis=1) / 3600.0)


def _gcs_url(path):
    return f"https://storage.googleapis.com/{path}"


def _gcs_exists(path):
    try:
        urlopen(Request(_gcs_url(path), method="HEAD"), timeout=60)
        return True
    except HTTPError as e:
        if e.code == 404:
            return False
        raise


def latest_arco_day():
    """Last day whose 24 hours ARCO has released."""
    attrs = json.load(urlopen(_gcs_url(f"{ARCO}/.zattrs"), timeout=60))
    day = date.fromisoformat(attrs["valid_time_stop_era5t"][:10])
    while not _gcs_exists(f"{ARCO}/{ARCO_VAR}/{hour_index(day) + 23}.0.0"):
        day -= timedelta(days=1)
    return day


# ============================================================
# Archive files
# ============================================================

def year_path(year):
    return os.path.join(OUT_DIR, f"era5_olr_{year}.nc")


def archived_days():
    days = set()
    if not os.path.isdir(OUT_DIR):
        return days
    for fname in sorted(os.listdir(OUT_DIR)):
        if fname.startswith("era5_olr_") and fname.endswith(".nc"):
            with xr.open_dataset(os.path.join(OUT_DIR, fname)) as ds:
                days.update(t.astype("datetime64[D]").astype(date) for t in ds.time.values)
    return days


def write_days(days, olr, sources):
    """Merge days (all in one year) into that year's file, atomically."""
    new = xr.Dataset(
        {"olr": (("time", "latitude", "longitude"), olr)},
        coords={"time": np.array(days, dtype="datetime64[ns]"), "latitude": LATS, "longitude": LONS,
                "source": ("time", np.array(sources, dtype=object))},
    )
    path = year_path(days[0].year)
    if os.path.exists(path):
        with xr.open_dataset(path) as old:
            old = old.load()
        new = xr.concat([old, new], dim="time").sortby("time")
        new = new.isel(time=~new.get_index("time").duplicated(keep="last"))
    new.olr.attrs = {
        "units": "W m-2",
        "long_name": "Outgoing longwave radiation (daily mean of the hourly fields valid 00-23 UTC)",
        "comment": "-1 x ERA5 top net thermal radiation. source=wb2: WeatherBench2 era5_daily "
                   "(conservative regrid); source=arco: ARCO-ERA5 hourly, box-averaged from 0.25 degree",
    }
    new.attrs = {"sources": f"gs://{WB2} ; gs://{ARCO}",
                 "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = path + ".tmp"
    new.to_netcdf(tmp, encoding={"olr": {"zlib": True, "complevel": 4, "dtype": "float32"}})
    os.replace(tmp, path)
    print(f"  wrote {days[0]} .. {days[-1]} ({len(days)} days) to {path}", flush=True)


def runs(days):
    """Split sorted days into runs of consecutive days within one year."""
    out = []
    for d in days:
        if out and d == out[-1][-1] + timedelta(days=1) and d.year == out[-1][-1].year:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", help="first day YYYY-MM-DD (default: day after the archive's last day)")
    parser.add_argument("--end", help="last day YYYY-MM-DD (default: latest complete ERA5T day)")
    parser.add_argument("--hour-stride", type=int, default=1, choices=[1, 2, 3, 4, 6],
                        help="ARCO only: average every Nth hour (default 1 = all 24)")
    parser.add_argument("--batch-days", type=int, default=4,
                        help="ARCO days per request (~100 MB of memory per day)")
    parser.add_argument("--concurrency", type=int, default=96, help="ARCO parallel chunk downloads")
    parser.add_argument("--checkpoint-days", type=int, default=32,
                        help="ARCO: write to disk at least this often")
    args = parser.parse_args()

    have = archived_days()
    end = date.fromisoformat(args.end) if args.end else latest_arco_day()
    if args.start:
        start = date.fromisoformat(args.start)
    elif have:
        start = max(have) + timedelta(days=1)
    else:
        sys.exit(f"{OUT_DIR}/ is empty: give --start for the first build (see --help)")

    todo = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    todo = [d for d in todo if d not in have]
    wb2_days = [d for d in todo if d <= WB2_LAST_DAY]
    arco_days = [d for d in todo if d > WB2_LAST_DAY]
    print(f"ERA5 OLR archive {OUT_DIR}/: {len(have)} days present; fetching {len(wb2_days)} days "
          f"from WeatherBench2 and {len(arco_days)} from ARCO ({start} .. {end})")

    # WeatherBench2: one request per year
    for run in runs(wb2_days):
        write_days(run, fetch_wb2(run), ["wb2"] * len(run))

    if not arco_days:
        return
    arr = open_arco(args.concurrency)
    t0, done = time.time(), 0
    for run in runs(arco_days):
        pending_days, pending = [], []
        for i in range(0, len(run), args.batch_days):
            batch = run[i:i + args.batch_days]
            pending.append(fetch_arco(arr, batch, args.hour_stride))
            pending_days += batch
            done += len(batch)
            rate = done / (time.time() - t0)
            print(f"  {batch[-1]}  {done}/{len(arco_days)} ARCO days, {rate * 60:.1f} days/min "
                  f"(~{(len(arco_days) - done) / rate / 60:.0f} min left)", flush=True)
            if len(pending_days) >= args.checkpoint_days or batch[-1] == run[-1]:
                write_days(pending_days, np.concatenate(pending), ["arco"] * len(pending_days))
                pending_days, pending = [], []


if __name__ == "__main__":
    main()
