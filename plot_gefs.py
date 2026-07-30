import xarray as xr
import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt
import os
from datetime import datetime, timedelta

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix=os.environ["MAIN_PATH"]
data_path=f'{prefix}/data/{date_str}'

countries=os.environ["COUNTRIES"].split(',')

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

for country in countries:
    if country=='Madagascar':
        fs=12
    if country=='Malawi':
        fs=14
    else:
        fs=16

    base_path=f'plots/{country}/{date_str}'
    weekly_path=f'{base_path}/weekly'
    dekade_path=f'{base_path}/dekadal'
    monthly_path=f'{base_path}/monthly'

    os.makedirs(base_path, exist_ok=True)
    os.makedirs(weekly_path, exist_ok=True)
    os.makedirs(dekade_path, exist_ok=True)
    os.makedirs(monthly_path, exist_ok=True)

    gef.lat1=bboxes[country]['lat1']
    gef.lat2=bboxes[country]['lat2']
    gef.lon1=bboxes[country]['lon1']
    gef.lon2=bboxes[country]['lon2']

    gefs_data=xr.open_zarr(data_path+f'/gefs_{country. lower()}.zarr')
    gefs_data=gefs_data.cumsum(dim='step').assign_coords({'step':gefs_data.step})
    steps=(gefs_data.step.values*1e-9/3600).astype('int')
    dekade = [gefs_data.step.values[i] for i in np.where(steps%240==0)[0]]
    weekly=[gefs_data.step.values[i] for i in np.where(steps%168==0)[0]]

    gefs_monthly=gefs_data.sel(step=weekly).isel(step=4)
    gefs_weekly=gef.accum_to_instant(gefs_data.sel(step=weekly))
    gefs_dekade=gef.accum_to_instant(gefs_data.sel(step=dekade))
    
    ds_to_plot=gefs_weekly
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{weekly_path}/gefs_weekly_precip.png',bbox_inches='tight')
    plt.close()

    ds_to_plot=gefs_dekade
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/gefs_dekade_precip.png',bbox_inches='tight')
    plt.close()

    ds_to_plot=gefs_monthly
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{monthly_path}/gefs_monthly_precip.png',bbox_inches='tight')
    plt.close()