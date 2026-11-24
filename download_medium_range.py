#!/usr/bin/env python3
"""
Download ECMWF open-data IFS total precipitation (tp) at 0.1 x 0.1 degrees,
crop it to a box around Africa and write it as a local Zarr store:

    <OUTPUT_DIR>/<date>/medium_range_precip_01.zarr

Nothing is read from or written to Google Cloud here. The nightly workflow
uploads everything under data/ (the "Upload zarr and other data files" step).

Requirements:
    pip install ecmwf-opendata xarray cfgrib zarr
    (cfgrib needs the ecCodes library: conda install -c conda-forge eccodes
     or apt install libeccodes-dev)

Notes:
  * Deterministic IFS forecast (stream oper, type fc) at 0.1 degrees.
  * Only the 00 and 12 UTC runs are downloaded. They have steps out to 360 h
    (15 days); the 06 and 18 UTC runs stop at 144 h, so they are not used.
  * The open-data server delivers global GRIB2 files; there is no server-side
    area subsetting, so the cropping to Africa is done locally afterwards.
  * "tp" is accumulated from the start of the forecast, in metres of water.
    By default the store holds the amount in each interval, in mm. Use
    --accumulated to keep the running total since the start of the forecast.
  * If the request fails, check the current open data documentation:
    https://www.ecmwf.int/en/forecasts/datasets/open-data
"""

import argparse
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

from ecmwf.opendata import Client

import get_ECMWF_functions as gef

# ============================================================
# CONFIG
# ============================================================

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    date_str = (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "data"))

# N, W, S, E
BOUNDING_BOX = list(map(float, os.environ.get("BOUNDING_BOX", "22.5,-21,-34.5,55.5").split(",")))

# ---- Data set selection -----------------------------------------------------
MODEL = "ifs"          # IFS HRES/ENS atmospheric model
STREAM = "oper"        # high-resolution deterministic forecast
TYPE = "fc"
RESOLUTION = "0p1"     # 0.1 x 0.1 degrees
PARAM = "tp"           # total precipitation
ZARR_NAME = "medium_range_precip_01"

PRECIP_ATTRS = {
    "GRIB_paramId": 228228, "GRIB_typeOfLevel": "surface", "GRIB_stepType": "accum",
    "GRIB_cfVarName": "tp", "GRIB_name": "Total Precipitation", "GRIB_shortName": "tp",
    "GRIB_units": "kg m**-2", "long_name": "Total Precipitation", "units": "mm",
    "standard_name": "unknown",
}


# ============================================================
# HELPERS
# ============================================================

def parse_date(text):
    """Accept YYYY-MM-DD or YYYYMMDD."""
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"date must be YYYY-MM-DD or YYYYMMDD, got {text!r}")


def retry(fn, what, attempts=3, delay=20):
    """The open-data server times out intermittently."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == attempts:
                raise
            print(f"  {what} failed ({type(e).__name__}: {e}), "
                  f"retry {attempt}/{attempts - 1} in {delay}s")
            time.sleep(delay)


def download(init, steps, target, source):
    """Retrieve the global GRIB file for one init time and open it."""
    client = Client(source=source, model=MODEL, resol=RESOLUTION)
    client.retrieve(
        date=int(init.strftime("%Y%m%d")),
        time=init.hour,
        type=TYPE,
        stream=STREAM,
        step=steps,
        param=PARAM,
        target=str(target),
    )
    print(f"Downloaded global GRIB: {target}")
    return xr.open_dataset(target, engine="cfgrib", backend_kwargs={"indexpath": ""}, decode_timedelta=True)


def subset_bbox(da, bbox):
    """N/W/S/E subset. Source longitudes run 180..179.9, so wrap them to -180..180 first."""
    north, west, south, east = bbox
    da = da.assign_coords(
        latitude=da["latitude"].round(6),
        longitude=(((da["longitude"] + 180.0) % 360.0) - 180.0).round(6),
    )
    da = da.sortby("longitude").sortby("latitude")
    out = da.sel(latitude=slice(south, north), longitude=slice(west, east))
    if out.sizes["latitude"] == 0 or out.sizes["longitude"] == 0:
        raise RuntimeError(f"bbox {bbox} selects no grid cells")
    return out


def precip_mm(tp_m, accumulated):
    """Metres of accumulated precipitation -> mm. Per-interval amounts unless accumulated."""
    total_mm = (tp_m * 1000.0).astype("float32")
    if accumulated:
        return total_mm
    interval = total_mm.diff("step", label="upper").clip(min=0)
    first = xr.zeros_like(total_mm.isel(step=[0]))
    return xr.concat([first, interval], dim="step").astype("float32")


def build_precip(init, args, grib):
    """Build the cropped precipitation Dataset for one init time."""
    steps = list(range(0, args.max_step + 1, args.step_interval))
    raw = download(init, steps, grib, args.source)
    try:
        got = np.asarray(raw["time"].values).astype("datetime64[ns]")
        if got != np.datetime64(init, "ns"):
            raise RuntimeError(f"ECMWF returned init {got}, requested {init}")
        da = subset_bbox(raw[PARAM], BOUNDING_BOX).load()
    finally:
        raw.close()

    tp = precip_mm(da, args.accumulated).reset_coords(drop=True)
    tp = tp.expand_dims(number=[0]).transpose("number", "step", "latitude", "longitude")
    tp.attrs = dict(PRECIP_ATTRS)

    ds = xr.Dataset({"tp": tp})
    ds = ds.assign_coords(time=np.datetime64(init, "ns"))
    ds = ds.assign_coords(valid_time=("step", np.datetime64(init, "ns") + ds["step"].values))
    ds.attrs = {
        "history": f"{datetime.now():%Y-%m-%dT%H:%M} built by download_medium_range.py "
                   f"from ECMWF open data ({MODEL} {STREAM} {TYPE}, {RESOLUTION} degrees)",
    }
    return ds


def ensure_store(init, args, out_dir):
    """Skip if the store is already complete, else build it and mark it complete."""
    zarr_path = out_dir / f"{ZARR_NAME}.zarr"
    grib = out_dir / f"medium_range_tp_global_{RESOLUTION}.grib2"

    if zarr_path.is_dir() and gef.is_complete(str(zarr_path)):
        print(f"{zarr_path} already complete, skipping")
        return zarr_path
    if zarr_path.exists():
        shutil.rmtree(zarr_path)

    print(f"Building {zarr_path}")
    ds = retry(lambda: build_precip(init, args, grib), f"build {ZARR_NAME}")
    print(f"  dims {dict(ds.sizes)} vars {list(ds.data_vars)}")
    ds.to_zarr(str(zarr_path), mode="w", consolidated=True)
    gef.mark_complete(str(zarr_path))
    if grib.exists():
        grib.unlink()
    return zarr_path


# ============================================================
# MAIN
# ============================================================

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", default=date_str, help="Model date, YYYY-MM-DD or YYYYMMDD. Default: DATE_STR or two days ago")
    p.add_argument("--time", type=int, default=0, choices=[0, 12], help="Run time UTC, 0 or 12. Default: 0")
    p.add_argument("--max-step", type=int, default=336, help="Last forecast step in hours (336 = 2 weeks). Default: 336")
    p.add_argument("--step-interval", type=int, default=6, help="Hours between steps. Default: 6")
    p.add_argument("--source", default="aws", choices=["ecmwf", "azure", "aws", "google"],
                   help="Mirror to download from. Default: aws")
    p.add_argument("--outdir", default=None, help="Output directory. Default: <OUTPUT_DIR>/<date>")
    p.add_argument("--accumulated", action="store_true",
                   help="Keep accumulated precipitation instead of per-interval amounts")
    args = p.parse_args()

    init = parse_date(args.date) + timedelta(hours=args.time)
    out_dir = Path(args.outdir) if args.outdir else OUTPUT_DIR / init.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {ZARR_NAME} for init {init:%Y-%m-%d %H:%M} UTC into {out_dir}")

    failed = False
    try:
        ensure_store(init, args, out_dir)
        print(f"Wrote {out_dir / (ZARR_NAME + '.zarr')}")
    except Exception as e:
        failed = True
        print(f"FAILED {ZARR_NAME}: {type(e).__name__}: {e}")
        traceback.print_exc()

    # Exit without interpreter teardown: the native stack (cfgrib/eccodes) can abort
    # on shutdown, and a signal death reports no exit code. The store is already
    # written and marked complete synchronously above.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    main()
