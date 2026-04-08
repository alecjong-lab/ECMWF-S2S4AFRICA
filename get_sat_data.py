import os
from datetime import datetime, timedelta
import argparse
from sheerwater.data.imerg import imerg_raw_live
from sheerwater.data.chirps import chirps_raw_live
from sheerwater.spatial_subdivisions import clip_region

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download and process IMERG data for a specific country.")
    parser.add_argument(
        "--country", type=str, default="Kenya,Ghana,Senegal,Ethiopia", help="Country code for stations to process (default: Kenya)"
    )
    parser.add_argument(
        "--satellite", type=str, default="imerg,chirps", help="Satellite to process (default: imerg,chirps)"
    )
    args = parser.parse_args()
    countries = args.country.split(',')
    satellites = args.satellite.split(',')

    now_dt = datetime.now().date()
    live_time = now_dt.strftime("%Y-%m-%d")

    # Get the global IMERG data
    if "imerg" in satellites:
        imerg_live_lag = 8 # IMERG early release is 4 days behind, but pad to 8 to overlap with CHIRPS
        imerg_lagged_now_dt = now_dt - timedelta(days=imerg_live_lag)
        imerg_start_time = (imerg_lagged_now_dt - timedelta(days=50)).strftime("%Y-%m-%d")
        ds_imerg = imerg_raw_live(
            imerg_start_time, live_time, version='late', cache_mode='local_overwrite', delayed=False, recompute=True)
        ds_imerg = ds_imerg.rename({'precipitation': 'precip'})

    if "chirps" in satellites:
        chirps_live_lag = 8 # CHIRPS prelim releases every dekad, and is up to 3 days behind
        chirps_lagged_now_dt = now_dt - timedelta(days=chirps_live_lag)
        chirps_start_time = (chirps_lagged_now_dt - timedelta(days=50)).strftime("%Y-%m-%d")
        ds_chirps = chirps_raw_live(chirps_start_time, live_time, recompute=True, cache_mode='local_overwrite')

    # Clip the IMERG data to the countries
    for country in countries:
        # Write as a NETCDF file
        dir_path = f"satellite_data/{country}/{live_time}"
        os.makedirs(dir_path, exist_ok=True)
        if "imerg" in satellites:
            ds_clip_imerg = clip_region(ds_imerg, region=country, grid='global0_1')
            ds_clip_imerg = ds_clip_imerg.drop_attrs()
            ds_clip_imerg.to_netcdf(f"{dir_path}/imerg_data_{country}.nc")
        if "chirps" in satellites:
            ds_clip_chirps = clip_region(ds_chirps, region=country, grid='global0_1')
            # some attributes were causing problems
            ds_clip_chirps = ds_clip_chirps.drop_attrs()
            ds_clip_chirps.to_netcdf(f"{dir_path}/chirps_data_{country}.nc")


