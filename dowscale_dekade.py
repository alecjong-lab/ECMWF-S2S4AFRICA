import xarray as xr
import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt

data_dekade=xr.open_dataset('data/data_dekade.nc')

#####change after test######################
month=2#int(data_dekade.time.dt.month.values)
day=17#int(data_dekade.time.dt.day.values)+7

forecast_files = {
    (2, 17): ["ECMWF_tp_forecasts_02-17-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_06_Kenya.nc"],
    (2, 27): ["ECMWF_tp_forecasts_02-27-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_07_Kenya.nc"],
    (3, 9): ["ECMWF_tp_forecasts_03-09-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_08_Kenya.nc"],
    (3, 19): ["ECMWF_tp_forecasts_03-19-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_09_Kenya.nc"],
    (3, 31): ["ECMWF_tp_forecasts_03-31-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_10_Kenya.nc"],
    (4, 9): ["ECMWF_tp_forecasts_04-09-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_11_Kenya.nc"],
    (4, 19): ["ECMWF_tp_forecasts_04-19-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_12_Kenya.nc"],
    (4, 29): ["ECMWF_tp_forecasts_04-29-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_13_Kenya.nc"],
}

try:
    keys = list(forecast_files)
    start = keys.index((month, day))
    fclim_chirps = np.array([forecast_files[k] for k in keys[start:start+4]]).T
except KeyError:
    print('not the right day, will have to wait patiently...')

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

rescaled_forecast=gef.rank_upscale_and_align(extended_fclim.tp.isel(year=slice(1,None)),chirps_dekades_ds.tp)
rescaled_forecast=rescaled_forecast.assign_coords({'time':extended_fclim.time,'valid_time':extended_fclim.valid_time}).to_dataset(name='tp')
rescaled_forecast.tp.attrs=data_dekade.tp.attrs

fs=12
country='Kenya'

gef.lat1=bboxes[country]['lat1']
gef.lat2=bboxes[country]['lat2']
gef.lon1=bboxes[country]['lon1']
gef.lon2=bboxes[country]['lon2']

ds_to_plot=rescaled_forecast.sel(longitude=slice(bboxes[country]['lon1'],bboxes[country]['lon2']),latitude=slice(bboxes[country]['lat1'],bboxes[country]['lat2']))
fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='rainbow',fontsize=fs,vmax=int(ds_to_plot.quantile(0.99).tp.values))
plt.savefig(f'plots/{country}/dekadal/dekadal_precip_downscaled.png',bbox_inches='tight')