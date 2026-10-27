"""
Plot the one-week DWD ICON-EPS forecast made by download_icon_eps.py, per country, into
plots/<country>/<icon date>/icon/.

Precipitation (tot_prec):
  icon_week_precip.png               ensemble-mean total for the week
  icon_daily_precip.png              ensemble-mean total per day
  icon_week_precip_p<q>.png          ensemble percentiles of the week total (PERCENTILES)
  icon_week_precip_prob_<t>mm.png    chance (% of members) that the week total exceeds t mm
  icon_daily_precip_prob_<t>mm.png   same per day (DAILY_THRESHOLDS)
Any other downloaded variable (t_2m, relhum_2m, ...) gets icon_daily_<var>.png (daily
ensemble mean) and icon_week_<var>.png (week mean); add an entry to PLOT_SETTINGS to give it
its own colour map.

The ICON date is its own date (DWD keeps only the latest day, so it is the run's init
date, not the ECMWF DATE_STR): ICON_DATE_STR, else the newest data/<date>/ICON_eps_* store.
"""
import glob
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

import get_ECMWF_functions as gef

prefix = os.environ.get('MAIN_PATH', os.getcwd())
countries = os.environ['COUNTRIES'].split(',')
variables = os.environ.get('ICON_VARIABLES', 'tot_prec').split(',')

WEEK_THRESHOLDS = [10, 25, 50]      # mm per week
DAILY_THRESHOLDS = [10]             # mm per day
PERCENTILES = [10, 90]

# colour map and fixed range per variable; anything not listed uses viridis and the data range
PLOT_SETTINGS = {
    't_2m':      {'cmap': 'RdYlBu_r'},
    'td_2m':     {'cmap': 'BrBG'},
    'relhum_2m': {'cmap': 'BrBG', 'vmin': 0, 'vmax': 100},
    'clct':      {'cmap': 'Greys', 'vmin': 0, 'vmax': 100},
}

if 'ICON_DATE_STR' in os.environ:
    date_str = os.environ['ICON_DATE_STR']
else:
    stores = sorted(glob.glob(f'{prefix}/data/*/ICON_eps_{variables[0]}_*.zarr'))
    if not stores:
        raise SystemExit('no ICON_eps store under data/; run download_icon_eps.py first')
    date_str = os.path.basename(os.path.dirname(stores[-1]))
data_path = f'{prefix}/data/{date_str}'

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

major_cities = {
    "Namibia":     [(-22.5594, 17.0832), (-17.9333, 19.7667), ('Windhoek', 'Rundu')],
    "Botswana":    [(-24.6545, 25.9086), (-21.1700, 27.5000), ('Gaborone', 'Francistown')],
    "Kenya":       [(-1.28333, 36.8167), (-4.0547, 39.6636),  ('Nairobi', 'Mombasa')],
    "Zambia":      [(-15.4067, 28.2871), (-12.80243, 28.21323), ('Lusaka', 'Kitwe')],
    "Madagascar":  [(-18.9137, 47.5361), (-18.1500, 49.4000), ('Antananarivo', 'Toamasina')],
    "Angola":      [(-8.8368, 13.2343),  (-11.2027, 17.8739), ('Luanda', 'Huambo')],
    "Ghana":       [(5.5600, -0.2057),   (6.6885, -1.6244),   ('Accra', 'Kumasi')],
    "Senegal":     [(14.6937, -17.4441), (12.3500, -16.7167), ('Dakar', 'Ziguinchor')],
    "Ethiopia":    [(9.0272, 38.7369),   (11.1400, 42.8000),  ('Addis Ababa', 'Dire Dawa')],
    "Great_Horn":  [(9.0272, 38.7369),   (2.02000, 45.2000),  ('Addis Ababa', 'Mogadishu')],
    "Zimbabwe":    [(-17.8292, 31.0522), (-20.1500, 28.5833), ('Harare', 'Bulawayo')],
    "Malawi":      [(-13.9626, 33.7741), (-15.7861, 35.0058), ('Lilongwe', 'Blantyre')],
    "Somalia":     [(2.0469, 45.3182),   (9.5624, 44.0770),   ('Mogadishu', 'Hargeisa')],
}


def open_icon(var):
    return xr.open_zarr(f'{data_path}/ICON_eps_{var}_{date_str}.zarr').load()


def daily_and_week_precip(ds):
    """Daily and whole-week totals from the accumulated-since-init store. Daily steps are
    labelled by their end (1..7 days), the week by its last step, matching the ECMWF stores
    so panel titles come out as '<start> until <end>'."""
    day = np.timedelta64(1, 'D')
    daily_steps = [s for s in ds.step.values if s % day == np.timedelta64(0, 'ns')]
    daily = gef.acum_to_instant(ds.sel(step=daily_steps), dim='step', var='tp')
    week = ds.isel(step=[-1]) - ds.isel(step=0)
    week['tp'].attrs = ds['tp'].attrs
    return daily, week


def daily_and_week_mean(ds, var):
    """Daily means of an instantaneous field, each day being the samples in (d-1, d],
    plus the mean over all steps after init."""
    day = np.timedelta64(1, 'D')
    ds = ds.isel(step=slice(1, None)).drop_vars('valid_time', errors='ignore')
    days = np.ceil(ds.step.values / day).astype(int)
    daily = ds.assign_coords(day=('step', days)).groupby('day').mean('step')
    daily = daily.rename(day='step')
    daily = daily.assign_coords(step=daily.step.values * day)
    week = ds.mean('step').expand_dims(step=[ds.step.values[-1]])
    for out in (daily, week):
        out[var].attrs = ds[var].attrs
        out.coords['time'] = ds.time
    return daily, week


# discrete week-total scale of the Kenya multi-model comparison figure
COMPARISON_LEVELS = [0, 1, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 250]
COMPARISON_COLORS = ['#ffffff', '#f5e6c4', '#e3cc98', '#b9f5b9', '#73e873', '#19b419',
                     '#56aaf5', '#1f6be6', '#a690f5', '#6a5bd8', '#fff59a', '#ffa500',
                     '#ff1a00', '#a00000']


def plot_kenya_comparison_panel(week, save_path, label):
    """One panel of the week-total ensemble mean in the style of the Kenya rainfall comparison
    figure (grey outside Kenya, county lines, discrete scale, no colorbar), to paste into it."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import cartopy.io.shapereader as shpreader
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from shapely import contains_xy

    # Natural Earth for the outline as well as the other lines, so they all line up
    countries_shp = shpreader.Reader(shpreader.natural_earth('10m', 'cultural', 'admin_0_countries'))
    kenya = next(c.geometry for c in countries_shp.records() if c.attributes['ADM0_A3'] == 'KEN')
    tp = week['tp'].mean('number').isel(step=0).sel(latitude=slice(6, -5.5), longitude=slice(33, 42.5))
    lon2d, lat2d = np.meshgrid(tp.longitude, tp.latitude)
    # keep the cells whose centre is within half a cell of Kenya, so the border isn't ragged
    inside = contains_xy(kenya.buffer(0.12), lon2d, lat2d)
    tp = tp.where(inside)

    cmap = ListedColormap(COMPARISON_COLORS)
    cmap.set_over(COMPARISON_COLORS[-1])
    norm = BoundaryNorm(COMPARISON_LEVELS, cmap.N)

    fig = plt.figure(figsize=(3.9, 5.0))
    ax = fig.add_subplot(projection=ccrs.PlateCarree())
    ax.set_extent([33.9, 42.1, -4.85, 5.5], crs=ccrs.PlateCarree())
    ax.set_facecolor('#c8c8c8')
    ax.pcolormesh(tp.longitude, tp.latitude, tp, cmap=cmap, norm=norm, transform=ccrs.PlateCarree())
    ax.add_feature(cfeature.STATES.with_scale('10m'), facecolor='none', edgecolor='#666666', linewidth=0.4)
    ax.add_feature(cfeature.BORDERS.with_scale('10m'), edgecolor='#555555', linewidth=0.6)
    ax.add_feature(cfeature.LAKES.with_scale('10m'), facecolor='#6e7f95', edgecolor='none')
    ax.add_geometries([kenya], crs=ccrs.PlateCarree(), facecolor='none', edgecolor='black', linewidth=1.0)

    ax.set_xticks(range(34, 43, 2), crs=ccrs.PlateCarree())
    ax.set_yticks(range(-4, 5, 2), crs=ccrs.PlateCarree())
    ax.set_xticklabels([f'{x}°' for x in range(34, 43, 2)], fontsize=9)
    ax.set_yticklabels([f'{y}°'.replace('-', '−') for y in range(-4, 5, 2)], fontsize=9)
    ax.set_title(label, fontsize=10)
    fig.savefig(save_path, bbox_inches='tight', dpi=150)
    plt.close(fig)


def save(fig, path):
    if fig is None:
        return
    fig.savefig(path, bbox_inches='tight')
    plt.close(fig)


def plot_precip(ds, out_path, fs, cities, country):
    daily, week = daily_and_week_precip(ds)

    if country == 'Kenya':
        init = np.datetime64(ds.time.values, 'D').astype(object)
        plot_kenya_comparison_panel(week, f'{out_path}/icon_week_precip_comparison_panel.png',
                                    f'ICON-EPS mean ({init.day} {init:%b})')

    fig = gef.panel_plot_variable(week, 'tp', week.step.values, cmap=gef.cmap, fontsize=fs,
                                  cities=cities, vmin=0)
    save(fig, f'{out_path}/icon_week_precip.png')

    fig = gef.panel_plot_variable(daily, 'tp', daily.step.values, cmap=gef.cmap, fontsize=fs,
                                  cities=cities, vmin=0, outer_labels_only=True, max_ticks=4)
    save(fig, f'{out_path}/icon_daily_precip.png')

    for q in PERCENTILES:
        pct = week.quantile(q / 100, dim='number').drop_vars('quantile').assign_coords(time=week.time)
        pct['tp'].attrs = {**week['tp'].attrs, 'GRIB_name': f'{q}th percentile of the week total'}
        fig = gef.panel_plot_variable(pct, 'tp', pct.step.values, cmap=gef.cmap, fontsize=fs,
                                      cities=cities, vmin=0)
        save(fig, f'{out_path}/icon_week_precip_p{q}.png')

    for name, data, thresholds in (('week', week, WEEK_THRESHOLDS), ('daily', daily, DAILY_THRESHOLDS)):
        for t in thresholds:
            prob = gef.get_exceedance_percentage(data, 'tp', t, comparison='greater')
            fig = gef.panel_plot_variable(prob, 'tp', prob.step.values, cmap='jet_r', fontsize=fs,
                                          cities=cities, vmin=0, vmax=100,
                                          outer_labels_only=name == 'daily', max_ticks=4 if name == 'daily' else None)
            save(fig, f'{out_path}/icon_{name}_precip_prob_{t}mm.png')


def plot_generic(ds, var, out_path, fs, cities):
    name = list(ds.data_vars)[0]
    settings = PLOT_SETTINGS.get(var, {})
    daily, week = daily_and_week_mean(ds, name)
    kwargs = dict(cmap=settings.get('cmap', 'viridis'), vmin=settings.get('vmin'),
                  vmax=settings.get('vmax'), fontsize=fs, cities=cities)
    save(gef.panel_plot_variable(daily, name, daily.step.values, outer_labels_only=True, max_ticks=4, **kwargs),
         f'{out_path}/icon_daily_{var}.png')
    save(gef.panel_plot_variable(week, name, week.step.values, **kwargs),
         f'{out_path}/icon_week_{var}.png')


data = {}
for var in variables:
    try:
        data[var] = open_icon(var)
    except Exception as e:
        print(f'{var}: no ICON store for {date_str} ({e})')

for country in countries:
    if country not in bboxes:
        print(f'{country}: no bounding box, skipped')
        continue
    fs = 12 if country == 'Madagascar' else 14 if country == 'Malawi' else 16
    gef.lat1, gef.lat2 = bboxes[country]['lat1'], bboxes[country]['lat2']
    gef.lon1, gef.lon2 = bboxes[country]['lon1'], bboxes[country]['lon2']
    (lat_a, lon_a), (lat_b, lon_b), (name_a, name_b) = major_cities[country]
    cities = {name_a: (lat_a, lon_a), name_b: (lat_b, lon_b)}

    out_path = f'plots/{country}/{date_str}/icon'
    os.makedirs(out_path, exist_ok=True)
    for var, ds in data.items():
        try:
            if var == 'tot_prec':
                plot_precip(ds, out_path, fs, cities, country)
            else:
                plot_generic(ds, var, out_path, fs, cities)
            print(f'{country}: {var} plotted')
        except Exception as e:  # keep going for the other countries/variables
            print(f'{country}: {var} failed: {e}')
            plt.close('all')
