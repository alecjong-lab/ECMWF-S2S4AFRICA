"""
Re-render the raw precipitation panels (S2S, GEFS, and downscaled) so that, for each
country and each timescale (weekly/dekadal/monthly), the S2S/GEFS/downscaled panels share
one color scale -- overwriting the existing PNGs. Scales are kept separate per country
(so Ethiopia's heavy rainfall doesn't wash out Namibia's plots) and per timescale (so
monthly totals, naturally several times a weekly total, don't wash out that country's
weekly plots). Reuses data already written to disk by plot_s2s.py / plot_gefs.py /
dowscale_dekade.py for DATE_STR -- it does not re-download or re-run the pipeline.

Downscaled precip is only available on disk for Kenya (weekly + dekadal) and Great_Horn
(dekadal) -- dowscale_dekade.py never persists Ghana/Ethiopia's downscaled fields to netcdf,
so those are skipped here.
"""
import os
import numpy as np
import xarray as xr
import matplotlib
# Force a non-interactive backend before matplotlib.pyplot is imported anywhere
# (including transitively via get_ECMWF_functions) -- this script never shows a
# window, and on Windows the default TkAgg backend can leave zombie processes
# behind across repeated runs.
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix = os.environ["MAIN_PATH"]
data_path = f'{prefix}/data/{date_str}'
plots_path = f'{prefix}/plots'

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

# dowscale_dekade.py renders downscaled precip with its own, slightly different bboxes --
# reuse the exact same ones here so re-rendering doesn't shift the visible extent.
DEKADE_BBOXES = {
    "Kenya":      {"lat1": 7,    "lon1": 33,   "lat2": -6, "lon2": 42},
    "Great_Horn": {"lat1": 25.5, "lon1": 19.5, "lat2": -8, "lon2": 57},
}
WEEKLY_BBOXES = {
    "Kenya": {"lat1": 6, "lon1": 33, "lat2": -5, "lon2": 42},
}
DOWNSCALED_FONTSIZE = 12

S2S_FILENAMES = {'weekly': 'weekly_precip.png', 'dekadal': 'dekadal_precip.png', 'monthly': 'monthly_precip.png'}
GEFS_FILENAMES = {'weekly': 'gefs_weekly_precip.png', 'dekadal': 'gefs_dekade_precip.png', 'monthly': 'gefs_monthly_precip.png'}


def fontsize_for(country):
    # Mirrors plot_s2s.py's per-country fontsize logic exactly -- the dangling `else`
    # always overrides the Madagascar branch, so only Malawi actually ends up at 14.
    if country == 'Madagascar':
        fs = 12
    if country == 'Malawi':
        fs = 14
    else:
        fs = 16
    return fs


def clip_to_bbox(ds, bbox):
    return ds.sel(longitude=slice(bbox['lon1'], bbox['lon2']), latitude=slice(bbox['lat1'], bbox['lat2']))


def minmax_or_percentile(ds, variable='tp', percentile=None):
    data = ds[variable]
    if 'number' in data.dims:
        data = gef.ensemble_mean(ds)[variable]
    values = data.values
    if values.size == 0:
        return None
    if percentile is not None:
        lo, hi = percentile
        return float(np.nanquantile(values, lo)), float(np.nanquantile(values, hi))
    return float(np.nanmin(values)), float(np.nanmax(values))


def load_gefs_country(country):
    zarr_path = f'{data_path}/gefs/gefs_{country.lower()}.zarr'
    try:
        gefs_data = xr.open_zarr(zarr_path, consolidated=True)
    except Exception as e:
        print(f'  [skip] GEFS {country}: {e}')
        return None

    gefs_data = gefs_data.cumsum(dim='step').assign_coords({'step': gefs_data.step})
    steps = (gefs_data.step.values * 1e-9 / 3600).astype('int')
    dekade = [gefs_data.step.values[i] for i in np.where(steps % 240 == 0)[0]]
    weekly = [gefs_data.step.values[i] for i in np.where(steps % 168 == 0)[0]]

    return {
        'weekly': gef.acum_to_instant(gefs_data.sel(step=weekly)),
        'dekadal': gef.acum_to_instant(gefs_data.sel(step=dekade)),
        'monthly': gefs_data.sel(step=weekly).isel(step=4),
    }


print(f'Loading S2S data for {date_str}...')
s2s_full = {
    'weekly': xr.open_dataset(f'{data_path}/data_weekly.nc'),
    'dekadal': xr.open_dataset(f'{data_path}/data_dekade.nc'),
    'monthly': xr.open_dataset(f'{data_path}/data_monthly.nc'),
}

print('Loading downscaled precip data...')
downscaled_jobs = []  # (country, timescale, ds, bbox, save_path)

try:
    # build_rescaled_forecast's native dim order isn't consistent between the weekly and
    # dekadal code paths (weekly comes out as longitude/latitude/step, dekadal as
    # latitude/longitude/step) -- dowscale_dekade.py always transposes right before
    # plotting, so do the same here rather than assume an order.
    kenya_weekly = xr.open_dataset(f'{data_path}/data_weekly_Kenya_downscaled.nc').transpose('latitude', 'longitude', 'step')
    kenya_monthly = (kenya_weekly.isel(step=slice(0, 4)).sum('step', keep_attrs=True)
                      .assign_coords(step=kenya_weekly.isel(step=3).step).expand_dims('step'))
    downscaled_jobs.append(('Kenya', 'weekly', kenya_weekly, WEEKLY_BBOXES['Kenya'],
                             f'{plots_path}/Kenya/{date_str}/weekly/weekly_precip_downscaled.png'))
    downscaled_jobs.append(('Kenya', 'monthly', kenya_monthly, WEEKLY_BBOXES['Kenya'],
                             f'{plots_path}/Kenya/{date_str}/monthly/monthly_precip_downscaled.png'))
except FileNotFoundError as e:
    print(f'  [skip] Kenya weekly/monthly downscaled: {e}')

try:
    kenya_dekade = xr.open_dataset(f'{data_path}/data_dekade_Kenya_downscaled.nc').transpose('latitude', 'longitude', 'step')
    downscaled_jobs.append(('Kenya', 'dekadal', kenya_dekade, DEKADE_BBOXES['Kenya'],
                             f'{plots_path}/Kenya/{date_str}/dekadal/dekadal_precip_downscaled.png'))
except FileNotFoundError as e:
    print(f'  [skip] Kenya dekadal downscaled: {e}')

try:
    great_horn_dekade = xr.open_dataset(f'{data_path}/data_dekade_Great_Horn_downscaled.nc').transpose('latitude', 'longitude', 'step')
    downscaled_jobs.append(('Great_Horn', 'dekadal', great_horn_dekade, DEKADE_BBOXES['Great_Horn'],
                             f'{plots_path}/Great_Horn/{date_str}/dekadal/dekadal_precip_downscaled.png'))
except FileNotFoundError as e:
    print(f'  [skip] Great_Horn dekadal downscaled: {e}')

# ---- Pass 1: gather vmin/vmax candidates from every source/country/timescale ----

records = []        # (source, country, timescale, vmin, vmax)
s2s_gefs_jobs = []  # (source, country, timescale, ds, bbox, fontsize, save_path)

for country, bbox in bboxes.items():
    fs = fontsize_for(country)

    for timescale, ds in s2s_full.items():
        ds_clip = clip_to_bbox(ds, bbox)
        stats = minmax_or_percentile(ds_clip)
        if stats is None:
            print(f'  [skip] S2S {country} {timescale}: empty after bbox clip')
            continue
        records.append(('S2S', country, timescale, *stats))
        save_path = f'{plots_path}/{country}/{date_str}/{timescale}/{S2S_FILENAMES[timescale]}'
        s2s_gefs_jobs.append(('S2S', country, timescale, ds_clip, bbox, fs, save_path))

    print(f'Loading GEFS for {country}...')
    gefs = load_gefs_country(country)
    if gefs is None:
        continue
    for timescale, ds in gefs.items():
        ds_clip = clip_to_bbox(ds, bbox)
        stats = minmax_or_percentile(ds_clip)
        if stats is None:
            print(f'  [skip] GEFS {country} {timescale}: empty after bbox clip')
            continue
        records.append(('GEFS', country, timescale, *stats))
        save_path = f'{plots_path}/{country}/{date_str}/{timescale}/{GEFS_FILENAMES[timescale]}'
        s2s_gefs_jobs.append(('GEFS', country, timescale, ds_clip, bbox, fs, save_path))

for country, timescale, ds, bbox, save_path in downscaled_jobs:
    stats = minmax_or_percentile(ds, percentile=(0.11, 0.90))
    records.append(('Downscaled', country, timescale, *stats))

# ---- Report which dataset drives each (country, timescale) group's shared vmin/vmax ----
# Grouped by country AND timescale, not one scale across everything -- Ethiopia's heavy
# rainfall shouldn't wash out Namibia's plots, and monthly totals (naturally ~3-4x a
# weekly total) shouldn't wash out that country's weekly plots either.

group_scale = {}  # (country, timescale) -> (vmin, vmax)

print('\n' + '=' * 66)
print(f'{"Source":<12}{"Country":<14}{"Timescale":<11}{"vmin":>14}{"vmax":>15}')
for country in bboxes:
    for timescale in ('weekly', 'dekadal', 'monthly'):
        group = [r for r in records if r[1] == country and r[2] == timescale]
        if not group:
            continue
        for source, _, _, vmin, vmax in sorted(group, key=lambda r: r[3], reverse=True):
            print(f'{source:<12}{country:<14}{timescale:<11}{vmin:>14.2f}{vmax:>15.2f}')

        min_record = min(group, key=lambda r: r[3])
        max_record = max(group, key=lambda r: r[4])
        group_vmin, group_vmax = min_record[3], max_record[4]
        group_scale[(country, timescale)] = (group_vmin, group_vmax)
        print(f'  -> {country}/{timescale} vmin = {group_vmin:.2f} mm ({min_record[0]}), '
              f'vmax = {group_vmax:.2f} mm ({max_record[0]})')
        print('-' * 66)
print('=' * 66 + '\n')

# ---- Pass 2: re-plot everything with its (country, timescale) scale, overwriting the PNGs ----

print('Re-plotting with per-country, per-timescale shared color scales...')

for source, country, timescale, ds, bbox, fs, save_path in s2s_gefs_jobs:
    vmin, vmax = group_scale[(country, timescale)]
    gef.lat1, gef.lat2, gef.lon1, gef.lon2 = bbox['lat1'], bbox['lat2'], bbox['lon1'], bbox['lon2']
    fig = gef.panel_plot_variable(ds, variable='tp', forecast_timestep=ds.step.values,
                                   cmap=gef.cmap, fontsize=fs, vmin=vmin, vmax=vmax)
    if fig is None:
        print(f'  [skip] {source} {country} {timescale}: empty after bbox clip')
        continue
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, bbox_inches='tight')
    plt.close()
    print(f'  wrote {save_path}')

for country, timescale, ds, bbox, save_path in downscaled_jobs:
    vmin, vmax = group_scale[(country, timescale)]
    gef.lat1, gef.lat2, gef.lon1, gef.lon2 = bbox['lat1'], bbox['lat2'], bbox['lon1'], bbox['lon2']
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    gef.plot_panel_and_save(ds, 'tp', gef.cmap, DOWNSCALED_FONTSIZE, save_path,
                             vmin=vmin, vmax=vmax)
    plt.close()
    print(f'  wrote {save_path}')
