"""
Which date the 0.4 degree products of a run are for, and keeping them from being
overwritten by the next run's 1.5 degree ones.

CHC posts the 0.4 degree forecast initialized on day X at about 21:05 UTC on day X,
so a run at 02:33 UTC already has the init of the day before. The 1.5 degree ECMWF
forecast the rest of the pipeline uses lags two days. So a run has two forecast
dates: the 1.5 degree one (ECMWF date, run - 2) and the 0.4 degree one (normally
run - 1). The 0.4 degree products -- plot_s2s_04deg_.py's plots and
downscale_04deg_.py's Kenya weekly downscaling, with everything that reads it --
go under their own date's folders (plots/<country>/<date>/, data/<date>/).

The next day's run has that date as its ECMWF date, and dowscale_dekade.py then
writes the 1.5 degree Kenya weekly downscaled outputs under the same names into
the same folders. keep-published stops those from replacing the published 0.4
degree ones in the buckets.

    python forecast_04deg_dates.py resolve --run-date 2026-10-15 --ecmwf-date 2026-10-13
        The newest 0.4 degree init on CHC's server from the ECMWF date up to the day
        before the run date; the ECMWF date when there is none, FORECAST_04DEG_URL is
        not set or the server can't be reached. Printed (alone on stdout), and written
        as date=... to $GITHUB_OUTPUT when that is set.

    python forecast_04deg_dates.py keep-published --date 2026-10-13 --date-04deg 2026-10-14
        If gs://kenya-forecasting-data/<date>/ already holds the 0.4 degree Kenya
        weekly product, removes the local 1.5 degree outputs of the same names for
        <date> so the uploads leave the published ones alone; when there is no local
        0.4 degree product for --date-04deg (its downscaling failed), downloads the
        published ones for <date> instead, so the run's downscaled products are still
        the 0.4 degree ones. Run from the repo root (MAIN_PATH). Nothing to do when the
        two dates are the same.

    python forecast_04deg_dates.py archive [--date 2026-10-14 ...] [--budget-minutes 90]
        Keeps the global CHC files (pr_IFS-SUBS-0.4_<YYYYMMDD>.daily.nc, ~2.2 GB each)
        in FORECAST_04DEG_ARCHIVE/<YYYY>/ as zarr stores,
        pr_IFS-SUBS-0.4_<YYYYMMDD>.zarr: every one on CHC's server (this month's and
        last month's folders) that isn't archived yet, or only the given inits. CHC
        keeps a file for only a couple of weeks, so the daily workflow runs this to
        keep them. Archived .daily.nc files from before the switch to zarr are
        converted too (the .nc stays; --delete-nc removes it once its zarr is
        verified). No new conversion starts after --budget-minutes. The archive is
        the private Sheerwater data lake: the forecast is licensed ECMWF data, never
        copy it anywhere public. Needs Google credentials that can write there.

The zarr store is the CHC file unchanged (same `pr` variable, M/L/Y/X dims, coords
and attrs) but chunked over all members and lead days in ZARR_CHUNK x ZARR_CHUNK
cells, so a regional read of the whole forecast is a few concurrent requests instead
of the netCDF file's one chunk per lead day, read one after another.

resolve and plot_s2s_04deg_.py look in the archive too (the zarr store, else a
not yet converted .nc), so a date CHC no longer has can still be (re)run.

Env vars: FORECAST_04DEG_URL (resolve, archive; not public, never printed), MAIN_PATH
(keep-published), FORECAST_04DEG_ARCHIVE (default gs://sheerwater-datalake/ecmwf-0_4/forecast)
"""
import argparse
import glob
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

KENYA_BUCKET = "kenya-forecasting-data"
ARCHIVE = os.environ.get("FORECAST_04DEG_ARCHIVE", "gs://sheerwater-datalake/ecmwf-0_4/forecast").rstrip('/')
# lat/lon cells per zarr chunk; each chunk holds every member and lead day
ZARR_CHUNK = 25
# rows of the CHC file converted at a time (a multiple of ZARR_CHUNK and of the
# file's own 50-row chunks); ~0.8 GB in memory
CONVERT_ROWS = 50

# what downscale_04deg_.py writes, relative to plots/Kenya/<date>/ and data/<date>/.
# The Kenya bucket holds the plots under <date>/ and the data under <date>/data/.
KENYA_WEEKLY_04DEG_PLOTS = [
    'weekly/weekly_precip_downscaled.png',
    'weekly/weekly_precip_downscaled_clipped.png',
    'weekly/weekly_precip_downscaled_anomaly.png',
    'weekly/weekly_precip_downscaled_anomaly_clipped.png',
    'weekly/prob_*spell_*days_downscaled.png',
    'weekly/median_*spell_length_downscaled.png',
    'weekly/weekly_chance_higherthan_*mm_downscaled.png',
    'weekly/counties',
    'monthly/monthly_precip_downscaled.png',
    'monthly/monthly_precip_downscaled_anomaly.png',
]
KENYA_WEEKLY_04DEG_DATA = [
    'data_weekly_Kenya_downscaled.nc',
    'data_weekly_Kenya_downscaled.zarr',
    'data_weekly_Kenya_downscaled_onset.nc',
    'data_weekly_Kenya_downscaled_onset.zarr',
    'daily_downscaled_kenya.tif',
    'daily_downscaled_kenya.zarr',
    'daily_downscaled_kenya_members.zarr',
    'daily_downscaled_kenya_members_onset.zarr',
    'CE_Kenya_dwnscaled_timeseries_daily.zarr',
]


def list_chc_inits(url, months):
    """
    {init datetime: file url} of the 0.4 degree forecasts on CHC's server in the
    given month folders ('01'..'12'; the server keeps one folder per month).
    A month that can't be listed is left out.
    """
    import requests
    available = {}
    for month in sorted(set(months)):
        link = f'{url}/{month}/'
        try:
            html = requests.get(link, timeout=30).text
        except requests.RequestException as e:
            print(f"Could not list month {month} on CHC's server ({type(e).__name__})", file=sys.stderr)
            continue
        for f in re.findall(r'href="(pr_IFS-SUBS-0\.4_(\d{8})\.daily\.nc)"', html):
            available[datetime.strptime(f[1], '%Y%m%d')] = link + f[0]
    return available


def archive_path(init, suffix='zarr'):
    """Where the forecast of init (a datetime) is kept in the archive: the zarr store, or
    with suffix='daily.nc' the CHC file archived before the switch to zarr."""
    return f"{ARCHIVE}/{init:%Y}/pr_IFS-SUBS-0.4_{init:%Y%m%d}.{suffix}"


def zarr_complete(fs, path):
    """True if the archived zarr store at path is complete: its root zarr.json (with the
    consolidated metadata) is uploaded last."""
    return fs.exists(f"{path.removeprefix('gs://')}/zarr.json")


def archive_fs(asynchronous=False):
    """gcsfs with Google credentials (the archive is private): the default ones (CI), else the gcloud CLI's login."""
    import gcsfs
    import google.auth
    try:
        google.auth.default()
        return gcsfs.GCSFileSystem(asynchronous=asynchronous)
    except google.auth.exceptions.DefaultCredentialsError:
        import subprocess
        import google.oauth2.credentials
        token = subprocess.run('gcloud auth print-access-token', shell=True, capture_output=True,
                               text=True, check=True).stdout.strip()
        return gcsfs.GCSFileSystem(token=google.oauth2.credentials.Credentials(token), asynchronous=asynchronous)


def open_archived_zarr(path):
    """
    The archived zarr store at path (gs://...) as a lazy dataset. zarr is handed an
    asynchronous gcsfs itself: from a plain mapper it would rebuild the filesystem from
    JSON, which can't hold the gcloud login's credentials.
    """
    import xarray as xr
    from zarr.storage import FsspecStore
    store = FsspecStore(archive_fs(asynchronous=True), path=path.removeprefix('gs://'), read_only=True)
    return xr.open_zarr(store, consolidated=True, decode_times=False)


def _archive_listing(fs, years):
    """{init datetime: {'zarr' or 'nc': gs:// path}} of what the archive's year folders hold
    (zarr stores not checked for completeness)."""
    found = {}
    for year in sorted(set(years)):
        try:
            entries = fs.ls(f"{ARCHIVE}/{year}".removeprefix('gs://'), detail=False)
        except FileNotFoundError:
            continue
        for path in entries:
            match = re.search(r'pr_IFS-SUBS-0\.4_(\d{8})\.(zarr|daily\.nc)$', path.rstrip('/'))
            if match:
                kind = 'zarr' if match.group(2) == 'zarr' else 'nc'
                init = datetime.strptime(match.group(1), '%Y%m%d')
                found.setdefault(init, {})[kind] = 'gs://' + path.rstrip('/').removeprefix('gs://')
    return found


def list_archived_inits(first, last):
    """
    {init datetime: gs:// path} of the archived 0.4 degree forecasts from first to last
    (datetimes): the complete zarr store, else the .nc archived before the switch to zarr;
    empty when the archive can't be read (no credentials, no access, no network).
    """
    archived = {}
    try:
        fs = archive_fs()
        listing = _archive_listing(fs, range(first.year, last.year + 1))
        for init, kinds in listing.items():
            if not first <= init <= last:
                continue
            if 'zarr' in kinds and zarr_complete(fs, kinds['zarr']):
                archived[init] = kinds['zarr']
            elif 'nc' in kinds:
                archived[init] = kinds['nc']
    except Exception as e:
        print(f"Could not list the 0.4 degree archive {ARCHIVE} ({type(e).__name__})", file=sys.stderr)
    return archived


def available_inits(first, last):
    """
    {init datetime: url or gs:// path} of the 0.4 degree forecasts from first to last
    (datetimes): the archived zarr store (the fastest to read), else CHC's file, else
    an archived .nc.
    """
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    archived = list_archived_inits(first, last)
    found = {}
    url = os.environ.get("FORECAST_04DEG_URL", "").strip().rstrip('/')
    if url:
        found.update(list_chc_inits(url, {d.strftime('%m') for d in days}))
    else:
        print("FORECAST_04DEG_URL is not set: only the archive is looked in", file=sys.stderr)
    for d, where in archived.items():
        if where.endswith('.zarr') or d not in found:
            found[d] = where
    return {d: where for d, where in found.items() if first <= d <= last}


def resolve(run_date, ecmwf_date):
    """The newest 0.4 degree init in [ecmwf_date, run_date - 1 day], else ecmwf_date."""
    first = datetime.strptime(ecmwf_date, '%Y-%m-%d')
    last = datetime.strptime(run_date, '%Y-%m-%d') - timedelta(days=1)
    usable = available_inits(first, last)
    if not usable:
        print(f"No 0.4 degree init from {ecmwf_date} to {last:%Y-%m-%d} on CHC's server or in the "
              f"archive: the 0.4 degree date is the ECMWF date", file=sys.stderr)
        return ecmwf_date
    return max(usable).strftime('%Y-%m-%d')


def nc_to_zarr(nc_path, zarr_path):
    """
    Write the CHC file at nc_path as a local zarr store: the same variables, coords and
    attrs, chunked over all members and lead days in ZARR_CHUNK x ZARR_CHUNK cells and
    Blosc/zstd compressed, with consolidated metadata. Converted CONVERT_ROWS latitude
    rows at a time (appended along Y) so the 5 GB of values never sit in memory at once.
    """
    import xarray as xr
    from zarr.codecs import BloscCodec

    with xr.open_dataset(nc_path, decode_times=False) as src:
        missing = [d for d in ('M', 'L', 'Y', 'X') if d not in src['pr'].dims]
        if missing:
            raise ValueError(f"{nc_path} is not the CHC layout (pr has no {', '.join(missing)})")
        for y0 in range(0, src.sizes['Y'], CONVERT_ROWS):
            slab = src.isel(Y=slice(y0, y0 + CONVERT_ROWS)).load()
            for var in slab.variables.values():
                var.encoding.clear()
            if y0 == 0:
                chunks = tuple(slab.sizes[d] if d in ('M', 'L') else ZARR_CHUNK for d in slab['pr'].dims)
                compressor = BloscCodec(cname='zstd', clevel=3, shuffle='shuffle')
                slab.to_zarr(zarr_path, mode='w', zarr_format=3, consolidated=True,
                             encoding={'pr': {'chunks': chunks, 'compressors': [compressor]}})
            else:
                slab.to_zarr(zarr_path, append_dim='Y', consolidated=True)


def upload_zarr(fs, local, dest):
    """
    Upload the local zarr store to dest (gs:// path), replacing whatever a failed upload
    left there. The root zarr.json goes last, so a store without it is incomplete
    (zarr_complete) and an interrupted upload never looks archived.
    """
    remote = dest.removeprefix('gs://')
    if fs.exists(remote):
        fs.rm(remote, recursive=True)
    lpaths, rpaths = [], []
    for root, _, files in os.walk(local):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, local).replace(os.sep, '/')
            if rel != 'zarr.json':
                lpaths.append(path)
                rpaths.append(f"{remote}/{rel}")
    fs.put(lpaths, rpaths)
    fs.put_file(os.path.join(local, 'zarr.json'), f"{remote}/zarr.json")


def verify_zarr(fs, dest, local, nc_path):
    """
    Raise unless every object of the uploaded store matches the local one's MD5 and the
    store opens with the CHC file's sizes and coords. (Reading values back would fetch
    the whole store: every chunk holds all lead days.)
    """
    import base64
    import hashlib
    import numpy as np
    import xarray as xr

    remote = dest.removeprefix('gs://')
    uploaded = {path[len(remote) + 1:]: info.get('md5Hash') for path, info in fs.find(remote, detail=True).items()}
    for root, _, files in os.walk(local):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, local).replace(os.sep, '/')
            with open(path, 'rb') as f:
                md5 = base64.b64encode(hashlib.md5(f.read()).digest()).decode()
            if uploaded.pop(rel, None) != md5:
                raise IOError(f"archived zarr's {rel} is missing or differs from the converted one")
    if uploaded:
        raise IOError(f"archived zarr has {len(uploaded)} objects the converted one doesn't")
    with xr.open_dataset(nc_path, decode_times=False) as src:
        ds = open_archived_zarr(dest)
        if dict(ds.sizes) != dict(src.sizes):
            raise IOError(f"archived zarr has sizes {dict(ds.sizes)}, the CHC file {dict(src.sizes)}")
        for coord in ('M', 'L', 'Y', 'X'):
            if not np.array_equal(ds[coord].values, src[coord].values):
                raise IOError(f"archived zarr's {coord} differs from the CHC file's")


def _download_chc(src, local):
    """Stream CHC's file at src to local, checking its size against CHC's."""
    import requests
    with requests.get(src, stream=True, timeout=60) as r:
        r.raise_for_status()
        size = int(r.headers['Content-Length'])
        with open(local, 'wb') as f:
            for chunk in r.iter_content(chunk_size=16 * 2**20):
                f.write(chunk)
    if os.path.getsize(local) != size:
        raise IOError(f"downloaded {os.path.getsize(local)} bytes, CHC has {size}")


def archive(dates=None, budget_minutes=None, delete_nc=False):
    """
    Archive CHC files as zarr stores and convert archived .nc files (see the module
    docstring). Each file is downloaded to a temporary folder, converted, uploaded with
    its root zarr.json last and checked against the file. Returns the number of failures.
    """
    url = os.environ.get("FORECAST_04DEG_URL", "").strip().rstrip('/')
    today = datetime.now(timezone.utc)
    months = {today.strftime('%m'), (today - timedelta(days=35)).strftime('%m')}
    wanted = [datetime.strptime(d, '%Y-%m-%d') for d in dates] if dates else None
    if wanted:
        months |= {d.strftime('%m') for d in wanted}
    if url:
        on_chc = list_chc_inits(url, months)
    else:
        print("FORECAST_04DEG_URL is not set: only archived .nc files are converted", file=sys.stderr)
        on_chc = {}

    fs = archive_fs()
    years = {d.year for d in on_chc} | {today.year - 1, today.year} | {d.year for d in (wanted or [])}
    listing = _archive_listing(fs, years)
    sources = {}  # init -> ('chc', url) or ('nc', gs:// path)
    for init in sorted(set(on_chc) | set(listing)):
        if wanted is not None and init not in wanted:
            continue
        kinds = listing.get(init, {})
        if 'zarr' in kinds and zarr_complete(fs, kinds['zarr']):
            if delete_nc and 'nc' in kinds:
                fs.rm(kinds['nc'].removeprefix('gs://'))
                print(f"{init:%Y-%m-%d}: removed {kinds['nc']} (the zarr store is archived)")
            continue
        if 'nc' in kinds:
            sources[init] = ('nc', kinds['nc'])
        elif init in on_chc:
            sources[init] = ('chc', on_chc[init])
        else:
            print(f"{init:%Y-%m-%d}: incomplete zarr store in the archive and no file to redo it from",
                  file=sys.stderr)
    if wanted:
        for d in wanted:
            if d not in sources and d not in listing:
                print(f"{d:%Y-%m-%d}: not on CHC's server (any more) nor archived", file=sys.stderr)
    if not sources:
        print("Everything on CHC's server is archived as zarr")

    started = time.monotonic()
    failures = 0
    for init, (kind, src) in sorted(sources.items(), reverse=True):  # newest first: CHC drops the oldest
        if budget_minutes is not None and time.monotonic() - started > budget_minutes * 60:
            print(f"Time budget of {budget_minutes} min used up: {init:%Y-%m-%d} and older are left for the next run")
            break
        dest = archive_path(init)
        tick = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix='archive04deg_') as tmp:
                nc_path = os.path.join(tmp, f'pr_IFS-SUBS-0.4_{init:%Y%m%d}.daily.nc')
                if kind == 'chc':
                    print(f"{init:%Y-%m-%d}: downloading from CHC")
                    _download_chc(src, nc_path)
                else:
                    print(f"{init:%Y-%m-%d}: downloading {src}")
                    fs.get(src.removeprefix('gs://'), nc_path)
                zarr_path = os.path.join(tmp, 'store.zarr')
                nc_to_zarr(nc_path, zarr_path)
                upload_zarr(fs, zarr_path, dest)
                verify_zarr(fs, dest, zarr_path, nc_path)
            print(f"{init:%Y-%m-%d}: archived as {dest} ({(time.monotonic() - tick) / 60:.1f} min)")
            if delete_nc and kind == 'nc':
                fs.rm(src.removeprefix('gs://'))
                print(f"{init:%Y-%m-%d}: removed {src}")
        except Exception as e:
            failures += 1
            # the exception text could hold CHC's (private) address, so for a CHC file only its type is shown
            detail = f": {e}" if kind == 'nc' else ''
            print(f"{init:%Y-%m-%d}: archiving failed ({type(e).__name__}{detail})", file=sys.stderr)
    return failures


def _matches(root, patterns):
    return [p for pattern in patterns for p in sorted(glob.glob(os.path.join(root, pattern)))]


def _remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def published_is_04deg(fs, date):
    """True if the Kenya bucket's weekly downscaled forecast for date is downscale_04deg_.py's."""
    import xarray as xr
    remote = f'{KENYA_BUCKET}/{date}/data/data_weekly_Kenya_downscaled.nc'
    if not fs.exists(remote):
        return False
    # the bucket is public: "#mode=bytes" has netCDF read just the header over HTTP
    url = f'https://storage.googleapis.com/{remote}#mode=bytes'
    with xr.open_dataset(url, engine='netcdf4') as ds:
        return str(ds.attrs.get('forecast_resolution', '')).startswith('0.4deg')


def keep_published(date, date_04deg, prefix):
    if date == date_04deg:
        print(f"0.4 degree and ECMWF date are both {date}: nothing to keep")
        return
    import gcsfs
    fs = gcsfs.GCSFileSystem(token='anon')
    if not published_is_04deg(fs, date):
        print(f"No published 0.4 degree Kenya weekly product for {date}: the 1.5 degree one stays")
        return

    plots_dir = os.path.join(prefix, 'plots', 'Kenya', date)
    data_dir = os.path.join(prefix, 'data', date)
    for path in _matches(plots_dir, KENYA_WEEKLY_04DEG_PLOTS) + _matches(data_dir, KENYA_WEEKLY_04DEG_DATA):
        _remove(path)
    print(f"Removed the 1.5 degree Kenya weekly downscaled outputs for {date}: "
          f"gs://{KENYA_BUCKET}/{date}/ already holds the 0.4 degree ones")

    if os.path.exists(os.path.join(prefix, 'data', date_04deg, 'data_weekly_Kenya_downscaled.nc')):
        return
    # today's 0.4 degree downscaling didn't run: use the published one for date
    print(f"No 0.4 degree Kenya weekly product for {date_04deg}: downloading the published one for {date}")
    for local_root, remote_root, patterns in ((plots_dir, f'{KENYA_BUCKET}/{date}', KENYA_WEEKLY_04DEG_PLOTS),
                                              (data_dir, f'{KENYA_BUCKET}/{date}/data', KENYA_WEEKLY_04DEG_DATA)):
        for pattern in patterns:
            for remote in fs.glob(f'{remote_root}/{pattern}'):
                local = os.path.join(local_root, remote[len(remote_root) + 1:])
                os.makedirs(os.path.dirname(local), exist_ok=True)
                fs.get(remote, local, recursive=True)
                print(f"  gs://{remote} -> {local}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('resolve')
    p.add_argument('--run-date', required=True)
    p.add_argument('--ecmwf-date', required=True)
    p = sub.add_parser('archive')
    p.add_argument('--date', action='append', help="init YYYY-MM-DD to archive (repeatable); default every one "
                                                   "on CHC's server or archived as .nc that isn't a zarr store yet")
    p.add_argument('--budget-minutes', type=float, help="start no new file after this many minutes")
    p.add_argument('--delete-nc', action='store_true', help="remove archived .nc files once their zarr store is "
                                                             "archived and verified")
    p = sub.add_parser('keep-published')
    p.add_argument('--date', required=True)
    p.add_argument('--date-04deg', required=True)
    args = parser.parse_args()

    if args.command == 'resolve':
        date = resolve(args.run_date, args.ecmwf_date)
        print(f"0.4 degree date {date} (ECMWF date {args.ecmwf_date})", file=sys.stderr)
        print(date)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"date={date}\n")
    elif args.command == 'archive':
        sys.exit(1 if archive(args.date, args.budget_minutes, args.delete_nc) else 0)
    else:
        keep_published(args.date, args.date_04deg, os.environ.get("MAIN_PATH", os.getcwd()))


if __name__ == '__main__':
    main()
