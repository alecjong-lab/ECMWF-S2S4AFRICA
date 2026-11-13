"""
Dekadal downscaling from the 0.4 degree forecast -- the dekadal counterpart of
downscale_04deg_.py. When its inputs are there, this replaces the 1.5 degree
dekadal product dowscale_dekade.py makes.

It runs only for a forecast initialized on a dekad start in
gef.DEKAD_FORECAST_FILES (the same table dowscale_dekade.py uses), and makes the
dekads of that table from the forecast's init on, as many as the forecast and
the table hold (4 for the 46-day forecast).

Every member's dekad total is ranked among all 0.4 degree hindcast members of
every hindcast year (20 years x 11 members) for the same lead dekad, read from
the hindcast store at the init closest in the year, and that rank is looked up
in the sorted CHIRPS 0.05 degree dekad climatology (gef.rank_downscale_to_grid,
the same settings as downscale_04deg_.py). The dekadal product is the ensemble
mean of the downscaled members, as dowscale_dekade.py's 1.5 degree one is an
ensemble mean (there the ensemble mean is ranked among the reforecast years'
ensemble means).

The domain is dowscale_dekade.py's Great_Horn box cut to what the 0.4 degree
forecast and hindcast cover (25N-15S, 20-55E): the CHIRPS climatology's strip
east of 55E (sea apart from a sliver of Socotra) and north of 25N is left out.

It writes the dekadal outputs under the names dowscale_dekade.py uses, so
everything downstream uses them unchanged:
    data/<date>/data_dekade_Great_Horn_downscaled.nc, data_dekade_Kenya_downscaled.nc
    data/<date>/geotifs_kenya/
    data/Kenya2026.csv (the ire2026<dekad> columns)
    plots/<country>/<date>/dekadal/dekadal_precip_downscaled{,_anomaly}.png for DEKADE_COUNTRIES
    plots/Kenya/<date>/dekadal/dekadal_precip_downscaled{,_anomaly}_clipped.png and counties/
The netCDF files carry forecast_resolution = '0.4deg...', which is how
forecast_04deg_dates.py keep-published tells them from the 1.5 degree ones.

Run it after dowscale_dekade.py and plot_s2s_04deg_.py. If the 0.4 degree forecast
for DATE_STR, the hindcast store, a hindcast close to that date or the CHIRPS
climatology is missing, it exits without touching anything and the 1.5 degree
outputs stay (when dowscale_dekade.py made them for this date).

Inputs
    private_data/forecast_04deg/<date>/ECMWF_s2s_precip_04deg_<date>.zarr   written by plot_s2s_04deg_.py
    <HINDCAST_04DEG_STORE>   the private hindcast zarr store (needs Google credentials that can read it).
                             Unlike downscale_04deg_.py there is no file fallback: the
                             hindcast_weekly_<init>.nc files only hold weekly totals
    downscale_data/<CHIRPS dekad climatology of gef.DEKAD_FORECAST_FILES>

Env vars: DATE_STR, MAIN_PATH and DEKADE_COUNTRIES as in dowscale_dekade.py, and
HINDCAST_04DEG_STORE, DOWNSCALE_CLOSING_SIZE, DOWNSCALE_RANK_SIGMA and
DOWNSCALE_FIELD_SIGMA as in downscale_04deg_.py, plus
    DOWNSCALE_MEMBER_BATCH   members downscaled at a time (default 10; bounds the memory)
"""
import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import geopandas as gpd
import rioxarray  # noqa: F401 -- the .rio accessor
import regionmask
import pandas as pd
from datetime import datetime, timedelta
import os
import shutil
import sys

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    date_str=(datetime.today()-timedelta(days=2)).strftime("%Y-%m-%d")

prefix=os.environ["MAIN_PATH"]
data_path=f'{prefix}/data/{date_str}'
hindcast_store=os.environ.get("HINDCAST_04DEG_STORE","gs://sheerwater-datalake/ecmwf-0_4/ECMWF_ext_range_hindcast_tp_04deg_2025.zarr")
closing_size=int(os.environ.get("DOWNSCALE_CLOSING_SIZE",0))
rank_sigma=float(os.environ.get("DOWNSCALE_RANK_SIGMA",1.5))
field_sigma=float(os.environ.get("DOWNSCALE_FIELD_SIGMA",0.4))
member_batch=int(os.environ.get("DOWNSCALE_MEMBER_BATCH",10))
countries=[c for c in os.environ.get("DEKADE_COUNTRIES","Kenya").split(',') if c]

# as in downscale_04deg_.py; the store's inits fall on the 1st, 11th and 21st, so a
# dekad start normally has its own
MAX_HINDCAST_GAP_DAYS=4
# half a 0.4 degree cell: CHIRPS cells further than this outside the 0.4 degree grid
# would only copy the rank of its edge
HALF_CELL=0.2

domain='Great_Horn'
bboxes=gef.DEKADE_BBOXES

def keep_1p5deg(reason):
    """Nothing has been written yet at this point: leave dowscale_dekade.py's outputs as they are."""
    print(f"0.4 degree dekadal downscaling not run: {reason}. "
          f"The 1.5 degree dekadal outputs from dowscale_dekade.py (if any) are kept.")
    sys.exit(0)

#-----inputs, all checked before anything is written-------------------------------------------------------------------#
init_day=datetime.strptime(date_str,'%Y-%m-%d')
keys=list(gef.DEKAD_FORECAST_FILES)
if (init_day.month,init_day.day) not in keys:
    print(f"No dekadal downscaling for {date_str}: not a dekad start in gef.DEKAD_FORECAST_FILES")
    sys.exit(0)

precip_zarr=f'{prefix}/private_data/forecast_04deg/{date_str}/ECMWF_s2s_precip_04deg_{date_str}.zarr'
if not os.path.isdir(precip_zarr):
    keep_1p5deg(f"no 0.4 degree forecast initialized on {date_str} ({precip_zarr} not found; "
                f"plot_s2s_04deg_.py downloads it)")
data=xr.open_zarr(precip_zarr,consolidated=True).compute()

# same dekadal aggregates as plot_s2s_04deg_.py
steps=(data.step.values*1e-9/3600).astype('int')
dekade=[data.step.values[i] for i in np.where(steps%240==0)[0]]
data_dekade=gef.acum_to_instant(data.sel(step=dekade))

start=keys.index((init_day.month,init_day.day))
dekad_keys=keys[start:start+min(4,data_dekade.sizes['step'])]
if not dekad_keys:
    keep_1p5deg(f"{precip_zarr} holds no whole dekad")
n_dekads=len(dekad_keys)
data_dekade=data_dekade.isel(step=slice(0,n_dekads))
fclim=np.array([gef.DEKAD_FORECAST_FILES[k] for k in dekad_keys]).T
chirps_files,tif_names=list(fclim[1]),list(fclim[2])
missing=[f for f in chirps_files if not os.path.isfile(f'downscale_data/{f}')]
if missing:
    keep_1p5deg(f"CHIRPS dekad climatology missing: downscale_data/{missing[0]}")

def open_hindcast_store():
    """
    The hindcast zarr store, or None. Read with the default Google credentials (CI),
    else with the gcloud CLI's login (forecast_04deg_dates.archive_fs).
    """
    try:
        return xr.open_zarr(hindcast_store,consolidated=True)
    except Exception as e:
        first=f"{type(e).__name__}: {e}"
    try:
        from zarr.storage import FsspecStore
        from forecast_04deg_dates import archive_fs
        store=FsspecStore(archive_fs(asynchronous=True),path=hindcast_store.removeprefix('gs://'),read_only=True)
        return xr.open_zarr(store,consolidated=True)
    except Exception as e:
        print(f"Hindcast store {hindcast_store} not opened ({first}; with the gcloud login: {type(e).__name__}: {e})")
        return None

def gap_in_year(month,day,init_time):
    """Days between a month and day and init_time's, whatever their years."""
    target=pd.Timestamp(init_time).replace(year=2000)
    gap=abs((datetime(2000,month,day)-target).days)
    return min(gap,366-gap)  # wrap around the new year

store=open_hindcast_store()
if store is None:
    keep_1p5deg("the hindcast store can't be opened")
store_inits=pd.to_datetime(store.init.values)
gaps=[gap_in_year(init.month,init.day,data.time.values) for init in store_inits]
hindcast_index=int(np.argmin(gaps))
hindcast_name=f"{os.path.basename(hindcast_store.rstrip('/'))} init {store_inits[hindcast_index]:%Y-%m-%d}"
if gaps[hindcast_index]>MAX_HINDCAST_GAP_DAYS:
    keep_1p5deg(f"the closest hindcast ({hindcast_name}) is {gaps[hindcast_index]} days from "
                f"{date_str[5:]}, more than the {MAX_HINDCAST_GAP_DAYS} allowed")

# hindcast dekad k is ranked against forecast dekad k: totals of every member of every year
dekad_marks=[np.timedelta64(10*k,'D') for k in range(n_dekads+1)]
if dekad_marks[-1]>store.step.values[-1]:
    keep_1p5deg(f"{hindcast_name} ends before day {10*n_dekads}")
accumulated=store.tp.isel(init=hindcast_index).sel(step=dekad_marks).compute()
if bool(accumulated.isnull().any()):
    keep_1p5deg(f"{hindcast_name} is incomplete in the store")
hindcast=accumulated.diff('step').clip(min=0).assign_coords(step=data_dekade.step.values)
hindcast=gef.align_to_forecast_grid(hindcast,data_dekade)
if hindcast.sizes['latitude']==0 or hindcast.sizes['longitude']==0:
    keep_1p5deg(f"{hindcast_name} doesn't overlap the 0.4 degree forecast")
# every member of every year is one sample to rank among (20 x 11 = 220 per cell)
hindcast=hindcast.stack(sample=['year','number']).reset_index('sample',drop=True)
forecast=data_dekade.tp.sel(latitude=hindcast.latitude,longitude=hindcast.longitude)
print(f"Hindcast from: {hindcast_name} ({hindcast.sizes['sample']} samples per cell and dekad)")

# the CHIRPS dekads of the Great_Horn box that the 0.4 degree grid reaches
lat_n=min(bboxes[domain]['lat1'],float(hindcast.latitude.max())+HALF_CELL)
lat_s=max(bboxes[domain]['lat2'],float(hindcast.latitude.min())-HALF_CELL)
lon_w=max(bboxes[domain]['lon1'],float(hindcast.longitude.min())-HALF_CELL)
lon_e=min(bboxes[domain]['lon2'],float(hindcast.longitude.max())+HALF_CELL)
chirps_dekades_ds=gef.stack_climatology_steps(chirps_files,data_dekade.step.values,base_path='downscale_data/',
                                              apply_rank_sort=True,
                                              bbox={'lat1':lat_n,'lat2':lat_s,'lon1':lon_w,'lon2':lon_e})
chirps_dekades_ds=chirps_dekades_ds.sortby('latitude',ascending=False)
print(f"Domain: {lat_n:g}N..{-lat_s:g}S, {lon_w:g}..{lon_e:g}E, {n_dekads} dekads ({', '.join(tif_names)})")

#-----downscale every member, a batch at a time, and keep the ensemble mean--------------------------------------------#
total=None
members=forecast.number.values
for i in range(0,len(members),member_batch):
    batch=forecast.sel(number=members[i:i+member_batch])
    downscaled=gef.rank_downscale_to_grid(batch,hindcast,chirps_dekades_ds.tp,rank_dim='sample',
                                          closing_size=closing_size,rank_sigma=rank_sigma,field_sigma=field_sigma)
    part=downscaled.sum('number')
    total=part if total is None else total+part
rescaled=(total/len(members)).transpose('step','latitude','longitude')

rescaled_forecast=rescaled.assign_coords({'time':data_dekade.time,'valid_time':data_dekade.valid_time}).to_dataset(name='tp')
rescaled_forecast=rescaled_forecast.where(rescaled_forecast>=0)
rescaled_forecast['tp'].attrs=data_dekade.tp.attrs
# which version made the product: dowscale_dekade.py's 1.5 degree file has no such attribute
rescaled_forecast.attrs={'forecast_resolution':'0.4deg','hindcast':hindcast_name,
                         'hindcast_samples':int(hindcast.sizes['sample']),'ensemble':f'mean of {len(members)} downscaled members',
                         'downscaling':f'downscale_dekade_04deg_.py, closing {closing_size}, rank sigma {rank_sigma:g}, field sigma {field_sigma:g}'}
anomaly=gef.compute_rainfall_anomaly(rescaled_forecast,chirps_dekades_ds)

def country_box(ds,country):
    b=bboxes[country]
    return ds.sortby('latitude',ascending=False).sel(longitude=slice(b['lon1'],b['lon2']),latitude=slice(b['lat1'],b['lat2']))

kenya_dekade=country_box(rescaled_forecast,'Kenya')

#-----from here on the 1.5 degree outputs are replaced-----------------------------------------------------------------#
print(f"Writing the 0.4 degree dekadal downscaled forecast for {date_str}")
os.makedirs(data_path,exist_ok=True)
rescaled_forecast.to_netcdf(f'{data_path}/data_dekade_{domain}_downscaled.nc')
kenya_dekade.to_netcdf(f'{data_path}/data_dekade_Kenya_downscaled.nc')

rescaled_forecast=rescaled_forecast.rio.write_crs("EPSG:4326")
anomaly=anomaly.rio.write_crs("EPSG:4326")

districts=gpd.read_file("Kenya_shapes/ken_admin2.shp")
states1=gpd.read_file("Kenya_shapes/ken_admin1.shp")
kenya_counties_shp="downscale_data/Kenya_Counties_KNSDI.shp"
fs=16
cmap=gef.cmap

# Kenya dekad totals per district, as dowscale_dekade.py writes them
df=pd.read_csv("data/Kenya2026.csv",index_col='Feature')
mask=regionmask.mask_geopandas(districts,rescaled_forecast["longitude"],rescaled_forecast["latitude"])
dekade_names=[f'ire2026{gef.dekad_index(f)}' for f in chirps_files]
records=np.zeros((districts.shape[0],rescaled_forecast.sizes['step']))
for i,row in districts.iterrows():
    records[i]=rescaled_forecast.tp.where(mask==i).mean(['longitude','latitude']).values
dff=pd.DataFrame(data=records,index=df.index,columns=dekade_names)
for name in dekade_names:
    df[name]=dff[name]
df.to_csv('data/Kenya2026.csv')

# Kenya GeoTIFFs (and ESRI .bil), one per dekad. The folder only ever holds this
# init's dekads: cleared so a longer 1.5 degree set leaves no extra dekad behind.
dirname=f'{data_path}/geotifs_kenya/'
shutil.rmtree(dirname,ignore_errors=True)
os.makedirs(dirname,exist_ok=True)
kenya_to_save=kenya_dekade.rio.write_crs("EPSG:4326")
for i,name in enumerate(tif_names):
    fname=f'downscaled_rainfall_forecast_init_{date_str}_{name}.tif'
    to_save=kenya_to_save.isel(step=i)
    to_save.rio.to_raster(dirname+fname)
    # EHdr (ESRI .bil) has no Float64 band type -- write float32.
    to_save.tp.astype('float32').rio.to_raster(f"{dirname+fname}.bil",driver="EHdr")

#-----plots------------------------------------------------------------------------------------------------------------#
try:
    gef.plot_admin1_county_breakdown(rescaled_forecast,chirps_dekades_ds,states1,
                                     save_dir=f'plots/Kenya/{date_str}/dekadal/counties')
except Exception as e:
    print(f"Downscaled dekadal county breakdown plots failed ({e}), skipping")

for country in countries:
    if country not in bboxes:
        print(f"No dekadal box for {country}, skipping")
        continue
    gef.lat1,gef.lat2=bboxes[country]['lat1'],bboxes[country]['lat2']
    gef.lon1,gef.lon2=bboxes[country]['lon1'],bboxes[country]['lon2']
    out=f'plots/{country}/{date_str}/dekadal'
    os.makedirs(out,exist_ok=True)

    ds_to_plot=country_box(rescaled_forecast,country).transpose('latitude','longitude','step')
    gef.plot_panel_and_save(ds_to_plot,'tp',cmap,fs,f'{out}/dekadal_precip_downscaled.png',
                            vmax=int(ds_to_plot.quantile(0.99).tp.values))
    ds_to_plot_anom=country_box(anomaly,country)
    vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot_anom)
    gef.plot_panel_and_save(ds_to_plot_anom,'tp','BrBG',fs,f'{out}/dekadal_precip_downscaled_anomaly.png',
                            vmin=vmin,vmax=vmax)

    if country=='Kenya':
        ds_to_plot=gef.clip_to_shapefile(rescaled_forecast,kenya_counties_shp,transpose=True,sortby_lat=True)
        # map ends at the shapefile's edges, not the country bbox
        with gef.extent_of(ds_to_plot):
            gef.plot_panel_and_save(ds_to_plot,'tp',cmap,fs,f'{out}/dekadal_precip_downscaled_clipped.png',
                                    vmax=int(ds_to_plot.quantile(0.99).tp.values))
        ds_to_plot=gef.clip_to_shapefile(anomaly,kenya_counties_shp,reproject_gdf=False)
        vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot)
        with gef.extent_of(ds_to_plot):
            gef.plot_panel_and_save(ds_to_plot,'tp','BrBG',fs,f'{out}/dekadal_precip_downscaled_anomaly_clipped.png',
                                    vmin=vmin,vmax=vmax,boundary_gdf=districts,
                                    boundary_axes=slice(0,ds_to_plot.sizes['step']))
print("Done")
