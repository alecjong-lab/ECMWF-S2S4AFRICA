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

dailyvars=gef.open_forecast(date_str,'CAPE_tcw_t2m_d2m_RH')
Tminmax=gef.open_forecast(date_str,'Tminmax')
wind10=gef.open_forecast(date_str,'10wind')
wind500=gef.open_forecast(date_str,'500wind')
wind700=gef.open_forecast(date_str,'700wind')

week_dailyvars=gef.week_mean(dailyvars)
week_6hTminmax=gef.week_mean(gef.day_mean_6h_accum(Tminmax,['mx2t6', 'mn2t6']))
week_wind10=gef.week_mean(gef.day_mean(wind10))
week_wind500=gef.week_mean(wind500.assign_coords(step=[stepp + 86400000000000 for stepp in wind500.step.values ])).rename({"isobaricInhPa":"level"})
week_wind700=gef.week_mean(wind700.assign_coords(step=[stepp + 86400000000000 for stepp in wind500.step.values ])).rename({"isobaricInhPa":"level"})

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
    plt.close()

    ds_to_plot_monthly=data_monthly.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot_monthly,variable='tp',forecast_timestep=ds_to_plot_monthly.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{monthly_path}/monthly_precip.png',bbox_inches='tight')
    plt.close()

    ds_to_plot_dekade=data_dekade.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
    fig=gef.panel_plot_variable(ds_to_plot_dekade,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
    plt.savefig(f'{dekade_path}/dekadal_precip.png',bbox_inches='tight')
    plt.close()

    gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='seismic',change=True,fontsize=fs)
    plt.savefig(f'{weekly_path}/weekly_change_in_precip.png',bbox_inches='tight')
    plt.close()

    if country=='Kenya':
        exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,'tp',20,comparison='greater')
        fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{dekade_path}/chance_higherthan_20mm.png',bbox_inches='tight')
        plt.close()

        exceedance_percentage=gef.get_exceedance_percentage(ds_to_plot_dekade,'tp',25,comparison='greater')
        fig=gef.panel_plot_variable(exceedance_percentage,variable='tp',forecast_timestep=ds_to_plot_dekade.step.values,cmap=gef.cmap,fontsize=fs)
        plt.savefig(f'{dekade_path}/chance_higherthan_25mm.png',bbox_inches='tight')
        plt.close()

        #-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
        inputs=['tcw','t2m','d2m','cape']
        cmaps=['YlGnBu','rainbow','YlGnBu','jet']

        mclim=gef.open_mclimate(week_dailyvars,var="CAPE_tcw_t2m_d2m_RH").sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))

        for i,var in enumerate(inputs):
            save_path=f'{weekly_path}/{var}/'
            if var=='t2m':
                ds_to_plot_var=gef.convert_to_celcius(week_dailyvars,'t2m')
                mclim_var=gef.convert_to_celcius(mclim,'t2m')
            else:
                ds_to_plot_var=week_dailyvars
                mclim_var=mclim

            ds_to_plot_var=ds_to_plot_var.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
            gef.ensemble_plots(ds_to_plot=ds_to_plot_var,m_climate=mclim_var,var=var,save_path=save_path,country='Kenya',fontsize=fs,major_cities=major_cities)

            fig=gef.panel_plot_variable(ds_to_plot_var,variable=var,forecast_timestep=ds_to_plot_var.step.values,cmap=cmaps[i],fontsize=fs)
            plt.savefig(f'{save_path}/{var}.png',bbox_inches='tight')
            plt.close()

        ds_to_plot_var=ds_to_plot_var.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        gef.ensemble_plots(ds_to_plot=ds_to_plot_var,m_climate=mclim_var,var=var,save_path=save_path,country='Kenya',fontsize=fs,major_cities=major_cities)
        #------------winds 500hPa--------------------------------------------------------------------------------------------------------------------------------------------------------
        mclim=gef.open_mclimate(week_wind500,var="700_500_wind_new").sel(level=500).sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        var='w'
        save_path=f'{weekly_path}/w_500hPa/'
        ds_to_plot_var=week_wind500.sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))

        gef.ensemble_plots(ds_to_plot=ds_to_plot_var,m_climate=mclim,var=var,save_path=save_path,country='Kenya',fontsize=fs,major_cities=major_cities)

        fig=gef.panel_plot_variable(ds_to_plot_var,variable=var,forecast_timestep=ds_to_plot_var.step.values,cmap='seismic',fontsize=fs)
        plt.savefig(f'{save_path}/{var}.png',bbox_inches='tight')
        plt.close()

        #----------winds 10m----------------------------------------------------------------------------------------------------------------------------------------------------------
        save_path=f'{weekly_path}/10m-wind/'
        week_wind10_speed=gef.windspeed(week_wind10,'u10','v10').sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))

        mclim=gef.open_mclimate(week_wind10,var="10m_wind").sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        mclim_speed=gef.windspeed(mclim,'u10','v10')
        gef.ensemble_plots_quiver(week_wind10_speed,mclim_speed,'u10','u10','v10',save_path,'Kenya',fs,major_cities)
        gef.ensemble_plots_quiver(week_wind10_speed,mclim_speed,'v10','u10','v10',save_path,'Kenya',fs,major_cities)

        fig=gef.quiver_plot_variable(week_wind10_speed,"u10","v10",week_wind10["step"],cmap='YlGn')
        plt.savefig(f'{save_path}/10m-wind_vectors.png',bbox_inches='tight')
        plt.close()

        #----------winds 700hPa----------------------------------------------------------------------------------------------------------------------------------------------------------
        save_path=f'{weekly_path}/700hpa-wind/'

        week_wind700_speed=gef.windspeed(week_wind700,'u','v').sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))

        mclim=gef.open_mclimate(week_wind700_speed,var="700_500_wind_new").sel(level=700).sel(longitude=slice(gef.lon1, gef.lon2),latitude=slice(gef.lat1, gef.lat2))
        mclim_speed=gef.windspeed(mclim,'u','v')
        gef.ensemble_plots_quiver(week_wind700_speed,mclim_speed,'u','u','v',save_path,'Kenya',fs,major_cities)
        gef.ensemble_plots_quiver(week_wind700_speed,mclim_speed,'v','u','v',save_path,'Kenya',fs,major_cities)

        fig=gef.quiver_plot_variable(week_wind700_speed,"u","v",week_wind700_speed["step"],cmap='YlGn')
        plt.savefig(f'{save_path}/700hPa-wind_vectors.png',bbox_inches='tight')
        plt.close()
        #--------------temp-----------------------------------------------------------------------------------------------------------------------------------------------------
        vmaxt6h=gef.convert_to_celcius(week_6hTminmax,'mx2t6').mean('number').mx2t6.max()
        vmint6h=gef.convert_to_celcius(week_6hTminmax,'mn2t6').mean('number').mn2t6.min()

        week_6hTminmax.mx2t6.attrs['GRIB_name']='Weekly mean maximum temperature at 2 metres'
        week_6hTminmax.mn2t6.attrs['GRIB_name']='Weekly mean minimum temperature at 2 metres'

        fig=gef.panel_plot_variable(gef.convert_to_celcius(week_6hTminmax,'mx2t6'),variable='mx2t6',forecast_timestep=week_6hTminmax.step.values,cmap='rainbow',fontsize=fs,vmax=vmaxt6h,vmin=vmint6h)
        plt.savefig(f'{weekly_path}/t2m/max_temp.png',bbox_inches='tight')
        plt.close()

        fig=gef.panel_plot_variable(gef.convert_to_celcius(week_6hTminmax,'mn2t6'),variable='mn2t6',forecast_timestep=week_6hTminmax.step.values,cmap='rainbow',fontsize=fs,vmax=vmaxt6h,vmin=vmint6h)
        plt.savefig(f'{weekly_path}/t2m/min_temp.png',bbox_inches='tight')
        plt.close()
        
    if country!="Senegal":

        print(country)

        fig=gef.panel_plot_variable(efi,variable='tp',forecast_timestep=efi.step.values,vmax=1,vmin=0.5,cmap=gef.cmap_efi,add_contour=sot.tp,contourlevels=[0,1,2,5,8],contourcmap='k',fontsize=fs)
        plt.savefig(f'{weekly_path}/efi_sot_precip.png',bbox_inches='tight')
        plt.close()

        gef.ensemble_plots(ds_to_plot,m_climate,'tp',weekly_path,country=country,fontsize=fs,major_cities=major_cities)

# efi,sot = efi_sot.EFI_SOT(data_path_pf, filelist_path, weekly_path)

# for step in range(len(efi["step"])):
#     efi_sot.plot_map_EFI(efi.isel(step=step,time=0).drop_vars("time"),title="Extreme Forecast Index (EFI) for precipitation",
#             cbar_title_upper="EFI for dry events", cbar_title_lower="EFI for wet events",)
#     plt.savefig(f'{weekly_path}/EFI_step_{step}.png',bbox_inches='tight')

#     #print(sot)
#     efi_sot.plot_map_SOT(sot.isel(step=step,time=0))
#     plt.title("Shift of tails (SOT) for precipitation")
#     plt.savefig(f'{weekly_path}/SOT_step_{step}.png',bbox_inches='tight')