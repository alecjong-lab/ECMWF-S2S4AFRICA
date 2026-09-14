"""Write a Kenya-wide daily-disaggregated GeoTIFF from weekly downscaled S2S.

Reads data/<date>/data_weekly_Kenya_downscaled.nc (ensemble mean) and the
native daily ECMWF accumulations, splits each week into lead days with
get_ECMWF_functions.disaggregate_weekly_to_daily, clips to the Kenya
counties shapefile, and writes data/<date>/daily_downscaled_kenya.tif
(one band per lead day).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import rioxarray  # noqa: F401 — registers .rio on xarray objects
import xarray as xr

import get_ECMWF_functions as gef

KENYA_SHP = "downscale_data/Kenya_Counties_KNSDI.shp"
WEEKLY_NAME = "data_weekly_Kenya_downscaled.nc"
OUT_NAME = "daily_downscaled_kenya.tif"


def _date_str():
    if "DATE_STR" in os.environ:
        return os.environ["DATE_STR"]
    return (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")


def _data_dir(date_str):
    prefix = os.environ.get("MAIN_PATH", "")
    return os.path.join(prefix, "data", date_str) if prefix else os.path.join("data", date_str)


def _ensemble_mean(obj):
    if "number" in obj.dims:
        return obj.mean("number", keep_attrs=True)
    return obj


def _open_weekly(path):
    if not os.path.exists(path):
        raise SystemExit(f"weekly Kenya downscaled file not found: {path}")
    ds = xr.open_dataset(path)
    if "tp" not in ds:
        raise SystemExit(f"{path} has no 'tp' variable")
    return _ensemble_mean(ds).tp


def _open_ecmwf_daily(data_dir, date_str):
    zarr = os.path.join(data_dir, f"ECMWF_s2s_precip_{date_str}.zarr")
    if not os.path.isdir(zarr):
        raise SystemExit(f"ECMWF daily zarr not found: {zarr}")
    ds = xr.open_zarr(zarr, consolidated=True)
    if "tp" not in ds:
        raise SystemExit(f"{zarr} has no 'tp' variable")
    return _ensemble_mean(ds).tp


def _to_geotiff(daily, out_path):
    da = daily.tp if isinstance(daily, xr.Dataset) else daily
    drop = [c for c in ("year", "time", "valid_time", "surface") if c in da.coords]
    if drop:
        da = da.drop_vars(drop, errors="ignore")
    ds = da.to_dataset(name="tp")
    ds = ds.rio.write_crs("EPSG:4326")
    ds = ds.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
    ds = gef.clip_to_shapefile(ds, KENYA_SHP, sortby_lat=True)
    da = ds.tp
    da = da.rio.write_crs("EPSG:4326")
    da = da.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
    if "step" in da.dims:
        da = da.transpose("step", "latitude", "longitude")
    else:
        da = da.transpose("latitude", "longitude")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    da.rio.to_raster(out_path, compress="deflate")
    n_bands = int(da.sizes["step"]) if "step" in da.sizes else 1
    print(f"Wrote: {out_path} ({n_bands} daily band(s))")


def main():
    date_str = _date_str()
    data_dir = _data_dir(date_str)
    weekly = _open_weekly(os.path.join(data_dir, WEEKLY_NAME))
    ecmwf = _open_ecmwf_daily(data_dir, date_str)
    daily = gef.disaggregate_weekly_to_daily(weekly, ecmwf)
    _to_geotiff(daily, os.path.join(data_dir, OUT_NAME))


if __name__ == "__main__":
    main()
