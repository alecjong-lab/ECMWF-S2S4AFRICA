"""
Download the DWD ICON-EPS global ensemble (40 members) from
https://opendata.dwd.de/weather/nwp/icon-eps/grib/<HH>/<var>/ and regrid it to a regular
lat/lon grid over Africa, writing one zarr store per variable to
data/<date>/ICON_eps_<var>_<date>.zarr with the same layout as the ECMWF S2S stores
(number, step, latitude [descending], longitude), so the gef plotting functions work on it.

The native grid is ICON grid 39 (R02B07): 1,310,720 triangular cells, ~20 km mean cell size
(~0.18 deg; DWD quotes ~26 km). The regridding is linear (barycentric) between the cell centres,
onto 0.25 deg by default, the same grid as the IFS-ENS and GEFS 0.25 deg stores. Tested against
conservative remapping (CDO remapcon equivalent): at 0.25 deg area means differ by <0.3% and
peaks are kept slightly better, so linear is good enough here. For box averages on a coarser
grid (e.g. the CHC 0.4 deg one) conservative remapping from the native cells would be better.

DWD keeps only the last ~24 h of runs, so this has to run on the day of the init: there is
no backfill. The date therefore comes from ICON_DATE_STR (not DATE_STR, which is the ECMWF
date, two days old) and defaults to the newest run on the server.

ICON-EPS data are open data under CC BY 4.0 (source: Deutscher Wetterdienst).

Usage:
    python download_icon_eps.py                              # tot_prec, 0-168 h, newest 00 UTC run
    python download_icon_eps.py --variables tot_prec,t_2m --max-lead 168 --run 12
    ICON_DATE_STR=2026-10-21 python download_icon_eps.py
"""
import argparse
import bz2
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import requests
import xarray as xr
from scipy.spatial import Delaunay

BASE_URL = 'https://opendata.dwd.de/weather/nwp/icon-eps/grib'
FILE_TEMPLATE = 'icon-eps_global_icosahedral_{kind}_{init}_{step}{var}.grib2.bz2'

# per-variable settings, keyed by the DWD folder name. 'name' is the variable name in the
# output store (matching the ECMWF stores where there is one), 'interval' the default step
# spacing in hours, 'accumulated' marks fields summed since init (so 24 h steps are enough
# for daily totals). A variable missing here is still downloaded with the defaults below.
VARIABLES = {
    'tot_prec':  {'name': 'tp',   'units': 'mm',    'accumulated': True,  'interval': 24},
    't_2m':      {'name': 't2m',  'units': 'degC',  'accumulated': False, 'interval': 6},
    'td_2m':     {'name': 'd2m',  'units': 'degC',  'accumulated': False, 'interval': 6},
    'relhum_2m': {'name': 'r2',   'units': '%',     'accumulated': False, 'interval': 6},
    'u_10m':     {'name': 'u10',  'units': 'm s**-1', 'accumulated': False, 'interval': 6},
    'v_10m':     {'name': 'v10',  'units': 'm s**-1', 'accumulated': False, 'interval': 6},
    'vmax_10m':  {'name': 'fg10', 'units': 'm s**-1', 'accumulated': False, 'interval': 6},
    'clct':      {'name': 'tcc',  'units': '%',     'accumulated': False, 'interval': 6},
}
DEFAULT_SETTINGS = {'name': None, 'units': None, 'accumulated': False, 'interval': 6}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--date', default=os.environ.get('ICON_DATE_STR'),
                   help='init date YYYY-MM-DD (default: ICON_DATE_STR, else the newest run on the server)')
    p.add_argument('--run', default=os.environ.get('ICON_RUN', '00'), choices=['00', '06', '12', '18'],
                   help='init hour; 00 and 12 run to +180 h, 06 and 18 only to +120 h')
    p.add_argument('--variables', default=os.environ.get('ICON_VARIABLES', 'tot_prec'),
                   help='comma-separated DWD variable names (folder names on the server)')
    p.add_argument('--max-lead', type=int, default=int(os.environ.get('ICON_MAX_LEAD', 168)),
                   help='last lead time in hours (default 168 = one week)')
    p.add_argument('--interval', type=int, default=None,
                   help='step spacing in hours; overrides the per-variable default')
    p.add_argument('--bbox', default=os.environ.get('BOUNDING_BOX', '22.5,-21,-34.5,55.5'),
                   help='N,W,S,E as in download_s2s.py')
    p.add_argument('--resolution', type=float, default=float(os.environ.get('ICON_RESOLUTION', 0.25)),
                   help='target grid spacing in degrees (native ICON-EPS cells are ~0.18)')
    p.add_argument('--out-dir', default=None, help='default: <MAIN_PATH or cwd>/data/<date>')
    p.add_argument('--workers', type=int, default=4, help='parallel downloads')
    return p.parse_args()


def get(url, retries=4, stream=False):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=120, stream=stream)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            print(f'  retrying {url} ({e})')
            time.sleep(5 * (attempt + 1))


def available_steps(run, var, init):
    """Lead times (hours) of `var` for `init` (YYYYMMDDHH) listed on the server."""
    r = get(f'{BASE_URL}/{run}/{var}/')
    if r is None:
        raise RuntimeError(f'variable folder {var} not found on the server')
    return sorted({int(s) for s in re.findall(rf'_{init}_(\d{{3}})_{var}\.grib2\.bz2', r.text)})


def newest_init(run, var):
    r = get(f'{BASE_URL}/{run}/{var}/')
    inits = re.findall(r'single-level_(\d{10})_\d{3}_', r.text) if r is not None else []
    if not inits:
        raise RuntimeError(f'no {run} UTC run of {var} on the server')
    return max(inits)


def choose_steps(available, max_lead, interval):
    """Steps that are multiples of `interval` up to `max_lead`. ICON-EPS output gets
    sparser with lead time (1 h to 48 h, 3 h to 72 h, 6 h to 120 h, then 12 h), so a step
    the server doesn't have is skipped rather than failing."""
    wanted = [s for s in range(0, max_lead + 1, interval)]
    steps = [s for s in wanted if s in available]
    missing = sorted(set(wanted) - set(steps))
    if missing:
        print(f'  not on the server, skipped: {missing} h')
    if not steps or steps[-1] != max_lead:
        print(f'  warning: last step is {steps[-1] if steps else None} h, not {max_lead} h')
    return steps


def download_grib(url, dest):
    """Download a .bz2 grib and decompress it to `dest` while streaming."""
    r = get(url, stream=True)
    if r is None:
        raise FileNotFoundError(url)
    decomp = bz2.BZ2Decompressor()
    with open(dest, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(decomp.decompress(chunk))
    return dest


def open_grib(path):
    ds = xr.open_dataset(path, engine='cfgrib', backend_kwargs={'indexpath': ''})
    return ds[list(ds.data_vars)[0]]


class IcosahedralRegridder:
    """Linear (barycentric) interpolation from the ICON cell centres inside the bbox to a
    regular grid. The triangulation and weights are computed once and reused for every
    file and member."""

    def __init__(self, clat, clon, bbox, resolution, margin=1.0):
        north, west, south, east = bbox
        if np.nanmax(np.abs(clat)) <= np.pi / 2 + 1e-6:  # some ICON grid files are in radians
            clat, clon = np.rad2deg(clat), np.rad2deg(clon)
        clon = ((clon + 180) % 360) - 180
        self.index = np.where((clat >= south - margin) & (clat <= north + margin) &
                              (clon >= west - margin) & (clon <= east + margin))[0]
        points = np.column_stack([clon[self.index], clat[self.index]])

        self.lat = np.round(np.arange(north, south - resolution / 2, -resolution), 4)
        self.lon = np.round(np.arange(west, east + resolution / 2, resolution), 4)
        lon2d, lat2d = np.meshgrid(self.lon, self.lat)
        targets = np.column_stack([lon2d.ravel(), lat2d.ravel()])

        tri = Delaunay(points)
        simplex = tri.find_simplex(targets)
        if (simplex < 0).any():
            raise ValueError('target grid extends outside the ICON points; increase margin')
        transform = tri.transform[simplex]
        bary = np.einsum('nij,nj->ni', transform[:, :2], targets - transform[:, 2])
        self.weights = np.column_stack([bary, 1 - bary.sum(axis=1)]).astype('float32')
        self.vertices = tri.simplices[simplex]

    def __call__(self, values):
        """values: (..., n_cells) on the full icosahedral grid -> (..., nlat, nlon)."""
        sub = values[..., self.index]
        out = (sub[..., self.vertices] * self.weights).sum(axis=-1)
        return out.reshape(values.shape[:-1] + (len(self.lat), len(self.lon)))


def load_regridder(run, init, bbox, resolution, tmp):
    coords = {}
    for name in ('clat', 'clon'):
        url = f'{BASE_URL}/{run}/{name}/' + FILE_TEMPLATE.format(kind='time-invariant', init=init, step='', var=name)
        coords[name] = open_grib(download_grib(url, os.path.join(tmp, f'{name}.grib2'))).values
    return IcosahedralRegridder(coords['clat'], coords['clon'], bbox, resolution)


def convert_units(da, units):
    if units == 'degC' and da.attrs.get('units') == 'K':
        da = da - 273.15
    elif units == 'mm' and da.attrs.get('units') in ('kg m**-2', 'kg m-2'):
        pass  # 1 kg m-2 of water is 1 mm
    elif units is None:
        return da
    return da.assign_attrs(units=units)


def process_variable(var, run, init, args, regridder, tmp, out_dir, date_str):
    settings = {**DEFAULT_SETTINGS, **VARIABLES.get(var, {})}
    interval = args.interval or settings['interval']
    steps = choose_steps(available_steps(run, var, init), args.max_lead, interval)
    print(f'{var}: {len(steps)} steps ({steps[0]}-{steps[-1]} h), init {init}')

    def fetch_and_regrid(step):
        url = f'{BASE_URL}/{run}/{var}/' + FILE_TEMPLATE.format(
            kind='single-level', init=init, step=f'{step:03d}_', var=var)
        path = download_grib(url, os.path.join(tmp, f'{var}_{step:03d}.grib2'))
        da = open_grib(path)
        if 'number' not in da.dims:  # a single-member file
            da = da.expand_dims('number')
        out = xr.DataArray(
            regridder(da.values.astype('float32')),
            dims=('number', 'latitude', 'longitude'),
            coords={'number': da.number.values, 'latitude': regridder.lat, 'longitude': regridder.lon},
            attrs={k: v for k, v in da.attrs.items() if not k.startswith('GRIB_') or k in ('GRIB_name', 'GRIB_units')},
            name=settings['name'] or da.name,
        ).expand_dims(step=[np.timedelta64(step, 'h').astype('timedelta64[ns]')])
        out = out.assign_coords(time=da.time.values)
        da.close()
        os.remove(path)
        print(f'  {var} +{step:03d} h done')
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        fields = list(pool.map(fetch_and_regrid, steps))

    da = xr.concat(fields, dim='step').transpose('number', 'step', 'latitude', 'longitude')
    if settings['accumulated']:
        # the step-0 file is empty for accumulated fields on some runs; the accumulation is
        # zero there by definition
        da = da.where(da.step != np.timedelta64(0, 'ns'), 0.0)
    da = convert_units(da, settings['units'])
    da = da.assign_coords(valid_time=('step', (da.time.values + da.step.values)))
    ds = da.to_dataset()
    ds.attrs.update({
        'source': f'DWD ICON-EPS global, {BASE_URL}/{run}/{var}/',
        'license': 'CC BY 4.0, Deutscher Wetterdienst',
        'regridding': f'linear (barycentric) from the icosahedral grid to {args.resolution} deg',
        'accumulated_since_init': int(settings['accumulated']),
    })

    zarr_path = os.path.join(out_dir, f'ICON_eps_{var}_{date_str}.zarr')
    if os.path.exists(zarr_path):
        shutil.rmtree(zarr_path)
    ds.chunk({'number': -1, 'step': 1}).to_zarr(zarr_path, consolidated=True)
    print(f'wrote {zarr_path}')


def main():
    args = parse_args()
    variables = [v.strip() for v in args.variables.split(',') if v.strip()]
    bbox = [float(x) for x in args.bbox.split(',')]
    run = args.run

    if args.date:
        init = datetime.strptime(args.date, '%Y-%m-%d').strftime('%Y%m%d') + run
    else:
        init = newest_init(run, variables[0])
    date_str = datetime.strptime(init[:8], '%Y%m%d').strftime('%Y-%m-%d')

    prefix = os.environ.get('MAIN_PATH', os.getcwd())
    out_dir = args.out_dir or os.path.join(prefix, 'data', date_str)
    os.makedirs(out_dir, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix='icon_eps_')
    try:
        regridder = load_regridder(run, init, bbox, args.resolution, tmp)
        failed = []
        for var in variables:
            try:
                process_variable(var, run, init, args, regridder, tmp, out_dir, date_str)
            except Exception as e:  # one variable failing shouldn't stop the others
                print(f'{var} failed: {e}')
                failed.append(var)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # last line is the init date, for a workflow to pick up as ICON_DATE_STR
    print(date_str)
    if failed:
        raise SystemExit(f'failed variables: {failed}')


if __name__ == '__main__':
    main()
