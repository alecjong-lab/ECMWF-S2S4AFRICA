import cdsapi
from datetime import datetime, timedelta
import os
from ecmwf.opendata import Client
import xarray as xr
import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

print(f"Downloading data for: {date_str}")

path=f'data/{date_str}/'
os.makedirs(path, exist_ok=True)

key=os.environ["CDSAPI_KEY"]
bounding_box=list(map(float, os.environ["BOUNDING_BOX"].split(',')))
#bounding_box=list(map(float,"22.5,-21,-34.5,55.5".split(',')))

client = cdsapi.Client(url="https://ecds.ecmwf.int/api", 
key=key)
dataset = "s2s-forecasts"

# ============================================================
# DOWNLOADS
# ============================================================

for ftype in ['perturbed_forecast','control_forecast']:

    base_request = {
        "origin": "ecmwf",
        "year": date_str[:4],
        "month": date_str[5:7],
        "day": date_str[8:],
        "forecast_type": ftype,
        "time": "00:00",
        "data_format": "grib",
        "area": bounding_box
    }


    # ========================================================
    # PRECIPITATION
    # ========================================================

    edit_request_precip = {
        "level_type": "single_level",
        "variable": ["total_precipitation"], 
        "leadtime_hour": ["0/to/1104/by/24"],
    }

    precip_file = (
        "ECMWF_s2s_{ftype}_forecast_precip_46days_23N-20W-37S-59E.grib"
    )

    client.retrieve(
        dataset,
        base_request | edit_request_precip
    ).download(
        f"{path}/{precip_file.format(ftype=ftype)}"
    )


    # ========================================================
    # DAILY VARIABLES
    # ========================================================

    edit_request_daily_vars = {
        "level_type": "single_level",
        "variable": [
            "2_m_dewpoint_temperature",
            "2_m_temperature",
            "convective_available_potential_energy",
            "total_column_water"
        ],
        "leadtime_hour": [
            "0_24","24_48","48_72","72_96","96_120",
            "120_144","144_168","168_192","192_216",
            "216-240","240_264","264_288","288_312",
            "312_336","336_360","360_384","384_408",
            "408_432","432_456","456_480","480_504",
            "504_528","528_552","552_576","576_600",
            "600_624","624_648","648_672","672_696",
            "696_720","720_744","744_768","768_792",
            "792_816","816_840","840_864","864_888",
            "888_912","912_936","936-960","960_984",
            "984_1008"
        ],
    }

    daily_file = (
        "ECMWF_s2s_{ftype}_CAPE_tcw_t2m_d2m_RH_42days_7N-32E-6S-43E.grib"
    )

    client.retrieve(
        dataset,
        base_request | edit_request_daily_vars
    ).download(
        f"{path}/{daily_file.format(ftype=ftype)}"
    )


    # ========================================================
    # 10m WIND
    # ========================================================

    edit_request_10wind_vars = {
        "level_type": "single_level",
        "variable": [
            "10_m_u_component_of_wind",
            "10_m_v_component_of_wind"
        ],
        "leadtime_hour": ["0/to/1008/by/24"],
    }

    wind10_file = (
        "ECMWF_s2s_{ftype}_10wind_42days_7N-32E-6S-43E.grib"
    )

    client.retrieve(
        dataset,
        base_request | edit_request_10wind_vars
    ).download(
        f"{path}/{wind10_file.format(ftype=ftype)}"
    )


    # ========================================================
    # Tmin / Tmax
    # ========================================================

    edit_request_Tminmax_vars = {
        "level_type": "single_level",
        "variable": [
            "maximum_2_m_temperature_in_the_last_6_hours",
            "minimum_2_m_temperature_in_the_last_6_hours"
        ],
        "leadtime_hour": ["6/to/1014/by/12"],
    }

    Tminmax_file = (
        "ECMWF_s2s_{ftype}_Tminmax_42days_7N-32E-6S-43E.grib"
    )

    client.retrieve(
        dataset,
        base_request | edit_request_Tminmax_vars
    ).download(
        f"{path}/{Tminmax_file.format(ftype=ftype)}"
    )


    # ========================================================
    # 700 hPa WIND
    # ========================================================

    edit_request_700wind_vars = {
        "level_type": "pressure",
        "level_value": ["700_hpa"],
        "variable": [
            "u_component_of_wind",
            "v_component_of_wind"
        ],
        "leadtime_hour": ["0/to/984/by/24"],
    }

    wind700_file = (
        "ECMWF_s2s_{ftype}_700wind_42days_7N-32E-6S-43E.grib"
    )

    client.retrieve(
        dataset,
        base_request | edit_request_700wind_vars
    ).download(
        f"{path}/{wind700_file.format(ftype=ftype)}"
    )


    # ========================================================
    # 500 hPa VERTICAL VELOCITY
    # ========================================================

    edit_request_500wind_vars = {
        "level_type": "pressure",
        "level_value": ["500_hpa"],
        "variable": ["vertical_velocity"],
        "leadtime_hour": ["0/to/984/by/24"],
    }

    wind500_file = (
        "ECMWF_s2s_{ftype}_500wind_42days_7N-32E-6S-43E.grib"
    )

    client.retrieve(
        dataset,
        base_request | edit_request_500wind_vars
    ).download(
        f"{path}/{wind500_file.format(ftype=ftype)}"
    )


# ============================================================
# CREATE ZARR FILES AFTER BOTH FORECAST TYPES EXIST
# ============================================================

gef.combine_to_zarr(
    path,
    precip_file,
    f"ECMWF_s2s_precip_{date_str}"
)

gef.combine_to_zarr(
    path,
    daily_file,
    f"ECMWF_s2s_daily_vars_{date_str}"
)

gef.combine_to_zarr(
    path,
    wind10_file,
    f"ECMWF_s2s_10wind_{date_str}"
)

gef.combine_to_zarr(
    path,
    Tminmax_file,
    f"ECMWF_s2s_Tminmax_{date_str}"
)

gef.combine_to_zarr(
    path,
    wind700_file,
    f"ECMWF_s2s_700wind_{date_str}"
)

gef.combine_to_zarr(
    path,
    wind500_file,
    f"ECMWF_s2s_500wind_{date_str}"
)

try:
    #download medium range precip
    client = Client("ecmwf", beta=False)

    filename1 = f'{path}/medium-tp-{date_str}-mean-pf_big.grib'
    filename2 = f'{path}/medium-tp-{date_str}-mean-cf_big.grib'

    client.retrieve(
        date=date_str,
        time=0,
        step=[0,168,336],
        stream="enfo",
        type="pf",
        levtype="sfc",
        param=['tp'],
        target=filename1,
    )

    client.retrieve(
        date=date_str,
        time=0,
        step=[0,168,336],
        stream="oper",
        type="fc",
        levtype="sfc",
        param=['tp'],
        target=filename2
    )

    data_medium_pf=xr.open_dataset(filename1,engine='cfgrib').sel(longitude=slice(bounding_box[1],bounding_box[3]),latitude=slice(bounding_box[0],bounding_box[2]))
    data_medium_cf=xr.open_dataset(filename2,engine='cfgrib').assign_coords({'number':0}).sel(longitude=slice(bounding_box[1],bounding_box[3]),latitude=slice(bounding_box[0],bounding_box[2]))
    data_medium=xr.concat([data_medium_pf,data_medium_cf],dim='number')
    data_weekly_medium=data_medium.diff('step')*1000
    data_weekly_medium.tp.attrs=data_medium_pf.tp.attrs
    data_weekly_medium.tp.attrs['units']='mm'

    data_weekly_medium.to_zarr(f'{path}/medium_range_precip.zarr')

    os.remove(filename1)
    os.remove(filename1+'.5b7b6.idx')
    os.remove(filename2)
    os.remove(filename2+'.5b7b6.idx')
except:
    print('medium range is unavailable')