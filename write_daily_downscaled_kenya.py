"""Write data/<date>/daily_downscaled_kenya.tif from weekly Kenya downscaling.

Ensemble-mean weekly grid, split to daily leads, clipped to Kenya counties.
"""
import os
from datetime import datetime, timedelta

import rioxarray  # noqa: F401 — registers .rio
import xarray as xr

import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    date_str = (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")

prefix = os.environ.get("MAIN_PATH", "")
data_dir = os.path.join(prefix, "data", date_str) if prefix else os.path.join("data", date_str)

weekly = xr.open_dataset(f"{data_dir}/data_weekly_Kenya_downscaled.nc").tp
ecmwf = xr.open_zarr(f"{data_dir}/ECMWF_s2s_precip_{date_str}.zarr", consolidated=True).tp
if "number" in weekly.dims:
    weekly = weekly.mean("number", keep_attrs=True)
if "number" in ecmwf.dims:
    ecmwf = ecmwf.mean("number", keep_attrs=True)

daily = gef.disaggregate_weekly_to_daily(weekly, ecmwf).tp
daily = daily.drop_vars(
    [c for c in ("year", "time", "valid_time", "surface") if c in daily.coords],
    errors="ignore",
)
ds = daily.to_dataset(name="tp").rio.write_crs("EPSG:4326")
ds = ds.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
ds = gef.clip_to_shapefile(ds, "downscale_data/Kenya_Counties_KNSDI.shp", sortby_lat=True)
da = ds.tp.rio.write_crs("EPSG:4326")
da = da.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
if "step" in da.dims:
    da = da.transpose("step", "latitude", "longitude")

out = f"{data_dir}/daily_downscaled_kenya.tif"
os.makedirs(data_dir, exist_ok=True)
da.rio.to_raster(out, compress="deflate")
print(f"Wrote: {out}")
