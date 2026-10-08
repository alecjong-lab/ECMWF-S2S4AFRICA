"""
0.4 degree counterpart of plot_s2s.py's precipitation plots.

Reads the CHC-standardized 0.4 degree daily ensemble straight from CHC's server
(pr_IFS-SUBS-0.4_<YYYYMMDD>.daily.nc, the init on DATE_STR or the closest one
before it; the server's address comes from FORECAST_04DEG_URL), and writes the
same precip plots as plot_s2s.py, under the same filenames, into
private_plots/<country>/<date>/04deg/{weekly,dekadal,monthly}/.

The 0.4 degree precipitation m-climate is made on the fly from the hindcast
zarr store in the private bucket (needs Google credentials that can read it);
when that can't be opened, the files build_mclimate_0p4.py wrote to
m-climate/T_pr_0p4/ are used instead.

The cut-out of the forecast (the Horn of Africa by default) is kept as
private_data/forecast_04deg/<date>/ECMWF_s2s_precip_04deg_<init>.zarr, in the
same layout as ECMWF_s2s_precip_<date>.zarr, and reused when it is already
there: the remote file is 1.6 GB and only a recent few are kept on CHC's server.
The 0.4 degree forecast is not public data. It is kept out of data/<date>/ on
purpose: the workflows upload every .zarr under data/ to the public bucket,
while private_data/ is gitignored and never uploaded. Don't write it, or
anything it can be reconstructed from, under data/. The plots of the raw
0.4 degree fields (precipitation totals, their week to week change, the
meteograms) stay out of plots/ (synced to the public bucket) for the same
reason. The derived ones (probabilities, EFI/SOT, anomalies, spell lengths:
PUBLIC_PLOTS below) are also copied to
plots/<country>/<date>/04deg/{weekly,dekadal,monthly}/, which is public.

Only precipitation exists at 0.4 degrees, so the temperature, wind and moisture
plots, the website NetCDF export and the AI prompt data all stay in plot_s2s.py.

Env vars: DATE_STR, MAIN_PATH and COUNTRIES as in plot_s2s.py, plus
    FORECAST_04DEG_URL   address of the folder on CHC's server holding the monthly
                         folders of forecasts. Not public: a GitHub secret in the
                         workflows, test/.env locally. Without it only a cut-out
                         that is already there is plotted.
    BOUNDING_BOX         "N,W,S,E" domain cut out of the global file. Defaults
                         to the Horn of Africa ("25,20,-15,55", what the 0.4
                         degree m-climate covers) rather than the workflows'
                         all-Africa box: the file is stored in 20 x 60 degree
                         blocks and this box touches 3 of them per lead day
                         where all of Africa touches 8, so it downloads in
                         about a third of the time. Countries outside the box
                         are skipped.
    HINDCAST_04DEG_STORE hindcast zarr store the m-climate is made from (default
                         gs://sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_2025.zarr)
    MCLIMATE_04DEG_PATH  folder holding T_pr_0p4/ (default <MAIN_PATH>/m-climate/)
"""
import xarray as xr
import get_ECMWF_functions as gef
import efi_sot
import build_mclimate_0p4
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
import re
import sys
import glob
import shutil
import fnmatch
import requests
from datetime import datetime, timedelta

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix=os.environ["MAIN_PATH"]
# not under data/: see the note on the 0.4 degree forecast in the docstring
forecast_path=f'{prefix}/private_data/forecast_04deg/{date_str}'
hindcast_store=os.environ.get("HINDCAST_04DEG_STORE","gs://sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_2025.zarr")
mclimate_path=os.environ.get("MCLIMATE_04DEG_PATH",f'{prefix}/m-climate/')
# hindcast inits pooled into the m-climate: those within this many days of the forecast's
# day of the year (hindcasts come every 2 days, so 4 or 5 of them), as ECMWF pools its own
MCLIMATE_WINDOW_DAYS=4
MCLIMATE_WEEKS=6
bounding_box=list(map(float,os.environ.get("BOUNDING_BOX","25,20,-15,55").split(',')))
# the plots that don't show the raw 0.4 degree field and so may go to the public plots/.
# Anything not matched here stays private, so a new plot is private until it is added.
PUBLIC_PLOTS=['efi_sot_precip.png','*th_percentile_exedance.png','anomaly_from_*th.png','chance_of_above_or_below.png',
              '*chance_higherthan_*mm.png','median_*spell_length.png','prob_*spell_*days.png']

#-----precip extended range, 0.4 degrees----------------------------------------------------------------------------#
# the address is not public, so it is never printed either
CHC_URL=os.environ.get("FORECAST_04DEG_URL","").strip().rstrip('/')
# an init older than this many days before DATE_STR is not plotted as DATE_STR's forecast
MAX_INIT_LAG_DAYS=3

def find_chc_forecast(date_str):
    """
    (url, init date string) of the forecast on CHC's server initialized on
    date_str or, if that one isn't there, the closest init before it. The server
    keeps one folder per month, so the previous month's is listed too for dates
    early in a month. Returns (None, None) if nothing recent enough is there.
    """
    target=datetime.strptime(date_str,'%Y-%m-%d')
    months=sorted({target.strftime('%m'),(target-timedelta(days=MAX_INIT_LAG_DAYS)).strftime('%m')})
    available={}
    for month in months:
        link=f'{CHC_URL}/{month}/'
        try:
            html=requests.get(link,timeout=30).text
        except requests.RequestException as e:
            print(f"Could not list month {month} on CHC's server ({type(e).__name__})")
            continue
        for f in re.findall(r'href="(pr_IFS-SUBS-0\.4_(\d{8})\.daily\.nc)"',html):
            available[datetime.strptime(f[1],'%Y%m%d')]=link+f[0]

    usable=[d for d in available if timedelta(0)<=target-d<=timedelta(days=MAX_INIT_LAG_DAYS)]
    if not usable:
        return None,None
    init=max(usable)
    return available[init],init.strftime('%Y-%m-%d')

def download_04deg_precip(url,init_str):
    """
    The BOUNDING_BOX cut-out of one CHC forecast file as the cumulative-since-init
    dataset the rest of the pipeline works with (same layout as
    ECMWF_s2s_precip_<date>.zarr). The file carries daily totals in mm with
    integer day steps, step i being the rain falling in the 24h starting at
    init + i days, and no time coords. "#mode=bytes" has netCDF read it over
    HTTP range requests, so only the chunks covering the cut-out are fetched.
    """
    north,west,south,east=bounding_box
    raw=xr.open_dataset(url+"#mode=bytes",engine="netcdf4")
    raw=raw.rename({'M':'number','L':'step','Y':'latitude','X':'longitude','pr':'tp'})
    raw=raw.sel(latitude=slice(north,south),longitude=slice(west,east)).load()
    # float32 coords like 0.4000001 don't match the m-climate's float64 ones
    raw=raw.assign_coords(latitude=np.round(raw.latitude.values.astype('float64'),4),
                          longitude=np.round(raw.longitude.values.astype('float64'),4),
                          number=raw.number.values.astype('int64'))

    accumulated=raw.tp.cumsum('step').astype('float32')
    accumulated=accumulated.assign_coords(step=((raw.step.values.astype('int64')+1)*np.timedelta64(1,'D')).astype('timedelta64[ns]'))
    zero=xr.zeros_like(accumulated.isel(step=[0])).assign_coords(step=[np.timedelta64(0,'ns')])
    tp=xr.concat([zero,accumulated],dim='step')
    tp.attrs={'GRIB_name':'Total Precipitation','units':'kg m**-2','long_name':'Total Precipitation'}

    init=np.datetime64(f'{init_str}T00:00:00','ns')
    ds=xr.Dataset({'tp':tp}).assign_coords(time=init)
    ds=ds.assign_coords(valid_time=('step',init+ds.step.values))
    ds=ds.transpose('number','step','latitude','longitude')
    for var in ds.variables.values():
        var.encoding.clear()
    return ds

cached=sorted(glob.glob(f'{forecast_path}/ECMWF_s2s_precip_04deg_*.zarr'))
if cached:
    print(f"Using {cached[-1]}")
    data=xr.open_zarr(cached[-1],consolidated=True).compute()
else:
    if not CHC_URL:
        print(f"Skipping 0.4 degree plots: FORECAST_04DEG_URL is not set and no forecast is in {forecast_path}")
        sys.exit(0)
    url,init_str=find_chc_forecast(date_str)
    if url is None:
        print(f"Skipping 0.4 degree plots: no forecast initialized on {date_str} or up to "
              f"{MAX_INIT_LAG_DAYS} days before it on CHC's server")
        sys.exit(0)
    if init_str!=date_str:
        print(f"No 0.4 degree forecast initialized on {date_str}, using the {init_str} one")
    print(f"Downloading the {init_str} forecast")
    # the range requests to CHC's server get reset now and then
    for attempt in range(1,4):
        try:
            data=download_04deg_precip(url,init_str)
            break
        except Exception as e:
            if attempt==3:
                raise
            print(f"  download failed ({type(e).__name__}), retry {attempt}/2")
    os.makedirs(forecast_path,exist_ok=True)
    data.to_zarr(f'{forecast_path}/ECMWF_s2s_precip_04deg_{init_str}.zarr',mode='w',consolidated=True)

steps=data.step.values*1e-9/3600
steps=steps.astype('int')
dekade = [data.step.values[i] for i in np.where(steps%240==0)[0]]
weekly=[data.step.values[i] for i in np.where(steps%168==0)[0]]

data_weekly=gef.acum_to_instant(data.sel(step=weekly))
data_dekade=gef.acum_to_instant(data.sel(step=dekade))

# the 0.4 degree forecast is shorter than the 1.5 degree one, so a full month isn't guaranteed
has_month=data_weekly.sizes['step']>=4
if has_month:
    data_monthly=data_weekly.isel(step=slice(0,4)).sum('step',keep_attrs=True).assign_coords(step=data_weekly.isel(step=3).step).expand_dims('step')

#-----0.4 degree m-climate--------------------------------------------------------------------------------------------#
def mclimate_from_hindcast_store(init_time):
    """
    The weekly m-climate for a forecast initialized on init_time, made from the
    hindcast store the way build_mclimate_0p4.py makes the files: quantiles of the
    weekly totals of every hindcast init within MCLIMATE_WINDOW_DAYS of init_time's
    day of the year, every hindcast year and every member. None when the store
    can't be opened (no credentials, no access, no network) or has no init that close.
    """
    try:
        store=xr.open_zarr(hindcast_store,consolidated=True)
    except Exception as e:
        print(f"Hindcast store {hindcast_store} not opened ({type(e).__name__}: {e})")
        return None
    target=pd.Timestamp(init_time).replace(year=2000)
    store_inits=pd.to_datetime(store.init.values)
    gaps=np.array([abs((datetime(2000,init.month,init.day)-target).days) for init in store_inits])
    gaps=np.minimum(gaps,366-gaps)  # wrap around the new year
    near=np.where(gaps<=MCLIMATE_WINDOW_DAYS)[0]
    weekly_marks=[np.timedelta64(7*k,'D') for k in range(MCLIMATE_WEEKS+1)]
    accumulated=store.tp.isel(init=near).sel(step=weekly_marks).compute()
    # an init that was laid out in the store but never written reads as NaN
    complete=accumulated.notnull().all(['year','number','step','latitude','longitude']).values
    near,accumulated=near[complete],accumulated.isel(init=complete)
    if not len(near):
        print(f"No hindcast init within {MCLIMATE_WINDOW_DAYS} days of {target:%m-%d} in {hindcast_store}")
        return None
    pooled=accumulated.diff('step').clip(min=0).rename(step='week')
    pooled=pooled.assign_coords(week=np.arange(1,MCLIMATE_WEEKS+1),init=store_inits[near].strftime('%Y-%m-%d').values)
    label=store_inits[near[np.argmin(gaps[near])]].strftime('%Y-%m-%d')
    print(f"Model climatology from the hindcasts of {', '.join(pooled.init.values)}")
    return build_mclimate_0p4.quantile_mclimate(pooled,label).sortby('latitude',ascending=False)

m_climate_big=mclimate_from_hindcast_store(data.time.values)
if m_climate_big is None:
    print(f"Using the m-climate files in {mclimate_path}/T_pr_0p4/ instead")
    m_climate_big=gef.open_mclimate(data_weekly,folder_path=mclimate_path,var="T_pr_0p4")
# the hindcasts the m-climate is built from don't have to sit on the forecast's grid
m_climate_big=gef.align_to_forecast_grid(m_climate_big,data)
clim_lats=m_climate_big.latitude.values
clim_lons=m_climate_big.longitude.values

data_weekly_cut_to_mclimate=data_weekly.sel(latitude=clim_lats,longitude=clim_lons)

efi,sot = efi_sot.EFI_SOT(data_weekly_cut_to_mclimate, m_climate_big)
ensemble_stats_tp=gef.ensemble_data(data_weekly_cut_to_mclimate,m_climate_big,'tp',quantiles=[75,50,25])

if has_month:
    m_climate_big_month=m_climate_big.isel(time=slice(None,4)).sum('time',keep_attrs=True).assign_coords(time=m_climate_big.isel(time=3).time).expand_dims('time')
    data_monthly_cut_to_mclimate=data_monthly.sel(latitude=clim_lats,longitude=clim_lons)
    ensemble_stats_tp_month=gef.ensemble_data(data_monthly_cut_to_mclimate,m_climate_big_month,'tp',quantiles=[75,50,25])

#----plotting-----------------------------------------------------------------------------------------#

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
}

countries=os.environ["COUNTRIES"].split(',')

for country in countries:
    gef.lat1=bboxes[country]['lat1']
    gef.lat2=bboxes[country]['lat2']
    gef.lon1=bboxes[country]['lon1']
    gef.lon2=bboxes[country]['lon2']

    ds_to_plot=data_weekly.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    if ds_to_plot.sizes['latitude']==0 or ds_to_plot.sizes['longitude']==0:
        print(f"{country}: outside the 0.4 degree forecast cut-out, skipping")
        continue

    m_climate=m_climate_big.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fs={'Madagascar':12,'Malawi':14}.get(country,16)

    # everything is written here first: the raw 0.4 degree fields can't go under plots/,
    # which is synced to the public bucket. The PUBLIC_PLOTS are copied there at the end.
    base_path=f'private_plots/{country}/{date_str}/04deg'
    weekly_path=f'{base_path}/weekly'
    dekade_path=f'{base_path}/dekadal'
    monthly_path=f'{base_path}/monthly'

    os.makedirs(weekly_path, exist_ok=True)
    os.makedirs(dekade_path, exist_ok=True)
    os.makedirs(monthly_path, exist_ok=True)

    #plot weekly precip from extended range forecast
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_precip.png',bbox_inches='tight')
    plt.close()

    #plot monthly precip from extended range forecast
    if has_month:
        ds_to_plot_monthly=data_monthly.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        fig=gef.panel_plot_variable(ds_to_plot_monthly,variable='tp',forecast_timestep=ds_to_plot_monthly.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{monthly_path}/monthly_precip.png',bbox_inches='tight')
        plt.close()

    #plot dekadal precip from extended range forecast
    ds_to_plot_dekade=data_dekade.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot_dekade,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/dekadal_precip.png',bbox_inches='tight')
    plt.close()

    #plot change in weekly extended range precip
    gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='seismic',change=True,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_change_in_precip.png',bbox_inches='tight')
    plt.close()

    ##----------------------------- Ensemble stats plot -------------------------------------------------##
    # only where the 0.4 degree m-climate reaches; a country that is partly covered
    # (e.g. Great_Horn) gets maps of the covered part
    if m_climate.sizes['latitude']==0 or m_climate.sizes['longitude']==0:
        print(f"{country}: outside the 0.4 degree m-climate domain, skipping EFI/SOT and climatology plots")
    else:
        print(country)
        ds_to_plot_clim=ds_to_plot.sel(latitude=m_climate.latitude,longitude=m_climate.longitude)
        efi_country=efi.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        sot_country=sot.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))

        fig=gef.panel_plot_variable(efi_country,variable='tp',forecast_timestep=efi_country.step.values,vmax=1,vmin=0.5,cmap=gef.cmap_efi,add_contour=sot_country.tp,contourlevels=[0,1,2,5,8],contourcmap='k',fontsize=fs)
        plt.savefig(f'{weekly_path}/efi_sot_precip.png',bbox_inches='tight')
        plt.close()

        gef.ensemble_plots(ds_to_plot_clim,m_climate,ensemble_stats_tp[0],ensemble_stats_tp[1],ensemble_stats_tp[2],'tp',weekly_path,country=country,fontsize=fs,major_cities=major_cities)

        if has_month:
            m_climate_month=m_climate_big_month.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
            ds_to_plot_monthly_clim=ds_to_plot_monthly.sel(latitude=m_climate.latitude,longitude=m_climate.longitude)
            gef.ensemble_plots(ds_to_plot_monthly_clim,m_climate_month,ensemble_stats_tp_month[0],ensemble_stats_tp_month[1],ensemble_stats_tp_month[2],'tp',monthly_path,country=country,fontsize=fs,major_cities=major_cities)

    if country=='Kenya':
        exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,'tp',20,comparison='greater')
        fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{dekade_path}/chance_higherthan_20mm.png',bbox_inches='tight')
        plt.close()

        exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,'tp',25,comparison='greater')
        fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{dekade_path}/chance_higherthan_25mm.png',bbox_inches='tight')
        plt.close()

        weekly_exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot,'tp',20,comparison='greater')
        fig=gef.panel_plot_variable(weekly_exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='jet_r',fontsize=fs,vmax=100,vmin=0)
        plt.savefig(f'{weekly_path}/weekly_chance_higherthan_20mm.png',bbox_inches='tight')
        plt.close()

        #---probability of dry/wet spells of a given minimum length, and median spell length (Kenya-only: slow, and only needed for Kenya)---#
        # plot_s2s.py's climatological wet spell maps come from the 1.5 degree reforecast archive and have no 0.4 degree counterpart yet
        spell_days=min(28,data.sizes['step']-1)
        precip_kenya=data.diff('step').tp.isel(step=slice(None,spell_days)).sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        precip_kenya.attrs=data.tp.attrs
        last_step=precip_kenya.isel(step=-1).step.values

        def median_spell_length(spell_probability,name):
            # the longest spell length at least half the members reach
            hold=[spell_probability(precip_kenya, threshold=1.0, spell_length_threshold=i)[0] for i in range(spell_days)]  # mm/day threshold
            stacked=xr.concat(hold, dim=xr.DataArray(np.arange(spell_days), dims="spell_length", name="spell_length"))
            count_above=(stacked >= 50).sum(dim="spell_length")
            median_length=(count_above - 1).where(count_above > 0)  # count of True values minus 1 (0-indexing); NaN where prob never reaches 0.5
            median_length.attrs['GRIB_name']=name
            median_length.attrs['units']='days'
            return median_length.assign_coords({'step':last_step}).to_dataset()

        median_length=median_spell_length(gef.dry_spell_probability,'Median dry spell length')
        median_wet_length=median_spell_length(gef.wet_spell_probability,'Median wet spell length')

        dry_spell5=gef.dry_spell_probability(precip_kenya, threshold=1.0, spell_length_threshold=5)[0].assign_coords({'step':last_step}).to_dataset(name='tp')
        dry_spell7=gef.dry_spell_probability(precip_kenya, threshold=1.0, spell_length_threshold=7)[0].assign_coords({'step':last_step}).to_dataset(name='tp')
        wet_spell5=gef.wet_spell_probability(precip_kenya, threshold=1.0, spell_length_threshold=5)[0].assign_coords({'step':last_step}).to_dataset(name='tp')
        wet_spell7=gef.wet_spell_probability(precip_kenya, threshold=1.0, spell_length_threshold=7)[0].assign_coords({'step':last_step}).to_dataset(name='tp')

        # dry signals top out in red ('jet'), wet signals in blue ('jet_r')
        fig=gef.panel_plot_variable(median_length,variable='tp',forecast_timestep=median_length.step.values,cmap='jet',fontsize=fs,vmin=0)
        plt.savefig(f'{monthly_path}/median_dryspell_length.png',bbox_inches='tight')
        plt.close()

        fig=gef.panel_plot_variable(median_wet_length,variable='tp',forecast_timestep=median_wet_length.step.values,cmap='jet_r',fontsize=fs,vmin=0)
        plt.savefig(f'{monthly_path}/median_wetspell_length.png',bbox_inches='tight')
        plt.close()

        for spell_ds, spell_name, spell_cmap in [(dry_spell5,'dryspell_5days','jet'),(dry_spell7,'dryspell_7days','jet'),
                                                  (wet_spell5,'wetspell_5days','jet_r'),(wet_spell7,'wetspell_7days','jet_r')]:
            fig=gef.panel_plot_variable(spell_ds,variable='tp',forecast_timestep=spell_ds.step.values,cmap=spell_cmap,fontsize=fs,vmin=0,vmax=100)
            plt.savefig(f'{monthly_path}/prob_{spell_name}.png',bbox_inches='tight')
            plt.close()

    #copy the derived plots to the public plots folder, in a 04deg/ folder next to the 1.5 degree ones (same filenames)
    for sub in ['weekly','dekadal','monthly']:
        public_path=f'plots/{country}/{date_str}/04deg/{sub}'
        for plot in sorted(glob.glob(f'{base_path}/{sub}/*.png')):
            if any(fnmatch.fnmatch(os.path.basename(plot),pattern) for pattern in PUBLIC_PLOTS):
                os.makedirs(public_path, exist_ok=True)
                shutil.copy2(plot,public_path)
