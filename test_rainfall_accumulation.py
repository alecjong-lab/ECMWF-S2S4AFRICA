"""
Test script: rainfall accumulation curves from the start of the season, per
grid cell, observed CHIRPS continued with the daily downscaled forecast, set
against the CHIRPS climatology of the same curve.

1. Observed: daily CHIRPS from SEASON_START (default Sep 1, the same fixed
   start as run_rainfall_onset.py) up to DATE_STR, or the latest CHIRPS day
   if that's earlier -- the final product topped up with the preliminary one.
2. Forecast: the weekly downscaled forecast split into days per ensemble
   member (same recipe as run_rainfall_onset.py: each member's own raw S2S
   daily profile, interpolated onto the CHIRPS grid), appended after the last
   observed day. Each member's curve continues from the observed total.
3. Climatology: the same Sep 1 -> forecast-end window in every CHIRPS year
   (1981-2025), accumulated per year.

Everything is accumulated per grid cell (mm since SEASON_START). Area curves
average the daily rain over an area first and then accumulate per year /
member, so area percentiles come from each year's / member's own area-mean
curve (not from averaging grid-cell percentiles).

Outputs (test/rainfall_accumulation/<country>/<date>/):
  accumulation_regions.png   Kenya + the 7 briefing regions (gef.KENYA_BRIEFING_REGION_MAP)
  accumulation_counties.png  all 47 counties
  accumulation_maps.png      observed total to date, % of normal to date,
                             forecast-end ensemble median % of normal, and
                             chance the season total by the forecast end is
                             above the climatological 90th percentile
  accumulation_gridpoint_<country>.nc   per-grid-cell curves: observed,
                             forecast ensemble percentiles, climatology percentiles

Run from the repo root against a test fixture forecast:

    DATE_STR=2026-09-26 python test_rainfall_accumulation.py

FORECAST_DATA_PATH (default test/<DATE_STR>) must hold
data_weekly_Kenya_downscaled.nc + ECMWF_s2s_precip_<DATE_STR>.zarr.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import cartopy.crs as ccrs
import geopandas as gpd
import icechunk
import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import regionmask
import rioxarray  # noqa: F401  (registers .rio for clipping)
import xarray as xr

import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

repo = os.path.dirname(os.path.abspath(__file__))
country = "Kenya"  # the downscaled forecast is Kenya-only
bbox = {"lat1": 6, "lon1": 33, "lat2": -5, "lon2": 42}
kenya_shapefile = f"{repo}/downscale_data/Kenya_Counties_KNSDI.shp"
admin1_shapefile = f"{repo}/Kenya_shapes/ken_admin1.shp"

forecast_path = os.environ.get("FORECAST_DATA_PATH", f"{repo}/test/{date_str}")
out_dir = os.environ.get("OUT_DIR", f"{repo}/test/rainfall_accumulation/{country}/{date_str}")
# shared with the CHIRPS climatology test script, so its Sep 1 + 150d window is reused
clim_cache = os.environ.get("CHIRPS_CLIM_CACHE", f"{repo}/test/run/chirps_clim_cache")
SEASON_START = os.environ.get("SEASON_START", "09-01")
CLIM_DAYS = 150  # cached climatology window length (must cover the forecast end)
PROFILE_SIGMA = float(os.environ.get("ONSET_PROFILE_SIGMA", 10))  # same as run_rainfall_onset.py
N_READ_WORKERS = 8
# analog years drawn as their own curves (same default set as chirps_agro_climatology.py),
# each with a colour distinct from the grey climatology / blue forecast / black observed
ANALOG_YEARS = [int(y) for y in os.environ.get("ANALOG_YEARS", "2006,2015,2019,2023").split(",")]
ANALOG_COLORS = ['#e69f00', '#cc79a7', '#d55e00', '#009e73', '#56b4e9', '#f0e442']
os.makedirs(out_dir, exist_ok=True)
os.makedirs(clim_cache, exist_ok=True)

init = pd.Timestamp(date_str)
season_start = pd.Timestamp(f"{init.year}-{SEASON_START}")
if season_start > init:
    season_start -= pd.DateOffset(years=1)


# ---- CHIRPS ------------------------------------------------------------------------
def open_chirps(product):
    storage = icechunk.s3_storage(
        bucket="dynamical-ucsb-chc-chirps",
        prefix=f"ucsb-chc-chirps-analysis-{product}/v0.1.0.icechunk",
        region="us-west-2",
        anonymous=True,
    )
    session = icechunk.Repository.open(storage).readonly_session("main")
    return xr.open_zarr(session.store, chunks=None).precipitation_surface


def cut(da):
    return da.sel(latitude=slice(bbox['lat1'], bbox['lat2']), longitude=slice(bbox['lon1'], bbox['lon2']))


def clean(da):
    """mm/day, without dynamical.org's coord attrs (dict-valued ones can't go to netCDF)."""
    da = (da.drop_vars('spatial_ref', errors='ignore') * 86400).astype('float32')
    for c in da.coords:
        da.coords[c].attrs = {}
    da.attrs = {'units': 'mm day-1'}
    return da


def load_observed(start, end):
    """Daily CHIRPS start..end (or the latest day available), final product
    topped up with the preliminary one (same as run_rainfall_onset.py)."""
    final = cut(open_chirps("final").sel(time=slice(start, end))).compute()
    prelim = cut(open_chirps("preliminary").sel(time=slice(start, end)))
    if final.sizes['time']:
        prelim = prelim.sel(time=slice(final.time.values[-1] + np.timedelta64(1, 'D'), None))
    da = xr.concat([final, prelim.compute()], dim='time') if prelim.sizes['time'] else final
    print(f"observed: CHIRPS {final.sizes['time']} days final + {prelim.sizes['time']} days preliminary")
    return clean(da)


def load_climatology(start, ndays):
    """The same calendar window in every CHIRPS year the final product fully
    covers, (year, step, lat, lon) mm/day. Same cache file format/name as
    test_chirps_agro_climatology.py, so its Sep 1 + 150d window is reused."""
    path = f'{clim_cache}/chirps_window_{country}_{start:%m-%d}_{ndays}d.nc'
    if os.path.exists(path):
        print(f"climatology: using cache {path}")
        return xr.open_dataarray(path).load()

    print(f"climatology: reading CHIRPS {start:%b %d} + {ndays}d for every year")
    da = cut(open_chirps("final"))
    first, last = pd.Timestamp(da.time.values[0]), pd.Timestamp(da.time.values[-1])
    windows = {}
    for y in range(first.year, last.year + 1):
        s = start + pd.DateOffset(years=y - start.year)
        if s >= first and s + pd.Timedelta(days=ndays - 1) <= last:
            windows[y] = s

    def load(y):
        sub = da.sel(time=slice(windows[y], windows[y] + pd.Timedelta(days=ndays - 1))).load()
        return sub.rename(time='step').assign_coords(step=np.arange(ndays))

    with ThreadPoolExecutor(N_READ_WORKERS) as pool:
        stacked = xr.concat(list(pool.map(load, windows)), dim=pd.Index(list(windows), name='year'))
    stacked = clean(stacked).assign_coords(step=pd.to_timedelta(np.arange(ndays), unit='D'))
    stacked.name = 'tp'
    stacked.to_netcdf(path, encoding={'tp': {'zlib': True, 'complevel': 4}})
    return stacked


def clip_kenya(da):
    da = gef.clip_to_shapefile(da.rio.write_crs("EPSG:4326"), kenya_shapefile)
    return da.drop_vars('spatial_ref', errors='ignore')


# ---- forecast ----------------------------------------------------------------------
def forecast_members(grid, land):
    """Generator over ensemble members of the daily downscaled forecast on the
    CHIRPS grid, with a `time` dim = the date each day's rain falls on (same
    recipe as run_rainfall_onset.py's load_daily_downscaled_forecast)."""
    rescaled = xr.open_dataset(f'{forecast_path}/data_weekly_Kenya_downscaled.nc').load()
    rescaled = rescaled.interp(latitude=grid.latitude, longitude=grid.longitude)
    s2s = xr.open_zarr(f'{forecast_path}/ECMWF_s2s_precip_{date_str}.zarr', consolidated=True).compute()
    fc_init = pd.Timestamp(s2s.time.values)
    for n in rescaled.number.values:
        daily = gef.disaggregate_weekly_to_daily(rescaled.tp.sel(number=n), s2s.tp.sel(number=n),
                                                 profile_sigma=PROFILE_SIGMA or None).tp
        # step s covers the 24h ending at init + s: the rain on date init + s - 1 day
        dates = fc_init + pd.to_timedelta(daily.step.values) - pd.Timedelta(days=1)
        daily = daily.assign_coords(time=('step', dates)).swap_dims(step='time')
        daily = daily.drop_vars(['step', 'valid_time', 'number', 'surface', 'year', 'spatial_ref'], errors='ignore')
        yield n, daily.transpose('time', 'latitude', 'longitude').where(land).astype('float32')


# ---- areas -------------------------------------------------------------------------
def area_masks(grid, land):
    """(area, lat, lon) boolean masks: Kenya, the briefing regions, and every county."""
    states = gpd.read_file(admin1_shapefile)
    states['region'] = states['adm1_name'].map(gef.KENYA_BRIEFING_REGION_MAP)
    regions = states.dropna(subset=['region']).dissolve(by='region').reset_index()
    lon, lat = grid.longitude, grid.latitude

    def masks(gdf, name_col):
        m = regionmask.from_geopandas(gdf, names=name_col, overlap=False).mask_3D(lon, lat)
        return m.assign_coords(region=m.names.values).drop_vars(['abbrevs', 'names']).rename(region='area')

    kenya = land.expand_dims(area=['Kenya'])
    counties = masks(states, 'adm1_name')
    return (xr.concat([kenya, masks(regions, 'region')], dim='area') & land,
            counties.sortby('area') & land)


def area_mean(da, masks):
    """Area mean of a daily field per area, cos(lat)-weighted -> (area, ..., time).
    xr.dot contracts lat/lon directly instead of broadcasting the field
    against every area mask (members x days x grid x areas won't fit in memory)."""
    w = (np.cos(np.deg2rad(da.latitude)) * masks).astype('float32')
    return xr.dot(da.fillna(0), w, dim=['latitude', 'longitude']) / w.sum(['latitude', 'longitude'])


# ---- plotting ----------------------------------------------------------------------
def plot_curves(areas, clim_q, analogs, obs_cum, fc_q, save_path, ncols, title, start):
    """One panel per area: climatology percentile bands, the analog years'
    own curves, observed curve, and the forecast ensemble plume continuing
    from the last observed day. obs_cum=None leaves the observed curve out
    (the forecast-only version, where everything is accumulated from the
    first forecast day); the x-axis follows the climatology's time axis."""
    names = list(areas)
    x = pd.DatetimeIndex(clim_q.time.values)
    nrows = int(np.ceil(len(names) / ncols))
    # a single-area figure gets one larger panel instead of a small grid cell
    panel_w, panel_h = (9, 5.5) if len(names) == 1 else (4.6, 3.4)
    fig, axes = plt.subplots(nrows, ncols, figsize=(panel_w * ncols, panel_h * nrows), sharex=True, squeeze=False)
    for ax, name in zip(axes.ravel(), names):
        c = clim_q.sel(area=name)
        ax.fill_between(x, c.sel(q=0.1), c.sel(q=0.9), color='#d9d9d9', lw=0, label='clim. 10-90%')
        ax.fill_between(x, c.sel(q=1 / 3), c.sel(q=2 / 3), color='#a6a6a6', lw=0, label='clim. tercile range')
        ax.plot(x, c.sel(q=0.5), color='#525252', lw=1.2, ls='--', label='clim. median')
        for y, color in zip(analogs.year.values, ANALOG_COLORS):
            ax.plot(x, analogs.sel(area=name, year=y), color=color, lw=1.3, label=str(y))
        f = fc_q.sel(area=name)
        ax.fill_between(fc_dates, f.sel(q=0.1), f.sel(q=0.9), color='#4a90d9', alpha=0.35, lw=0,
                        label='forecast 10-90%')
        ax.plot(fc_dates, f.sel(q=0.5), color='#1f5fb4', lw=1.8, label='forecast median')
        if obs_cum is not None:
            ax.plot(obs_dates, obs_cum.sel(area=name), color='black', lw=2, label='observed (CHIRPS)')
            ax.axvline(fc_dates[0], color='#1f5fb4', lw=0.8, ls=':')
        ax.set_title(name, fontsize=12, fontweight='bold')
        ax.grid(alpha=0.3)
        ax.set_ylim(bottom=0)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
    for ax in axes.ravel()[len(names):]:
        ax.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel(f'mm since {start:%b %d}')
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if len(names) == 1:
        # single panel: legend inside the axes (upper left, where the curves start near 0)
        axes[0, 0].legend(handles, labels, loc='upper left', ncol=2, fontsize=10, framealpha=0.9)
        legend_in = 0
    else:
        fig.legend(handles, labels, loc='lower center', ncol=int(np.ceil(len(labels) / 2)), fontsize=11,
                   frameon=False)
        legend_in = 0.8
    fig.suptitle(title, fontsize=15 if len(names) > 1 else 13, fontweight='bold')
    fig.autofmt_xdate()
    # room for the two-row legend at the bottom and the two-line title at the
    # top, in inches, whatever the figure height
    title_in = 0.7 if len(names) > 1 else 0
    fig.tight_layout(rect=(0, legend_in / fig.get_figheight(), 1, 1 - title_in / fig.get_figheight()))
    plt.savefig(save_path, bbox_inches='tight', dpi=110)
    plt.close(fig)
    print(f"curves -> {save_path}")


def plot_maps(obs_total, obs_pct, fc_pct, p_above90, save_path, domain=country):
    outline = gpd.read_file(kenya_shapefile).set_crs("EPSG:4326", allow_override=True).dissolve()
    pct_bounds = [0, 25, 50, 75, 90, 110, 125, 150, 200, 300]
    pct_cmap, pct_norm = gef.discrete_cmap('BrBG', pct_bounds, start=0.0)
    tot_bounds = [0, 10, 25, 50, 75, 100, 150, 200, 300, 400, 600]
    tot_cmap, tot_norm = gef.discrete_cmap('YlGnBu', tot_bounds)
    prob_cmap, prob_norm = gef.discrete_cmap(gef.EXCEEDANCE_COLORS, gef.PROB_BOUNDS)
    panels = [
        (obs_total, tot_cmap, tot_norm, tot_bounds, 'mm',
         f'Observed\n{season_start:%b %d} - {obs_end:%b %d}'),
        (obs_pct, pct_cmap, pct_norm, pct_bounds, '% of clim. median',
         f'Observed to {obs_end:%b %d}\n% of normal'),
        (fc_pct, pct_cmap, pct_norm, pct_bounds, '% of clim. median',
         f'Obs + forecast to {fc_end:%b %d}\nens. median, % of normal'),
        (p_above90, prob_cmap, prob_norm, gef.PROB_BOUNDS, '% of members',
         f'Chance total to {fc_end:%b %d}\nis above clim. 90th percentile'),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(24, 7), subplot_kw={'projection': ccrs.PlateCarree()})
    for ax, (da, cmap_, norm, bounds, units, title) in zip(axes, panels):
        mesh = da.plot.pcolormesh(x='longitude', y='latitude', ax=ax, cmap=cmap_, norm=norm,
                                  transform=ccrs.PlateCarree(), add_colorbar=False)
        ax.add_geometries(outline.geometry, crs=ccrs.PlateCarree(), facecolor='none', edgecolor='black', lw=1)
        cbar = fig.colorbar(mesh, ax=ax, orientation='horizontal', pad=0.05, shrink=0.9, ticks=bounds)
        cbar.set_label(units, fontsize=12)
        ax.set_title(title, fontsize=13, fontweight='bold')
    fig.suptitle(f'Seasonal rainfall accumulation since {season_start:%b %d} — {domain} '
                 f'(forecast init {date_str}, CHIRPS {clim_years})', fontsize=16, fontweight='bold')
    plt.savefig(save_path, bbox_inches='tight', dpi=110)
    plt.close(fig)
    print(f"maps -> {save_path}")


# ---- observed ----------------------------------------------------------------------
obs = clip_kenya(load_observed(season_start, init))
obs_end = pd.Timestamp(obs.time.values[-1])
land = obs.notnull().any('time')
grid = obs.isel(time=0, drop=True)
print(f"observed: {season_start:%Y-%m-%d} to {obs_end:%Y-%m-%d} ({obs.sizes['time']} days)")

# ---- forecast, per member, appended after the last observed day ---------------------
fc_daily = []
for n, daily in forecast_members(grid, land):
    daily = daily.sel(time=slice(obs_end + pd.Timedelta(days=1), None))
    fc_daily.append(daily.expand_dims(number=[n]))
fc_daily = xr.concat(fc_daily, dim='number')
fc_dates = pd.DatetimeIndex(fc_daily.time.values)
if fc_dates[0] != obs_end + pd.Timedelta(days=1):
    raise ValueError(f"forecast starts {fc_dates[0]:%Y-%m-%d}, leaving a gap after the last CHIRPS day "
                     f"{obs_end:%Y-%m-%d}; pick a forecast initialized on or before {obs_end + pd.Timedelta(days=1):%Y-%m-%d}")
fc_end = fc_dates[-1]
obs_dates = pd.DatetimeIndex(obs.time.values)
dates = pd.date_range(season_start, fc_end)
print(f"forecast: {fc_daily.sizes['number']} members, {fc_dates[0]:%Y-%m-%d} to {fc_end:%Y-%m-%d}")

# ---- climatology: Sep 1 -> forecast end in every year --------------------------------
n_days = len(dates)
if n_days > CLIM_DAYS:
    raise ValueError(f"forecast end {fc_end:%Y-%m-%d} is past the {CLIM_DAYS}-day climatology window")
clim = clip_kenya(load_climatology(season_start, CLIM_DAYS)).isel(step=slice(0, n_days))
clim = clim.assign_coords(step=dates).rename(step='time')
clim_years = f'{int(clim.year.min())}-{int(clim.year.max())}'
print(f"climatology: CHIRPS {clim_years} ({clim.sizes['year']} years)")
analog_years = [y for y in ANALOG_YEARS if y in clim.year.values]
if len(analog_years) < len(ANALOG_YEARS):
    print(f"analog years {sorted(set(ANALOG_YEARS) - set(analog_years))} not in the CHIRPS climatology, skipped")

# ---- per grid cell -------------------------------------------------------------------
quantiles = [0.1, 1 / 3, 0.5, 2 / 3, 0.9]
obs_cum = obs.cumsum('time').where(land)
fc_cum = (obs_cum.isel(time=-1, drop=True) + fc_daily.cumsum('time')).where(land)
clim_cum = clim.cumsum('time').where(land)
clim_q = clim_cum.quantile(quantiles, dim='year').rename(quantile='q')
fc_q = fc_cum.quantile([0.1, 0.5, 0.9], dim='number').rename(quantile='q')

# maps: to date and at the forecast end, against the climatology of the same day
clim_med_obs_end = clim_q.sel(q=0.5, time=obs_end)
clim_med_fc_end = clim_q.sel(q=0.5, time=fc_end)
obs_total = obs_cum.isel(time=-1)
obs_pct = (100 * obs_total / clim_med_obs_end.where(clim_med_obs_end > 1)).where(land)
fc_pct = (100 * fc_q.sel(q=0.5, time=fc_end) / clim_med_fc_end.where(clim_med_fc_end > 1)).where(land)
p_above90 = ((fc_cum.sel(time=fc_end) > clim_q.sel(q=0.9, time=fc_end)).mean('number') * 100).where(land)

# ---- per calendar week: chance the week's total is above the clim. 90th percentile --
# same calendar weeks (1st/8th/15th/22nd) as the downscaled spell / >20mm maps
windows = gef.calendar_windows(init.to_datetime64(), max_lead_days=(fc_end - init).days + 1)


def week_totals(daily):
    """Total per calendar week, stacked along 'step' (window_coords' coords)."""
    weeks = [daily.sel(time=slice(w.start, w.end - pd.Timedelta(days=1))) for w in windows.itertuples()]
    for wk, w in zip(weeks, windows.itertuples()):
        if wk.sizes['time'] != w.n_days:
            raise ValueError(f"{w.label}: only {wk.sizes['time']} of {w.n_days} days covered")
    return xr.concat([wk.sum('time') for wk in weeks], dim='step').assign_coords(
        gef.window_coords(windows, init.to_datetime64()))


clim_week_p90 = week_totals(clim).quantile(0.9, dim='year', skipna=False).drop_vars('quantile')
p_week_above90 = ((week_totals(fc_daily) > clim_week_p90).mean('number') * 100).where(land)
print(f"weekly: {', '.join(windows.label)}; land-mean chance above clim. 90th percentile "
      f"{[round(float(v)) for v in p_week_above90.mean(['latitude', 'longitude']).values]} %")


def plot_weekly_above90(prob, save_path):
    """% of members whose calendar-week total is above that week's CHIRPS
    90th percentile, one panel per week, in the same layout and colours as
    gef.plot_downscaled_exceedance's weekly >20mm map."""
    ds = prob.assign_coords(time=init.to_datetime64()).to_dataset(name='tp')
    ds['tp'].attrs = {'GRIB_name': f'chance week total above clim. 90th percentile (CHIRPS {clim_years})',
                      'units': '%'}
    ds = ds.transpose(*gef.plot_dim_order(ds))
    outline = gpd.read_file(kenya_shapefile).set_crs("EPSG:4326", allow_override=True).dissolve()
    cmap_, norm = gef.discrete_cmap(gef.EXCEEDANCE_COLORS, gef.PROB_BOUNDS)
    with gef.extent_of(ds):
        gef.plot_panel_and_save(ds, 'tp', cmap_, 16, save_path, norm=norm, cbar_ticks=gef.PROB_BOUNDS,
                                boundary_gdf=outline, boundary_axes=slice(0, ds.sizes['step']))
    plt.close()
    print(f"weekly map -> {save_path}")

# ---- areas ---------------------------------------------------------------------------
region_masks, county_masks = area_masks(grid, land)
# central-eastern Kenya: the CE_Kenya box of dowscale_dekade.py (lon 36-42, lat 5 to -5)
ce_box = {'lat1': 5, 'lon1': 36, 'lat2': -5, 'lon2': 42}
in_box = ((grid.latitude <= ce_box['lat1']) & (grid.latitude >= ce_box['lat2'])
          & (grid.longitude >= ce_box['lon1']) & (grid.longitude <= ce_box['lon2']))
ce_mask = (land & in_box).expand_dims(area=['Central-eastern Kenya'])
# counties with most of their land inside the box, kept whole (not cut at the box edge)
share_in_box = (county_masks & in_box).sum(['latitude', 'longitude']) / county_masks.sum(['latitude', 'longitude'])
ce_counties = county_masks.sel(area=share_in_box.area[share_in_box > 0.5])


def crop(da, box):
    return da if box is None else da.sel(latitude=slice(box['lat1'], box['lat2']),
                                         longitude=slice(box['lon1'], box['lon2']))


def make_domain_plots(domain, box, area_sets, dom_dir):
    """Maps (cropped to box), per-grid-cell netCDF, and the season / forecast
    period accumulation curves for each (masks, file name, ncols, label) set."""
    os.makedirs(dom_dir, exist_ok=True)
    xr.Dataset({
        'observed': crop(obs_cum, box),
        'forecast': crop(fc_q, box),
        'climatology': crop(clim_q, box),
        'analog_years': crop(clim_cum.sel(year=analog_years), box),
    }).assign_attrs(units=f'mm since {season_start:%Y-%m-%d}', forecast_init=date_str,
                    climatology_years=clim_years, domain=domain).to_netcdf(
        f'{dom_dir}/accumulation_gridpoint_{country}.nc',
        encoding={v: {'zlib': True, 'complevel': 4} for v in ('observed', 'forecast', 'climatology', 'analog_years')})

    plot_maps(*(crop(da, box) for da in (obs_total, obs_pct, fc_pct, p_above90)),
              f'{dom_dir}/accumulation_maps.png', domain)
    plot_weekly_above90(crop(p_week_above90, box), f'{dom_dir}/weekly_chance_above_clim_p90.png')

    for masks, fname, ncols, label in area_sets:
        obs_area = area_mean(obs, masks).cumsum('time')
        fc_area = (obs_area.isel(time=-1, drop=True) + area_mean(fc_daily, masks).cumsum('time'))
        clim_area = area_mean(clim, masks).cumsum('time')
        clim_area_q = clim_area.quantile(quantiles, dim='year').rename(quantile='q')
        fc_area_q = fc_area.quantile([0.1, 0.5, 0.9], dim='number').rename(quantile='q')
        plot_curves(list(masks.area.values), clim_area_q, clim_area.sel(year=analog_years), obs_area, fc_area_q,
                    f'{dom_dir}/{fname}', ncols,
                    f'Rainfall accumulated since {season_start:%b %d} — {label}\n'
                    f'CHIRPS to {obs_end:%b %d}, then downscaled forecast (init {date_str}); '
                    f'climatology CHIRPS {clim_years}', season_start)

        # forecast-only version: everything accumulated from the first forecast
        # day, so the forecast period isn't offset by how wet/dry the observed
        # part of the season was
        fc_only = area_mean(fc_daily, masks).cumsum('time')
        clim_fc = area_mean(clim.sel(time=fc_dates), masks).cumsum('time')
        plot_curves(list(masks.area.values), clim_fc.quantile(quantiles, dim='year').rename(quantile='q'),
                    clim_fc.sel(year=analog_years), None,
                    fc_only.quantile([0.1, 0.5, 0.9], dim='number').rename(quantile='q'),
                    f'{dom_dir}/{fname.replace(".png", "_forecast_period.png")}', ncols,
                    f'Rainfall accumulated over the forecast period {fc_dates[0]:%b %d} - {fc_end:%b %d} — {label}\n'
                    f'downscaled forecast (init {date_str}); climatology CHIRPS {clim_years}', fc_dates[0])
        for name in masks.area.values[:1]:
            f_end = fc_area_q.sel(area=name, time=fc_end)
            c_end = clim_area_q.sel(area=name, time=fc_end)
            print(f"  {name}: observed {float(obs_area.sel(area=name).isel(time=-1)):.0f} mm by {obs_end:%b %d} "
                  f"(clim. median {float(clim_area_q.sel(area=name, q=0.5, time=obs_end)):.0f}); "
                  f"by {fc_end:%b %d} forecast median {float(f_end.sel(q=0.5)):.0f} mm "
                  f"vs clim. median {float(c_end.sel(q=0.5)):.0f} / 90th {float(c_end.sel(q=0.9)):.0f} mm")


make_domain_plots('Kenya', None,
                  [(region_masks, 'accumulation_regions.png', 4, 'Kenya and briefing regions'),
                   (county_masks, 'accumulation_counties.png', 7, 'Kenya counties')],
                  out_dir)
make_domain_plots('Central-eastern Kenya', ce_box,
                  [(ce_mask, 'accumulation_area.png', 1, 'central-eastern Kenya (36-42E, 5N-5S)'),
                   (ce_counties, 'accumulation_counties.png', 5,
                    'counties mostly inside central-eastern Kenya (36-42E, 5N-5S)')],
                  f'{out_dir}/central_eastern')
print(f"done: {out_dir}")

