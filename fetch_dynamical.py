#!/usr/bin/env python
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "cftime",
#   "dynamical-catalog==0.5.0",
#   "xarray",
#   "zarr",
#   "numpy",
# ]
# ///
"""
Standalone version of the weather-skills `dynamical-fetch` skill, stripped of
the weather_skills_core framework so it can run directly from a GitHub Actions
step, e.g.:

    uv run fetch_dynamical.py \\
        --dataset noaa-gfs-forecast \\
        --date latest \\
        --bbox 10 45 -10 55 \\
        --variable temperature_2m \\
        --output ./out.zarr

Date grammar for --date / --start / --end:
  YYYY-MM-DD | now | today | latest | now-<int>d | now-<int>w |
  latest-<int>d | latest-<int>w   (w = 7 days)

Forecast datasets (have `lead_time` and/or `ensemble_member` dims) take
--date. Analysis datasets (have `time` but no `lead_time`) take --start/--end.
"""

import argparse
import re
import sys
from datetime import date, datetime, timedelta

import numpy as np

_DROP_COORDS = (
    "valid_time",
    "expected_forecast_length",
    "ingested_forecast_length",
    "spatial_ref",
)

_OFFSET_TOKEN_RE = re.compile(r"^(now|latest)-(\d+)([dw])$")

class UsageError(Exception):
    """Bad arguments / bad usage. Caller exits with code 2."""

class DataError(Exception):
    """Requested data isn't available. Caller exits with code 1."""

def np_to_date(val) -> date:
    return np.datetime64(val, "D").astype(date)

def parse_token(token: str, latest_fn=None) -> date:
    """Parse a single date token (see module docstring for grammar)."""
    if token in ("now", "today"):
        return datetime.utcnow().date()
    if token == "latest":
        if latest_fn is None:
            raise UsageError("'latest' is not valid here.")
        return latest_fn()
    m = _OFFSET_TOKEN_RE.match(token)
    if m:
        base_token, amount, unit = m.groups()
        days = int(amount) * (7 if unit == "w" else 1)
        return parse_token(base_token, latest_fn) - timedelta(days=days)
    try:
        return date.fromisoformat(token)
    except ValueError:
        raise UsageError(
            f"invalid date token {token!r}; expected YYYY-MM-DD, 'now'/'today', "
            "'latest', or an offset 'now-<int>{d|w}' / 'latest-<int>{d|w}'."
        ) from None

def resolve_window(start_token: str, end_token: str, latest_fn):
    start = parse_token(start_token, latest_fn)
    end = parse_token(end_token, latest_fn)
    if start > end:
        raise UsageError(f"--start {start_token} resolves after --end {end_token}.")
    return start, end

def open_dataset(dataset: str):
    """Validate the dataset id, open it, and detect its shape.

    Lazy, icechunk-backed open: this reads only metadata, so shape detection
    happens before any array bytes are pulled.
    """
    import dynamical_catalog

    catalog = dynamical_catalog.list()
    if dataset not in catalog:
        raise UsageError(
            f"unknown dataset {dataset!r}. Available datasets:\n  " + "\n  ".join(catalog)
        )
    ds = dynamical_catalog.open(dataset)

    # Projected grids (e.g. NOAA HRRR on a Lambert Conformal Conic grid) expose
    # 1-D y/x in meters with 2-D latitude(y,x)/longitude(y,x) and a CRS in
    # spatial_ref, not 1-D latitude/longitude dims. Reprojecting to a regular
    # lat/lon grid is out of scope for this fetcher.
    if "latitude" not in ds.dims or "longitude" not in ds.dims:
        raise UsageError(
            f"{dataset} is on a projected grid (dims {tuple(ds.dims)}); this "
            "fetcher only handles regular 1-D latitude/longitude grids."
        )

    if "ensemble_member" in ds.dims:
        shape = "ensemble"
    elif "lead_time" in ds.dims:
        shape = "forecast"
    elif "time" in ds.dims:
        shape = "analysis"
    else:
        raise UsageError(
            f"{dataset} has an unrecognized shape (dims {tuple(ds.dims)}); "
            "expected a forecast (lead_time) or an analysis (time)."
        )
    return ds, shape

def latest_from_dataset(ds, shape) -> date:
    """Newest available date, read cheaply from the opened dataset's own coords.

    Max init for forecasts, max time for analysis.
    """
    is_forecast = shape in ("ensemble", "forecast")
    coord = "init_time" if is_forecast else "time"
    vals = ds[coord].values
    if is_forecast:
        # --date selects the 00 UTC init, so `latest` must be the newest date
        # that HAS one, in case a later same-day cycle (e.g. 18 UTC) exists
        # without a 00 UTC init yet.
        midnight = vals[vals == vals.astype("datetime64[D]")]
        if midnight.size:
            vals = midnight
    return np_to_date(vals.max())

def bbox_subset(ds, bbox):
    """Subset a regular 1-D lat/lon grid to an N/W/S/E bbox."""
    north, west, south, east = bbox
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    lat_slice = slice(north, south) if lat[0] > lat[-1] else slice(south, north)
    lon_slice = slice(west, east) if lon[0] < lon[-1] else slice(east, west)
    ds = ds.sel(latitude=lat_slice, longitude=lon_slice)
    if ds.sizes.get("latitude", 0) == 0 or ds.sizes.get("longitude", 0) == 0:
        raise DataError(f"--bbox {bbox} selects no grid cells; check the extent and N/W/S/E order.")
    return ds

def fetch(dataset, date_arg, start_arg, end_arg, bbox, variable, output):
    ds, shape = open_dataset(dataset)
    is_forecast = shape in ("ensemble", "forecast")

    def latest_fn():
        return latest_from_dataset(ds, shape)

    # Resolve date tokens now that `latest` can be answered.
    if is_forecast:
        if start_arg or end_arg:
            raise UsageError(f"{dataset} is a forecast dataset; use --date, not --start/--end.")
        if not date_arg:
            raise UsageError(f"{dataset} is a forecast dataset; --date is required.")
        date_iso = parse_token(date_arg, latest_fn).isoformat()
    else:
        if date_arg:
            raise UsageError(f"{dataset} is an analysis dataset; use --start/--end, not --date.")
        if not (start_arg and end_arg):
            raise UsageError(f"{dataset} is an analysis dataset; --start and --end are required.")
        start_date, end_date = resolve_window(start_arg, end_arg, latest_fn)
        start_iso, end_iso = start_date.isoformat(), end_date.isoformat()

    if bbox:
        ds = bbox_subset(ds, bbox)

    if is_forecast:
        inits = ds["init_time"].values
        init_target = np.datetime64(f"{date_iso}T00:00:00").astype(inits.dtype)

        def no_init():
            return DataError(
                f"{dataset} has no {date_iso} 00 UTC init; available init range is "
                f"{np_to_date(inits.min()).isoformat()}..{np_to_date(inits.max()).isoformat()}."
            )

        if init_target not in inits:
            raise no_init()
        try:
            ds = ds.sel(init_time=init_target)
        except KeyError:
            raise no_init() from None
        ds = ds.drop_vars([c for c in _DROP_COORDS if c in ds.coords])
        rename = {"lead_time": "step"}
        if shape == "ensemble":
            rename["ensemble_member"] = "number"
        ds = ds.rename(rename)
        ds = ds.assign_coords(time=ds["init_time"]).drop_vars("init_time")
    else:
        ds = ds.sel(time=slice(np.datetime64(start_iso), np.datetime64(end_iso)))
        if ds.sizes.get("time", 0) == 0:
            raise DataError(f"{dataset} has no data in {start_iso}..{end_iso}.")
        ds = ds.drop_vars([c for c in _DROP_COORDS if c in ds.coords])

    if variable:
        missing = [v for v in variable if v not in ds.data_vars]
        if missing:
            raise UsageError(
                f"variable(s) not in {dataset}: {', '.join(missing)}.\n"
                f"Available: {', '.join(sorted(ds.data_vars))}"
            )
        ds = ds[variable]

    print(f"Fetching dynamical:{dataset} (shape={shape})", file=sys.stderr)

    ds.attrs.update(
        weather_skills_source=f"dynamical:{dataset}",
        Conventions="CF-1.13",
    )

    for var in ds.variables.values():
        for key in ("chunks", "shards", "compressors", "filters", "preferred_chunks"):
            var.encoding.pop(key, None)
    ds = ds.chunk(-1)

    ds.to_zarr(output, mode="w")
    print(f"Wrote {output}", file=sys.stderr)

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch a dynamical.org open-catalog dataset to Zarr.")
    p.add_argument("--dataset", required=True, help="Catalog dataset id.")
    p.add_argument(
        "--date",
        help="Forecast init date (forecast datasets). YYYY-MM-DD, 'now'/'today', 'latest', "
        "or an offset 'now-<int>{d|w}' / 'latest-<int>{d|w}'.",
    )
    p.add_argument("--start", help="Range start, inclusive (analysis datasets). Same grammar as --date.")
    p.add_argument("--end", help="Range end, inclusive (analysis datasets). Same grammar as --date.")
    p.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("NORTH", "WEST", "SOUTH", "EAST"),
        help="Bounding box to subset to.",
    )
    p.add_argument(
        "--variable",
        action="append",
        help="Restrict to this data variable. Repeat once per variable; omit for all.",
    )
    p.add_argument("--output", required=True, help="Output Zarr store path.")
    return p

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fetch(
            dataset=args.dataset,
            date_arg=args.date,
            start_arg=args.start,
            end_arg=args.end,
            bbox=args.bbox,
            variable=args.variable,
            output=args.output,
        )
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except DataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())