#!/usr/bin/env bash
# Kenya weekly rainfall: analog years + current-year CHIRPS + KMSA weekly
# downscaled ensemble (101 members) and its national-mean line.
# Observed weeks are 7-day windows strided on Mondays (Mon–Sun).
# The weekly downscale is already weekly — convert-to-totals only.
set -eo pipefail

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"
PLOT=($WS plot-timeseries)

BBOX="5.506/33.893569/-4.67677/41.855083"
GEOJSON="intermediate_results/kenya.geojson"
mkdir -p intermediate_results

CUR_YEAR=$(date -u +%Y)
END=$($WS chirps-fetch --probe-latest)
# Do not inherit DATE_STR: KMSA/ECMWF inits lag the briefing date. Pin with
# INIT_OVERRIDE for a local rerun.
INIT="${INIT_OVERRIDE:-$($WS kenya-forecast-fetch --probe-latest precip_downscaled)}"

# 1982 and 1997 are intentionally absent: CHIRPS v3.0 sat starts in 1998.
ANALOG_YEARS=(2006 2015 2019 2023)

$WS resolve-region KEN --geojson "$GEOJSON"

# ---- CHIRPS observed branches -------------------------------------------
for Y in "${ANALOG_YEARS[@]}"; do
  $WS chirps-fetch \
      --start-time "${Y}-08-01" --end-time "${Y}-12-31" \
      --bbox "$BBOX" --workers 8 \
      --output "intermediate_results/chirps_${Y}.zarr"
done
$WS chirps-fetch \
    --start-time "${CUR_YEAR}-08-01" --end-time "$END" \
    --bbox "$BBOX" --workers 8 \
    --output "intermediate_results/chirps_${CUR_YEAR}.zarr"

for Y in "${ANALOG_YEARS[@]}" "$CUR_YEAR"; do
  $WS clip-region \
      --input "intermediate_results/chirps_${Y}.zarr" \
      --geojson "$GEOJSON" \
      --output "intermediate_results/clip_${Y}.zarr"

  # 7-day windows strided on Mondays -> Mon–Sun bins
  $WS aggregate-temporal \
      --input "intermediate_results/clip_${Y}.zarr" \
      --output "intermediate_results/mon_${Y}.zarr" \
      --window 7 --align left --stride Monday

  $WS summarize-dim \
      --input "intermediate_results/mon_${Y}.zarr" \
      --dim latitude --dim longitude --method mean --lat-weighted \
      --output "intermediate_results/monavg_${Y}.zarr"

  # 0.1 keeps partially-covered weeks (trailing CHIRPS / last analog week)
  $WS convert-to-totals \
      --input "intermediate_results/monavg_${Y}.zarr" \
      --min-coverage 0.1 \
      --output "intermediate_results/montot_${Y}.zarr"
done

# ---- KMSA weekly downscaled forecast ------------------------------------
# CHIRPS-resolution weekly NetCDF: 101 members. Keep `number` through the
# spatial reduce. Already weekly — do not re-aggregate.
$WS kenya-forecast-fetch \
    --dataset precip_downscaled \
    --date "$INIT" -v tp \
    --bbox "$BBOX" \
    --output intermediate_results/s2s_downscaled.zarr

$WS clip-region \
    --input intermediate_results/s2s_downscaled.zarr \
    --geojson "$GEOJSON" \
    --output intermediate_results/clip_s2s.zarr

$WS step-to-time \
    --input intermediate_results/clip_s2s.zarr \
    --output intermediate_results/s2s_t.zarr

$WS summarize-dim \
    --input intermediate_results/s2s_t.zarr \
    --dim latitude --dim longitude --method mean --lat-weighted \
    --output intermediate_results/s2s_avg.zarr

$WS convert-to-totals \
    --input intermediate_results/s2s_avg.zarr \
    --output intermediate_results/s2s_totals.zarr

$WS rename \
    --input intermediate_results/s2s_totals.zarr \
    --variable tp --to-name precip \
    --output intermediate_results/s2s_final.zarr

$WS summarize-dim \
    --input intermediate_results/s2s_final.zarr \
    --dim number --method mean \
    --output intermediate_results/s2s_ensmean.zarr

# Biweekly Monday ticks for Aug–Dec of the current year (DOY + labels).
PLOT_PATCH=$(python3 -c "
import json
from datetime import date, timedelta
year = int('${CUR_YEAR}')
d = date(year, 8, 1)
d += timedelta(days=(7 - d.weekday()) % 7)
months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
xticks, labels = [], []
end = date(year, 12, 31)
while d <= end:
    xticks.append(d.timetuple().tm_yday)
    labels.append(f'{months[d.month - 1]} {d.day:02d}')
    d += timedelta(days=14)
print(json.dumps({
    'axes': {
        'xticks': xticks,
        'xticklabels': labels,
        'xtickrotation': 45,
    }
}))
")

# Analog years: seaborn deep. Observed: black. Members: grey. Mean: purple.
# Output stem stays kenya_weekly_rainfall_analog_years for the briefing.
"${PLOT[@]}" \
    --input intermediate_results/montot_2006.zarr \
    --input intermediate_results/montot_2015.zarr \
    --input intermediate_results/montot_2019.zarr \
    --input intermediate_results/montot_2023.zarr \
    --input "intermediate_results/montot_${CUR_YEAR}.zarr" \
    --input intermediate_results/s2s_final.zarr \
    --input intermediate_results/s2s_ensmean.zarr \
    --label '2006 (analog)' \
    --label '2015 (analog)' \
    --label '2019 (analog)' \
    --label '2023 (analog)' \
    --label "${CUR_YEAR} CHIRPS (observed)" \
    --label "${CUR_YEAR} S2S members (101)" \
    --label "${CUR_YEAR} S2S ensemble mean" \
    --variable precip \
    --along number \
    --align-day-of-year \
    --theme weather_skills \
    --title "Kenya weekly rainfall (init ${INIT})" \
    --ylabel 'Weekly total (mm)' \
    --xlabel 'Week starting (Monday)' \
    --fontsize 26 \
    --figsize 20,13 \
    --trace '1:color=#4c72b0,linewidth=2.4' \
    --trace '2:color=#dd8452,linewidth=2.4' \
    --trace '3:color=#55a868,linewidth=2.4' \
    --trace '4:color=#c44e52,linewidth=2.4' \
    --trace "5:color=black,linewidth=4.2,zorder=10" \
    --trace '6:color=#9e9e9e,linewidth=0.8,zorder=2' \
    --trace '7:color=#7b1fa2,linewidth=5.0,zorder=12' \
    --patch "$PLOT_PATCH" \
    --output kenya_weekly_rainfall_analog_years.png
