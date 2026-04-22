import xarray as xr
import get_ECMWF_functions as gef
import efi_sot
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt
import os
from datetime import datetime, timedelta

today = datetime.today()
two_days_earlier = today - timedelta(days=2)
date_str = two_days_earlier.strftime("%Y-%m-%d")
prefix = "./"

data_path_pf = f"{prefix}data/{date_str}/ECMWF_s2s_pf_precip_forecast_weekly-and-dekade_23N-20W-37S-59E.grib"
data_path_cf = f"{prefix}data/{date_str}/ECMWF_s2s_cf_precip_forecast_weekly-and-dekade_23N-20W-37S-59E.grib"
filelist_path = f"{prefix}m-climate/*.nc"

pf=xr.open_dataset(data_path_pf,engine='cfgrib')
cf=xr.open_dataset(data_path_cf,engine='cfgrib').assign_coords({'number':0})

data=xr.concat([pf,cf],dim='number')

pf_other_var=xr.open_dataset(f"{prefix}data/{date_str}/ECMWF_s2s_pf_othervars_forecast_42days_7N-32E-6S-43E.grib",engine='cfgrib')
cf_other_var=xr.open_dataset(f"{prefix}data/{date_str}/ECMWF_s2s_cf_othervars_forecast_42days_7N-32E-6S-43E.grib",engine='cfgrib')

data_other_var=xr.concat([pf_other_var,cf_other_var],dim='number')
data_other_var_averaged_week=gef.week_mean(data_other_var)

pf_instant = xr.open_dataset(f"{prefix}data/{date_str}/ECMWF_s2s_pf_instant_forecast_42days_7N-32E-6S-43E.grib",engine='cfgrib')
cf_instant = xr.open_dataset(f"{prefix}data/{date_str}/ECMWF_s2s_cf_instant_forecast_42days_7N-32E-6S-43E.grib",engine='cfgrib')

data_instant = xr.concat([pf_instant,cf_instant],dim='number')
data_instant_averaged_week=gef.week_mean(data_instant)

pf_levels = xr.open_dataset(f"{prefix}data/{date_str}/ECMWF_s2s_pf_levels_forecast_42days_7N-32E-6S-43E.grib",engine='cfgrib')
cf_levels = xr.open_dataset(f"{prefix}data/{date_str}/ECMWF_s2s_cf_levels_forecast_42days_7N-32E-6S-43E.grib",engine='cfgrib')

data_levels = xr.concat([pf_levels,cf_levels],dim='number')
data_levels = data_levels.rename({"isobaricInhPa":"level"})
data_levels_averaged_week=gef.week_mean(data_levels)

steps=data.step.values*1e-9/3600
steps=steps.astype('int')
dekade = [data.step.values[i] for i in np.where(steps%240==0)[0]]
weekly=[data.step.values[i] for i in np.where(steps%168==0)[0]]

data_weekly=data.sel(step=weekly)
data_dekade=data.sel(step=dekade)

data_monthly=data_weekly.isel(step=4)
data_weekly=gef.acum_to_instant(data_weekly)
data_dekade=gef.acum_to_instant(data_dekade)

data_weekly.to_netcdf(f'{prefix}data/{date_str}/data_weekly.nc')
data_dekade.to_netcdf(f'{prefix}data/{date_str}/data_dekade.nc')
data_monthly.to_netcdf(f'{prefix}data/{date_str}/data_monthly.nc')

bboxes = {
    "Namibia": {"lat1": -15, "lon1": 10, "lat2": -31, "lon2": 27},
    "Botswana": {"lat1": -15, "lon1": 18, "lat2": -28, "lon2": 31},
    "Kenya": {"lat1": 7, "lon1": 32, "lat2": -6, "lon2": 43},
    "Zambia": {"lat1": -6, "lon1": 20, "lat2": -20, "lon2": 35},
    "Madagascar": {"lat1": -10, "lon1": 42, "lat2": -27, "lon2": 52},
    "Angola": {"lat1": -4, "lon1": 11.5, "lat2": -18.5, "lon2": 24.5},
    "Ghana": {"lat1": 12, "lon1": -4, "lat2": 4, "lon2": 2},
    "Senegal": {"lat1": 17, "lon1": -17.5, "lat2": 12, "lon2": -11},
    "Ethiopia": {"lat1": 16, "lon1": 32, "lat2": 2, "lon2": 49},
 }

m_climate_big = gef.open_mclimate(data_weekly)

efi,sot = efi_sot.EFI_SOT(data_weekly, m_climate_big)


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
}

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
    monthly_path=f'{base_path}/monthly'

    os.makedirs(base_path, exist_ok=True)
    os.makedirs(weekly_path, exist_ok=True)
    os.makedirs(dekade_path, exist_ok=True)
    os.makedirs(monthly_path, exist_ok=True)

    ds_to_plot=data_weekly.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_precip.png',bbox_inches='tight')

    ds_to_plot_monthly=data_monthly.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot_monthly,variable='tp',forecast_timestep=ds_to_plot_monthly.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{monthly_path}/monthly_precip.png',bbox_inches='tight')

    ds_to_plot_dekade=data_dekade.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot_dekade,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/dekadal_precip.png',bbox_inches='tight')

    if country=='Kenya':
        exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,'tp',20,comparison='greater')
        fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{dekade_path}/chance_higherthan_20mm.png',bbox_inches='tight')

        exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,'tp',25,comparison='greater')
        fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{dekade_path}/chance_higherthan_25mm.png',bbox_inches='tight')

        fig=gef.panel_plot_variable(gef.convert_to_celcius(data_other_var_averaged_week,'t2m'),variable='t2m',forecast_timestep=data_other_var_averaged_week.step.values,cmap='rainbow',fontsize=fs)
        plt.savefig(f'{weekly_path}/t2m.png',bbox_inches='tight')

        fig=gef.panel_plot_variable(data_other_var_averaged_week,variable='cape',forecast_timestep=data_other_var_averaged_week.step.values,cmap='jet',fontsize=fs)
        plt.savefig(f'{weekly_path}/cape.png',bbox_inches='tight')

        fig=gef.panel_plot_variable(data_other_var_averaged_week,variable='tcw',forecast_timestep=data_other_var_averaged_week.step.values,cmap='YlGnBu',fontsize=fs)
        plt.savefig(f'{weekly_path}/tcw.png',bbox_inches='tight')

        fig=gef.panel_plot_variable(data_levels_averaged_week,variable='w',forecast_timestep=data_levels_averaged_week.step.values,cmap='YlGnBu',fontsize=fs,level=500)
        plt.savefig(f'{weekly_path}/w_500.png',bbox_inches='tight')

        fig=gef.quiver_plot_variable(data_instant_averaged_week,"u10","v10",data_instant_averaged_week["step"])
        plt.savefig(f'{weekly_path}/quiver_u10_v10.png',bbox_inches='tight')

        fig=gef.quiver_plot_variable(data_levels_averaged_week,"u","v",data_levels_averaged_week["step"],level=700)
        plt.savefig(f'{weekly_path}/quiver_u_v_700_hPa.png',bbox_inches='tight')

    gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='seismic',change=True,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_change_in_precip.png',bbox_inches='tight')

    if country!="Senegal":

        print(country)

        fig=gef.panel_plot_variable(efi,variable='tp',forecast_timestep=efi.step.values,vmax=1,vmin=0.5,cmap=gef.cmap_efi,add_contour=sot.tp,contourlevels=[0,1,2,5,8],contourcmap='k',fontsize=fs)
        plt.savefig(f'{weekly_path}/efi_sot_precip.png',bbox_inches='tight')

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

# efi,sot = efi_sot.EFI_SOT(data_path_pf, filelist_path, weekly_path)

# for step in range(len(efi["step"])):
#     efi_sot.plot_map_EFI(efi.isel(step=step,time=0).drop_vars("time"),title="Extreme Forecast Index (EFI) for precipitation",
#             cbar_title_upper="EFI for dry events", cbar_title_lower="EFI for wet events",)
#     plt.savefig(f'{weekly_path}/EFI_step_{step}.png',bbox_inches='tight')

#     #print(sot)
#     efi_sot.plot_map_SOT(sot.isel(step=step,time=0))
#     plt.title("Shift of tails (SOT) for precipitation")
#     plt.savefig(f'{weekly_path}/SOT_step_{step}.png',bbox_inches='tight')