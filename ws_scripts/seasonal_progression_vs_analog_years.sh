#!/usr/bin/env bash
# Kenya weekly rainfall totals, Aug-Dec:
#   analog years + current-year observed (CHIRPS) + KMSA weekly downscaled
#   ensemble (101 members) and its national-mean line.
# Week bins match the other forecast slides: Monday-aligned ISO weeks, not
# weeks that start at 1 January, 1 August, or at the forecast init.
set -eo pipefail

# ---------------------------------------------------------------- skill pins
# weather-skills @dev — every step in this pipeline comes from this repo.
WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"

# chc-skills @dev — africa-itf, mjo-forecast-fetch, subc-mme-fetch,
# iod-mode-index. Pinned per request; NOT used by this figure (see notes).
CHC="uvx --from git+https://github.com/rhiza-research/chc-skills@dev chc-skills"

BBOX="5.506/33.893569/-4.67677/41.855083"
GEOJSON="intermediate_results/kenya.geojson"
mkdir -p intermediate_results

# ---------------------------------------------------------------- dynamic dates
TODAY=$(date -u +%Y-%m-%d)
CUR_YEAR=$(date -u +%Y)
END=$($WS chirps-fetch --probe-latest)      # latest available CHIRPS day
# DATE_STR pins the briefing init when the runner exports it; otherwise take
# the latest published data_weekly_Kenya_downscaled.nc.
INIT="${DATE_STR:-$($WS kenya-forecast-fetch --probe-latest precip_downscaled)}"

# ---------------------------------------------------------------- 0. inputs
# Analog years for the current season -> 1982 1994 1997 2006 2015 2019 2023
# 1982 and 1997 are NOT fetched: CHIRPS v3.0 sat only reaches back to 1998.
# 1994 uses CHIRPS v3.0 rnl (ERA5-disaggregated daily; same pentad totals).
$CHC analog-years --date "$TODAY"

# Kenya bbox + boundary polygon
$WS resolve-region KEN --geojson "$GEOJSON"

# Collapse a daily (step or time) cube onto Monday-aligned weeks.
# Same helper as kenya_daily_downscaled_precip.sh. how=sum of daily mm day-1
# is the weekly total; partial weeks keep the days that exist.
bin_monday_weeks() {
  local src="$1" dest="$2" how="$3"
  uv run --python 3.12 --with xarray --with zarr --with numpy --with pandas - \
    "$src" "$dest" "$how" <<'PY'
import sys
import numpy as np
import pandas as pd
import xarray as xr

src, dest, how = sys.argv[1], sys.argv[2], sys.argv[3]
ds = xr.open_zarr(src).load()
drop = [
    name
    for name in ("aggregation_coverage", "step_bounds", "valid_time")
    if name in ds.coords or name in ds.variables
]
if drop:
    ds = ds.drop_vars(drop)

if "step" in ds.dims:
    init = pd.Timestamp(np.asarray(ds["time"].values).reshape(()).item())
    valid = init + pd.to_timedelta(np.asarray(ds["step"].values))
    ds = ds.assign_coords(valid_time=("step", valid)).swap_dims({"step": "valid_time"})
    ds = ds.drop_vars([n for n in ("step", "time") if n in ds.variables or n in ds.coords])
    ds = ds.rename({"valid_time": "time"})

times = pd.DatetimeIndex(pd.to_datetime(np.asarray(ds["time"].values)))
week = times - pd.to_timedelta(times.weekday, unit="D")
ds = ds.assign_coords(week=("time", week.to_numpy().astype("datetime64[ns]")))
grouped = ds.groupby("week")
if how == "last":
    out = grouped.last(skipna=True)
elif how == "max":
    out = grouped.max(skipna=True)
else:
    out = grouped.sum(skipna=True)
out = out.rename({"week": "time"})
out["time"].attrs.update(standard_name="time", axis="T")
for name in out.data_vars:
    if how == "sum":
        out[name].attrs["units"] = "mm"
        out[name].attrs["long_name"] = "Total precipitation"
        out[name].attrs.pop("standard_name", None)
        out[name].attrs["aggregation_period"] = "7 day"
        out[name].attrs["cell_methods"] = "time: sum"
out.to_zarr(dest, mode="w")
PY
}

# Weekly-already forecast: keep values, move each left-edge valid time onto
# the Monday week that contains it so DOY ticks match the map slides.
label_monday_weeks() {
  local src="$1" dest="$2"
  uv run --python 3.12 --with xarray --with zarr --with numpy --with pandas - \
    "$src" "$dest" <<'PY'
import sys
import numpy as np
import pandas as pd
import xarray as xr

src, dest = sys.argv[1], sys.argv[2]
ds = xr.open_zarr(src).load()
times = pd.DatetimeIndex(pd.to_datetime(np.asarray(ds["time"].values)))
week = times - pd.to_timedelta(times.weekday, unit="D")
ds = ds.assign_coords(time=("time", week.to_numpy().astype("datetime64[ns]")))
ds["time"].attrs.update(standard_name="time", axis="T")
ds.to_zarr(dest, mode="w")
PY
}

# ------------------------------------------------- 1. CHIRPS observed years
for Y in 1994 2006 2015 2019 2023; do
  $WS chirps-fetch \
      --start-time "${Y}-08-01" --end-time "${Y}-12-31" \
      --bbox "$BBOX" --workers 8 \
      --output "intermediate_results/chirps_${Y}.zarr"
done

# Current year: through the latest available CHIRPS day ($END).
$WS chirps-fetch \
    --start-time "${CUR_YEAR}-08-01" --end-time "$END" \
    --bbox "$BBOX" --workers 8 \
    --output "intermediate_results/chirps_${CUR_YEAR}.zarr"

# Clip to the Kenya polygon -> area-weighted national mean -> weekly totals
for Y in 1994 2006 2015 2019 2023 "$CUR_YEAR"; do
  $WS clip-region \
      --input "intermediate_results/chirps_${Y}.zarr" \
      --geojson "$GEOJSON" \
      --output "intermediate_results/clip_${Y}.zarr"

  $WS summarize-dim \
      --input "intermediate_results/clip_${Y}.zarr" \
      --dim latitude --dim longitude --method mean --lat-weighted \
      --output "intermediate_results/mean_${Y}.zarr"

  bin_monday_weeks \
      "intermediate_results/mean_${Y}.zarr" \
      "intermediate_results/tot_${Y}.zarr" \
      sum
done

# -------------------------------------- 2. KMSA weekly downscaled forecast
# CHIRPS-resolution weekly NetCDF: 101 members. Keep `number` through the
# spatial reduce. Already weekly — convert-to-totals, do not re-aggregate.
# The daily GeoTIFF (precip_downscaled_daily) is ensemble-mean only.
$WS kenya-forecast-fetch \
    --dataset precip_downscaled \
    --date "$INIT" -v tp \
    --bbox "$BBOX" \
    --output intermediate_results/kmsa_ds_raw.zarr

$WS clip-region \
    --input intermediate_results/kmsa_ds_raw.zarr \
    --geojson "$GEOJSON" \
    --output intermediate_results/kmsa_ds_clip.zarr

$WS step-to-time \
    --input intermediate_results/kmsa_ds_clip.zarr \
    --output intermediate_results/kmsa_ds_time.zarr

$WS summarize-dim \
    --input intermediate_results/kmsa_ds_time.zarr \
    --dim latitude --dim longitude --method mean --lat-weighted \
    --output intermediate_results/kmsa_ds_mean.zarr

$WS convert-to-totals \
    --input intermediate_results/kmsa_ds_mean.zarr \
    --min-coverage 1.0 \
    --output intermediate_results/kmsa_ds_tot.zarr

$WS rename \
    --input intermediate_results/kmsa_ds_tot.zarr \
    --variable tp --to-name precip \
    --output intermediate_results/kmsa_ds_tot_named.zarr

label_monday_weeks \
    intermediate_results/kmsa_ds_tot_named.zarr \
    intermediate_results/kmsa_ds_final.zarr

$WS summarize-dim \
    --input intermediate_results/kmsa_ds_final.zarr \
    --dim number --method mean \
    --output intermediate_results/kmsa_ds_ensmean.zarr

# ------------------------------------------------------------- 3. the plot
# Analog years: seaborn deep. Current-year CHIRPS: heavy black.
# Downscaled members: grey spaghetti. Downscaled ensemble mean: purple.
# --trace selectors use full labels so "${CUR_YEAR}" is not ambiguous.
$WS plot-timeseries \
    --input intermediate_results/tot_1994.zarr \
    --input intermediate_results/tot_2006.zarr \
    --input intermediate_results/tot_2015.zarr \
    --input intermediate_results/tot_2019.zarr \
    --input intermediate_results/tot_2023.zarr \
    --input "intermediate_results/tot_${CUR_YEAR}.zarr" \
    --input intermediate_results/kmsa_ds_final.zarr \
    --input intermediate_results/kmsa_ds_ensmean.zarr \
    --label '1994 (analog)' \
    --label '2006 (analog)' \
    --label '2015 (analog)' \
    --label '2019 (analog)' \
    --label '2023 (analog)' \
    --label "${CUR_YEAR} observed (CHIRPS)" \
    --label "${CUR_YEAR} KMSA downscaled members" \
    --label "${CUR_YEAR} KMSA downscaled mean" \
    --variable precip \
    --along number \
    --align-day-of-year \
    --trace "${CUR_YEAR} observed (CHIRPS):color=black,linewidth=5,zorder=10" \
    --trace "${CUR_YEAR} KMSA downscaled members:color=grey,linewidth=0.5,zorder=3" \
    --trace "${CUR_YEAR} KMSA downscaled mean:color=purple,linewidth=5,zorder=8" \
    --trace '1994 (analog):color=#4c72b0,linewidth=1.4' \
    --trace '2006 (analog):color=#dd8452,linewidth=1.4' \
    --trace '2015 (analog):color=#55a868,linewidth=1.4' \
    --trace '2019 (analog):color=#c44e52,linewidth=1.4' \
    --trace '2023 (analog):color=#8172b3,linewidth=1.4' \
    --title "OND Seasonal Progression: analog years vs ${CUR_YEAR} + KMSA downscaled (init ${INIT})" \
    --ylabel 'Weekly rainfall total (mm)' \
    --fontsize 15 \
    --figsize 16,9 \
    --output kenya_weekly_rainfall_analog_years.png