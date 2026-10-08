"""
Gathers everything the tropical wave filtering (Janiga et al. 2018, MWR) needs
for one ECMWF extended-range forecast, all as outgoing longwave radiation
(OLR, W m-2, positive upward) on the 1.5 degree S2S grid, 30S-30N, every
longitude (the wavenumber-frequency filter needs the full circle):

  data/<date>/ECMWF_s2s_olr_<date>.zarr
      the forecast: every member, lead days 1-46, from dynamical.org
      (ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree).
  data/<date>/ECMWF_s2s_olr_reforecast_<date>.zarr
      the reforecasts nearest this calendar day in each year 2006-2024
      (Planette, via gef.load_reforecast), ensemble mean -- for the
      lead-dependent model bias.
  data/<date>/olr_obs_leadin_<date>.zarr
      the LEADIN_DAYS observed days before the forecast starts, from the ERA5
      archive built by olr_obs_archive.py. ERA5T runs ~5 days behind, so the
      last few days are filled with each day's own lead-1 forecast (ensemble
      mean); the `source` coordinate says which is which.

Run olr_obs_archive.py first so the archive reaches as far as ERA5T allows.

Env vars:
    DATE_STR      forecast init date, defaults to today-2
    OLR_OBS_DIR   ERA5 OLR archive folder (default olr_obs/), see olr_obs_archive.py

Lead time convention: step N is the daily mean over the N-th forecast day,
00-24 UTC on valid_date = init + N - 1 days, for the forecast and the
reforecasts alike. The observed days are 23-23 UTC (see olr_obs_archive.py).
"""

import os
import shutil
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr

import dynamical_catalog

import get_ECMWF_functions as gef
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

print(f"Downloading OLR for the tropical wave filtering: {date_str}")

path = f'data/{date_str}/'
os.makedirs(path, exist_ok=True)

INIT = pd.Timestamp(date_str)
LAT_MAX = 30           # 30S-30N
LEADIN_DAYS = 730      # observed days padded in front of the forecast (2 years)
DAY1_FALLBACK = 3      # a gap day with no forecast started on it may use lead 2 .. this

DAILY_DATASET = "ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree"
DYNAMICAL_VAR = "net_long_wave_radiation_flux_top_of_atmosphere"

OLR_ATTRS = {"units": "W m-2", "long_name": "Outgoing longwave radiation"}


def open_forecast_olr():
    """Lazy forecast OLR, 30S-30N. dynamical publishes the top net longwave
    flux, i.e. negative upward, as the mean over the 24 h before each lead."""
    ds = dynamical_catalog.open(DAILY_DATASET)
    return -ds[DYNAMICAL_VAR].sel(latitude=slice(LAT_MAX, -LAT_MAX))


def write(ds, out):
    if os.path.exists(out):
        shutil.rmtree(out)
    ds.to_zarr(out, mode="w")
    print(f"Wrote {out}")


# ============================================================
# 1. Forecast
# ============================================================

def get_forecast(olr):
    out = f"{path}ECMWF_s2s_olr_{date_str}.zarr"
    if os.path.exists(out):
        print(f"Forecast already there: {out}")
        return
    if INIT not in olr.get_index("init_time"):
        raise RuntimeError(f"dynamical has no {DAILY_DATASET} run started {date_str}")
    fc = olr.sel(init_time=INIT).sel(lead_time=olr.lead_time > pd.Timedelta(0)).compute()
    if fc.isnull().all(("ensemble_member", "latitude", "longitude")).any():
        raise RuntimeError(f"the {date_str} run is incomplete on dynamical (some lead days are all missing)")
    fc = fc.rename({"ensemble_member": "number", "lead_time": "step"})
    fc = fc.drop_vars([c for c in fc.coords if c not in fc.dims and c != "init_time"])
    fc = fc.assign_coords(valid_date=("step", (INIT + fc.step.to_index() - pd.Timedelta(days=1)).values))
    fc.name = "olr"
    fc.attrs = {**OLR_ATTRS, "comment": "-1 x dynamical net_long_wave_radiation_flux_top_of_atmosphere; "
                                        "step N = mean over 00-24 UTC on valid_date"}
    write(fc.to_dataset(), out)


# ============================================================
# 2. Reforecasts
# ============================================================

def get_reforecast():
    out = f"{path}ECMWF_s2s_olr_reforecast_{date_str}.zarr"
    if os.path.exists(out):
        print(f"Reforecast already there: {out}")
        return
    rf = gef.load_reforecast(date_str, "single", "olr",
                             bbox={"lat1": LAT_MAX, "lon1": -180, "lat2": -LAT_MAX, "lon2": 178.5},
                             time_range=slice(0, 46), all_years=True)
    n_members = rf.sizes["number"]
    rf = rf.mean("number", keep_attrs=True)
    rf = rf.assign_coords(centre_day=gef.reforecast_center_day(rf, date_str))
    rf.name = "olr"
    rf.attrs = {**OLR_ATTRS, "comment": f"Planette ECMWF IFS reforecasts, mean of {n_members} members; "
                                        "step N = mean over the N-th forecast day"}
    write(rf.to_dataset(), out)


# ============================================================
# 3. Observed lead-in
# ============================================================

def read_obs_archive(days):
    """ERA5 OLR for whichever of `days` the archive has, 30S-30N."""
    files = [os.path.join(OBS_DIR, f"era5_olr_{y}.nc") for y in sorted({d.year for d in days})]
    files = [f for f in files if os.path.exists(f)]
    if not files:
        return None
    obs = xr.open_mfdataset(files, combine="by_coords").olr
    obs = obs.sel(latitude=slice(LAT_MAX, -LAT_MAX))
    return obs.sel(time=obs.time.isin(days.values)).load()


def day1_forecast(olr, day):
    """Ensemble-mean forecast OLR for `day` (00-24 UTC) from the run started
    that day (lead 1), or else from up to DAY1_FALLBACK - 1 days before it."""
    inits = olr.get_index("init_time")
    for lead in range(1, DAY1_FALLBACK + 1):
        init = day - pd.Timedelta(days=lead - 1)
        if init not in inits:
            continue
        fc = olr.sel(init_time=init, lead_time=pd.Timedelta(days=lead)).mean("ensemble_member").compute()
        if not fc.isnull().any():
            return fc.drop_vars([c for c in fc.coords if c not in ("latitude", "longitude")]), f"ecmwf_lead{lead}"
    raise RuntimeError(f"no ECMWF run on dynamical covers {day:%Y-%m-%d} at leads 1-{DAY1_FALLBACK}")


def get_obs_leadin(olr):
    out = f"{path}olr_obs_leadin_{date_str}.zarr"
    days = pd.date_range(INIT - pd.Timedelta(days=LEADIN_DAYS), INIT - pd.Timedelta(days=1), freq="D")
    obs = read_obs_archive(days)
    have = obs.get_index("time") if obs is not None else pd.DatetimeIndex([])
    last_obs = have.max() if len(have) else days[0] - pd.Timedelta(days=1)

    holes = days[(days <= last_obs) & ~days.isin(have)]
    if len(holes):
        sys.exit(f"The ERA5 archive in {OBS_DIR}/ is missing {len(holes)} lead-in days "
                 f"({holes[0]:%Y-%m-%d} .. {holes[-1]:%Y-%m-%d}): run "
                 f"python olr_obs_archive.py --start {holes[0]:%Y-%m-%d}")
    gap = days[days > last_obs]
    if len(gap) > 15:
        sys.exit(f"The ERA5 archive in {OBS_DIR}/ ends {last_obs:%Y-%m-%d}, {len(gap)} days before "
                 f"this forecast: run python olr_obs_archive.py "
                 f"--start {(last_obs + pd.Timedelta(days=1)):%Y-%m-%d} first")

    parts = []
    if obs is not None and len(have):
        parts.append(obs.assign_coords(source=("time", obs.source.values.astype("U12"))))
    for day in gap:
        fc, source = day1_forecast(olr, day)
        parts.append(fc.expand_dims(time=[day]).assign_coords(source=("time", np.array([source], dtype="U12"))))
        print(f"  {day:%Y-%m-%d}: not in ERA5 yet, using the ECMWF forecast ({source})")

    leadin = xr.concat(parts, dim="time").sortby("time")
    assert leadin.sizes["time"] == LEADIN_DAYS and not leadin.isnull().any()
    leadin.name = "olr"
    leadin.attrs = {**OLR_ATTRS, "comment": "ERA5 (source wb2/arco, see olr_obs_archive.py), then "
                                            "ECMWF ensemble-mean forecasts for the days ERA5T hasn't reached"}
    sources = pd.Series(leadin.source.values).value_counts().to_dict()
    print(f"Observed lead-in {days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d}: {sources}")
    write(leadin.astype("float32").to_dataset(), out)


if __name__ == "__main__":
    olr = open_forecast_olr()
    get_forecast(olr)
    get_reforecast()
    get_obs_leadin(olr)
