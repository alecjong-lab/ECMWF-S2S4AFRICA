import os
from datetime import datetime, timedelta
import argparse
from sheerwater.data.imerg import imerg_raw_live
from sheerwater.spatial_subdivisions import clip_region

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download and process IMERG data for a specific country.")
    parser.add_argument(
        "--country", type=str, default="Kenya", help="Country code for stations to process (default: Kenya)"
    )
    args = parser.parse_args()
    countries = args.country.split(',')

    # Get the global IMERG data
    live_lag = 4 # IMERG early release is 4 days behind
    now_dt = datetime.now().date()
    lagged_now_dt = now_dt - timedelta(days=live_lag)
    start_time = (lagged_now_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    end_time = lagged_now_dt.strftime("%Y-%m-%d")
    live_time = now_dt.strftime("%Y-%m-%d")

    ds_imerg = imerg_raw_live(
        start_time, live_time, version='late', cache_mode='local_overwrite', delayed=False, recompute=True)
    ds_imerg = ds_imerg.rename({'precipitation': 'precip'})

    # Clip the IMERG data to the countries
    for country in countries:
        ds_clip = clip_region(ds_imerg, region=country, grid='global0_1')

        # Write as a NETCDF file
        dir_path = f"satellite_data/{country}/{live_time}"
        os.makedirs(dir_path, exist_ok=True)

        # some attributes were causing problems
        ds_clip = ds_clip.drop_attrs()
        ds_clip.to_netcdf(f"{dir_path}/imerg_data_{country}.nc")
