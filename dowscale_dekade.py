import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt
import geopandas as gpd
import rioxarray
from matplotlib.colors import LinearSegmentedColormap
from datetime import datetime, timedelta
import os

today = datetime.today()
two_days_earlier = today - timedelta(days=2)
date_str = two_days_earlier.strftime("%Y-%m-%d")

data_dekade=xr.open_dataset(f'data/{date_str}/data_dekade.nc')
month=int(data_dekade.time.dt.month.values)
day=int(data_dekade.time.dt.day.values)

forecast_files = {
    (2, 17): ["ECMWF_tp_forecasts_02-17-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_06_Kenya.nc","Febuary_Dekad3.tif"],
    (2, 27): ["ECMWF_tp_forecasts_02-27-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_07_Kenya.nc","March_Dekad1.tif"],
    (3, 9): ["ECMWF_tp_forecasts_03-09-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_08_Kenya.nc","March_Dekad2.tif"],
    (3, 20): ["ECMWF_tp_forecasts_03-19-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_09_Kenya.nc","March_Dekad3.tif"],
    (4, 1): ["ECMWF_tp_forecasts_03-31-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_10_Kenya.nc","April_Dekad1.tif"],
    (4, 11): ["ECMWF_tp_forecasts_04-09-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_11_Kenya.nc","April_Dekad2.tif"],
    (4, 21): ["ECMWF_tp_forecasts_04-19-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_12_Kenya.nc","April_Dekad3.tif"],
    (5, 1): ["ECMWF_tp_forecasts_04-29-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_13_Kenya.nc","May_Dekad1.tif"],
}

try:
    keys = list(forecast_files)
    start = keys.index((month, day))
    fclim_chirps = np.array([forecast_files[k] for k in keys[start:start+4]]).T

    reforecast_clims=[]
    for i,file in enumerate(fclim_chirps[0]):
        fclim=xr.open_dataset('downscale_data/'+file)
        reforecast_clims.append(fclim.assign_coords({'step':data_dekade.step.values[i]}))
    reforecast_clims_ds=xr.concat(reforecast_clims,dim='step')

                            
    chirps_dekades=[]
    for i,file in enumerate(fclim_chirps[1]):
        chirps=xr.open_dataset('downscale_data/'+file)
        chirps_dekades.append(chirps.assign_coords({'step':data_dekade.step.values[i]}))
    chirps_dekades_ds=xr.concat(chirps_dekades,dim='step')

    bboxes = {
        "Kenya": {"lat1": 7, "lon1": 33, "lat2": -6, "lon2": 42},
        "kenya_plus":{"lat1": 7.5, "lon1":27, "lat2": -7.5, "lon2": 43}
    }

    country="kenya_plus"
    data_to_add=data_dekade.assign_coords({"year":int(data_dekade.time.dt.year.values)}).mean('number').sel(longitude=slice(bboxes[country]['lon1'],bboxes[country]['lon2']),latitude=slice(bboxes[country]['lat1'],bboxes[country]['lat2']))
    extended_fclim=xr.concat([reforecast_clims_ds,data_to_add],dim='year')

    rescaled_forecast=gef.rank_upscale_and_align(extended_fclim.tp,chirps_dekades_ds.tp)
    rescaled_forecast=rescaled_forecast.assign_coords({'time':extended_fclim.time,'valid_time':extended_fclim.valid_time}).to_dataset(name='tp')
    rescaled_forecast.tp.attrs=data_dekade.tp.attrs

    rescaled_forecast.to_netcdf(f'data/{date_str}/data_dekade_downscaled.nc')

    fs=12
    country='Kenya'

    gef.lat1=bboxes[country]['lat1']
    gef.lat2=bboxes[country]['lat2']
    gef.lon1=bboxes[country]['lon1']
    gef.lon2=bboxes[country]['lon2']

    cmap=gef.cmap

    ds_to_plot=rescaled_forecast.sel(longitude=slice(bboxes[country]['lon1'],bboxes[country]['lon2']),latitude=slice(bboxes[country]['lat1'],bboxes[country]['lat2']))
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=cmap,fontsize=fs,vmax=int(ds_to_plot.quantile(0.99).tp.values))
    plt.savefig(f'plots/{country}/{date_str}/dekadal/dekadal_precip_downscaled.png',bbox_inches='tight')

    dirname=f'data/{date_str}/geotifs_kenya/'
    os.makedirs(dirname,exist_ok=True)
    
    for i,forecast_timestep in enumerate(data_dekade.step.values):
         #get start and end time
        if forecast_timestep == np.atleast_1d(data_dekade.step)[0]:
            start_time=data_dekade.time
            end_time=(data_dekade.time+forecast_timestep)
        else:
            dt=data_dekade.step[1]-data_dekade.step[0]
            start_time=data_dekade.sel(step=forecast_timestep).valid_time-dt
            end_time=data_dekade.sel(step=forecast_timestep).valid_time
        fname=f'downscaled_rainfall_forecast_init_{str(data_dekade.time.values)[0:10]}_{fclim_chirps[2][i]}.tif'

        ds_to_plot.isel(step=i).rio.to_raster(dirname+fname)    

    gdf = gpd.read_file("downscale_data/Kenya_Counties_KNSDI.shp").set_crs("EPSG:4326")

    rescaled_forecast = rescaled_forecast.rio.write_crs("EPSG:4326")

    # Reproject shapefile
    gdf = gdf.to_crs(rescaled_forecast.rio.crs)

    # Clip
    ds_to_plot = rescaled_forecast.rio.clip(gdf.geometry, gdf.crs, drop=True)
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=cmap,fontsize=fs,vmax=int(ds_to_plot.quantile(0.99).tp.values))
    plt.savefig(f'plots/{country}/{date_str}/dekadal/dekadal_precip_downscaled_clipped.png',bbox_inches='tight')

except ValueError:
    print('these are not the days you are looking for...')