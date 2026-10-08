"""
Weekly Kenya downscaling from the 0.4 degree forecast -- when its inputs are
there, this replaces the 1.5 degree product dowscale_dekade.py made earlier in
the same run.

Ranks every member's weekly total among the 0.4 degree hindcast years of the
same lead week, and looks that rank up in the sorted CHIRPS 0.05 degree weekly
climatology (gef.rank_downscale_to_grid).

It writes the Kenya weekly downscaled outputs under the same names
dowscale_dekade.py uses (data_weekly_Kenya_downscaled.nc, daily_downscaled_kenya.tif,
weekly_precip_downscaled*.png, the spell and 20/50 mm maps, the county
breakdown, promt_unformat3.json, ...), so everything downstream -- rainfall
onset, the CHIRPS climatology maps, the briefing, the email, the Kenya bucket
-- uses them unchanged. Run it after dowscale_dekade.py: if the 0.4 degree
forecast for DATE_STR or a hindcast close to that date is missing, it exits
without touching anything and the 1.5 degree outputs stay. The dekadal and
Ghana downscaling are still dowscale_dekade.py's.

The 0.4 degree forecast is 33 days long, so this product has 4 weeks where the
1.5 degree one has 6. Rainfall onset needs the longer horizon (its definitions
look up to 30 days ahead of a candidate day), so the 1.5 degree weeks 5-6 that
dowscale_dekade.py made are kept in a second file,
data_weekly_Kenya_downscaled_onset.nc: 0.4 degree weeks 1-4 followed by the 1.5
degree weeks 5-6. Only run_rainfall_onset.py and the onset part of
chirps_agro_climatology.py read it.

Inputs
    private_data/forecast_04deg/<date>/ECMWF_s2s_precip_04deg_<date>.zarr
                                                     written by plot_s2s_04deg_.py. Not
                                                     public data, which is why it isn't
                                                     under data/<date>/ (uploaded to the
                                                     public bucket); only the downscaled
                                                     outputs of this script go there
    <HINDCAST_04DEG_STORE>                           the hindcast zarr store in the private
                                                     bucket (init, year, number, step,
                                                     latitude, longitude; accumulated tp);
                                                     the init closest in the year is used.
                                                     Needs Google credentials that can read
                                                     the bucket
    <HINDCAST_04DEG_PATH>/hindcast_weekly_<init>.nc  used when the store can't be opened;
                                                     written by build_mclimate_0p4.py
                                                     --yearly-output
    downscale_data/chirpsv3_weeks/chirpsv3_weeks_2005_2025_sorted_<MM-DD>_Kenya.nc

Env vars: DATE_STR and MAIN_PATH as in dowscale_dekade.py, plus
    HINDCAST_04DEG_STORE    default gs://sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_2025.zarr
    HINDCAST_04DEG_PATH     default downscale_data/hindcast_04deg
    DOWNSCALE_CLOSING_SIZE  grey closing window of the ranks, in 0.05 degree cells (default 0 = off)
    DOWNSCALE_RANK_SIGMA    gaussian sigma on the ranks, in 0.05 degree cells (default 1.5)
    DOWNSCALE_FIELD_SIGMA   gaussian sigma on the downscaled field (default 0.4)
    SPELL_WINDOWS           weeks the dry/wet spell and 20/50 mm maps are made for:
                            "calendar" (default; weeks starting on the 1st, 8th, 15th
                            and 22nd, as in dowscale_dekade.py, as many as fit in the
                            downscaled forecast) or "rolling" (7-day weeks from init)
    SPELL_PROFILE_SIGMA     gaussian sigma (0.05 degree cells) smoothing the 0.4 degree
                            daily timing profile the weeks are split into days with
                            (default 3; dowscale_dekade.py uses 10 for 1.5 degree cells)

The defaults were picked from a side-by-side comparison of closing/smoothing
levels: a 0.4 degree cell is only 8 CHIRPS cells wide, so the 1.5 degree
chain's 50-cell closing is not needed, and it added about 40% rain here.
"""
import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import geopandas as gpd
import rioxarray
from datetime import datetime, timedelta
import os
import re
import sys
import glob
import pandas as pd

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix=os.environ["MAIN_PATH"]
data_path=f'{prefix}/data/{date_str}'
hindcast_store=os.environ.get("HINDCAST_04DEG_STORE","gs://sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_2025.zarr")
hindcast_path=os.environ.get("HINDCAST_04DEG_PATH","downscale_data/hindcast_04deg")

closing_size=int(os.environ.get("DOWNSCALE_CLOSING_SIZE",0))
rank_sigma=float(os.environ.get("DOWNSCALE_RANK_SIGMA",1.5))
field_sigma=float(os.environ.get("DOWNSCALE_FIELD_SIGMA",0.4))
spell_windows=os.environ.get("SPELL_WINDOWS","calendar")
SPELL_PROFILE_SIGMA=float(os.environ.get("SPELL_PROFILE_SIGMA",3))

# a hindcast further than this from the forecast's day of the year is a different
# part of the season, not this forecast's climatology (hindcasts come every 2 days)
MAX_HINDCAST_GAP_DAYS=4

country='Kenya'
bbox      = {"lat1": 6,  "lon1":33,  "lat2": -5,  "lon2": 42 }
bbox_plus = {"lat1": 7.5,"lon1":27,  "lat2": -7.5,"lon2": 43 }   # the CHIRPS weekly climatology's own extent

def keep_1p5deg(reason):
    """Nothing has been written yet at this point: leave dowscale_dekade.py's outputs as they are."""
    print(f"0.4 degree downscaling not run: {reason}. "
          f"The 1.5 degree downscaled outputs from dowscale_dekade.py are kept.")
    sys.exit(0)

#-----inputs, all checked before anything is written-------------------------------------------------------------------#
# plot_s2s_04deg_.py falls back to an init a few days older when DATE_STR's isn't on
# the server yet; fine for its own maps, not for the dated downscaled product
precip_zarr=f'{prefix}/private_data/forecast_04deg/{date_str}/ECMWF_s2s_precip_04deg_{date_str}.zarr'
if not os.path.isdir(precip_zarr):
    keep_1p5deg(f"no 0.4 degree forecast initialized on {date_str} ({precip_zarr} not found; "
                f"plot_s2s_04deg_.py downloads it)")

data=xr.open_zarr(precip_zarr,consolidated=True).compute()

# same weekly aggregates as plot_s2s_04deg_.py
steps=data.step.values*1e-9/3600
steps=steps.astype('int')
weekly=[data.step.values[i] for i in np.where(steps%168==0)[0]]
data_weekly=gef.acum_to_instant(data.sel(step=weekly))
data_weekly=data_weekly.sel(longitude=slice(bbox_plus['lon1'],bbox_plus['lon2']),latitude=slice(bbox_plus['lat1'],bbox_plus['lat2']))

def covers_chirps_box(ds):
    """True if ds's grid reaches every edge of the CHIRPS weekly box (to within one 0.4 degree cell)."""
    if ds.sizes['latitude']==0 or ds.sizes['longitude']==0:
        return False
    return (float(ds.latitude.max())>=bbox_plus['lat1']-0.4 and float(ds.latitude.min())<=bbox_plus['lat2']+0.4 and
            float(ds.longitude.min())<=bbox_plus['lon1']+0.4 and float(ds.longitude.max())>=bbox_plus['lon2']-0.4)

if not covers_chirps_box(data_weekly):
    keep_1p5deg(f"{precip_zarr} does not cover Kenya's CHIRPS box "
                f"({bbox_plus['lat1']}N..{-bbox_plus['lat2']}S, {bbox_plus['lon1']}..{bbox_plus['lon2']}E)")

def gap_in_year(month,day,init_time):
    """Days between a month and day and init_time's, whatever their years."""
    target=pd.Timestamp(init_time).replace(year=2000)
    gap=abs((datetime(2000,month,day)-target).days)
    return min(gap,366-gap)  # wrap around the new year

def open_hindcast_store():
    """The hindcast zarr store, or None when it can't be opened (no credentials, no access, no network)."""
    try:
        return xr.open_zarr(hindcast_store,consolidated=True)
    except Exception as e:
        print(f"Hindcast store {hindcast_store} not opened ({type(e).__name__}: {e}); looking in {hindcast_path} instead")
        return None

def find_hindcast_file(init_time):
    """(path, gap in days) of the hindcast_weekly_<init>.nc whose init is closest in the year to init_time."""
    best,best_gap=None,None
    for path in glob.glob(f'{hindcast_path}/hindcast_weekly_*.nc'):
        match=re.search(r'hindcast_weekly_\d{4}-(\d{2})-(\d{2})\.nc$',path)
        if not match:
            continue
        gap=gap_in_year(int(match.group(1)),int(match.group(2)),init_time)
        if best_gap is None or gap<best_gap:
            best,best_gap=path,gap
    return best,best_gap

store=open_hindcast_store()
if store is not None:
    store_inits=pd.to_datetime(store.init.values)
    gaps=[gap_in_year(init.month,init.day,data.time.values) for init in store_inits]
    hindcast_index=int(np.argmin(gaps))
    hindcast_gap=gaps[hindcast_index]
    hindcast_name=f"{os.path.basename(hindcast_store.rstrip('/'))} init {store_inits[hindcast_index]:%Y-%m-%d}"
else:
    hindcast_file,hindcast_gap=find_hindcast_file(data.time.values)
    if hindcast_file is None:
        keep_1p5deg(f"no hindcast store and no hindcast_weekly_*.nc in {hindcast_path}")
    hindcast_name=os.path.basename(hindcast_file)
if hindcast_gap>MAX_HINDCAST_GAP_DAYS:
    keep_1p5deg(f"the closest hindcast ({hindcast_name}) is {hindcast_gap} days from "
                f"{date_str[5:]}, more than the {MAX_HINDCAST_GAP_DAYS} allowed")

# hindcast week k is ranked against forecast week k
n_weeks=data_weekly.sizes['step']
if store is not None:
    # ensemble-mean weekly total of every hindcast year, as build_mclimate_0p4.py --yearly-output makes them
    weekly_marks=[np.timedelta64(7*k,'D') for k in range(n_weeks+1)]
    accumulated=store.tp.isel(init=hindcast_index).sel(step=weekly_marks).compute()
    if bool(accumulated.isnull().any()):
        keep_1p5deg(f"{hindcast_name} is incomplete in the store")
    hindcast=accumulated.diff('step').clip(min=0).mean('number')
else:
    hindcast=xr.open_dataset(hindcast_file).tp.isel(week=slice(0,n_weeks)).rename(week='step')
hindcast=hindcast.assign_coords(step=data_weekly.step.values)
hindcast=gef.align_to_forecast_grid(hindcast,data_weekly)
if not (hindcast.sizes['latitude']==data_weekly.sizes['latitude'] and hindcast.sizes['longitude']==data_weekly.sizes['longitude']):
    keep_1p5deg(f"{hindcast_name} does not cover Kenya's CHIRPS box")
print(f"Hindcast years from: {hindcast_name}")

#-----CHIRPS weekly climatology (same file choice as dowscale_dekade.py)-----------------------------------------------#
forecast_year = int(data.time.dt.year.values)
all_dates = []
for month in range(1, 13):
    day = datetime(forecast_year, month, 1)
    if month == 12:
        next_month = datetime(forecast_year + 1, 1, 1)
    else:
        next_month = datetime(forecast_year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    # the climatology files exist every 2 days from day 1 of each month
    while day <= last_day:
        all_dates.append(day)
        day += timedelta(days=2)

dates=[pd.to_datetime(str(date)[:10])- timedelta(days=7) for date in data_weekly.valid_time.values]
closest=[pd.Series(all_dates).iloc[(pd.Series(all_dates) - date).abs().idxmin()] for date in dates]
day_and_month=[("%02d" % date.month,"%02d" % date.day) for date in closest]

fclim_chirps=[f"downscale_data/chirpsv3_weeks/chirpsv3_weeks_2005_2025_sorted_{dix[0]}-{dix[1]}_{country}.nc" for dix in day_and_month]
missing_chirps=[f for f in fclim_chirps if not os.path.isfile(f)]
if missing_chirps:
    keep_1p5deg(f"CHIRPS weekly climatology missing: {missing_chirps[0]}")
chirps_weeks_ds = gef.stack_climatology_steps(fclim_chirps, data_weekly.step.values)

#-----downscale every member, and derive the daily products, before writing anything-----------------------------------#
rescaled=gef.rank_downscale_to_grid(data_weekly.tp,hindcast,chirps_weeks_ds.tp,
                                    closing_size=closing_size,rank_sigma=rank_sigma,field_sigma=field_sigma)
rescaled_forecast=rescaled.assign_coords({'time':data_weekly.time,'valid_time':data_weekly.valid_time}).to_dataset(name='tp')
rescaled_forecast=rescaled_forecast.where(rescaled_forecast>=0)
rescaled_forecast['tp'].attrs=data_weekly.tp.attrs
rescaled_forecast=rescaled_forecast.transpose('number','step','latitude','longitude')
# which version made the product: dowscale_dekade.py's 1.5 degree file has no such attribute
rescaled_forecast.attrs={'forecast_resolution':'0.4deg','hindcast':hindcast_name,
                         'downscaling':f'downscale_04deg_.py, closing {closing_size}, rank sigma {rank_sigma:g}, field sigma {field_sigma:g}'}

kenya_weekly=rescaled_forecast.sel(longitude=slice(bbox['lon1'],bbox['lon2']),latitude=slice(bbox['lat1'],bbox['lat2']))

has_month=n_weeks>=4
if has_month:
    rescaled_forecast_month = rescaled_forecast.isel(step=slice(0,4)).sum('step',keep_attrs=True).assign_coords(step=rescaled_forecast.isel(step=3).step).expand_dims('step')

# Weeks beyond the 0.4 degree forecast, for rainfall onset: taken from the 1.5 degree
# file dowscale_dekade.py wrote earlier in this run, which is about to be overwritten.
# On a rerun that file is already the 0.4 degree one, and the weeks are carried over
# from the previous onset file instead.
weekly_file=f'{data_path}/data_weekly_Kenya_downscaled.nc'
onset_file=f'{data_path}/data_weekly_Kenya_downscaled_onset.nc'

def later_1p5deg_weeks():
    for path in (weekly_file,onset_file):
        if not os.path.exists(path):
            continue
        with xr.open_dataset(path) as ds:
            if path==weekly_file and 'forecast_resolution' in ds.attrs:
                continue  # already the 0.4 degree product
            later=ds.tp.sel(step=ds.step>kenya_weekly.step.values[-1]).load()
            same_run=(pd.Timestamp(ds.time.values)==pd.Timestamp(kenya_weekly.time.values)
                      and later.sizes['number']==kenya_weekly.sizes['number']
                      and later.sizes['latitude']==kenya_weekly.sizes['latitude']
                      and later.sizes['longitude']==kenya_weekly.sizes['longitude'])
        if later.sizes['step'] and same_run:
            later=later.drop_vars([c for c in later.coords if c not in kenya_weekly.tp.coords])
            return later.assign_coords(latitude=kenya_weekly.latitude,longitude=kenya_weekly.longitude)
    return None

later_weeks=later_1p5deg_weeks()
if later_weeks is not None:
    onset_weekly=xr.concat([kenya_weekly.tp,later_weeks.transpose(*kenya_weekly.tp.dims)],dim='step').to_dataset(name='tp')
    onset_weekly['tp'].attrs=kenya_weekly.tp.attrs
    onset_weekly.attrs=dict(kenya_weekly.attrs,forecast_resolution=f"0.4deg weeks 1-{n_weeks}, 1.5deg weeks {n_weeks+1}-{onset_weekly.sizes['step']}")
else:
    print("No 1.5 degree weeks beyond the 0.4 degree forecast found: rainfall onset will only have "
          f"the {n_weeks} weeks of the 0.4 degree product")

CE_Kenya_dwnscaled_timeseries=rescaled_forecast.sel(longitude=slice(36,42),latitude=slice(5,-5)).mean({'longitude','latitude'})
CE_Kenya_dwnscaled_timeseries_daily=gef.disaggregate_weekly_to_daily(CE_Kenya_dwnscaled_timeseries.tp,data.sel(longitude=slice(36,42),latitude=slice(5,-5)).mean({'longitude','latitude'}).tp)

# Daily downscaled GeoTIFF, one band per lead day, on the same (CHIRPS box) extent as
# dowscale_dekade.py's. rioxarray only writes 2D/3D arrays, so it holds the ensemble
# mean -- disaggregation is linear in the weekly field
daily_downscaled=gef.disaggregate_weekly_to_daily(
    rescaled_forecast.tp.mean('number', keep_attrs=True), data.tp.mean('number')
)
da = daily_downscaled.tp.reset_coords(drop=True).astype('float32')
da = da.transpose('step','latitude','longitude').sortby('latitude', ascending=False)
da = da.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
da = da.rio.write_crs("EPSG:4326").rio.write_nodata(np.nan)

#-----from here on the 1.5 degree outputs are replaced-----------------------------------------------------------------#
print(f"Replacing the Kenya weekly downscaled forecast for {date_str} with the 0.4 degree version ({n_weeks} weeks)")
kenya_weekly.to_netcdf(weekly_file)
gef.save_downscaled_zarr(kenya_weekly, weekly_file.replace('.nc','.zarr'))
if later_weeks is not None:
    onset_weekly.to_netcdf(onset_file)
    gef.save_downscaled_zarr(onset_weekly, onset_file.replace('.nc','.zarr'))
    print(f"Rainfall onset forecast: {onset_weekly.attrs['forecast_resolution']} -> {onset_file}")
da.rio.to_raster(f'{data_path}/daily_downscaled_kenya.tif', tags={"band_dim_name": "day"})
gef.save_downscaled_zarr(daily_downscaled.tp.astype('float32').transpose('step','latitude','longitude'), f'{data_path}/daily_downscaled_kenya.zarr')
# the per-member daily field, split into days as the spell maps below are
gef.save_daily_downscaled_members_zarr(kenya_weekly, data, f'{data_path}/daily_downscaled_kenya_members.zarr', profile_sigma=SPELL_PROFILE_SIGMA)
CE_Kenya_dwnscaled_timeseries_daily.to_zarr(f'{data_path}/CE_Kenya_dwnscaled_timeseries_daily.zarr', mode='w', consolidated=True)

#-----plots------------------------------------------------------------------------------------------------------------#
districts=gpd.read_file("Kenya_shapes/ken_admin2.shp")
states1=gpd.read_file("Kenya_shapes/ken_admin1.shp")
kenya_counties_shp = "downscale_data/Kenya_Counties_KNSDI.shp"

fs=16
cmap=gef.cmap
gef.lat1=bbox['lat1']
gef.lat2=bbox['lat2']
gef.lon1=bbox['lon1']
gef.lon2=bbox['lon2']

weekly_path=f'plots/{country}/{date_str}/weekly'
monthly_path=f'plots/{country}/{date_str}/monthly'
os.makedirs(weekly_path, exist_ok=True)
os.makedirs(monthly_path, exist_ok=True)

def kenya_box(ds):
    return ds.sel(longitude=slice(bbox['lon1'],bbox['lon2']),latitude=slice(bbox['lat1'],bbox['lat2']))

ds_to_plot=kenya_weekly.transpose('latitude', 'longitude','number','step')
gef.plot_panel_and_save(
    ds_to_plot,'tp',cmap,fs,
    f'{weekly_path}/weekly_precip_downscaled.png',
    vmax=int(ds_to_plot.quantile(0.95).tp.values)
)

if has_month:
    ds_to_plot_month=kenya_box(rescaled_forecast_month).transpose('latitude', 'longitude','number','step')
    gef.plot_panel_and_save(
        ds_to_plot_month,'tp',cmap,fs,
        f'{monthly_path}/monthly_precip_downscaled.png',
        vmax=int(ds_to_plot_month.quantile(0.95).tp.values)
    )

# Dry/wet spell maps and chance of > 20mm / > 50mm per week, from the per-member daily
# downscaled forecast (see gef.plot_downscaled_spell_maps).
def spell_week_windows():
    init=pd.Timestamp(data.time.values)
    lead_days=int(kenya_weekly.step.values[-1] / np.timedelta64(1, 'D'))
    if spell_windows=='rolling':
        starts=[init+pd.Timedelta(days=7*k) for k in range(lead_days//7)]
        return pd.DataFrame({'start':starts,'end':[s+pd.Timedelta(days=7) for s in starts],
                             'label':[f'Week {k+1}' for k in range(len(starts))],'n_days':7})
    # the downscaled forecast is shorter than the 42 days four calendar weeks can need
    return gef.calendar_windows_that_fit(init, max_lead_days=lead_days)

week_totals = None
try:
    windows = spell_week_windows()
    print(f"Spell/exceedance weeks: {', '.join(windows.label)}")
    week_totals = gef.plot_downscaled_spell_maps(
        kenya_weekly, data, kenya_counties_shp, weekly_path, fs, windows, profile_sigma=SPELL_PROFILE_SIGMA,
    )
except Exception as e:
    print(f"Downscaled dry/wet spell plots failed ({e}), skipping")

for threshold in (20, 50):
    try:
        if week_totals is None:
            raise ValueError("no weekly totals, the spell maps step failed")
        gef.plot_downscaled_exceedance(
            week_totals, threshold, kenya_counties_shp,
            f'{weekly_path}/weekly_chance_higherthan_{threshold}mm_downscaled.png', fs,
        )
    except Exception as e:
        print(f"Downscaled {threshold}mm exceedance plot failed ({e}), skipping")

rescaled_forecast = rescaled_forecast.rio.write_crs("EPSG:4326")
ds_to_plot = gef.clip_to_shapefile(rescaled_forecast, kenya_counties_shp, transpose=True)
# map ends at the shapefile's edges, not the country bbox
with gef.extent_of(ds_to_plot):
    gef.plot_panel_and_save(
        ds_to_plot,'tp',cmap,fs,
        f'{weekly_path}/weekly_precip_downscaled_clipped.png',
        vmax=int(ds_to_plot.quantile(0.99).tp.values),
        boundary_gdf=districts,boundary_axes=slice(0,ds_to_plot.sizes['step'])
    )

anomaly = gef.compute_rainfall_anomaly(rescaled_forecast, chirps_weeks_ds)

ds_to_plot=kenya_box(anomaly).transpose('latitude', 'longitude','number','step')
vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot)
gef.plot_panel_and_save(
    ds_to_plot,'tp','BrBG',fs,
    f'{weekly_path}/weekly_precip_downscaled_anomaly.png',
    vmin=vmin,vmax=vmax
)

if has_month:
    chirps_weeks_ds_month=chirps_weeks_ds.isel(step=slice(0,4)).sum('step',keep_attrs=True).assign_coords(step=chirps_weeks_ds.isel(step=3).step).expand_dims('step')
    anomaly_month = gef.compute_rainfall_anomaly(rescaled_forecast_month, chirps_weeks_ds_month)

    ds_to_plot_anom_month=kenya_box(anomaly_month).transpose('latitude', 'longitude','number','step')
    vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot_anom_month)
    gef.plot_panel_and_save(
        ds_to_plot_anom_month,'tp','BrBG',fs,
        f'{monthly_path}/monthly_precip_downscaled_anomaly.png',
        vmin=vmin,vmax=vmax
    )

anomaly = anomaly.rio.write_crs("EPSG:4326")
ds_to_plot = gef.clip_to_shapefile(anomaly, kenya_counties_shp, transpose=True)
vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot)
with gef.extent_of(ds_to_plot):
    gef.plot_panel_and_save(
        ds_to_plot,'tp','BrBG',fs,
        f'{weekly_path}/weekly_precip_downscaled_anomaly_clipped.png',
        vmin=vmin,vmax=vmax,boundary_gdf=districts,boundary_axes=slice(0,ds_to_plot.sizes['step'])
    )

try:
    gef.plot_admin1_county_breakdown(
        rescaled_forecast, chirps_weeks_ds, states1,
        save_dir=f'plots/Kenya/{date_str}/weekly/counties',
        transpose_first=False
    )
except Exception as e:
    print(f"Downscaled county breakdown plots failed ({e}), skipping")

#-----zone statistics for the AI briefing------------------------------------------------------------------------------#
try:
    states1_regions = states1.copy()
    states1_regions['region'] = states1_regions['adm1_name'].map(gef.KENYA_BRIEFING_REGION_MAP)
    regions_kenya = states1_regions.dropna(subset=['region']).dissolve(by='region')

    promt_unformat3={}
    rescaled_forecast_kenya=kenya_box(rescaled_forecast).mean('number')
    for region in states1_regions['region'].unique():
        promt_raw=gef.clip_by_overlap(rescaled_forecast_kenya.tp, regions_kenya, region, threshold=0.15).mean({'latitude','longitude'})
        promt_raw_dict=[{'raw_precip_mm':round(float(v),2)} for v in promt_raw]
        promt_unformat3[f"{region} (Downscaled)"]={f"week{i+1}": d for i, d in enumerate(promt_raw_dict)}
    gef.save_dict(promt_unformat3,f"{prefix}/promt_unformat3.json")
except:
    gef.save_dict({},f"{prefix}/promt_unformat3.json")
