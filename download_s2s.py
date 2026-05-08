import os
import json
from datetime import datetime, timedelta
from ecmwfapi import ECMWFDataServer

# ---------------------------
# Compute the date two days earlier
# ---------------------------
today = datetime.today()
two_days_earlier = today - timedelta(days=2)
date_str = two_days_earlier.strftime("%Y-%m-%d")
print(f"Downloading data for: {date_str}")

# ---------------------------
# Set up ECMWF API credentials from environment variables
# ---------------------------
api_config = {
    "url": os.environ["ECMWF_API_URL"],
    "key": os.environ["ECMWF_API_KEY"],
    "email": os.environ["ECMWF_API_EMAIL"]
}

# Write credentials to a temporary JSON file
file_path = "ecmwf_api_key.json"
with open(file_path, "w") as f:
    json.dump(api_config, f)

os.environ["ECMWF_API_RC_FILE"] = file_path

# ---------------------------
# Initialize ECMWF server
# ---------------------------
server = ECMWFDataServer()

# ---------------------------
# Retrieve S2S data
# ---------------------------
    
path=f'data/{date_str}'
os.makedirs(path, exist_ok=True)

target_file_pf = f"{path}/ECMWF_s2s_pf_precip_forecast_weekly-and-dekade_23N-20W-37S-59E.grib"

server.retrieve({
    "class": "s2",
    "dataset": "s2s",
    "date": date_str,
    "expver": "prod",
    "levtype": "sfc",
    "model": "glob",
    "number": "1/to/100",
    "origin": "ecmf",
    "param": "228228",
    "step": "0/168/240/336/480/504/672/720/840/960/1008",
    "stream": "enfo",
    "time": "00:00:00",
    "type": "pf",
    "area": "23/-20/-37/59",
    "target": target_file_pf
})

target_file_cf = f"{path}/ECMWF_s2s_cf_precip_forecast_weekly-and-dekade_23N-20W-37S-59E.grib"

server.retrieve({
    "class": "s2",
    "dataset": "s2s",
    "date": date_str,
    "expver": "prod",
    "model": "glob",
    "origin": "ecmf",
    "levtype": "sfc",
    "stream": "enfo",
    "time": "00:00:00",
    "area": "23/-20/-37/59",
    "param": "228228",
    "step": "0/168/240/336/480/504/672/720/840/960/1008",
    "type": "cf",
    "target": target_file_cf
})

ftypes=['pf','cf']

base_request = {
        "class": "s2",
        "dataset": "s2s",
        "date": date_str,
        "expver": "prod",
        "model": "glob",
        "area": "9/30/-6/45",
        "origin": "ecmf",
        "stream": "enfo",
        "time": "00:00:00",
    }

for ftype in ftypes:

    if ftype=='pf':
        base_request= base_request | {"number": "1/to/100","type": ftype,}
    else:
        base_request= base_request | {"type":ftype,}
 
    edit_request_daily_vars={
        "levtype": "sfc",
        "param": "59/136/167/168",
        "step": "0-24/24-48/48-72/72-96/96-120/120-144/144-168/168-192/192-216/216-240/240-264/264-288/288-312/312-336/336-360/360-384/384-408/408-432/432-456/456-480/480-504/504-528/528-552/552-576/576-600/600-624/624-648/648-672/672-696/696-720/720-744/744-768/768-792/792-816/816-840/840-864/864-888/888-912/912-936/936-960/960-984/984-1008",
        "target": f"{path}/ECMWF_s2s_{ftype}_CAPE_tcw_t2m_d2m_RH_forecast_42days_7N-32E-6S-43E.grib"
    }

    server.retrieve(base_request | edit_request_daily_vars)

    edit_request_10wind_vars={
        "levtype": "sfc",
        "param": "165/166",
        "step": "0/to/1008/by/6",
        "target": f"{path}/ECMWF_s2s_{ftype}_10wind_forecast_42days_7N-32E-6S-43E.grib"
    }

    server.retrieve(base_request | edit_request_10wind_vars)

    edit_request_Tminmax_vars={
        "levtype": "sfc",
        "param": "121/122",
        "step": "6/to/1014/by/6",
        "target": f"{path}/ECMWF_s2s_{ftype}_Tminmax_forecast_42days_7N-32E-6S-43E.grib"
    }

    server.retrieve(base_request | edit_request_Tminmax_vars)

    edit_request_700wind_vars={
        "levelist": "700",
        "levtype": "pl",
        "param": "131/132",
        "step": "0/to/984/by/24",
        "target": f"{path}/ECMWF_s2s_{ftype}_700wind_forecast_42days_7N-32E-6S-43E.grib"
    }

    server.retrieve(base_request | edit_request_700wind_vars)

    edit_request_500wind_vars={
        "levelist": "500",
        "levtype": "pl",
        "param": "135",
        "step": "0/to/984/by/24",
        "target": f"{path}/ECMWF_s2s_{ftype}_500wind_forecast_42days_7N-32E-6S-43E.grib"
    }

    server.retrieve(base_request | edit_request_500wind_vars)
