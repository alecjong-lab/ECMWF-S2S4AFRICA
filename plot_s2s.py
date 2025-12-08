import sys
import xarray as xr
import get_ECMWF_functions as gef
import xarray as xr
import cfgrib
import matplotlib.pyplot as plt

with open("latest_file.txt") as f:
    grib_file = f.read().strip()

print("Plotting from:", grib_file)
data=xr.open_dataset(grib_file,engine='cfgrib')

diff_data = xr.DataArray(
    data.isel(step=slice(1,None)).tp.values - data.isel(step=slice(0,6)).tp.values,
    coords={'latitude':data.latitude,'longitude':data.longitude,'step': data.step[1:],'time':data.time},  # new step coordinate
    dims=data.dims
).clip(min=0)
diff_data=diff_data.to_dataset(name='tp')
diff_data.tp.attrs=data.tp.attrs


bboxes = {
    "Namibia": {"lat1": -15, "lon1": 10, "lat2": -31, "lon2": 27},
    "Botswana": {"lat1": -15, "lon1": 18, "lat2": -28, "lon2": 31},
    "Kenya": {"lat1": 7, "lon1": 32, "lat2": -6, "lon2": 43},
    "Zambia": {"lat1": -6, "lon1": 20, "lat2": -20, "lon2": 35},
    "Madagascar": {"lat1": -10, "lon1": 42, "lat2": -27, "lon2": 52}
}

import os
import re
from datetime import date, timedelta, datetime
import numpy as np

HOME=os.getcwd()

folder_path = f'{HOME}/m-climate/'
    
# List all files
files = os.listdir(folder_path)

# Define a regex pattern to extract dates (assuming 'yiping_cd_YYYY-MM-DD.nc' format)
pattern = re.compile(r"yiping_cd_(\d{4})-(\d{2})-(\d{2})\.nc")

# Extract month and day, ignoring year
file_dates = []
file_list = []

for file in files:
    match = pattern.search(file)
    if match:
        month, day = int(match.group(2)), int(match.group(3))
        # Normalize all dates to the year 2000
        date_obj = datetime(2000, month, day)
        file_dates.append(date_obj)
        file_list.append(file)  # Keep track of valid files

# Convert file_dates to NumPy datetime64 for easier comparison
file_dates_np = np.array(file_dates, dtype="datetime64")

today = datetime.today()
two_days_earlier = today - timedelta(days=2)

# Specify the target date (only considering month and day)
target_date =two_days_earlier # Example target date
target_date_md = target_date_md = datetime(2000, target_date.month, target_date.day)

# Find the index of the closest date
closest_index = np.argmin(np.abs(file_dates_np - np.datetime64(target_date_md)))

# Get the closest file
closest_file = file_list[closest_index]

print(f"Model climatology starting on: {closest_file[10:20]}")

#open climatology
path=f'{HOME}/m-climate/'
file= closest_file
m_climate_big = xr.open_dataset(path+file, engine="netcdf4",decode_timedelta=True)

major_cities = {
    "Namibia": [(-22.5594, 17.0832), (-17.9333, 19.7667)],         # Windhoek, Rundu :contentReference[oaicite:0]{index=0}
    "Botswana": [(-24.6545, 25.9086), (-21.1700, 27.5000)],       # Gaborone, Francistown :contentReference[oaicite:1]{index=1}
    "Kenya": [(-1.28333, 36.8167), (-4.0547, 39.6636)],            # Nairobi, Mombasa :contentReference[oaicite:2]{index=2}
    "Zambia": [(-15.4067, 28.2871), (-12.80243, 28.21323)],        # Lusaka, Kitwe :contentReference[oaicite:3]{index=3}
    "Madagascar": [(-18.9137, 47.5361), (-18.1500, 49.4000)]       # Antananarivo, Toamasina  :contentReference[oaicite:4]{index=4}
}

for country in bboxes.keys():
    m_climate=m_climate_big.sel(longitude=slice([country]['lon1'], [country]['lon2']),latitude=slice([country]['lat1'], [country]['lat1']))
    gef.lat1=bboxes[country]['lat1']
    gef.lat2=bboxes[country]['lat2']
    gef.lon1=bboxes[country]['lon1']
    gef.lon2=bboxes[country]['lon2']

    ds_to_plot=diff_data.sel(longitude=slice(bboxes[country]['lon1'],bboxes[country]['lon2']),latitude=slice(bboxes[country]['lat1'],bboxes[country]['lat2']))
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='Blues',fontsize=12)
    plt.savefig(f'plots/{country}/weekly_precip.png',bbox_inches='tight')

    gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='seismic',change=True,weekly=True)
    plt.savefig(f'plots/{country}/weekly_change_in_precip.png',bbox_inches='tight')

    chance_to_exceed=gef.chance_to_exceed_mclimate(ds_to_plot,quantile=75,m_climate=m_climate)
    gef.panel_plot_variable(chance_to_exceed,'tp',chance_to_exceed.step.values,cmap='Blues',fontsize=16)
    plt.savefig(f'plots/{country}/75th_percentile_exedance_precip.png',bbox_inches='tight')

    chance_to_exceed=gef.chance_to_exceed_mclimate(ds_to_plot,quantile=50,m_climate=m_climate)
    gef.panel_plot_variable(chance_to_exceed,'tp',chance_to_exceed.step.values,cmap='Blues',fontsize=16)
    plt.savefig(f'plots/{country}/50th_percentile_exedance_precip.png',bbox_inches='tight')

    chance_to_exceed=gef.chance_to_exceed_mclimate(ds_to_plot,quantile=25,m_climate=m_climate)
    gef.panel_plot_variable(chance_to_exceed,'tp',chance_to_exceed.step.values,cmap='Reds_r',fontsize=16)
    plt.savefig(f'plots/{country}/25th_percentile_exedance_precip.png',bbox_inches='tight')

    anom_clim=gef.anomaly_from_mclimate(ds_to_plot,quantile=50,m_climate=m_climate)
    gef.panel_plot_variable(anom_clim,'tp',anom_clim.step.values,cmap='RdBu',fontsize=16)
    plt.savefig(f'plots/{country}/anomaly_from_50th.png',bbox_inches='tight')

    anom_clim=gef.anomaly_from_mclimate(ds_to_plot,quantile=75,m_climate=m_climate)
    gef.panel_plot_variable(anom_clim,'tp',anom_clim.step.values,cmap='RdBu',fontsize=16)
    plt.savefig(f'plots/{country}/anomaly_from_75th.png',bbox_inches='tight')

    anom_clim=gef.anomaly_from_mclimate(ds_to_plot,quantile=25,m_climate=m_climate)
    gef.panel_plot_variable(anom_clim,'tp',anom_clim.step.values,cmap='RdBu',fontsize=16)
    plt.savefig(f'plots/{country}/anomaly_from_25th.png',bbox_inches='tight')

    tercile_clim=gef.tercile_from_mclimate(ds_to_plot,'tp',category_choice='near-normal',m_climate=m_climate)
    gef.panel_plot_variable(tercile_clim,'tp',tercile_clim.step.values,cmap='rainbow')
    plt.savefig(f'plots/{country}/chance_of_near_normal.png',bbox_inches='tight')

    tercile_clim=gef.tercile_from_mclimate(ds_to_plot,'tp',category_choice='below-normal',m_climate=m_climate)
    gef.panel_plot_variable(tercile_clim,'tp',tercile_clim.step.values,cmap='rainbow')
    plt.savefig(f'plots/{country}/chance_of_below_normal.png',bbox_inches='tight')

    tercile_clim=gef.tercile_from_mclimate(ds_to_plot,'tp',category_choice='above-normal',m_climate=m_climate)
    gef.panel_plot_variable(tercile_clim,'tp',tercile_clim.step.values,cmap='rainbow')
    plt.savefig(f'plots/{country}/chance_of_above_normal.png',bbox_inches='tight')

    latf,lonf=major_cities[country][0][0],major_cities[country][0][1]
    gef.meteogram_double(ds_to_plot,m_climate,lat=latf,lon=lonf)
    plt.savefig(f'plots/{country}/meteogram_biggest_city.png',bbox_inches='tight')

    lats,lons=major_cities[country][0][0],major_cities[country][0][1]
    gef.meteogram_double(ds_to_plot,m_climate,lat=lats,lon=lons)
    plt.savefig(f'plots/{country}/meteogram_second_biggest_city.png',bbox_inches='tight')


