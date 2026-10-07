"""
CHIRPS climatology of the agro indicators, the reference the downscaled
forecast's calendar-week spell maps (gef.plot_downscaled_spell_maps, from
dowscale_dekade.py) and CHIRPS + forecast onset maps (run_rainfall_onset.py)
are set against in the briefing.

Replaces the ECMWF-reforecast climatologies (run_rainfall_onset_legacy.py's
"S2S reforecast climatology", plot_s2s.py's "climatological wet spell
probability"). Instead of reducing over ensemble members, every indicator is
reduced over the years of CHIRPS, with the same gef onset/spell functions the
forecast maps use and 'year' in place of 'number':

- Dry/wet spells, per calendar week: the same calendar windows
  (gef.calendar_windows: weeks starting on the 1st/8th/15th/22nd) the
  downscaled spell maps use. Per year, the longest dry/wet spell within each
  week -> % of years with a spell of at least 5/7 days, and the median spell
  length (longest length at least half the years reach).
  -> plots/<country>/<date>/weekly/{prob_{dry,wet}spell_{5,7}days,median_{dry,wet}spell_length}_chirps_clim.png

- Onset: searched from ONSET_SEARCH_START (default Sep 1, same as
  run_rainfall_onset.py) over SEASON_DAYS days, one onset date per year.
  Mapped as the % of years with an onset before DATE_STR, and by the same
  last_day run_rainfall_onset.py's "Chance onset has happened by ..." panel
  uses (end of the downscaled forecast minus the definition's confirmation
  window). Whether an onset before last_day is found doesn't depend on how
  far past last_day the series runs, so one long season window serves every
  run of the season.
  -> plots/<country>/<date>/monthly/onset_chirps_climatology{,_icpac10mm,_accum}.png
  Plus the onset date map of each ANALOG_YEARS year:
  -> plots/<country>/<date>/monthly/onset_chirps_analog_years{,_icpac10mm,_accum}.png

CHIRPS (final product, 0.05deg -- the grid the downscaled forecast is
interpolated onto) is read from dynamical.org's public icechunk store. Pulling
~45 years of a country bbox takes a few minutes, so only the small derived
results are cached under chirps_clim_cache/ (gitignored; the workflows keep
it in the GitHub Actions cache, since they can't commit to main): the spell
statistics per calendar week (reused by every run in that week, and the same
week next year) and the per-year onset days per season.

Usage (from the repo root; run after dowscale_dekade.py, whose
data/<date>/data_weekly_Kenya_downscaled.nc sets the forecast horizon):
    python chirps_agro_climatology.py
    COUNTRY=Kenya DATE_STR=2026-09-26 python chirps_agro_climatology.py
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import geopandas as gpd
import icechunk
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches
from matplotlib.colors import ListedColormap, BoundaryNorm, LinearSegmentedColormap
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

# same bbox convention (and duplication) as plot_s2s.py / plot_gefs.py / fetch_dynamical.py
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
}
bbox = bboxes[country]

# same Kenya county shapefile dowscale_dekade.py clips the downscaled forecast to
kenya_shapefile = "downscale_data/Kenya_Counties_KNSDI.shp"

weekly_dir = f'plots/{country}/{date_str}/weekly'
monthly_dir = f'plots/{country}/{date_str}/monthly'
# derived-climatology cache (see the module docstring); gitignored, kept
# between CI runs in the GitHub Actions cache
cache_dir = os.environ.get("CHIRPS_CLIM_CACHE", f'{prefix}/chirps_clim_cache')
spell_cache_dir = f'{cache_dir}/calweek_spells'
onset_cache_dir = f'{cache_dir}/onset'

# same 1mm/day dry/wet day threshold as the forecast spell maps
SPELL_THRESH = 1.0
# same fixed onset search start as run_rainfall_onset.py (MM-DD, most recent
# such day on or before DATE_STR); SEASON_DAYS has to cover the forecast's
# last day, which is checked below
search_start = os.environ.get("ONSET_SEARCH_START", "09-01")
SEASON_DAYS = int(os.environ.get("SEASON_DAYS", 150))
# analog years to show individual onset date maps for
ANALOG_YEARS = [int(y) for y in os.environ.get("ANALOG_YEARS", "2006,2015,2019,2023").split(",")]
# parallel per-year reads from the icechunk store
N_READ_WORKERS = int(os.environ.get("N_READ_WORKERS", 8))

for d in (weekly_dir, monthly_dir, spell_cache_dir, onset_cache_dir):
    os.makedirs(d, exist_ok=True)

init = pd.Timestamp(date_str)
season_start = pd.Timestamp(f"{init.year}-{search_start}")
if season_start > init:
    season_start -= pd.DateOffset(years=1)


def forecast_horizon_days(for_onset=False):
    """Lead days the downscaled forecast covers (42 for the weekly downscaled
    product), read from this date's file when it's there -- the same number
    dowscale_dekade.py passes calendar_windows as max_lead_days. for_onset:
    the horizon run_rainfall_onset.py works with, which is longer when the
    weekly product is the 4-week 0.4 degree one (downscale_04deg_.py then keeps
    the 1.5 degree weeks 5-6 in data_weekly_Kenya_downscaled_onset.nc)."""
    path = f'{data_path}/data_weekly_Kenya_downscaled.nc'
    onset_path = f'{data_path}/data_weekly_Kenya_downscaled_onset.nc'
    if for_onset and os.path.exists(onset_path):
        path = onset_path
    if os.path.exists(path):
        with xr.open_dataset(path) as ds:
            return int(ds.step.values[-1] / np.timedelta64(1, 'D'))
    return int(os.environ.get("FORECAST_HORIZON_DAYS", 42))


def open_chirps():
    storage = icechunk.s3_storage(
        bucket="dynamical-ucsb-chc-chirps",
        prefix="ucsb-chc-chirps-analysis-final/v0.1.0.icechunk",
        region="us-west-2",
        anonymous=True,
    )
    session = icechunk.Repository.open(storage).readonly_session("main")
    return xr.open_zarr(session.store, chunks=None)


def chirps_window_stack(start, ndays):
    """
    The same calendar window (start .. start + ndays - 1) cut out of every
    year CHIRPS fully covers, stacked as (year, step, latitude, longitude)
    daily rainfall in mm/day and clipped to the country. 'step' is the day
    within the window (0 days, 1 day, ...) so the years line up regardless of
    their calendar dates.

    Only years whose whole window is inside the record are used -- a window
    running past Dec 31 still counts for a year as long as the next January
    is available, and the in-progress year is dropped automatically.
    """
    print(f"CHIRPS {start:%b %d} + {ndays}d: reading every year from the icechunk store ({country})")
    da = open_chirps().precipitation_surface.sel(
        latitude=slice(bbox['lat1'], bbox['lat2']),
        longitude=slice(bbox['lon1'], bbox['lon2']))  # subset space before loading
    first, last = pd.Timestamp(da.time.values[0]), pd.Timestamp(da.time.values[-1])

    windows = {}
    for y in range(first.year, last.year + 1):
        s = start + pd.DateOffset(years=y - start.year)  # handles year shift and Feb 29
        if s >= first and s + pd.Timedelta(days=ndays - 1) <= last:
            windows[y] = s

    def load(y):
        s = windows[y]
        sub = da.sel(time=slice(s, s + pd.Timedelta(days=ndays - 1))).load()
        if sub.sizes['time'] != ndays:
            raise ValueError(f"CHIRPS window {s:%Y-%m-%d} has {sub.sizes['time']} days, expected {ndays}")
        return sub.rename(time='step').assign_coords(step=np.arange(ndays))

    with ThreadPoolExecutor(N_READ_WORKERS) as pool:
        slices = list(pool.map(load, windows))

    stacked = xr.concat(slices, dim=pd.Index(list(windows), name='year'))
    # kg m-2 s-1 (= mm/s, averaged over the day) -> mm/day
    stacked = (stacked.drop_vars('spatial_ref', errors='ignore') * 86400).astype('float32')
    stacked = stacked.assign_coords(step=pd.to_timedelta(np.arange(ndays), unit='D'))
    # dynamical.org coord attrs include dict-valued 'statistics_approximate',
    # which netCDF can't serialize
    for c in stacked.coords:
        stacked.coords[c].attrs = {}
    stacked.attrs = {'units': 'mm day-1'}
    if country == 'Kenya':
        stacked = gef.clip_to_shapefile(stacked.rio.write_crs("EPSG:4326"), kenya_shapefile)
        stacked = stacked.drop_vars('spatial_ref', errors='ignore')
    return stacked


def year_range(years):
    return f'{int(np.min(years))}-{int(np.max(years))}'


def spell_stats(week):
    """% of years with a dry/wet spell of at least 5/7 days within the week,
    and the median spell length (same definition as plot_s2s.py /
    plot_downscaled_spell_maps: the longest length at least half the years
    reach). week: (year, step, lat, lon) daily rainfall of one calendar week."""
    land = week.isel(year=0, step=0).notnull()  # CHIRPS is NaN over ocean/lakes
    stats = {}
    for spell_name, length_fn in [('dry', gef.dry_spell_length), ('wet', gef.wet_spell_length)]:
        lengths = length_fn(week, threshold=SPELL_THRESH)
        for min_len in (5, 7):
            stats[f'{spell_name}_prob{min_len}'] = ((lengths >= min_len).mean('year') * 100).where(land)
        probs = xr.concat([(lengths >= i).mean('year') * 100 for i in range(week.sizes['step'] + 1)],
                          dim='spell_length')
        count_above = (probs >= 50).sum('spell_length')
        stats[f'{spell_name}_median'] = (count_above - 1).where(count_above > 0).where(land)
    return xr.Dataset(stats).astype('float32')


def load_week_spell_stats(windows):
    """spell_stats per calendar window, stacked along 'step' with
    gef.window_coords' coords. Each window is cached on its own
    (MM-DD + length), so a run only reads CHIRPS for weeks not seen before."""
    paths = [f'{spell_cache_dir}/{country}_{w.start:%m-%d}_{w.n_days}d.nc' for w in windows.itertuples()]
    missing = [i for i, p in enumerate(paths) if not os.path.exists(p)]
    if missing:
        # one read covering every missing week: CHIRPS is chunked a year at a
        # time, so one longer window costs about the same as one short week
        span_start = windows.start.iloc[missing[0]]
        span_end = windows.end.iloc[missing[-1]]
        tp = chirps_window_stack(span_start, (span_end - span_start).days)
        for i in missing:
            w = windows.iloc[i]
            week = tp.isel(step=slice((w.start - span_start).days, (w.end - span_start).days))
            stats = spell_stats(week)
            stats.attrs = {'years': year_range(tp.year.values), 'window': f'{w.start:%m-%d} + {w.n_days}d',
                           'threshold': f'{SPELL_THRESH} mm/day'}
            stats.to_netcdf(paths[i], encoding={v: {'zlib': True, 'complevel': 4} for v in stats.data_vars})
            print(f"spells: cached {w.label} climatology -> {paths[i]}")
    else:
        print(f"spells: every calendar week cached in {spell_cache_dir}")

    weeks = [xr.open_dataset(p).load() for p in paths]
    years = weeks[0].attrs['years']
    stats = xr.concat(weeks, dim='step').assign_coords(gef.window_coords(windows, init.to_datetime64()))
    return stats, years


# label: (onset function, its kwargs, confirmation window days) -- same as
# run_rainfall_onset.py's definitions
definitions = {
    'standard': (gef.rainfall_onset_date, {}, 21),
    'icpac10mm': (gef.rainfall_onset_date, {'wet_spell_thresh': 10.0}, 21),
    'accum': (gef.rainfall_onset_date_accum, {}, 30),
}
png_suffix = {'standard': '', 'icpac10mm': '_icpac10mm', 'accum': '_accum'}


def load_season_onsets():
    """Onset per year and definition, in days since the search start (NaN:
    no onset within the season), cached per season window."""
    path = f'{onset_cache_dir}/{country}_{season_start:%m-%d}_{SEASON_DAYS}d.nc'
    if os.path.exists(path):
        print(f"onset: using cached season onsets {path}")
        return xr.open_dataset(path).load()

    tp = chirps_window_stack(season_start, SEASON_DAYS)
    land = tp.isel(year=0, step=0).notnull()
    onsets = {}
    for label, (onset_fn, kwargs, _) in definitions.items():
        # no valid_time: onset comes back as the step (timedelta since the
        # search start), turned into days
        onsets[label] = (onset_fn(tp, time_dim='step', **kwargs) / np.timedelta64(1, 'D')).where(land)
    # land mask stored alongside: a land cell may never reach an onset at all
    onsets['land'] = land
    onsets = xr.Dataset(onsets).astype('float32')
    onsets.attrs = {'units': f'days since {season_start:%m-%d}', 'years': year_range(tp.year.values)}
    onsets.to_netcdf(path, encoding={v: {'zlib': True, 'complevel': 4} for v in onsets.data_vars})
    print(f"onset: cached season onsets -> {path}")
    return onsets


def add_outline(ax):
    """Same map furniture as run_rainfall_onset.py's add_outline."""
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
    gl.xlabel_style = {'size': 12}
    gl.ylabel_style = {'size': 12}


def build_discrete_cmap(vmin, vmax, n_shades=4):
    """Same onset-date colorbar as run_rainfall_onset.py's build_discrete_cmap:
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
        colors.extend(seg_cmap((np.arange(n_shades) + 0.5) / n_shades))
        boundaries.extend(np.linspace(segment_edges[i], segment_edges[i + 1], n_shades + 1)[1:])

    cmap = ListedColormap(colors, name="onset_bands_discrete")
    norm = BoundaryNorm(boundaries, ncolors=cmap.N)
    return cmap, norm, np.array(boundaries), segment_edges


def plot_onset_climatology(onset_days, land, label, pad_days, years, save_path):
    """% of years with an onset before DATE_STR (left) and by last_day
    (right), on the same colours as run_rainfall_onset.py's
    'Chance onset has happened by ...' panel. NaN (no onset within the
    season) counts as no onset yet."""
    last_day = series_end - pd.Timedelta(days=pad_days - 1)
    panels = [
        (f'Onset before {init:%b %d}', onset_days < (init - season_start).days),
        (f'Chance onset has happened by {last_day:%b %d}', onset_days <= (last_day - season_start).days),
    ]
    pct_cmap, pct_norm = gef.discrete_cmap(gef.EXCEEDANCE_COLORS, gef.PROB_BOUNDS)

    fig, axes = plt.subplots(1, 2, figsize=(16, 7), subplot_kw={'projection': ccrs.PlateCarree()})
    for ax, (title, had_onset) in zip(axes, panels):
        pct = (had_onset.mean('year') * 100).where(land)
        mesh = pct.plot.pcolormesh(x='longitude', y='latitude', ax=ax, cmap=pct_cmap, norm=pct_norm,
                                   transform=ccrs.PlateCarree(), add_colorbar=False)
        cbar = fig.colorbar(mesh, ax=ax, pad=0.03, shrink=0.85, aspect=25, ticks=gef.PROB_BOUNDS)
        cbar.set_label('% of years with onset', fontsize=14)
        cbar.ax.tick_params(labelsize=12)
        add_outline(ax)
        ax.set_title(title, fontsize=15, fontweight='bold')
        print(f"{label}: {title.lower()}: land mean {float(pct.mean()):.0f}% of years")
    fig.suptitle(f'Climatological rainy season onset ({label}) — {country}\n'
                 f'CHIRPS {years}, onset searched from {season_start:%b %d}', fontsize=17, fontweight='bold')
    fig.tight_layout()
    plt.savefig(save_path, bbox_inches='tight', dpi=110)
    plt.close(fig)


def plot_analog_onsets(onset_days, land, label, pad_days, save_path):
    """
    One onset date map per ANALOG_YEARS year, on a shared colorbar from the
    search start to Dec 31 (or the last day of the season window that still
    leaves a full confirmation window, if that's earlier). Colored in days
    since the search start (not day-of-year); land cells with no onset by
    the end of the colorbar are hatched.
    """
    analogs = [y for y in ANALOG_YEARS if y in onset_days.year.values]
    missing = sorted(set(ANALOG_YEARS) - set(analogs))
    if missing:
        print(f"analog years {missing} not in CHIRPS window years, skipped")
    if not analogs:
        return

    dec31 = pd.Timestamp(f'{season_start.year}-12-31')
    vmax = float(min(SEASON_DAYS - pad_days, (dec31 - season_start).days))
    cmap_, norm, boundaries, segment_edges = build_discrete_cmap(0.0, vmax)
    ncols = min(len(analogs), 2)
    nrows = int(np.ceil(len(analogs) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(8 * ncols, 7 * nrows),
                             subplot_kw={'projection': ccrs.PlateCarree()}, squeeze=False)
    for ax, y in zip(axes.ravel(), analogs):
        days = onset_days.sel(year=y)
        days = days.where(days <= vmax)  # onsets after the colorbar's end count as none
        mesh = days.plot.pcolormesh(x='longitude', y='latitude', ax=ax, cmap=cmap_, norm=norm,
                                    transform=ccrs.PlateCarree(), add_colorbar=False)
        no_onset = (days.isnull() & land).transpose('latitude', 'longitude')
        if bool(no_onset.any()):
            hatch = ax.pcolor(no_onset.longitude.values, no_onset.latitude.values,
                              np.ma.masked_invalid(no_onset.where(no_onset).values.astype(float)),
                              cmap=ListedColormap(['white']), transform=ccrs.PlateCarree(),
                              shading='nearest', edgecolor='#555555', linewidth=0, hatch='////')
            hatch.set_zorder(mesh.get_zorder() + 0.1)
        add_outline(ax)
        pct_found = float(days.notnull().where(land).mean() * 100)
        ax.set_title(f'{y} ({pct_found:.0f}% of land with onset)', fontsize=15, fontweight='bold')
    for ax in axes.ravel()[len(analogs):]:
        ax.set_visible(False)

    cbar = fig.colorbar(mesh, ax=axes.ravel().tolist(), orientation='vertical', pad=0.03, shrink=0.85,
                        aspect=30, boundaries=boundaries, ticks=segment_edges)
    cbar.set_ticklabels([(season_start + pd.Timedelta(days=float(t))).strftime('%b %d') for t in segment_edges])
    cbar.set_label('Onset date', fontsize=14)
    cbar.ax.tick_params(labelsize=12)
    fig.legend(handles=[matplotlib.patches.Patch(facecolor='white', edgecolor='#555555', hatch='////',
                                                 label=f'no onset by {season_start + pd.Timedelta(days=vmax):%b %d}')],
               loc='lower center', fontsize=13, framealpha=0.9)
    fig.suptitle(f'CHIRPS rainy season onset ({label}) in analog years — {country}\n'
                 f'onset searched from {season_start:%b %d}', fontsize=17, fontweight='bold')
    plt.savefig(save_path, bbox_inches='tight', dpi=110)
    plt.close(fig)


fs = 16
horizon = forecast_horizon_days()
# last rain date the onset forecast covers: step s is the rain on init + s - 1 day
series_end = init + pd.Timedelta(days=forecast_horizon_days(for_onset=True) - 1)
outline = (gpd.read_file(kenya_shapefile).set_crs("EPSG:4326", allow_override=True).dissolve()
           if country == 'Kenya' else None)

# ---- dry / wet spells per calendar week ----------------------------------------
try:
    # as many calendar weeks as the downscaled forecast covers, like its own spell maps
    windows = gef.calendar_windows_that_fit(init.to_datetime64(), max_lead_days=horizon)
    print(f"{country}: calendar weeks {', '.join(windows.label)}")
    stats, years = load_week_spell_stats(windows)
    n_panels = len(windows)

    def save_panels(da, name, units, colors, bounds, save_path, scale=None):
        # the shape plot_downscaled_spell_maps' to_plot builds: a 'tp' dataset
        # with one step per calendar week (panel titles from the window coords)
        da = da.assign_coords(time=init.to_datetime64())
        da.attrs['GRIB_name'] = name
        da.attrs['units'] = units
        ds = da.to_dataset(name='tp')
        # scale: a ready (cmap, norm, ticks), for scales that aren't one colour per `bounds` bin
        cmap_, norm, ticks = scale or (*gef.discrete_cmap(colors, bounds), bounds)
        with gef.extent_of(ds):  # map ends at the shapefile's edges, not the country bbox
            gef.plot_panel_and_save(ds, 'tp', cmap_, fs, save_path, norm=norm, cbar_ticks=ticks,
                                    boundary_gdf=outline, boundary_axes=slice(0, n_panels))
        plt.close()

    for spell_name, spell_colors in [('dry', gef.SPELL_LENGTH_COLORS), ('wet', gef.WET_SPELL_COLORS)]:
        for min_len in (5, 7):
            save_panels(stats[f'{spell_name}_prob{min_len}'],
                        f'clim. chance of {spell_name} spell of at least {min_len} days in the week', '%',
                        spell_colors, gef.PROB_BOUNDS,
                        f'{weekly_dir}/prob_{spell_name}spell_{min_len}days_chirps_clim.png')
        save_panels(stats[f'{spell_name}_median'], f'clim. median {spell_name} spell length in the week', 'days',
                    spell_colors, None,
                    f'{weekly_dir}/median_{spell_name}spell_length_chirps_clim.png',
                    scale=gef.week_spell_scale(spell_colors, int(windows.n_days.max())))
    print(f"spells: CHIRPS {years} climatology maps -> {weekly_dir}/*_chirps_clim.png")
except Exception as e:
    print(f"spells: CHIRPS climatology failed ({e}), skipping")

# ---- onset, once per season ------------------------------------------------------
try:
    if (series_end - season_start).days + 1 > SEASON_DAYS:
        raise ValueError(f"SEASON_DAYS={SEASON_DAYS} from {season_start:%Y-%m-%d} doesn't reach the "
                         f"forecast's last day {series_end:%Y-%m-%d}; raise SEASON_DAYS")
    onsets = load_season_onsets()
    land = onsets['land'] == 1
    for label, (_, _, pad_days) in definitions.items():
        onset_days = onsets[label]
        plot_onset_climatology(onset_days, land, label, pad_days, onsets.attrs['years'],
                               f'{monthly_dir}/onset_chirps_climatology{png_suffix[label]}.png')
        plot_analog_onsets(onset_days, land, label, pad_days,
                           f'{monthly_dir}/onset_chirps_analog_years{png_suffix[label]}.png')
    print(f"onset: CHIRPS climatology maps -> {monthly_dir}/onset_chirps_{{climatology,analog_years}}*.png")
except Exception as e:
    print(f"onset: CHIRPS climatology failed ({e}), skipping")
