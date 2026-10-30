"""
CHIRPS weekly rainfall climatology files for the weekly downscaling
(dowscale_dekade.py's fclim_chirps list):

  downscale_data/chirpsv3_weeks/chirpsv3_weeks_<first year>_<last year>_sorted_<MM>-<DD>_<country>.nc

One file per start date (days 1, 3, 5, ... of every month, the dates
dowscale_dekade.py matches each forecast week to): the 7-day rainfall total
(mm) from MM-DD to MM-DD+6 in every year, sorted per grid cell, as
tp(latitude, longitude, rank) with rank 0 the driest year.

CHIRPS comes from the same public dynamical.org icechunk store
build_chirps_dekad_climatology.py reads (CHIRPS v3 final, 0.05deg daily), so
no credentials are needed. The original files came from another CHIRPS v3
build: rebuilding 10-15 and 11-29 this way gives the same grid and land mask,
a correlation of 0.98-0.99 and a mean bias of +0.2 mm. Run by hand, not in CI:

    python build_chirps_week_climatology.py --month 12
    python build_chirps_week_climatology.py --date 12-01 12-03 --country Ghana --force
"""
import argparse
import calendar
import glob
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import xarray as xr

from build_chirps_dekad_climatology import RES, open_chirps

# the countries' weekly_bboxes[country + '_plus'] in dowscale_dekade.py
week_bboxes = {
    "Kenya": {"lat1": 7.5, "lon1": 27, "lat2": -7.5, "lon2": 43},
    "Ghana": {"lat1": 12, "lon1": -4.5, "lat2": 4, "lon2": 3},
}


def month_dates(month):
    """MM-DD of days 1, 3, 5, ... of the month, as dowscale_dekade.py's all_dates."""
    ndays = calendar.monthrange(2025, month)[1]  # a non-leap year, like the existing files
    return [f'{month:02d}-{day:02d}' for day in range(1, ndays + 1, 2)]


def find_template(output_dir, country, skip):
    """An existing weekly file of this country to take the grid from."""
    for path in sorted(glob.glob(f'{output_dir}/chirpsv3_weeks_*_sorted_*_{country}.nc')):
        if os.path.abspath(path) in skip:
            continue
        try:
            with xr.open_dataset(path) as ds:
                return path, ds.latitude.values, ds.longitude.values
        except Exception as e:
            print(f"  cannot read {path} as a grid template ({e}), trying the next one")
    return None, None, None


def load_years(da, dates, years, n_workers):
    """Each year's daily CHIRPS from the first start date to 6 days after the last.

    The store is chunked a year at a time, so reading every date's 7 days
    separately downloads the same chunks once per date; this reads them once.
    """
    record_start, record_end = pd.Timestamp(da.time.values[0]), pd.Timestamp(da.time.values[-1])
    starts = sorted((int(md[:2]), int(md[3:])) for md in dates)

    def load(year):
        start = pd.Timestamp(year, *starts[0])
        end = pd.Timestamp(year, *starts[-1]) + pd.Timedelta(days=6)
        if start < record_start or end > record_end:
            raise ValueError(f"CHIRPS ({record_start:%Y-%m-%d} to {record_end:%Y-%m-%d}) "
                             f"does not cover {start:%Y-%m-%d} to {end:%Y-%m-%d}")
        return da.sel(time=slice(start, end)).load()

    with ThreadPoolExecutor(n_workers) as pool:
        return dict(zip(years, pool.map(load, years)))


def build_week(blocks, month, day):
    """tp(latitude, longitude, rank): the week's rainfall total in each year, sorted."""
    totals = []
    for year, block in blocks.items():
        start = pd.Timestamp(year, month, day)
        end = start + pd.Timedelta(days=6)
        sub = block.sel(time=slice(start, end))
        if sub.sizes['time'] != 7:
            raise ValueError(f"CHIRPS {start:%Y-%m-%d} to {end:%Y-%m-%d} has {sub.sizes['time']} days, expected 7")
        # kg m-2 s-1 (= mm/s, averaged over the day) -> mm over the week;
        # skipna=False keeps the ocean NaN instead of turning it into 0
        totals.append((sub * 86400).sum('time', skipna=False))

    tp = xr.concat(totals, dim='rank').transpose('latitude', 'longitude', 'rank')
    tp = tp.copy(data=np.sort(tp.values, axis=-1).astype('float32'))
    return tp.assign_coords(rank=np.arange(len(blocks))).drop_vars('spatial_ref', errors='ignore')


def main():
    parser = argparse.ArgumentParser(description="Build CHIRPS weekly climatology files for dowscale_dekade.py")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--month', type=int, nargs='+', help="build every start date of these month(s), 1-12")
    which.add_argument('--date', nargs='+', help="start date(s) as MM-DD")
    parser.add_argument('--country', default='Kenya', choices=list(week_bboxes))
    parser.add_argument('--start-year', type=int, default=2005)
    parser.add_argument('--end-year', type=int, default=2025)
    parser.add_argument('--output-dir', default='downscale_data/chirpsv3_weeks')
    parser.add_argument('--workers', type=int, default=8, help="parallel per-year reads from the icechunk store")
    parser.add_argument('--force', action='store_true', help="overwrite files that already exist")
    args = parser.parse_args()

    if args.month:
        for month in args.month:
            if not 1 <= month <= 12:
                parser.error(f"--month {month}: must be between 1 and 12")
        dates = [d for month in args.month for d in month_dates(month)]
    else:
        dates = args.date
        for d in dates:
            try:
                pd.Timestamp(f'2025-{d}')
            except ValueError:
                parser.error(f"--date {d}: expected MM-DD")
    years = list(range(args.start_year, args.end_year + 1))
    bbox = week_bboxes[args.country]

    def out_path(md):
        return (f'{args.output_dir}/chirpsv3_weeks_{args.start_year}_{args.end_year}'
                f'_sorted_{md}_{args.country}.nc')

    # As in the dekad files, cells are labelled by their western edge in
    # longitude and by their centre in latitude.
    da = open_chirps().precipitation_surface.sel(
        latitude=slice(bbox['lat1'], bbox['lat2']),
        longitude=slice(bbox['lon1'], bbox['lon2'] + RES))
    latitude = da.latitude.values
    longitude = np.round(da.longitude.values - RES / 2, 3)

    # dowscale_dekade.py concatenates these files along 'step', which only
    # lines up if their coordinates are identical -- so reuse the coordinates
    # of a file that is already there.
    template, t_lat, t_lon = find_template(args.output_dir, args.country,
                                           skip={os.path.abspath(out_path(d)) for d in dates})
    if template is not None:
        if (t_lat.shape != latitude.shape or t_lon.shape != longitude.shape
                or not np.allclose(t_lat, latitude, atol=RES / 10)
                or not np.allclose(t_lon, longitude, atol=RES / 10)):
            raise ValueError(f"The grid of {template} does not match the {args.country} bbox cut-out of CHIRPS")
        latitude, longitude = t_lat, t_lon
        print(f"grid taken from {template}")
    else:
        print(f"no existing {args.country} weekly file in {args.output_dir}, using the CHIRPS grid")

    os.makedirs(args.output_dir, exist_ok=True)
    todo = []
    for md in dates:
        if os.path.exists(out_path(md)) and not args.force:
            print(f"{out_path(md)} exists, skipping (use --force to overwrite)")
        else:
            todo.append(md)
    if not todo:
        return
    print(f"{args.country}, {years[0]}-{years[-1]}: reading CHIRPS for the weeks from {todo[0]} to {todo[-1]}")
    blocks = load_years(da, todo, years, args.workers)
    for md in todo:
        path = out_path(md)
        month, day = map(int, md.split('-'))
        tp = build_week(blocks, month, day)
        tp = tp.assign_coords(latitude=latitude, longitude=longitude)
        # dynamical.org coord attrs include dict-valued 'statistics_approximate',
        # which netCDF can't serialize
        for c in tp.coords:
            tp.coords[c].attrs = {}
        tp.attrs = {}
        tp.to_dataset(name='tp').to_netcdf(path, encoding={'tp': {'zlib': True, 'complevel': 4}})
        print(f"  -> {path}")


if __name__ == "__main__":
    main()
