import get_ECMWF_functions as gef
import xarray as xr
import numpy as np
import geopandas as gpd
import rioxarray
from datetime import datetime, timedelta
import os
import pandas as pd
import regionmask

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix=os.environ["MAIN_PATH"]

data_path=f'{prefix}/data/{date_str}'
data=xr.open_zarr(f'{data_path}/ECMWF_s2s_precip_{date_str}.zarr',consolidated=True).compute()

data_dekade=xr.open_dataset(f'data/{date_str}/data_dekade.nc')
month=int(data_dekade.time.dt.month.values)
day=int(data_dekade.time.dt.day.values)

districts=gpd.read_file("Kenya_shapes/ken_admin2.shp")
states1=gpd.read_file("Kenya_shapes/ken_admin1.shp")

kenya_counties_shp = "downscale_data/Kenya_Counties_KNSDI.shp"

region_map = {
    "Nyandarua": "Highlands East of the Rift Valley","Laikipia": "Highlands East of the Rift Valley","Nyeri": "Highlands East of the Rift Valley",
    "Kirinyaga": "Highlands East of the Rift Valley","Murang'a": "Highlands East of the Rift Valley","Kiambu": "Highlands East of the Rift Valley",
    "Meru": "Highlands East of the Rift Valley","Embu": "Highlands East of the Rift Valley","Tharaka-Nithi": "Highlands East of the Rift Valley",
    "Nairobi": "Highlands East of the Rift Valley","Nandi": "Highlands West of the Rift Valley","Kakamega": "Highlands West of the Rift Valley",
    "Vihiga": "Highlands West of the Rift Valley","Bungoma": "Highlands West of the Rift Valley","Siaya": "Highlands West of the Rift Valley",
    "Busia": "Highlands West of the Rift Valley","Baringo": "Highlands West of the Rift Valley","Nakuru": "Highlands West of the Rift Valley",
    "Trans Nzoia": "Highlands West of the Rift Valley","Uasin Gishu": "Highlands West of the Rift Valley","Elgeyo-Marakwet": "Highlands West of the Rift Valley",
    "West Pokot": "Highlands West of the Rift Valley","Kisii": "Rift Valley and Lake Victoria Basin","Nyamira": "Rift Valley and Lake Victoria Basin",
    "Kericho": "Rift Valley and Lake Victoria Basin","Bomet": "Rift Valley and Lake Victoria Basin","Kisumu": "Rift Valley and Lake Victoria Basin",
    "Homa Bay": "Rift Valley and Lake Victoria Basin","Migori": "Rift Valley and Lake Victoria Basin","Narok": "Rift Valley and Lake Victoria Basin",
    "Mombasa": "Coast","Kilifi": "Coast","Lamu": "Coast","Kwale": "Coast","Marsabit": "Northeastern Kenya","Mandera": "Northeastern Kenya",
    "Wajir": "Northeastern Kenya","Garissa": "Northeastern Kenya","Isiolo": "Northeastern Kenya","Machakos": "Southeastern Lowlands"
    ,"Kitui": "Southeastern Lowlands","Makueni": "Southeastern Lowlands","Kajiado": "Southeastern Lowlands","Taita Taveta": "Southeastern Lowlands",
    "Tana River": "Southeastern Lowlands","Turkana": "Northwestern Kenya","Samburu": "Northwestern Kenya",
}

states1_regions = states1.copy()
states1_regions['region'] = states1_regions['adm1_name'].map(region_map)
regions_kenya = states1_regions.dropna(subset=['region']).dissolve(by='region')

forecast_files = {
    (2, 17): ["ECMWF_tp_forecasts_02-17-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_06_Kenya.nc","Febuary_Dekad3.tif"],
    (2, 27): ["ECMWF_tp_forecasts_02-27-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_07_Kenya.nc","March_Dekad1.tif"],
    (3, 9): ["ECMWF_tp_forecasts_03-09-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_08_Kenya.nc","March_Dekad2.tif"],
    (3, 20): ["ECMWF_tp_forecasts_03-19-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_09_Kenya.nc","March_Dekad3.tif"],
    (4, 1): ["ECMWF_tp_forecasts_03-31-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_10_Kenya.nc","April_Dekad1.tif"],
    (4, 11): ["ECMWF_tp_forecasts_04-09-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_11_Kenya.nc","April_Dekad2.tif"],
    (4, 21): ["ECMWF_tp_forecasts_04-19-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_12_Kenya.nc","April_Dekad3.tif"],
    (5, 1): ["ECMWF_tp_forecasts_04-29-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_13_Kenya.nc","May_Dekad1.tif"],
    (5, 11): ["ECMWF_tp_forecasts_05-11-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_14_Kenya.nc","May_Dekad2"],
    (5, 21): ["ECMWF_tp_forecasts_05-21-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_15_Kenya.nc","May_Dekad3"],
    (6, 1): ["ECMWF_tp_forecasts_06-01-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_16_Great_Horn.nc","June_Dekad1"],
    (6, 11): ["ECMWF_tp_forecasts_06-11-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_17_Great_Horn.nc","June_Dekad2"],
    (6, 21): ["ECMWF_tp_forecasts_06-21-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_sorted_18_Great_Horn.nc","June_Dekad3"],
    (7, 1): ["ECMWF_tp_forecasts_07-01-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_19_Great_Horn.nc","July_Dekad1"],
    (7, 11): ["ECMWF_tp_forecasts_07-11-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_20_Great_Horn.nc","July_Dekad2"],
    (7, 21): ["ECMWF_tp_forecasts_07-21-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_21_Great_Horn.nc","July_Dekad3"],
    (8, 1): ["ECMWF_tp_forecasts_08-01-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_22_Great_Horn.nc","August_Dekad1"],
    (8, 11): ["ECMWF_tp_forecasts_08-11-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_23_Great_Horn.nc","August_Dekad2"],
    (8, 21): ["ECMWF_tp_forecasts_08-21-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_24_Great_Horn.nc","August_Dekad3"],
    (9, 1): ["ECMWF_tp_forecasts_09-01-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_25_Great_Horn.nc","September_Dekad1"],
    (9, 11): ["ECMWF_tp_forecasts_09-11-2025_day2_to_day11_Kenya.nc","chirpsv3_dekads_2005_2025_26_Great_Horn.nc","September_Dekad2"],
}

if (int(month),int(day)) in forecast_files.keys():
    keys = list(forecast_files)
    start = keys.index((month, day))

    dekade_bboxes = {
        "Kenya": {"lat1": 7, "lon1": 33, "lat2": -6, "lon2": 42},
        "kenya_plus":{"lat1": 7.5, "lon1":27, "lat2": -7.5, "lon2": 43},
        "Great_Horn":{"lat1": 25.5, "lon1": 19.5, "lat2": -8, "lon2": 57},
        "Ethiopia": {"lat1": 16, "lon1": 32, "lat2": 2, "lon2": 49},
    }

    country='Great_Horn'

    fclim_chirps = np.array([forecast_files[k] for k in keys[start:start+4]]).T

    reforecast_clims_ds = gef.stack_climatology_steps(
        fclim_chirps[0], data_dekade.step.values, base_path='downscale_data/',
        bbox=dekade_bboxes[country]
    )
    chirps_dekades_ds = gef.stack_climatology_steps(
        fclim_chirps[1], data_dekade.step.values, base_path='downscale_data/',
        apply_rank_sort=True, bbox=dekade_bboxes[country]
    )

    data_to_add=data_dekade.isel(step=slice(None,len(reforecast_clims_ds.step))).assign_coords({"year":int(data_dekade.time.dt.year.values)}).mean('number').sel(longitude=slice(dekade_bboxes[country]['lon1'],dekade_bboxes[country]['lon2']),latitude=slice(dekade_bboxes[country]['lat1'],dekade_bboxes[country]['lat2']))
    extended_fclim=xr.concat([reforecast_clims_ds,data_to_add],dim='year')

    rescaled_forecast = gef.build_rescaled_forecast(extended_fclim, chirps_dekades_ds, data_dekade, sortby_lat=True)
    rescaled_forecast.to_netcdf(f'data/{date_str}/data_dekade_{country}_downscaled.nc')

    anomaly = gef.compute_rainfall_anomaly(rescaled_forecast, chirps_dekades_ds)

    rescaled_forecast=rescaled_forecast.rio.write_crs("EPSG:4326")
    anomaly=anomaly.rio.write_crs("EPSG:4326")

    gef.plot_admin1_county_breakdown(
        rescaled_forecast, chirps_dekades_ds, states1,
        save_dir=f'plots/Kenya/{date_str}/dekadal/counties'
    )

    for country in os.environ["DEKADE_COUNTRIES"].split(','):
        fs=12
        gef.lat1=dekade_bboxes[country]['lat1']
        gef.lat2=dekade_bboxes[country]['lat2']
        gef.lon1=dekade_bboxes[country]['lon1']
        gef.lon2=dekade_bboxes[country]['lon2']

        cmap=gef.cmap

        os.makedirs(f'plots/{country}/{date_str}/dekadal/',exist_ok=True)

        ds_to_plot=rescaled_forecast.sortby('latitude',ascending=False).sel(longitude=slice(dekade_bboxes[country]['lon1'],dekade_bboxes[country]['lon2']),latitude=slice(dekade_bboxes[country]['lat1'],dekade_bboxes[country]['lat2'])).transpose('latitude', 'longitude','step')
        gef.plot_panel_and_save(
            ds_to_plot,'tp',cmap,fs,
            f'plots/{country}/{date_str}/dekadal/dekadal_precip_downscaled.png',
            vmax=int(ds_to_plot.quantile(0.99).tp.values)
        )

        ds_to_plot_anom=anomaly.sortby('latitude',ascending=False).sel(longitude=slice(dekade_bboxes[country]['lon1'],dekade_bboxes[country]['lon2']),latitude=slice(dekade_bboxes[country]['lat1'],dekade_bboxes[country]['lat2']))
        vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot_anom)
        gef.plot_panel_and_save(
            ds_to_plot_anom,'tp','BrBG',fs,
            f'plots/{country}/{date_str}/dekadal/dekadal_precip_downscaled_anomaly.png',
            vmin=vmin,vmax=vmax
        )

        if country=='Kenya':
            ##### update the dekadal forecast timeseries per administrative district
            df = pd.read_csv("data/Kenya2026.csv",index_col='Feature')
            mask = regionmask.mask_geopandas(
                districts,
                rescaled_forecast["longitude"],
                rescaled_forecast["latitude"],
            )

            records = np.zeros((districts.shape[0],4))
            for i, row in districts.iterrows():
                ds_masked = rescaled_forecast.where(mask == i)
                ds_mean = ds_masked.mean({'longitude', 'latitude'}).drop_vars({'year','time','valid_time'})
                records[i]=ds_mean.tp.values

            dekade_names=[f'ire2026{i[26:28]}' for i in fclim_chirps[1]]
            districts_names=df.index

            dff=pd.DataFrame(data=records, index=districts_names, columns=dekade_names)
            for name in dekade_names:
                df[name]=dff[name]

            df.to_csv('data/Kenya2026.csv')

            #Generate geotiffs and other file formats
            dirname=f'data/{date_str}/geotifs_kenya/'
            os.makedirs(dirname,exist_ok=True)
            for i,forecast_timestep in enumerate(data_dekade.step.values):
                fname=f'downscaled_rainfall_forecast_init_{str(data_dekade.time.values)[0:10]}_{fclim_chirps[2][i]}.tif'

                to_save=ds_to_plot.isel(step=i)
                to_save.rio.to_raster(dirname+fname)
                to_save.rio.write_crs("EPSG:4326", inplace=True)
                to_save.tp.rio.to_raster(f"{dirname+fname}.bil", driver="EHdr")

            rescaled_forecast = rescaled_forecast.rio.write_crs("EPSG:4326")
            ds_to_plot = gef.clip_to_shapefile(rescaled_forecast, kenya_counties_shp, transpose=True, sortby_lat=True)
            gef.plot_panel_and_save(
                ds_to_plot,'tp',cmap,fs,
                f'plots/{country}/{date_str}/dekadal/dekadal_precip_downscaled_clipped.png',
                vmax=int(ds_to_plot.quantile(0.99).tp.values)
            )

            anomaly = anomaly.rio.write_crs("EPSG:4326")
            ds_to_plot = gef.clip_to_shapefile(anomaly, kenya_counties_shp, reproject_gdf=False)
            vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot)
            gef.plot_panel_and_save(
                ds_to_plot,'tp','BrBG',fs,
                f'plots/{country}/{date_str}/dekadal/dekadal_precip_downscaled_anomaly_clipped.png',
                vmin=vmin,vmax=vmax,boundary_gdf=districts,boundary_axes=slice(0,6)
            )

weekly_bboxes = {
    "Kenya": {"lat1": 7, "lon1": 33, "lat2": -6, "lon2": 42},
    "Kenya_plus":{"lat1": 7.5, "lon1":27, "lat2": -7.5, "lon2": 43},
    "Ghana":{"lat1": 12, "lon1": -4, "lat2": 4, "lon2": 2},
    "Ghana_plus": {"lat1": 12, "lon1": -4.5, "lat2": 4, "lon2": 3},
}

countries_to_downscale= os.environ["WEEK_COUNTRIES"].split(',')

for country in countries_to_downscale:
    if country=='Kenya':
        upscale_factor=30
    if country=='Ghana':
        upscale_factor=27
    forecast_year = 2026
    all_dates = []

    # Loop months March (3)–December (12)
    for month in range(1, 13):
        # Start at the 1st of the month
        day = datetime(forecast_year, month, 1)

        # Compute last day of month
        if month == 12:
            next_month = datetime(forecast_year + 1, 1, 1)
        else:
            next_month = datetime(forecast_year, month + 1, 1)
        last_day = next_month - timedelta(days=1)

        # Add every 2 days from day 1
        while day <= last_day:
            all_dates.append(day)
            day += timedelta(days=2)

    data_weekly=xr.open_dataset(f'data/{date_str}/data_weekly.nc')

    dates=[pd.to_datetime(str(date)[:10])- timedelta(days=7) for date in data_weekly.valid_time.values]

    closest=[pd.Series(all_dates).iloc[(pd.Series(all_dates) - date).abs().idxmin()] for date in dates]
    day_and_month=[("%02d" % ((pd.to_datetime(str(date)[:10])).month,),"%02d" % ((pd.to_datetime(str(date)[:10])).day,)) for date in closest]

    freforecast_clims=[f"downscale_data/chirpsv3_weeks/ECMWF_tp_forecasts_2025-{dix[0]}-{dix[1]}_1week_{country}.nc" for dix in day_and_month]
    reforecast_clims_ds = gef.stack_climatology_steps(freforecast_clims, data_weekly.step.values)

    fclim_chirps=[f"downscale_data/chirpsv3_weeks/chirpsv3_weeks_2005_2025_sorted_{dix[0]}-{dix[1]}_{country}.nc" for dix in day_and_month]
    chirps_weeks_ds = gef.stack_climatology_steps(fclim_chirps, data_weekly.step.values)

    data_to_add=data_weekly.assign_coords({"year":int(data_weekly.time.dt.year.values)}).mean('number').sel(longitude=slice(weekly_bboxes[country+'_plus']['lon1'],weekly_bboxes[country+'_plus']['lon2']),latitude=slice(weekly_bboxes[country+'_plus']['lat1'],weekly_bboxes[country+'_plus']['lat2']))
    extended_fclim=xr.concat([reforecast_clims_ds,data_to_add],dim='year')

    rescaled_forecast = gef.build_rescaled_forecast(extended_fclim, chirps_weeks_ds, data_weekly, upscale_factor=upscale_factor)

    rescaled_forecast_month = rescaled_forecast.isel(step=slice(0,4)).sum('step',keep_attrs=True).assign_coords(step=rescaled_forecast.isel(step=3).step).expand_dims('step')

    fs=12

    gef.lat1=weekly_bboxes[country]['lat1']
    gef.lat2=weekly_bboxes[country]['lat2']
    gef.lon1=weekly_bboxes[country]['lon1']
    gef.lon2=weekly_bboxes[country]['lon2']

    cmap=gef.cmap

    ds_to_plot=rescaled_forecast.sel(longitude=slice(weekly_bboxes[country]['lon1'],weekly_bboxes[country]['lon2']),latitude=slice(weekly_bboxes[country]['lat1'],weekly_bboxes[country]['lat2'])).transpose('latitude', 'longitude','step')
    gef.plot_panel_and_save(
        ds_to_plot,'tp',cmap,fs,
        f'plots/{country}/{date_str}/weekly/weekly_precip_downscaled.png',
        vmax=int(ds_to_plot.quantile(0.99).tp.values)
    )

    os.makedirs(f'plots/{country}/{date_str}/monthly/', exist_ok=True)
    ds_to_plot_month=rescaled_forecast_month.sel(longitude=slice(weekly_bboxes[country]['lon1'],weekly_bboxes[country]['lon2']),latitude=slice(weekly_bboxes[country]['lat1'],weekly_bboxes[country]['lat2'])).transpose('latitude', 'longitude','step')
    gef.plot_panel_and_save(
        ds_to_plot_month,'tp',cmap,fs,
        f'plots/{country}/{date_str}/monthly/monthly_precip_downscaled.png',
        vmax=int(ds_to_plot_month.quantile(0.99).tp.values)
    )

    if country=='Kenya':
        daily_downscaled=gef.disaggregate_weekly_to_daily(rescaled_forecast.tp, data.tp.mean('number'))
        # make sure dims are named/ordered as rioxarray expects
        da = daily_downscaled.tp.drop_vars({'surface','time','year','rank'}) 
        da = da.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
        # set the CRS (assuming plain lat/lon WGS84 — adjust if not)
        da = da.rio.write_crs("EPSG:4326", inplace=False)
        da=da.rio.write_nodata(np.nan, inplace=True)

        da.rio.to_raster(
            f'{data_path}/daily_downscaled_kenya.tif',
            tags={
                "band_dim_name": "day",
            },
        )

        rescaled_forecast = rescaled_forecast.rio.write_crs("EPSG:4326")
        ds_to_plot = gef.clip_to_shapefile(rescaled_forecast, kenya_counties_shp, transpose=True)
        gef.plot_panel_and_save(
            ds_to_plot,'tp',cmap,fs,
            f'plots/{country}/{date_str}/weekly/weekly_precip_downscaled_clipped.png',
            vmax=int(ds_to_plot.quantile(0.99).tp.values),
            boundary_gdf=districts,boundary_axes=slice(0,6)
        )

        anomaly = gef.compute_rainfall_anomaly(rescaled_forecast, chirps_weeks_ds)

        ds_to_plot=anomaly.sel(longitude=slice(weekly_bboxes[country]['lon1'],weekly_bboxes[country]['lon2']),latitude=slice(weekly_bboxes[country]['lat1'],weekly_bboxes[country]['lat2'])).transpose('latitude', 'longitude','step')
        vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot)
        gef.plot_panel_and_save(
            ds_to_plot,'tp','BrBG',fs,
            f'plots/{country}/{date_str}/weekly/weekly_precip_downscaled_anomaly.png',
            vmin=vmin,vmax=vmax
        )

        chirps_weeks_ds_month=chirps_weeks_ds.isel(step=slice(0,4)).sum('step',keep_attrs=True).assign_coords(step=chirps_weeks_ds.isel(step=3).step).expand_dims('step')
        anomaly_month = gef.compute_rainfall_anomaly(rescaled_forecast_month, chirps_weeks_ds_month)

        ds_to_plot_anom_month=anomaly_month.sel(longitude=slice(weekly_bboxes[country]['lon1'],weekly_bboxes[country]['lon2']),latitude=slice(weekly_bboxes[country]['lat1'],weekly_bboxes[country]['lat2'])).transpose('latitude', 'longitude','step')
        vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot_anom_month)
        gef.plot_panel_and_save(
            ds_to_plot_anom_month,'tp','BrBG',fs,
            f'plots/{country}/{date_str}/monthly/monthly_precip_downscaled_anomaly.png',
            vmin=vmin,vmax=vmax
        )

        anomaly = anomaly.rio.write_crs("EPSG:4326")
        ds_to_plot = gef.clip_to_shapefile(anomaly, kenya_counties_shp, transpose=True)
        vmin,vmax=gef.symmetric_vmin_vmax(ds_to_plot)
        gef.plot_panel_and_save(
            ds_to_plot,'tp','BrBG',fs,
            f'plots/{country}/{date_str}/weekly/weekly_precip_downscaled_anomaly_clipped.png',
            vmin=vmin,vmax=vmax,boundary_gdf=districts,boundary_axes=slice(0,6)
        )

        gef.plot_admin1_county_breakdown(
            rescaled_forecast, chirps_weeks_ds, states1,
            save_dir=f'plots/Kenya/{date_str}/weekly/counties',
            transpose_first=True
        )

        try:
            promt_unformat3={}
            rescaled_forecast_kenya=rescaled_forecast.sel(longitude=slice(weekly_bboxes[country]['lon1'],weekly_bboxes[country]['lon2']),latitude=slice(weekly_bboxes[country]['lat1'],weekly_bboxes[country]['lat2']))
            for region in states1_regions['region'].unique():
                promt_raw=gef.clip_by_overlap(rescaled_forecast_kenya.tp, regions_kenya, region, threshold=0.15).mean({'latitude','longitude'})
                promt_raw_dict=[{'raw_precip_mm':round(float(v),2)} for v in promt_raw]
                promt_unformat3[f"{region} (Downscaled)"]={f"week{i+1}": d for i, d in enumerate(promt_raw_dict)}
            gef.save_dict(promt_unformat3,f"{prefix}/promt_unformat3.json")
        except:
            gef.save_dict({},f"{prefix}/promt_unformat3.json")
