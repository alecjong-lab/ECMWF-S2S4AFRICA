"""
Downloads the ECMWF IFS ENS extended-range forecast from the dynamical.org
open catalog.

This replaced the cdsapi/ecmwf.opendata version, which is kept as
download_s2s_legacy.py for reference; the two write identical zarr stores.

It writes exactly the same zarr stores, with the same names, dims, coords,
variable names, units and GRIB_* attributes as the cdsapi/cfgrib path, so
every downstream script (plot_s2s.py, IndianOceanState.py, dowscale_dekade.py,
climate_indices_timeseries.py, run_rainfall_onset.py) keeps working unchanged:

    ECMWF_s2s_precip_<date>.zarr        ECMWF_s2s_10wind_alt_<date>.zarr
    ECMWF_s2s_daily_vars_<date>.zarr    ECMWF_s2s_sst_<date>.zarr
    ECMWF_s2s_10wind_<date>.zarr        ECMWF_s2s_sst_nino34_<date>.zarr
    ECMWF_s2s_Tminmax_<date>.zarr       ECMWF_s2s_precip_alt_<date>.zarr
    ECMWF_s2s_700wind_<date>.zarr       ECMWF_s2s_tcw_<date>.zarr
    ECMWF_s2s_500wind_<date>.zarr       ECMWF_s2s_q_u_<date>.zarr
    medium_range_precip.zarr

Env vars (same as the legacy script, minus CDSAPI_KEY which is no longer needed):
    DATE_STR          forecast init date, defaults to today-2
    BOUNDING_BOX      "N,W,S,E" for the Africa domain, e.g. "22.5,-21,-34.5,55.5"
    BUCKET            GCS bucket to restore already-built stores from
    SKIP_GCS_RESTORE  set to "1" to always rebuild from dynamical, ignoring
                      anything already in the bucket (useful for A/B checks
                      against the CDS-built stores for the same date)

Four catalog datasets back all of this:
  * ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree      (surface, 24-hourly)
  * ... same id, group="pressure_level"                 (u/v/w/q on levels)
  * ecmwf-ifs-ens-forecast-46-day-6-hourly-1-5-degree   (6-hourly Tmin/Tmax)
  * ecmwf-ifs-ens-forecast-15-day-0-25-degree           (medium range precip)

Differences from the CDS path worth knowing about:
  * dynamical's extended-range archive starts 2026-01-01, so earlier dates can
    only come from GCS, not be rebuilt here.
  * dynamical publishes precipitation as a per-interval mean rate, not as an
    accumulation-since-init. It is re-accumulated below so that `tp` stays
    cumulative in mm exactly as cfgrib produced it, which is what
    gef.acum_to_instant and the `.diff('step')` callers all assume.
  * temperatures are published in degC and are converted back to K here, since
    the m-climate reference files and gef.convert_to_celcius both expect K.
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr

import dynamical_catalog

import get_ECMWF_functions as gef

# ============================================================
# CONFIG
# ============================================================

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

print(f"Downloading data for: {date_str}")

path = f'data/{date_str}/'
os.makedirs(path, exist_ok=True)

bounding_box = list(map(float, os.environ["BOUNDING_BOX"].split(',')))

BUCKET = os.environ.get("BUCKET", "africa-forecasting-data")
SKIP_GCS_RESTORE = os.environ.get("SKIP_GCS_RESTORE", "").lower() in ("1", "true", "yes")

DAILY_DATASET = "ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree"
SIXHOURLY_DATASET = "ecmwf-ifs-ens-forecast-46-day-6-hourly-1-5-degree"
MEDIUM_DATASET = "ecmwf-ifs-ens-forecast-15-day-0-25-degree"

INIT = np.datetime64(f"{date_str}T00:00:00", "ns")

# Same alternate domains the legacy script used, as N/W/S/E.
alt_bounding_box = [20, 45, -20, 120]
nino34_bounding_box = [5, -180, -5, 179.5]
indian_ocean_bounding_box = [20, -30, -20, 120]
precip_alt_bounding_box = [20, 30, -20, 120]

# Coordinates dynamical carries that cfgrib never produced - dropped so the
# written stores don't gain coords the CDS-built ones didn't have.
_DROP_COORDS = (
    "valid_time",
    "expected_forecast_length",
    "ingested_forecast_length",
    "spatial_ref",
)

# ============================================================
# VARIABLE TABLE
# dynamical name -> (cfgrib name, unit conversion, cfgrib attrs)
# The attrs are copied verbatim from the cfgrib-written stores so that gef's
# plotting helpers (which read GRIB_name / units off each variable) render
# identical labels.
# ============================================================

def _to_kelvin(da):
    """degC -> K, keeping float32 (a Python float scalar doesn't upcast)."""
    return da + 273.15


VARIABLES = {
    "precipitation_surface": ("tp", None, {
        "GRIB_paramId": 228228, "GRIB_typeOfLevel": "surface", "GRIB_stepType": "accum",
        "GRIB_cfVarName": "tp", "GRIB_name": "Total Precipitation", "GRIB_shortName": "tp",
        "GRIB_units": "kg m**-2", "long_name": "Total Precipitation", "units": "kg m**-2",
        "standard_name": "unknown"}),
    "average_temperature_2m": ("t2m", _to_kelvin, {
        "GRIB_paramId": 167, "GRIB_typeOfLevel": "heightAboveGround", "GRIB_stepType": "avg",
        "GRIB_cfVarName": "t2m", "GRIB_name": "2 metre temperature", "GRIB_shortName": "2t",
        "GRIB_units": "K", "long_name": "2 metre temperature", "units": "K",
        "standard_name": "air_temperature"}),
    "average_dew_point_temperature_2m": ("d2m", _to_kelvin, {
        "GRIB_paramId": 168, "GRIB_typeOfLevel": "heightAboveGround", "GRIB_stepType": "avg",
        "GRIB_cfVarName": "d2m", "GRIB_name": "2 metre dewpoint temperature",
        "GRIB_shortName": "2d", "GRIB_units": "K",
        "long_name": "2 metre dewpoint temperature", "units": "K",
        "standard_name": "unknown"}),
    "average_convective_available_potential_energy_surface": ("cape", None, {
        "GRIB_paramId": 59, "GRIB_typeOfLevel": "entireAtmosphere", "GRIB_stepType": "avg",
        "GRIB_cfVarName": "cape", "GRIB_name": "Convective available potential energy",
        "GRIB_shortName": "cape", "GRIB_units": "J kg**-1",
        "long_name": "Convective available potential energy", "units": "J kg**-1",
        "standard_name": "unknown"}),
    "total_column_water_atmosphere": ("tcw", None, {
        "GRIB_paramId": 136, "GRIB_typeOfLevel": "entireAtmosphere", "GRIB_stepType": "avg",
        "GRIB_cfVarName": "tcw", "GRIB_name": "Total column water", "GRIB_shortName": "tcw",
        "GRIB_units": "kg m**-2", "long_name": "Total column water", "units": "kg m**-2",
        "standard_name": "unknown"}),
    "wind_u_10m": ("u10", None, {
        "GRIB_paramId": 165, "GRIB_typeOfLevel": "heightAboveGround", "GRIB_stepType": "instant",
        "GRIB_cfVarName": "u10", "GRIB_name": "10 metre U wind component",
        "GRIB_shortName": "10u", "GRIB_units": "m s**-1",
        "long_name": "10 metre U wind component", "units": "m s**-1",
        "standard_name": "eastward_wind"}),
    "wind_v_10m": ("v10", None, {
        "GRIB_paramId": 166, "GRIB_typeOfLevel": "heightAboveGround", "GRIB_stepType": "instant",
        "GRIB_cfVarName": "v10", "GRIB_name": "10 metre V wind component",
        "GRIB_shortName": "10v", "GRIB_units": "m s**-1",
        "long_name": "10 metre V wind component", "units": "m s**-1",
        "standard_name": "northward_wind"}),
    "maximum_temperature_2m": ("mx2t6", _to_kelvin, {
        "GRIB_paramId": 121, "GRIB_typeOfLevel": "heightAboveGround", "GRIB_stepType": "max",
        "GRIB_cfVarName": "mx2t6",
        "GRIB_name": "Maximum temperature at 2 metres in the last 6 hours",
        "GRIB_shortName": "mx2t6", "GRIB_units": "K",
        "long_name": "Maximum temperature at 2 metres in the last 6 hours", "units": "K",
        "standard_name": "air_temperature"}),
    "minimum_temperature_2m": ("mn2t6", _to_kelvin, {
        "GRIB_paramId": 122, "GRIB_typeOfLevel": "heightAboveGround", "GRIB_stepType": "min",
        "GRIB_cfVarName": "mn2t6",
        "GRIB_name": "Minimum temperature at 2 metres in the last 6 hours",
        "GRIB_shortName": "mn2t6", "GRIB_units": "K",
        "long_name": "Minimum temperature at 2 metres in the last 6 hours", "units": "K",
        "standard_name": "air_temperature"}),
    "sea_surface_temperature": ("sst", _to_kelvin, {
        "GRIB_paramId": 34, "GRIB_typeOfLevel": "surface", "GRIB_stepType": "avg",
        "GRIB_cfVarName": "sst", "GRIB_name": "Sea surface temperature",
        "GRIB_shortName": "sst", "GRIB_units": "K",
        "long_name": "Sea surface temperature", "units": "K",
        "standard_name": "unknown"}),
    "wind_u": ("u", None, {
        "GRIB_paramId": 131, "GRIB_typeOfLevel": "isobaricInhPa", "GRIB_stepType": "instant",
        "GRIB_cfVarName": "u", "GRIB_name": "U component of wind", "GRIB_shortName": "u",
        "GRIB_units": "m s**-1", "long_name": "U component of wind", "units": "m s**-1",
        "standard_name": "eastward_wind"}),
    "wind_v": ("v", None, {
        "GRIB_paramId": 132, "GRIB_typeOfLevel": "isobaricInhPa", "GRIB_stepType": "instant",
        "GRIB_cfVarName": "v", "GRIB_name": "V component of wind", "GRIB_shortName": "v",
        "GRIB_units": "m s**-1", "long_name": "V component of wind", "units": "m s**-1",
        "standard_name": "northward_wind"}),
    "vertical_velocity": ("w", None, {
        "GRIB_paramId": 135, "GRIB_typeOfLevel": "isobaricInhPa", "GRIB_stepType": "instant",
        "GRIB_cfVarName": "w", "GRIB_name": "Vertical velocity", "GRIB_shortName": "w",
        "GRIB_units": "Pa s**-1", "long_name": "Vertical velocity", "units": "Pa s**-1",
        "standard_name": "lagrangian_tendency_of_air_pressure"}),
    "specific_humidity": ("q", None, {
        "GRIB_paramId": 133, "GRIB_typeOfLevel": "isobaricInhPa", "GRIB_stepType": "instant",
        "GRIB_cfVarName": "q", "GRIB_name": "Specific humidity", "GRIB_shortName": "q",
        "GRIB_units": "kg kg**-1", "long_name": "Specific humidity", "units": "kg kg**-1",
        "standard_name": "specific_humidity"}),
}

# The medium-range store isn't a plain rename of a catalog variable (it's the
# weekly difference of an accumulation), so its attrs are listed out here. They
# match what the ecmwf.opendata path left on it: cfgrib's own tp attrs, with
# 'units' overwritten to mm because the values were scaled from m to mm.
MEDIUM_TP_ATTRS = {
    "GRIB_paramId": 228, "GRIB_typeOfLevel": "surface", "GRIB_stepType": "accum",
    "GRIB_cfVarName": "tp", "GRIB_name": "Total precipitation", "GRIB_shortName": "tp",
    "GRIB_units": "m", "long_name": "Total precipitation", "units": "mm",
    "standard_name": "unknown",
}

# cfgrib's coordinate attrs. These are set explicitly rather than inherited,
# because dynamical attaches a dict-valued `statistics_approximate` to its
# lat/lon coords, and a dict is not a serializable netCDF attribute - it makes
# plot_s2s.py's `data_weekly.to_netcdf(...)` handoff raise TypeError.
COORD_ATTRS = {
    "latitude": {"units": "degrees_north", "standard_name": "latitude",
                 "long_name": "latitude", "stored_direction": "decreasing"},
    "longitude": {"units": "degrees_east", "standard_name": "longitude",
                  "long_name": "longitude"},
    "number": {"units": "1", "standard_name": "realization",
               "long_name": "ensemble member numerical id"},
    "step": {"long_name": "time since forecast_reference_time",
             "standard_name": "forecast_period"},
    "time": {"long_name": "initial time of forecast",
             "standard_name": "forecast_reference_time"},
    "valid_time": {"standard_name": "time", "long_name": "time"},
    "isobaricInhPa": {"long_name": "pressure", "units": "hPa", "positive": "down",
                      "stored_direction": "decreasing", "standard_name": "air_pressure"},
    "heightAboveGround": {"long_name": "height above the surface", "units": "m",
                          "positive": "up", "standard_name": "height"},
    "surface": {"long_name": "original GRIB coordinate for key: level(surface)",
                "units": "1"},
    "entireAtmosphere": {"long_name": "original GRIB coordinate for key: "
                                      "level(entireAtmosphere)", "units": "1"},
}

# Matches what cfgrib attached as the store-level attrs.
GLOBAL_ATTRS = {
    "GRIB_edition": "2",
    "GRIB_centre": "ecmf",
    "GRIB_centreDescription": "European Centre for Medium-Range Weather Forecasts",
    "GRIB_subCentre": "0",
    "Conventions": "CF-1.7",
    "institution": "European Centre for Medium-Range Weather Forecasts",
}


# ============================================================
# CATALOG ACCESS
# ============================================================

_source_cache = {}


def retry(fn, what, attempts=5, delay=10):
    """dynamical's STAC catalog and object reads both time out intermittently."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == attempts:
                raise
            print(f"  {what} failed ({type(e).__name__}: {e}), "
                  f"retry {attempt}/{attempts - 1} in {delay}s")
            time.sleep(delay)


def open_source(dataset, group=None):
    """Open (and cache) a catalog dataset. Lazy - only metadata is read here."""
    key = (dataset, group)
    if key not in _source_cache:
        kwargs = {"chunks": None}
        if group:
            kwargs["group"] = group
        label = dataset if not group else f"{dataset}[{group}]"
        print(f"Opening dynamical:{label}")
        _source_cache[key] = retry(
            lambda: dynamical_catalog.open(dataset, **kwargs), f"open {label}"
        )
    return _source_cache[key]


def select_init(ds, dataset_label):
    inits = ds["init_time"].values
    if INIT not in inits:
        raise RuntimeError(
            f"{dataset_label} has no {date_str} 00 UTC init; available inits run "
            f"{str(inits.min())[:10]}..{str(inits.max())[:10]}. dynamical publishes "
            f"with roughly 48h latency and its extended-range archive starts 2026-01-01."
        )
    return ds.sel(init_time=INIT)


# ============================================================
# TRANSFORMS
# ============================================================

def step_hours(ds):
    return (ds["step"].values / np.timedelta64(1, "h")).astype(int)


def select_steps(ds, first, last, by):
    """Subset to an explicit lead-time list, in hours, erroring on any gap."""
    want = np.arange(first, last + 1, by)
    have = step_hours(ds)
    index = {int(h): i for i, h in enumerate(have)}
    missing = [int(h) for h in want if int(h) not in index]
    if missing:
        raise RuntimeError(
            f"lead times {missing} are not in the source, which has "
            f"{have[0]}..{have[-1]} by {have[1] - have[0]}h"
        )
    return ds.isel(step=[index[int(h)] for h in want])


def subset_bbox(ds, bbox):
    """N/W/S/E subset. Source lat runs 90..-90 and lon -180..178.5."""
    north, west, south, east = bbox
    out = ds.sel(latitude=slice(north, south), longitude=slice(west, east))
    if out.sizes["latitude"] == 0 or out.sizes["longitude"] == 0:
        raise RuntimeError(f"bbox {bbox} selects no grid cells")
    return out


def normalize(ds):
    """dynamical dim/coord names and dtypes -> the cfgrib ones."""
    rename = {"lead_time": "step", "ensemble_member": "number"}
    if "pressure_level" in ds.coords:
        rename["pressure_level"] = "isobaricInhPa"
    ds = ds.rename(rename)
    ds = ds.drop_vars([c for c in _DROP_COORDS if c in ds.coords])
    ds = ds.assign_coords(
        step=ds["step"].values.astype("timedelta64[ns]"),
        number=ds["number"].values.astype("int64"),
    )
    if "isobaricInhPa" in ds.coords:
        ds = ds.assign_coords(isobaricInhPa=ds["isobaricInhPa"].values.astype("float64"))
    return ds.drop_vars("init_time")


def finalize(ds, level_coords):
    """Attach time/valid_time, the scalar level coords, attrs, and order the dims."""
    ds = ds.assign_coords(time=INIT)
    ds = ds.assign_coords(valid_time=("step", INIT + ds["step"].values))
    for name, value in level_coords.items():
        ds = ds.assign_coords({name: np.float64(value)})

    dims = ["number", "step"]
    if "isobaricInhPa" in ds.dims:
        dims.append("isobaricInhPa")
    dims += ["latitude", "longitude"]
    ds = ds.transpose(*dims)

    for name in ds.coords:
        ds[name].attrs = dict(COORD_ATTRS.get(name, {}))

    ds.attrs = dict(GLOBAL_ATTRS)
    ds.attrs["history"] = (
        f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M} built by download_s2s.py "
        f"from the dynamical.org open catalog"
    )
    # The source store's zarr v3 chunk/shard layout no longer matches these
    # subsets, so let the writer pick its own rather than inheriting it.
    for var in ds.variables.values():
        for key in ("chunks", "shards", "compressors", "filters", "preferred_chunks"):
            var.encoding.pop(key, None)
    return ds


def pick(ds, names, level_coords):
    """Rename the wanted dynamical variables to cfgrib names and fix units/attrs."""
    out = xr.Dataset()
    for source_name in names:
        target, convert, attrs = VARIABLES[source_name]
        da = ds[source_name]
        if convert is not None:
            da = convert(da)
        da = da.astype("float32")
        da.attrs = dict(attrs)
        out[target] = da
    out = out.assign_coords(ds.coords)
    return finalize(out, level_coords)


def accumulate_precip(da):
    """
    Per-interval mean rate (kg m-2 s-1) -> accumulation-since-init in mm.

    Each lead time t carries the mean rate over the interval ending at t, so
    that interval's depth is rate * (t - t_previous). Lead 0 has no preceding
    interval and is published as NaN; it becomes the 0 baseline, which is what
    cfgrib's accumulated `tp` had at step 0 and what gef.acum_to_instant
    detects and drops. The lead axis is not always evenly spaced (the 15-day
    dataset is 3-hourly out to 144h and 6-hourly after), hence per-step widths.
    """
    hours = (da["step"].values / np.timedelta64(1, "h")).astype("float64")
    width_s = xr.DataArray(
        np.diff(hours, prepend=hours[0]) * 3600.0,
        dims="step",
        coords={"step": da["step"]},
    )
    stepwise = (da.fillna(0.0) * width_s).astype("float32")
    return stepwise.cumsum("step").astype("float32")


# ============================================================
# GROUP BUILDERS
# Each returns the finished Dataset for one zarr store.
# ============================================================

def build_precip(bbox):
    ds = normalize(select_init(open_source(DAILY_DATASET), DAILY_DATASET))
    ds = subset_bbox(ds[["precipitation_surface"]], bbox).load()
    tp = accumulate_precip(ds["precipitation_surface"])
    tp.attrs = dict(VARIABLES["precipitation_surface"][2])
    out = xr.Dataset({"tp": tp}).assign_coords(ds.coords)
    return finalize(out, {"surface": 0.0})


def build_daily_vars(bbox):
    names = [
        "average_dew_point_temperature_2m",
        "average_temperature_2m",
        "average_convective_available_potential_energy_surface",
        "total_column_water_atmosphere",
    ]
    ds = normalize(select_init(open_source(DAILY_DATASET), DAILY_DATASET))
    ds = select_steps(subset_bbox(ds[names], bbox), 24, 1008, 24).load()
    return pick(ds, names, {"heightAboveGround": 2.0, "entireAtmosphere": 0.0})


def build_10wind(bbox):
    names = ["wind_u_10m", "wind_v_10m"]
    ds = normalize(select_init(open_source(DAILY_DATASET), DAILY_DATASET))
    ds = select_steps(subset_bbox(ds[names], bbox), 0, 1008, 24).load()
    return pick(ds, names, {"heightAboveGround": 10.0})


def build_tminmax(bbox):
    # The CDS request was "6/to/1014/by/12", i.e. only two of each day's four
    # 6-hour windows. gef.day_mean_6h_accum takes its daily max/min over those
    # two, and m-climate/Tmin_Tmax was built the same way - so the same subset
    # is reproduced here rather than the full 6-hourly series, which would bias
    # the forecast warm/cold relative to that climatology.
    names = ["maximum_temperature_2m", "minimum_temperature_2m"]
    ds = normalize(select_init(open_source(SIXHOURLY_DATASET), SIXHOURLY_DATASET))
    ds = select_steps(subset_bbox(ds[names], bbox), 6, 1014, 12).load()
    return pick(ds, names, {"heightAboveGround": 2.0})


def build_700wind(bbox):
    names = ["wind_u", "wind_v"]
    ds = normalize(select_init(open_source(DAILY_DATASET, "pressure_level"), DAILY_DATASET))
    ds = ds[names].sel(isobaricInhPa=700).drop_vars("isobaricInhPa")
    ds = select_steps(subset_bbox(ds, bbox), 0, 984, 24).load()
    return pick(ds, names, {"isobaricInhPa": 700.0})


def build_500wind(bbox):
    names = ["vertical_velocity"]
    ds = normalize(select_init(open_source(DAILY_DATASET, "pressure_level"), DAILY_DATASET))
    ds = ds[names].sel(isobaricInhPa=500).drop_vars("isobaricInhPa")
    ds = select_steps(subset_bbox(ds, bbox), 0, 984, 24).load()
    return pick(ds, names, {"isobaricInhPa": 500.0})


def build_sst(bbox):
    names = ["sea_surface_temperature"]
    ds = normalize(select_init(open_source(DAILY_DATASET), DAILY_DATASET))
    ds = select_steps(subset_bbox(ds[names], bbox), 24, 1008, 24).load()
    return pick(ds, names, {"surface": 0.0})


def build_tcw(bbox):
    names = ["total_column_water_atmosphere"]
    ds = normalize(select_init(open_source(DAILY_DATASET), DAILY_DATASET))
    ds = select_steps(subset_bbox(ds[names], bbox), 24, 1008, 24).load()
    return pick(ds, names, {"entireAtmosphere": 0.0})


def build_q_u(bbox):
    names = ["specific_humidity", "wind_u"]
    levels = [1000, 925, 850, 700, 500, 300]
    ds = normalize(select_init(open_source(DAILY_DATASET, "pressure_level"), DAILY_DATASET))
    ds = ds[names].sel(isobaricInhPa=levels)
    ds = select_steps(subset_bbox(ds, bbox), 0, 1104, 24).load()
    return pick(ds, names, {})


def build_medium_range(bbox):
    """
    Weekly precip totals (mm) at steps 168h and 336h, matching what the
    ecmwf.opendata path produced by differencing accumulated tp at 0/168/336.
    """
    ds = normalize(select_init(open_source(MEDIUM_DATASET), MEDIUM_DATASET))
    ds = subset_bbox(ds[["precipitation_surface"]], bbox).load()
    accumulated = accumulate_precip(ds["precipitation_surface"])
    marks = np.array([0, 168, 336], dtype="timedelta64[h]").astype("timedelta64[ns]")
    weekly = accumulated.sel(step=marks).diff("step").astype("float32")
    weekly.attrs = dict(MEDIUM_TP_ATTRS)
    out = xr.Dataset({"tp": weekly})
    out = out.assign_coords({k: v for k, v in ds.coords.items() if k != "step"})
    return finalize(out, {"surface": 0.0})


# ============================================================
# BUILD + WRITE
# ============================================================

def ensure_group_zarr(zarr_name, build_fn, bbox):
    """
    Same contract as gef.ensure_group_zarr: skip if already complete locally,
    else restore from GCS, else build it. Only the "build it" branch differs -
    there is no GRIB download/combine step any more.
    """
    zarr_path = f"{path}/{zarr_name}.zarr"
    gcs_prefix = f"data/{date_str}/{zarr_name}.zarr"

    if os.path.isdir(zarr_path) and gef.is_complete(zarr_path):
        print(f"{zarr_path} already complete locally, skipping")
        return zarr_path

    if not SKIP_GCS_RESTORE:
        if gef.restore_zarr_from_gcs(BUCKET, gcs_prefix, zarr_path) and gef.is_complete(zarr_path):
            print(f"Restored {zarr_path} from GCS")
            return zarr_path

    print(f"Building {zarr_path}")
    ds = retry(lambda: build_fn(bbox), f"build {zarr_name}", attempts=3, delay=20)
    print(f"  dims {dict(ds.sizes)} vars {list(ds.data_vars)}")
    ds.to_zarr(zarr_path, mode="w", consolidated=True)
    gef.mark_complete(zarr_path)
    return zarr_path


groups = [
    (f"ECMWF_s2s_precip_{date_str}", build_precip, bounding_box),
    (f"ECMWF_s2s_daily_vars_{date_str}", build_daily_vars, bounding_box),
    (f"ECMWF_s2s_10wind_{date_str}", build_10wind, bounding_box),
    (f"ECMWF_s2s_Tminmax_{date_str}", build_tminmax, bounding_box),
    (f"ECMWF_s2s_700wind_{date_str}", build_700wind, bounding_box),
    (f"ECMWF_s2s_500wind_{date_str}", build_500wind, bounding_box),
    (f"ECMWF_s2s_10wind_alt_{date_str}", build_10wind, alt_bounding_box),
    (f"ECMWF_s2s_sst_{date_str}", build_sst, alt_bounding_box),
    (f"ECMWF_s2s_sst_nino34_{date_str}", build_sst, nino34_bounding_box),
    (f"ECMWF_s2s_precip_alt_{date_str}", build_precip, precip_alt_bounding_box),
    (f"ECMWF_s2s_tcw_{date_str}", build_tcw, indian_ocean_bounding_box),
    (f"ECMWF_s2s_q_u_{date_str}", build_q_u, indian_ocean_bounding_box),
    ("medium_range_precip", build_medium_range, bounding_box),
]

# One group failing (a variable not yet ingested for this init, say) shouldn't
# stop the other twelve, the same way the CI workflow keeps going past a failed
# stage. Anything that did fail is re-raised at the end so the step is still
# marked failed and the workflow's retry only has the missing stores left to do.
failures = []
for zarr_name, build_fn, group_bbox in groups:
    try:
        ensure_group_zarr(zarr_name, build_fn, group_bbox)
    except Exception as e:
        print(f"FAILED {zarr_name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        failures.append(zarr_name)

if failures:
    print(f"{len(failures)} group(s) failed: {', '.join(failures)}", file=sys.stderr)
else:
    print("All groups complete.")

# Exit without interpreter teardown: something in the native stack can abort
# on shutdown ("double free or corruption"), and a signal death reports no
# exit code, which nick-fields/retry treats as success — so the workflow's
# CDS/ECDS fallback never ran. Every store is already written and marked
# complete synchronously above, so there's nothing left for teardown to do.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
