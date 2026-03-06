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
    "levtype": "sfc",
    "model": "glob",
    "origin": "ecmf",
    "param": "228228",
    "step": "0/168/240/336/480/504/672/720/840/960/1008",
    "stream": "enfo",
    "time": "00:00:00",
    "type": "cf",
    "area": "23/-20/-37/59",
    "target": target_file_cf
})

# Write to a helper file for the next step
with open("latest_file.txt", "w") as f:
    f.write(target_file_pf)
    f.write("\n")
    f.write(target_file_cf)