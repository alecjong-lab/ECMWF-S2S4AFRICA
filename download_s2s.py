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

BUCKET = os.environ.get("BUCKET", "africa-forecasting-data")

client = cdsapi.Client(url="https://ecds.ecmwf.int/api",
key=key)
dataset = "s2s-forecasts"

# ============================================================
# DOWNLOADS
# ============================================================

def base_request(ftype):
    return {
        "origin": "ecmwf",
        "year": date_str[:4],
        "month": date_str[5:7],
        "day": date_str[8:],
        "forecast_type": ftype,
        "time": "00:00",
        "data_format": "grib",
        "area": bounding_box
    }

def make_retrieve_fns(edit_request):
    """
    Build a {forecast_type: callable(tmp_path)} dict that issues the CDS
    retrieve() call for both forecast types, for use with gef.ensure_group_zarr.
    """
    fns = {}
    for ftype in ['perturbed_forecast', 'control_forecast']:
        request = base_request(ftype) | edit_request
        def _retrieve(tmp_path, request=request):
            client.retrieve(dataset, request).download(tmp_path)
        fns[ftype] = _retrieve
    return fns

# ========================================================
# PRECIPITATION
# ========================================================

precip_file = (
    "ECMWF_s2s_{ftype}_forecast_precip_46days_23N-20W-37S-59E.grib"
)

edit_request_precip = {
    "level_type": "single_level",
    "variable": ["total_precipitation"],
    "leadtime_hour": ["0/to/1104/by/24"],
}

# ========================================================
# DAILY VARIABLES
# ========================================================

daily_file = (
    "ECMWF_s2s_{ftype}_CAPE_tcw_t2m_d2m_RH_42days_7N-32E-6S-43E.grib"
)

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

# ========================================================
# 10m WIND
# ========================================================

wind10_file = (
    "ECMWF_s2s_{ftype}_10wind_42days_7N-32E-6S-43E.grib"
)

edit_request_10wind_vars = {
    "level_type": "single_level",
    "variable": [
        "10_m_u_component_of_wind",
        "10_m_v_component_of_wind"
    ],
    "leadtime_hour": ["0/to/1008/by/24"],
}

# ========================================================
# Tmin / Tmax
# ========================================================

Tminmax_file = (
    "ECMWF_s2s_{ftype}_Tminmax_42days_7N-32E-6S-43E.grib"
)

edit_request_Tminmax_vars = {
    "level_type": "single_level",
    "variable": [
        "maximum_2_m_temperature_in_the_last_6_hours",
        "minimum_2_m_temperature_in_the_last_6_hours"
    ],
    "leadtime_hour": ["6/to/1014/by/12"],
}

# ========================================================
# 700 hPa WIND
# ========================================================

wind700_file = (
    "ECMWF_s2s_{ftype}_700wind_42days_7N-32E-6S-43E.grib"
)

edit_request_700wind_vars = {
    "level_type": "pressure",
    "level_value": ["700_hpa"],
    "variable": [
        "u_component_of_wind",
        "v_component_of_wind"
    ],
    "leadtime_hour": ["0/to/984/by/24"],
}

# ========================================================
# 500 hPa VERTICAL VELOCITY
# ========================================================

wind500_file = (
    "ECMWF_s2s_{ftype}_500wind_42days_7N-32E-6S-43E.grib"
)

edit_request_500wind_vars = {
    "level_type": "pressure",
    "level_value": ["500_hpa"],
    "variable": ["vertical_velocity"],
    "leadtime_hour": ["0/to/984/by/24"],
}

# ============================================================
# DOWNLOAD + COMBINE EACH VARIABLE GROUP
# Each group is only downloaded/recombined if its zarr isn't already
# complete locally or restorable from GCS (see gef.ensure_group_zarr).
# ============================================================

groups = [
    (precip_file, f"ECMWF_s2s_precip_{date_str}", edit_request_precip),
    (daily_file, f"ECMWF_s2s_daily_vars_{date_str}", edit_request_daily_vars),
    (wind10_file, f"ECMWF_s2s_10wind_{date_str}", edit_request_10wind_vars),
    (Tminmax_file, f"ECMWF_s2s_Tminmax_{date_str}", edit_request_Tminmax_vars),
    (wind700_file, f"ECMWF_s2s_700wind_{date_str}", edit_request_700wind_vars),
    (wind500_file, f"ECMWF_s2s_500wind_{date_str}", edit_request_500wind_vars),
]

for filename, zarr_name, edit_request in groups:
    gef.ensure_group_zarr(
        date_str, path, BUCKET,
        filename, zarr_name,
        make_retrieve_fns(edit_request)
    )

try:
    #download medium range precip
    client = Client("ecmwf", beta=False)

    medium_zarr_path = f'{path}/medium_range_precip.zarr'
    medium_gcs_prefix = f'data/{date_str}/medium_range_precip.zarr'

    if os.path.isdir(medium_zarr_path) and gef.is_complete(medium_zarr_path):
        print(f"{medium_zarr_path} already complete locally, skipping medium range download")
    elif gef.restore_zarr_from_gcs(BUCKET, medium_gcs_prefix, medium_zarr_path) and gef.is_complete(medium_zarr_path):
        print(f"Restored {medium_zarr_path} from GCS")
    else:
        filename1 = f'{path}/medium-tp-{date_str}-mean-pf_big.grib'
        filename2 = f'{path}/medium-tp-{date_str}-mean-cf_big.grib'

        gef.ensure_grib_downloaded(
            filename1, None,
            lambda tmp_path: client.retrieve(
                date=date_str,
                time=0,
                step=[0,168,336],
                stream="enfo",
                type="pf",
                levtype="sfc",
                param=['tp'],
                target=tmp_path,
            ),
            BUCKET,
        )

        gef.ensure_grib_downloaded(
            filename2, None,
            lambda tmp_path: client.retrieve(
                date=date_str,
                time=0,
                step=[0,168,336],
                stream="oper",
                type="fc",
                levtype="sfc",
                param=['tp'],
                target=tmp_path,
            ),
            BUCKET,
        )

        data_medium_pf=xr.open_dataset(filename1,engine='cfgrib').sel(longitude=slice(bounding_box[1],bounding_box[3]),latitude=slice(bounding_box[0],bounding_box[2]))
        data_medium_cf=xr.open_dataset(filename2,engine='cfgrib').assign_coords({'number':0}).sel(longitude=slice(bounding_box[1],bounding_box[3]),latitude=slice(bounding_box[0],bounding_box[2]))
        data_medium=xr.concat([data_medium_pf,data_medium_cf],dim='number')
        data_weekly_medium=data_medium.diff('step')*1000
        data_weekly_medium.tp.attrs=data_medium_pf.tp.attrs
        data_weekly_medium.tp.attrs['units']='mm'

        data_weekly_medium.to_zarr(medium_zarr_path)
        gef.mark_complete(medium_zarr_path)

        os.remove(filename1)
        os.remove(filename1+'.5b7b6.idx')
        os.remove(filename2)
        os.remove(filename2+'.5b7b6.idx')
except:
    print('medium range is unavailable')
