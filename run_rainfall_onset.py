"""
Rainy season onset from observed CHIRPS rainfall, continued with the daily
downscaled forecast (Kenya). Replaces the S2S/GEFS/downscaled forecast-only
onset maps of run_rainfall_onset_legacy.py.

1. Observed: CHIRPS from ONSET_SEARCH_START (default September 1, MM-DD or
   YYYY-MM-DD) up to DATE_STR (or the latest CHIRPS day, if earlier), from
   dynamical.org's public icechunk stores -- the final product, topped up with
   the preliminary product for the most recent ~month the final product
   doesn't cover yet. Each grid cell gets a status:
     - met     : onset fully confirmed within the observed window
     - pending : the triggering rain was observed, but the confirmation window
                 (dry-spell search / second accumulation period) runs past the
                 last observed day and hasn't been violated so far
     - not met : neither
   -> plots/<country>/<date>/monthly/onset_chirps_observed{,_icpac10mm,_accum}.png
      data/<date>/rainfall_onset_{,icpac10mm_,accum_}chirps_<country>.nc

2. Observed + forecast (Kenya only -- the downscaled forecast is Kenya-only):
   the same CHIRPS series continued, per ensemble member, with the daily
   downscaled forecast that starts the day after the last observed day (see
   resolve_forecast), and the onset search rerun over the joined series.
   CHIRPS lags DATE_STR by ~4-9 days, so the days between its last day and
   DATE_STR are first filled with IMERG (Late, topped up with Early; see
   load_imerg_days), after which DATE_STR's forecast follows straight on.
   Where IMERG doesn't reach (or with ONSET_BRIDGE=forecast), an older
   forecast bridges the days up to the newest one (DATE_STR's, see
   resolve_latest_forecast), which takes over from its first day, member by
   member.
   -> plots/<country>/<date>/monthly/onset_downscaled{,_icpac10mm,_accum}.png
      data/<date>/rainfall_onset_{,icpac10mm_,accum_}downscaled_<country>.nc
      (rainfall_onset_downscaled_<country>.nc feeds ai_weather_briefing.py)

Onset definitions (same as the legacy script, from get_ECMWF_functions):
  - standard  : gef.rainfall_onset_date (3-day >20mm, no 7-day dry spell in 21 days)
  - icpac10mm : same, but a 10mm 3-day wet-spell total
  - accum     : gef.rainfall_onset_date_accum (10 days >=20mm, then 20 days >20mm)

Because the search start is fixed, every later run re-scans the same season
from the same day with more observed days appended -- cells only move from
'not met' -> 'pending' -> 'met' (or back from 'pending' to 'not met' if a dry
spell breaks the confirmation window), and an onset date, once met, stays put.

Usage (from the repo root):
    python run_rainfall_onset.py
    COUNTRY=Zambia ONSET_SEARCH_START=10-01 DATE_STR=2026-11-20 python run_rainfall_onset.py
"""
import os
import shutil
from datetime import datetime, timedelta

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import gcsfs
import geopandas as gpd
import icechunk
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm, LinearSegmentedColormap
import matplotlib.lines
import matplotlib.patches
import numpy as np
import pandas as pd
import rioxarray  # noqa: F401  (registers .rio for clipping)
import xarray as xr

import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix = os.environ.get("MAIN_PATH", os.getcwd())
data_path = f'{prefix}/data/{date_str}'

country = os.environ.get("COUNTRY", "Kenya")
plot_dir = f'plots/{country}/{date_str}/monthly'
os.makedirs(plot_dir, exist_ok=True)
os.makedirs(data_path, exist_ok=True)
# fixed start of the onset search window; MM-DD resolves to the most recent
# such day on or before DATE_STR (so a Sep 1 start in March means last September)
search_start = os.environ.get("ONSET_SEARCH_START", "09-01")
if len(search_start) == 5:
    start_this_year = pd.Timestamp(f"{pd.Timestamp(date_str).year}-{search_start}")
    search_start = (start_this_year if start_this_year <= pd.Timestamp(date_str)
                    else start_this_year - pd.DateOffset(years=1))
search_start = pd.Timestamp(search_start)
# 'combined' (final + preliminary top-up), 'final' or 'preliminary'
chirps_source = os.environ.get("CHIRPS_SOURCE", "combined")

# ---- combined observed + forecast (Kenya only: the downscaled forecast is Kenya-only)
# By default the forecast is picked to start the day right after the last
# observed day (see resolve_forecast): CHIRPS lags ~4-9 days, the forecast ~1-2,
# so IMERG fills the days in between first (ONSET_BRIDGE, load_imerg_days);
# without them the newest forecast would leave a hole after CHIRPS. The
# matching (older) forecast is taken from data/<date>/ if it's there locally,
# otherwise downloaded (anonymously -- the bucket is public) from the Kenya
# bucket into FORECAST_CACHE. The cache deliberately lives outside data/: the
# daily workflow uploads every .zarr under data/ to the main bucket, which would
# overwrite that date's full precip zarr with the Kenya-cropped copy.
# FORECAST_DATE / FORECAST_DATA_PATH pin a forecast by hand instead (the
# folder must hold data_weekly_Kenya_downscaled.nc + ECMWF_s2s_precip_<date>.zarr,
# e.g. a test/<date>/ fixture folder).
forecast_date_override = os.environ.get("FORECAST_DATE")
forecast_path_override = os.environ.get("FORECAST_DATA_PATH")
FORECAST_BUCKET = "kenya-forecasting-data"
forecast_cache = os.environ.get("FORECAST_CACHE", f"{prefix}/onset_forecast_cache")
# how many days further back than the ideal init to look if it's missing
FORECAST_MAX_LOOKBACK = int(os.environ.get("FORECAST_MAX_LOOKBACK", 7))
# what fills the days between the last CHIRPS day and DATE_STR's forecast:
# 'imerg' (observed IMERG, an older forecast only where IMERG doesn't reach)
# or 'forecast' (an older forecast, as before)
onset_bridge = os.environ.get("ONSET_BRIDGE", "imerg")
# dynamical.org's half-hourly IMERG, in the order they are tried: Late (~12 h
# behind) first, Early (~1 h behind) for the days Late doesn't fully cover yet
IMERG_DATASETS = ("nasa-imerg-analysis-late", "nasa-imerg-analysis-early")
# same knobs (and defaults) as run_rainfall_onset_legacy.py
ONSET_AGREEMENT_THRESH = float(os.environ.get("ONSET_AGREEMENT_THRESH", 66))
ONSET_PROFILE_SIGMA = float(os.environ.get("ONSET_PROFILE_SIGMA", 10))

bboxes = {
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
    "Somalia":    {"lat1": 13.5,  "lon1": 40.5, "lat2": -3,    "lon2": 52.5},
}
gef.lat1 = bboxes[country]['lat1']
gef.lat2 = bboxes[country]['lat2']
gef.lon1 = bboxes[country]['lon1']
gef.lon2 = bboxes[country]['lon2']

kenya_shapefile = "downscale_data/Kenya_Counties_KNSDI.shp"


def open_chirps(product):
    storage = icechunk.s3_storage(
        bucket="dynamical-ucsb-chc-chirps",
        prefix=f"ucsb-chc-chirps-analysis-{product}/v0.1.0.icechunk",
        region="us-west-2",
        anonymous=True,
    )
    session = icechunk.Repository.open(storage).readonly_session("main")
    return xr.open_zarr(session.store, chunks=None).precipitation_surface


def load_chirps_window(start, end_date):
    """Daily CHIRPS (mm/day) from start up to and including end_date (or the
    latest available day, if that's earlier), cut to the country bbox.
    CHIRPS latitude is stored north->south, so slice(lat1, lat2) with lat1 > lat2
    is the right order."""
    latest = open_chirps("final" if chirps_source == "final" else "preliminary").time.values[-1]
    end = min(pd.Timestamp(end_date), pd.Timestamp(latest))

    def cut(da):
        return da.sel(time=slice(start, end),
                      longitude=slice(gef.lon1, gef.lon2), latitude=slice(gef.lat1, gef.lat2))

    if chirps_source in ("final", "preliminary"):
        da = cut(open_chirps(chirps_source)).compute()
    else:
        final = cut(open_chirps("final")).compute()
        prelim = cut(open_chirps("preliminary"))
        if final.sizes['time']:
            prelim = prelim.sel(time=slice(final.time.values[-1] + np.timedelta64(1, 'D'), None))
        da = xr.concat([final, prelim.compute()], dim='time') if prelim.sizes['time'] else final
        print(f"CHIRPS: {final.sizes['time']} days final + {prelim.sizes['time']} days preliminary")

    # dynamical.org stores CHIRPS as a rate in kg m-2 s-1 (== mm/s) -- onset
    # thresholds are in mm/day
    da = da * 86400
    da.attrs['units'] = 'mm day-1'
    # rebuild coords without their dynamical.org attrs (dict-valued
    # 'statistics_approximate' can't be written to netCDF)
    return da.drop_vars('spatial_ref', errors='ignore').assign_coords(
        {d: da[d].values for d in ('time', 'latitude', 'longitude')})


def load_imerg_days(first_day, last_day, like):
    """
    Daily IMERG (mm/day) on like's (CHIRPS) grid and land cells, for the
    complete UTC days from first_day on, up to last_day, without gaps: the
    days IMERG_DATASETS[0] has all 48 half-hours of, then the next dataset's
    from where the previous one stops. Returns (DataArray or None, description
    of the days per dataset). IMERG's 0.1 degree grid is linearly interpolated
    onto CHIRPS's 0.05 degree one.
    """
    import dynamical_catalog
    margin = 0.2  # keeps the interpolation onto the grid's edge cells inside IMERG's cut-out
    land = like.notnull().any('time')
    day, days, used = pd.Timestamp(first_day), [], []
    for name in IMERG_DATASETS:
        if day > last_day:
            break
        try:
            pr = dynamical_catalog.open(name, chunks=None).precipitation_surface
            pr = pr.sel(time=slice(day, last_day + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)),
                        latitude=slice(gef.lat1 + margin, gef.lat2 - margin),
                        longitude=slice(gef.lon1 - margin, gef.lon2 + margin)).compute()
        except Exception as e:
            print(f"IMERG: {name} not read ({type(e).__name__}: {e})")
            continue
        if not pr.sizes['time']:
            continue
        # a rate in kg m-2 s-1 (== mm/s), like CHIRPS: the day's mean rate * 86400 is its mm/day
        counts = pr.time.resample(time='1D').count()
        daily = pr.resample(time='1D').mean() * 86400
        n_before = len(days)
        for t, n in zip(pd.to_datetime(daily.time.values), counts.values):
            if t != day or n < 48:
                break
            days.append(daily.sel(time=t))
            day += pd.Timedelta(days=1)
        if len(days) > n_before:
            used.append(f"{name.rsplit('-', 1)[1]} {days[n_before].time.dt.strftime('%m-%d').item()}"
                        f"-{days[-1].time.dt.strftime('%m-%d').item()}")
    if not days:
        return None, ""
    da = xr.concat(days, dim='time').drop_vars('spatial_ref', errors='ignore')
    da = da.interp(latitude=like.latitude, longitude=like.longitude).where(land)
    da.attrs = {'units': 'mm day-1'}
    da = da.assign_coords({d: da[d].values for d in ('time', 'latitude', 'longitude')})
    return da.transpose('time', 'latitude', 'longitude'), f"IMERG {', '.join(used)}"


def onset_status(da, onset_fn, fully_observed_days, pad_days, **kwargs):
    """
    Returns (onset_date, status) where status is 2 = met, 1 = pending, 0 = not met,
    NaN over ocean/no-data.

    'pending' is found by appending pad_days of very wet synthetic days after
    the last observation and re-running the onset search: a cell whose onset
    only appears once the confirmation window is allowed to extend into the
    (unknown) future hasn't failed yet. Onsets whose *first* part (the wet
    spell / first accumulation period, fully_observed_days long) would itself
    rely on padded days are discarded, so pending always means the triggering
    rain was actually observed.
    """
    n_obs = da.sizes['time']
    onset = onset_fn(da, time_dim='time', **kwargs)

    pad_time = pd.date_range(pd.Timestamp(da.time.values[-1]) + pd.Timedelta(days=1), periods=pad_days)
    last = da.isel(time=-1, drop=True)
    pad = xr.full_like(last, 1000.0).where(last.notnull())  # keep ocean NaN
    padded = xr.concat([da, pad.expand_dims(time=pad_time)], dim='time')
    onset_padded = onset_fn(padded, time_dim='time', **kwargs)

    last_trigger_day = pd.Timestamp(da.time.values[n_obs - fully_observed_days])
    pending = onset.isnull() & onset_padded.notnull() & (onset_padded <= np.datetime64(last_trigger_day))

    has_data = da.notnull().any('time')
    status = xr.where(onset.notnull(), 2, xr.where(pending, 1, 0)).where(has_data)
    return onset, status


def clip(da):
    if country != 'Kenya':
        return da
    return gef.clip_to_shapefile(da.rio.write_crs("EPSG:4326"), kenya_shapefile)


def add_outline(ax):
    if country == 'Kenya':
        outline = gpd.read_file(kenya_shapefile).set_crs("EPSG:4326", allow_override=True).dissolve()
        ax.add_geometries(outline.geometry, crs=ccrs.PlateCarree(),
                          facecolor='none', edgecolor='black', linewidth=1.0, zorder=3)
    else:
        ax.coastlines(resolution='10m', linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.6)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color='gray', alpha=0.5, linestyle='--')
    gl.top_labels = False
    gl.right_labels = False


def build_discrete_cmap(vmin, vmax, n_shades=4):
    """Same colorbar as run_rainfall_onset_legacy.py's onset maps:
    5 main color bands (sand, green, cyan, pink-purple, gray), each split into
    n_shades discrete light->dark steps. Returns (cmap, norm, boundaries, segment_edges)."""
    segments = [
        ("#EFDFC0", "#8B5A2B"),  # sand
        ("#B9E3A8", "#1B5E20"),  # green
        ("#A9F0EC", "#00838F"),  # cyan
        ("#F3BEDE", "#7B2D8E"),  # pink-purple
        ("#E3E3E3", "#4D4D4D"),  # gray
    ]
    segment_edges = np.linspace(vmin, vmax, len(segments) + 1)

    colors = []
    boundaries = [segment_edges[0]]
    for i, (c_light, c_dark) in enumerate(segments):
        seg_cmap = LinearSegmentedColormap.from_list("", [c_light, c_dark])
        shade_positions = (np.arange(n_shades) + 0.5) / n_shades
        colors.extend(seg_cmap(shade_positions))
        sub_edges = np.linspace(segment_edges[i], segment_edges[i + 1], n_shades + 1)[1:]
        boundaries.extend(sub_edges)

    cmap = ListedColormap(colors, name="onset_bands_discrete")
    norm = BoundaryNorm(boundaries, ncolors=cmap.N)
    return cmap, norm, np.array(boundaries), segment_edges


def draw_onset_dates(fig, ax, doy, first_day, last_day, low_agreement=None):
    """Onset day-of-year map with the legacy script's discrete colorbar,
    scaled from first_day to last_day. low_agreement cells are hatched instead
    of colored (same styling as plot_onset_map)."""
    vmin, vmax = float(first_day.dayofyear), float(last_day.dayofyear)
    if vmin == vmax:
        vmax = vmin + 1
    cmap, norm, boundaries, segment_edges = build_discrete_cmap(vmin, vmax, n_shades=4)

    colored = doy.where(~low_agreement) if low_agreement is not None else doy
    mesh = colored.plot.pcolormesh(x='longitude', y='latitude', ax=ax, cmap=cmap, norm=norm,
                                   transform=ccrs.PlateCarree(), add_colorbar=False)

    if low_agreement is not None and bool(low_agreement.any()):
        hatch_field = low_agreement.where(low_agreement).transpose('latitude', 'longitude')
        hatch = ax.pcolor(
            hatch_field.longitude.values, hatch_field.latitude.values,
            np.ma.masked_invalid(hatch_field.values.astype(float)),
            cmap=ListedColormap(['white']), transform=ccrs.PlateCarree(),
            shading='nearest', edgecolor='#555555', linewidth=0, hatch='////',
        )
        hatch.set_zorder(mesh.get_zorder() + 0.1)

    cbar = fig.colorbar(mesh, ax=ax, orientation='vertical', pad=0.03, shrink=0.85, aspect=25,
                        boundaries=boundaries, ticks=segment_edges)
    cbar.set_ticklabels([(pd.Timestamp(year=first_day.year, month=1, day=1)
                          + pd.Timedelta(days=t - 1)).strftime('%b %d') for t in segment_edges])
    cbar.set_label('Onset date', fontsize=12)
    return mesh


def plot_status(onset, status, window_start, window_end, label, save_path):
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), subplot_kw={'projection': ccrs.PlateCarree()})

    # left: met / pending / not met
    ax = axes[0]
    status_cmap = ListedColormap(['#E3E3E3', '#F2C14E', '#1B7F3B'])
    status.plot.pcolormesh(x='longitude', y='latitude', ax=ax, cmap=status_cmap,
                           norm=BoundaryNorm([-0.5, 0.5, 1.5, 2.5], 3),
                           transform=ccrs.PlateCarree(), add_colorbar=False)
    counts = {k: int((status == v).sum()) for k, v in [('not met', 0), ('pending', 1), ('met', 2)]}
    total = max(sum(counts.values()), 1)
    ax.legend(handles=[
        matplotlib.patches.Patch(color='#1B7F3B', label=f"met ({100 * counts['met'] / total:.0f}%)"),
        matplotlib.patches.Patch(color='#F2C14E', label=f"pending ({100 * counts['pending'] / total:.0f}%)"),
        matplotlib.patches.Patch(color='#E3E3E3', label=f"not met ({100 * counts['not met'] / total:.0f}%)"),
    ], loc='lower left', fontsize=10, framealpha=0.9)
    add_outline(ax)
    ax.set_title('Onset criterion status', fontsize=13, fontweight='bold')

    # right: observed onset date where met
    ax = axes[1]
    draw_onset_dates(fig, ax, onset.dt.dayofyear, window_start, window_end)
    add_outline(ax)
    ax.set_title('Observed onset date', fontsize=13, fontweight='bold')

    fig.suptitle(f'CHIRPS observed rainy season onset ({label}) — {country}\n'
                 f'{window_start:%Y-%m-%d} to {window_end:%Y-%m-%d}', fontsize=15, fontweight='bold')
    fig.tight_layout()
    plt.savefig(save_path, bbox_inches='tight', dpi=110)
    plt.close(fig)
    print(f"{label}: met {counts['met']}, pending {counts['pending']}, not met {counts['not met']} cells "
          f"-> {save_path}")


def forecast_files(init):
    return ['data_weekly_Kenya_downscaled.nc', f'ECMWF_s2s_precip_{init}.zarr']


# When the weekly downscaled forecast is the 4-week 0.4 degree one, downscale_04deg_.py
# also writes this file: the same weeks followed by the 1.5 degree weeks 5-6, so the
# onset definitions keep their 6-week look-ahead. Used instead when it is there.
ONSET_WEEKLY_FILE = 'data_weekly_Kenya_downscaled_onset.nc'


# What downscale_04deg_.py writes now instead: the per-member daily 0.4 degree forecast
# followed by the 1.5 degree run's days, already split into days. Read as it is when it's
# there -- the 0.4 degree forecast is a day newer than the 1.5 degree one, so its folder
# has no ECMWF_s2s_precip_<init>.zarr to split weeks with until the next day's run.
ONSET_MEMBERS_STORE = 'daily_downscaled_kenya_members_onset.zarr'


def downscaled_weekly_path(path):
    onset_path = f'{path}/{ONSET_WEEKLY_FILE}'
    return onset_path if os.path.exists(onset_path) else f'{path}/data_weekly_Kenya_downscaled.nc'


def forecast_members(path):
    """The ensemble member numbers of the downscaled forecast in path."""
    store = f'{path}/{ONSET_MEMBERS_STORE}'
    if os.path.isdir(store):
        return xr.open_zarr(store, consolidated=True).number.values
    with xr.open_dataset(downscaled_weekly_path(path)) as ds:
        return ds.number.values


def has_forecast(path, init):
    return (os.path.isdir(f'{path}/{ONSET_MEMBERS_STORE}')
            or all(os.path.exists(f'{path}/{f}') for f in forecast_files(init)))


def fetch_forecast(init):
    """Download one forecast's files from gs://kenya-forecasting-data/<init>/data/
    into FORECAST_CACHE/<init>/ (reused on later runs). The bucket is public, so
    this reads anonymously -- no gcloud auth needed (the daily workflow runs
    this step before it authenticates). Downloads into a temp folder that's
    only renamed into place once every file arrived, so a failed or interrupted
    download never looks like a complete forecast. Returns the folder, or None
    if the forecast isn't in the bucket."""
    dest = f'{forecast_cache}/{init}'
    if has_forecast(dest, init):
        return dest

    fs = gcsfs.GCSFileSystem(token='anon')
    remote = f'{FORECAST_BUCKET}/{init}/data'
    tmp = f'{dest}.partial'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        weekly = forecast_files(init)
        have_weekly = all(fs.exists(f'{remote}/{f}') for f in weekly)
        # the 6-week forms first, the smaller one before the bigger (members) one
        if have_weekly and fs.exists(f'{remote}/{ONSET_WEEKLY_FILE}'):
            wanted = weekly + [ONSET_WEEKLY_FILE]
        elif fs.exists(f'{remote}/{ONSET_MEMBERS_STORE}'):
            wanted = [ONSET_MEMBERS_STORE]
        elif have_weekly:
            wanted = weekly
        else:
            raise FileNotFoundError(f'gs://{remote}/{weekly[0]} + {weekly[1]} or {ONSET_MEMBERS_STORE}')
        for f in wanted:
            fs.get(f'{remote}/{f}', f'{tmp}/{f}', recursive=True)
    except Exception as e:
        print(f"forecast: {init} not available from the bucket ({e})")
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    return dest


def resolve_forecast(obs_end):
    """
    (init date string, folder) of the forecast to continue CHIRPS with.

    The ideal forecast is initialized the day after the last CHIRPS day, so its
    first forecast day (init + 1 step -> rain on the init date itself) follows
    straight on from the observations. If that one isn't available locally or
    in the bucket (e.g. a failed daily run), step back one day at a time: an
    older forecast just overlaps CHIRPS for a few days (CHIRPS wins there, see
    combine_obs_forecast) rather than leaving a gap. A forecast *newer* than the
    ideal one is never used, as that always leaves missing days.
    """
    if forecast_date_override or forecast_path_override:
        init = forecast_date_override or date_str
        return init, forecast_path_override or f'{prefix}/data/{init}'

    ideal = obs_end + pd.Timedelta(days=1)
    for back in range(FORECAST_MAX_LOOKBACK + 1):
        init = (ideal - pd.Timedelta(days=back)).strftime('%Y-%m-%d')
        local = f'{prefix}/data/{init}'
        path = local if has_forecast(local, init) else fetch_forecast(init)
        if path:
            note = "" if back == 0 else f" (ideal init {ideal:%Y-%m-%d} not available, {back} day(s) older)"
            print(f"forecast: using init {init} from {path}{note}")
            return init, path
    print(f"forecast: no forecast found between {ideal - pd.Timedelta(days=FORECAST_MAX_LOOKBACK):%Y-%m-%d} "
          f"and {ideal:%Y-%m-%d}")
    return None, None


def resolve_latest_forecast(bridge_init):
    """
    (init date string, folder) of the newest forecast, initialized after
    bridge_init: DATE_STR's own, else up to FORECAST_MAX_LOOKBACK days older.
    CHIRPS can lag DATE_STR by up to ~9 days (the preliminary product arrives
    per pentad), so the forecast that follows straight on from CHIRPS (the
    bridge, see resolve_forecast) can be a week older than the newest one. The
    bridge then only fills the days between the last CHIRPS day and this
    forecast's first day, and this one takes over from there (see
    combine_obs_forecast). (None, None) when there's nothing newer than the
    bridge, or when a forecast is pinned with FORECAST_DATE/FORECAST_DATA_PATH.
    """
    if forecast_date_override or forecast_path_override or bridge_init is None:
        return None, None
    for back in range(FORECAST_MAX_LOOKBACK + 1):
        init = (pd.Timestamp(date_str) - pd.Timedelta(days=back)).strftime('%Y-%m-%d')
        if init <= bridge_init:
            break
        local = f'{prefix}/data/{init}'
        path = local if has_forecast(local, init) else fetch_forecast(init)
        if path:
            print(f"forecast: latest init {init} from {path} takes over from its first day")
            return init, path
    return None, None


def load_daily_downscaled_forecast(chirps, forecast_date, forecast_path):
    """
    Generator over ensemble members of the daily downscaled forecast, on the
    CHIRPS grid, with a real `time` dim (the date each day's rain falls on).

    Same recipe as run_rainfall_onset_legacy.py's downscaled branch: the weekly
    downscaled forecast is split into days with each member's own raw S2S
    daily profile (gef.disaggregate_weekly_to_daily), one member at a time to
    keep memory down. The downscaled grid is offset half a cell (0.025deg) in
    longitude from CHIRPS, so it's linearly interpolated onto the CHIRPS grid
    first.

    Forecast step s covers the 24h ending at init + s, i.e. the rain that falls
    on date init + s - 1 day -- matching CHIRPS's "24h starting at time" label.

    When the folder holds ONSET_MEMBERS_STORE, its members are already split into
    days (by downscale_04deg_.py, with the 0.4 and 1.5 degree runs' own profiles),
    and are only interpolated onto the CHIRPS grid.
    """
    land = chirps.notnull().any('time')
    store = f'{forecast_path}/{ONSET_MEMBERS_STORE}'
    if os.path.isdir(store):
        members = xr.open_zarr(store, consolidated=True)
        print(f"forecast: {members.sizes['step']} days from {ONSET_MEMBERS_STORE}"
              + (f" ({members.attrs['forecast_resolution']})" if 'forecast_resolution' in members.attrs else ""))
        init = pd.Timestamp(members.time.values)
        for n in members.number.values:
            daily = members.tp.sel(number=n).load().interp(latitude=chirps.latitude, longitude=chirps.longitude)
            dates = init + pd.to_timedelta(daily.step.values) - pd.Timedelta(days=1)
            daily = daily.assign_coords(time=('step', dates)).swap_dims(step='time')
            daily = daily.drop_vars(['step', 'valid_time', 'number', 'surface', 'year', 'init', 'spatial_ref'], errors='ignore')
            yield n, daily.transpose('time', 'latitude', 'longitude').where(land)
        return

    weekly_path = downscaled_weekly_path(forecast_path)
    rescaled = xr.open_dataset(weekly_path).load()
    print(f"forecast: {rescaled.sizes['step']} weeks from {os.path.basename(weekly_path)}"
          + (f" ({rescaled.attrs['forecast_resolution']})" if 'forecast_resolution' in rescaled.attrs else ""))
    rescaled = rescaled.interp(latitude=chirps.latitude, longitude=chirps.longitude)
    s2s = xr.open_zarr(f'{forecast_path}/ECMWF_s2s_precip_{forecast_date}.zarr', consolidated=True).compute()
    init = pd.Timestamp(s2s.time.values)

    for n in rescaled.number.values:
        daily = gef.disaggregate_weekly_to_daily(rescaled.tp.sel(number=n), s2s.tp.sel(number=n),
                                                 profile_sigma=ONSET_PROFILE_SIGMA or None).tp
        dates = init + pd.to_timedelta(daily.step.values) - pd.Timedelta(days=1)
        daily = daily.assign_coords(time=('step', dates)).swap_dims(step='time')
        daily = daily.drop_vars(['step', 'valid_time', 'number', 'surface', 'year', 'init', 'spatial_ref'], errors='ignore')
        yield n, daily.transpose('time', 'latitude', 'longitude').where(land)


def combine_obs_forecast(chirps, forecast_daily, latest_daily=None):
    """CHIRPS up to its last day, then forecast days after it. Observations win
    where both exist; a gap between the two (forecast initialized after the
    day following the last CHIRPS day) is left NaN, which blocks any onset whose
    windows touch the gap. With latest_daily (a newer forecast, see
    resolve_latest_forecast), forecast_daily only bridges the days up to
    latest_daily's first day, and latest_daily takes over from there."""
    one_day = np.timedelta64(1, 'D')
    obs_end = chirps.time.values[-1]
    pieces = [chirps.drop_vars('spatial_ref', errors='ignore')]
    if latest_daily is None:
        pieces.append(forecast_daily.sel(time=slice(obs_end + one_day, None)))
    else:
        latest_start = max(latest_daily.time.values[0], obs_end + one_day)
        pieces.append(forecast_daily.sel(time=slice(obs_end + one_day, latest_start - one_day)))
        pieces.append(latest_daily.sel(time=slice(latest_start, None)))
    series = xr.concat(pieces, dim='time')
    return series.reindex(time=pd.date_range(series.time.values[0], series.time.values[-1]))


def plot_combined(onset, obs_met, first_day, last_day, obs_label, forecast_label, label, save_path):
    """Left: ensemble-mean onset date (obs + forecast), hatched where fewer than
    ONSET_AGREEMENT_THRESH % of members find an onset, with cells already met in
    CHIRPS outlined in black. Right: % of members with an onset by the end of
    the searchable window (100% wherever CHIRPS already met it)."""
    doy = onset.dt.dayofyear
    mean_doy = doy.mean('number', skipna=True)
    pct = (doy.notnull().mean('number') * 100).where(obs_met.notnull())
    low_agreement = (pct < ONSET_AGREEMENT_THRESH) & mean_doy.notnull()

    fig, axes = plt.subplots(1, 2, figsize=(16, 7), subplot_kw={'projection': ccrs.PlateCarree()})

    ax = axes[0]
    draw_onset_dates(fig, ax, mean_doy, first_day, last_day, low_agreement=low_agreement)
    met = obs_met.fillna(0).transpose('latitude', 'longitude')
    ax.contour(met.longitude, met.latitude, met, levels=[0.5],
               colors='black', linewidths=1.2, transform=ccrs.PlateCarree(), zorder=4)
    ax.legend(handles=[
        matplotlib.patches.Patch(facecolor='white', edgecolor='#555555', hatch='////',
                                 label=f'< {ONSET_AGREEMENT_THRESH:g}% of members find an onset'),
        matplotlib.lines.Line2D([], [], color='black', linewidth=1.2, label='already met in CHIRPS'),
    ], loc='lower left', fontsize=10, framealpha=0.9)
    add_outline(ax)
    ax.set_title('Onset date (ensemble mean)', fontsize=13, fontweight='bold')

    ax = axes[1]
    # same bins/colours as the pipeline's exceedance-chance maps
    pct_cmap, pct_norm = gef.discrete_cmap(gef.EXCEEDANCE_COLORS, gef.PROB_BOUNDS)
    mesh = pct.plot.pcolormesh(x='longitude', y='latitude', ax=ax, cmap=pct_cmap, norm=pct_norm,
                               transform=ccrs.PlateCarree(), add_colorbar=False)
    cbar = fig.colorbar(mesh, ax=ax, pad=0.03, shrink=0.85, aspect=25, ticks=gef.PROB_BOUNDS)
    cbar.set_label('% of members with onset', fontsize=12)
    add_outline(ax)
    ax.set_title(f'Chance onset has happened by {last_day:%b %d}', fontsize=13, fontweight='bold')

    fig.suptitle(f'Rainy season onset, CHIRPS + downscaled forecast ({label}) — {country}\n'
                 f'observed {first_day:%Y-%m-%d} to {obs_label}, {forecast_label}',
                 fontsize=15, fontweight='bold')
    fig.tight_layout()
    plt.savefig(save_path, bbox_inches='tight', dpi=110)
    plt.close(fig)
    print(f"{label} (combined): {float(pct.mean()):.0f}% of members with onset on average -> {save_path}")


chirps = load_chirps_window(search_start, date_str)
window_start = pd.Timestamp(chirps.time.values[0])
window_end = pd.Timestamp(chirps.time.values[-1])
print(f"CHIRPS window: {window_start:%Y-%m-%d} to {window_end:%Y-%m-%d} "
      f"({chirps.sizes['time']} days, {chirps.sizes['latitude']}x{chirps.sizes['longitude']} grid)")
if window_end < pd.Timestamp(date_str):
    print(f"note: CHIRPS only available up to {window_end:%Y-%m-%d}, not {date_str}")

chirps = clip(chirps)

# label: (onset function, its kwargs, days in the triggering part, confirmation window days)
definitions = {
    'standard': (gef.rainfall_onset_date, {}, 3, 21),
    'icpac10mm': (gef.rainfall_onset_date, {'wet_spell_thresh': 10.0}, 3, 21),
    'accum': (gef.rainfall_onset_date_accum, {}, 10, 30),
}

# output file name pieces per definition, matching the legacy script's
# naming (standard has no suffix): onset_downscaled{png_suffix}.png and
# rainfall_onset_{nc_prefix}downscaled_<country>.nc
png_suffix = {'standard': '', 'icpac10mm': '_icpac10mm', 'accum': '_accum'}
nc_prefix = {'standard': '', 'icpac10mm': 'icpac10mm_', 'accum': 'accum_'}

obs_status = {}
for label, (onset_fn, kwargs, fully_observed_days, pad_days) in definitions.items():
    onset, status = onset_status(chirps, onset_fn, fully_observed_days, pad_days, **kwargs)
    obs_status[label] = status
    xr.Dataset({'onset_date': onset, 'status': status}).drop_vars('spatial_ref', errors='ignore') \
        .to_netcdf(f'{data_path}/rainfall_onset_{nc_prefix[label]}chirps_{country}.nc')
    plot_status(onset, status, window_start, window_end, label,
                f'{plot_dir}/onset_chirps_observed{png_suffix[label]}.png')

# ---- CHIRPS continued with the daily downscaled forecast --------------------
# The days from the last CHIRPS day up to DATE_STR (the newest forecast's first
# rain day) come from IMERG where it has them, so the forecast that follows
# straight on is DATE_STR's own rather than an older one bridging the gap.
obs, obs_end = chirps.drop_vars('spatial_ref', errors='ignore'), window_end
obs_label = f'{window_end:%Y-%m-%d}'
if country == 'Kenya' and onset_bridge == 'imerg':
    last_bridge_day = pd.Timestamp(date_str) - pd.Timedelta(days=1)
    if last_bridge_day > window_end:
        imerg, imerg_label = load_imerg_days(window_end + pd.Timedelta(days=1), last_bridge_day, chirps)
        if imerg is None:
            print(f"IMERG: no complete day after {window_end:%Y-%m-%d}, an older forecast bridges the gap")
        else:
            obs = xr.concat([obs, imerg], dim='time')
            obs_end = pd.Timestamp(imerg.time.values[-1])
            obs_label = f'{window_end:%Y-%m-%d} + {imerg_label}'
            print(f"IMERG: {imerg.sizes['time']} days after CHIRPS ({imerg_label})"
                  + ("" if obs_end == last_bridge_day else
                     f", {(last_bridge_day - obs_end).days} day(s) short of {last_bridge_day:%Y-%m-%d}"))

forecast_date, forecast_path = None, None
if country != 'Kenya':
    print("combined: downscaled forecast only available for Kenya, skipping")
else:
    forecast_date, forecast_path = resolve_forecast(obs_end)
    if forecast_path and not has_forecast(forecast_path, forecast_date):
        print(f"combined: no downscaled forecast for {forecast_date} in {forecast_path}, skipping")
        forecast_path = None

# a newer forecast than the bridge one takes over from its first day; members
# are paired by number, so both need the same ensemble
latest_date, latest_path = resolve_latest_forecast(forecast_date if forecast_path else None)
if latest_path:
    members = [forecast_members(p) for p in (forecast_path, latest_path)]
    if not np.array_equal(*members):
        print(f"forecast: {forecast_date} and {latest_date} have different members, "
              f"using {forecast_date} alone")
        latest_date, latest_path = None, None

if forecast_path:
    if latest_path:
        forecast_label = (f'forecast init {latest_date} '
                          f'(gap to it bridged by init {forecast_date})')
    else:
        forecast_label = f'forecast init {forecast_date}'
    onsets = {label: [] for label in definitions}
    series = None
    members = load_daily_downscaled_forecast(obs, forecast_date, forecast_path)
    latest_members = (load_daily_downscaled_forecast(obs, latest_date, latest_path)
                      if latest_path else None)
    for n, forecast_daily in members:
        latest_daily = next(latest_members)[1] if latest_members else None
        series = combine_obs_forecast(obs, forecast_daily, latest_daily)
        for label, (onset_fn, kwargs, _, _) in definitions.items():
            onsets[label].append(onset_fn(series, time_dim='time', **kwargs).expand_dims(number=[n]))

    series_start, series_end = pd.Timestamp(series.time.values[0]), pd.Timestamp(series.time.values[-1])
    n_chirps, n_obs = chirps.sizes['time'], obs.sizes['time']
    n_gap = int(series.isel(time=slice(n_obs, None)).isnull().all(['latitude', 'longitude']).sum())
    print(f"combined series: {series_start:%Y-%m-%d} to {series_end:%Y-%m-%d} "
          f"({n_chirps} days CHIRPS + "
          + (f"{n_obs - n_chirps} days IMERG + " if n_obs > n_chirps else "")
          + f"{series.sizes['time'] - n_obs} days forecast"
          + (f", {n_gap} of them missing: gap between observations and forecast init" if n_gap else "")
          + (f", the first {(pd.Timestamp(latest_date) - obs_end).days - 1} from init {forecast_date}"
             if latest_path else "") + ")")

    for label, (_, _, _, pad_days) in definitions.items():
        onset = xr.concat(onsets[label], dim='number')
        onset.to_dataset(name='onset_date').to_netcdf(
            f'{data_path}/rainfall_onset_{nc_prefix[label]}downscaled_{country}.nc')
        # colorbar runs to the last day that still leaves a full confirmation
        # window, like the legacy script's forecast plots
        last_day = series_end - pd.Timedelta(days=pad_days - 1)
        obs_met = (obs_status[label] == 2).astype(float).where(obs_status[label].notnull())
        plot_combined(onset, obs_met, series_start, last_day, obs_label, forecast_label, label,
                      f'{plot_dir}/onset_downscaled{png_suffix[label]}.png')
