import xarray as xr
import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt
import os
from datetime import datetime, timedelta

today = datetime.today()
two_days_earlier = today - timedelta(days=2)
date_str = two_days_earlier.strftime("%Y-%m-%d")

with open("latest_file.txt") as f:
    grib_file = f.read().split("\n")

pf=xr.open_dataset(grib_file[0],engine='cfgrib')
cf=xr.open_dataset(grib_file[1],engine='cfgrib').assign_coords({'number':0})

data=xr.concat([pf,cf],dim='number')

steps=data.step.values*1e-9/3600
steps=steps.astype('int')
dekade = [data.step.values[i] for i in np.where(steps%240==0)[0]]
weekly=[data.step.values[i] for i in np.where(steps%168==0)[0]]

data_weekly=data.sel(step=weekly)
data_dekade=data.sel(step=dekade)

data_weekly=gef.acum_to_instant(data_weekly)
data_dekade=gef.acum_to_instant(data_dekade)

data_weekly.to_netcdf(f'data/{date_str}/data_weekly.nc')
data_dekade.to_netcdf(f'data/{date_str}/data_dekade.nc')

bboxes = {
    "Namibia": {"lat1": -15, "lon1": 10, "lat2": -31, "lon2": 27},
    "Botswana": {"lat1": -15, "lon1": 18, "lat2": -28, "lon2": 31},
    "Kenya": {"lat1": 7, "lon1": 32, "lat2": -6, "lon2": 43},
    "Zambia": {"lat1": -6, "lon1": 20, "lat2": -20, "lon2": 35},
    "Madagascar": {"lat1": -10, "lon1": 42, "lat2": -27, "lon2": 52},
    "Angola": {"lat1": -5, "lon1": 12, "lat2": -18, "lon2": 24}
}

m_climate_big = gef.open_mclimate(data_weekly)

major_cities = {
    "Namibia": [(-22.5594, 17.0832), (-17.9333, 19.7667), ('Windhoek', 'Rundu')],         # Windhoek, Rundu
    "Botswana": [(-24.6545, 25.9086), (-21.1700, 27.5000), ('Gaborone', 'Francistown')],   # Gaborone, Francistown
    "Kenya": [(-1.28333, 36.8167), (-4.0547, 39.6636), ('Nairobi', 'Mombasa')],            # Nairobi, Mombasa
    "Zambia": [(-15.4067, 28.2871), (-12.80243, 28.21323), ('Lusaka', 'Kitwe')],           # Lusaka, Kitwe
    "Madagascar": [(-18.9137, 47.5361), (-18.1500, 49.4000), ('Antananarivo', 'Toamasina')],  # Antananarivo, Toamasina
    "Angola": [(-8.8368, 13.2343), (-11.2027, 17.8739), ('Luanda', 'Huambo')]              # Luanda, Huambo
}

diff_data=data_weekly

for country in bboxes.keys():
    gef.lat1=bboxes[country]['lat1']
    gef.lat2=bboxes[country]['lat2']
    gef.lon1=bboxes[country]['lon1']
    gef.lon2=bboxes[country]['lon2']

    m_climate=m_climate_big.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))

    if country=='Madagascar':
        fs=12
    else:
        fs=16

    base_path=f'plots/{country}/{date_str}'
    weekly_path=f'{base_path}/weekly'
    dekade_path=f'{base_path}/dekadal'

    os.makedirs(base_path, exist_ok=True)
    os.makedirs(weekly_path, exist_ok=True)
    os.makedirs(dekade_path, exist_ok=True)

    ds_to_plot=diff_data.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_precip.png',bbox_inches='tight')

    ds_to_plot_dekade=data_dekade.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot_dekade,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/dekadal_precip.png',bbox_inches='tight')

    exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,var,20,comparison='greater')
    fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/chance_higherthan_20mm.png',bbox_inches='tight')

    exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,var,25,comparison='greater')
    fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/chance_higherthan_25mm.png',bbox_inches='tight')

    gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='seismic',change=True,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_change_in_precip.png',bbox_inches='tight')

    quantiles=[75,50,25]

    for quantile in quantiles:
        chance_to_exceed=gef.chance_to_exceed_mclimate(ds_to_plot,quantile=quantile,m_climate=m_climate)
        gef.panel_plot_variable(chance_to_exceed,'tp',chance_to_exceed.step.values,cmap='Blues',fontsize=fs)
        plt.savefig(f'{weekly_path}/{quantile}th_percentile_exedance_precip.png',bbox_inches='tight')

        anom_clim=gef.anomaly_from_mclimate(ds_to_plot,quantile=quantile,m_climate=m_climate)
        gef.panel_plot_variable(anom_clim,'tp',anom_clim.step.values,cmap='RdBu',fontsize=fs)
        plt.savefig(f'{weekly_path}/anomaly_from_{quantile}th.png',bbox_inches='tight')

    tercil_cats=['near-normal','below-normal','above-normal']

    for cat in tercil_cats:
        tercile_clim=gef.tercile_from_mclimate(ds_to_plot,'tp',category_choice=cat,m_climate=m_climate)
        gef.panel_plot_variable(tercile_clim,'tp',tercile_clim.step.values,cmap='rainbow',fontsize=fs)
        plt.savefig(f'{weekly_path}/chance_of_{cat}.png',bbox_inches='tight')

    for i in range(2):
        latf,lonf=major_cities[country][i][0],major_cities[country][i][1]
        gef.meteogram_double(ds_to_plot,m_climate,lat=latf,lon=lonf)
        plt.savefig(f'{weekly_path}/meteogram_{major_cities[country][2][i]}.png',bbox_inches='tight')
