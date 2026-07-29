#!/usr/bin/env python
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "cftime",
#   "dask",
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
        --dataset noaa-gefs-forecast-35-day \\
        --date latest \\
        --countries kenya,ghana,senegal \\
        --variable precipitation_surface \\
        --output ./data/2026-07-26/

For each country, subsets to that country's bbox and writes
./data/gefs_<country>.zarr (e.g. ./data/gefs_kenya.zarr).

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
from pathlib import Path

import numpy as np

_DROP_COORDS = (
    "valid_time",
    "expected_forecast_length",
    "ingested_forecast_length",
    "spatial_ref",
)

_OFFSET_TOKEN_RE = re.compile(r"^(now|latest)-(\d+)([dw])$")

# N/W/S/E bboxes, keyed by country/region name as it should appear in the
# output filename (gefs_<key.lower()>.zarr).
BBOXES = {
    "Namibia":    {"lat1": -16.5, "lon1": 11.5, "lat2": -30,   "lon2": 25.5},
    "Botswana":   {"lat1": -17.5, "lon1": 19.5, "lat2": -27,   "lon2": 30},
    "Kenya":      {"lat1": 6,     "lon1": 33,   "lat2": -5,    "lon2": 42},
    "Zambia":     {"lat1": -8,    "lon1": 21,   "lat2": -18.5, "lon2": 34},
    "Madagascar": {"lat1": -10.5, "lon1": 42,   "lat2": -27,   "lon2": 51},
    "Angola":     {"lat1": -5.5,  "lon1": 11.5, "lat2": -18,   "lon2": 24.5},
    "Ghana":      {"lat1": 12,    "lon1": -3.5, "lat2": 4,     "lon2": 1.5},
    "Senegal":    {"lat1": 17,    "lon1": -18,  "lat2": 12,    "lon2": -11.25},
    "Ethiopia":   {"lat1": 16.5,  "lon1": 31.5, "lat2": 1.5,   "lon2": 49.5},
    "Great_Horn": {"lat1": 25.5,  "lon1": 19.5, "lat2": -9,    "lon2": 57},
    "Zimbabwe":   {"lat1": -15,   "lon1": 25,   "lat2": -22.5, "lon2": 33.5},
    "Malawi":     {"lat1": -9,    "lon1": 31.5, "lat2": -18,   "lon2": 37.5},
}

# Case/spacing-insensitive lookup: "kenya", "Kenya", "great horn", "Great_Horn"
# all resolve to the canonical key used in BBOXES / output filenames.
_COUNTRY_LOOKUP = {k.lower().replace("_", " "): k for k in BBOXES}


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


def parse_countries(countries_arg: str) -> list[str]:
    """Parse a comma-separated country string into canonical BBOXES keys.

    Case- and spacing-insensitive ("kenya", "Great Horn", "great_horn" all
    resolve). Fails fast, before any network access, listing valid names.
    """
    raw = [c.strip() for c in countries_arg.split(",") if c.strip()]
    if not raw:
        raise UsageError("--countries must list at least one country.")
    resolved = []
    unknown = []
    for name in raw:
        key = _COUNTRY_LOOKUP.get(name.lower().replace("_", " "))
        if key is None:
            unknown.append(name)
        else:
            resolved.append(key)
    if unknown:
        raise UsageError(
            f"unknown countries: {', '.join(unknown)}.\n"
            f"Available: {', '.join(sorted(BBOXES))}"
        )
    return resolved


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


def _clear_stale_zarr_encoding(ds):
    """Drop chunk/shard/codec encoding inherited from the source store.

    Each variable's encoding carries the source icechunk store's original
    chunk/shard layout (zarr v3 sharding: "chunks" + "shards", plus
    "compressors"/"filters" tied to that layout). After a bbox and/or
    temporal subset, that layout no longer matches the array in hand, so
    writing with it raises either a uniform-chunk-size error or a
    shard-divisibility error. Drop it and write each variable as a single
    chunk, which sidesteps sharding entirely and is fine at subset sizes;
    revisit with explicit chunking if you later fetch full, unsubset grids.
    """
    for var in ds.variables.values():
        for key in ("chunks", "shards", "compressors", "filters", "preferred_chunks"):
            var.encoding.pop(key, None)
    return ds.chunk(-1)


def fetch(dataset, date_arg, start_arg, end_arg, countries, variable, output):
    country_keys = parse_countries(countries)

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

    # Time-based selection and renaming are the same for every country, so
    # they're applied once here; only the bbox subset differs per country.
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

    ds.attrs.update(
        weather_skills_source=f"dynamical:{dataset}",
        Conventions="CF-1.13",
    )

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching dynamical:{dataset} (shape={shape}) for {', '.join(country_keys)}", file=sys.stderr)

    for key in country_keys:
        box = BBOXES[key]
        bbox = (box["lat1"], box["lon1"], box["lat2"], box["lon2"])  # N, W, S, E
        country_ds = bbox_subset(ds, bbox)
        country_ds = _clear_stale_zarr_encoding(country_ds)

        out_path = output_dir / f"gefs_{key.lower()}.zarr"
        print(f"  {key}: writing {out_path}", file=sys.stderr)
        country_ds.to_zarr(out_path, mode="w")

    print("Done.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch a dynamical.org open-catalog dataset to Zarr, per country.")
    p.add_argument("--dataset", required=True, help="Catalog dataset id.")
    p.add_argument(
        "--date",
        help="Forecast init date (forecast datasets). YYYY-MM-DD, 'now'/'today', 'latest', "
        "or an offset 'now-<int>{d|w}' / 'latest-<int>{d|w}'.",
    )
    p.add_argument("--start", help="Range start, inclusive (analysis datasets). Same grammar as --date.")
    p.add_argument("--end", help="Range end, inclusive (analysis datasets). Same grammar as --date.")
    p.add_argument(
        "--countries",
        required=True,
        help=f"Comma-separated country/region names, e.g. 'kenya,ghana,senegal'. "
        f"Available: {', '.join(sorted(BBOXES))}",
    )
    p.add_argument(
        "--variable",
        action="append",
        help="Restrict to this data variable. Repeat once per variable; omit for all.",
    )
    p.add_argument("--output", required=True, help="Output directory; writes gefs_<country>.zarr per country here.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fetch(
            dataset=args.dataset,
            date_arg=args.date,
            start_arg=args.start,
            end_arg=args.end,
            countries=args.countries,
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
