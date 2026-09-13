#!/usr/bin/env bash
# Kenya week-1 rainfall forecast MAE vs CHIRPS (GFS, AIFS-ENS, IFS-ENS).
set -eo pipefail

SKILLS="git+https://github.com/rhiza-research/forecasting-skills@dev"
run() { uvx --from "$SKILLS" forecasting-skills "$@"; }

mkdir -p intermediate_results
cd intermediate_results

BBOX="5.506/33.893569/-4.67677/41.855083"
# First week shown on the template placeholder; trailing week is the latest
# complete CHIRPS week whose 0-lead init is also outside the ~2-day forecast delay.
SERIES_START="2025-09-01"

CHIRPS_LATEST=$(run chirps-fetch --probe-latest)
FCST_REF=$(date -u -d "2 days ago" +%Y-%m-%d)
CHIRPS_BOUND=$(date -u -d "$CHIRPS_LATEST -6 days" +%Y-%m-%d)
if [[ "$CHIRPS_BOUND" > "$FCST_REF" ]]; then
  VERIFY_END_START="$FCST_REF"
else
  VERIFY_END_START="$CHIRPS_BOUND"
fi

# Snap SERIES_START and the last verifying week onto Mondays if needed by
# walking 7-day steps from SERIES_START.
WEEKS=()
d="$SERIES_START"
while [[ "$d" < "$VERIFY_END_START" || "$d" == "$VERIFY_END_START" ]]; do
  WEEKS+=("$d")
  d=$(date -u -d "$d +7 days" +%Y-%m-%d)
done
echo "MAE weeks: ${WEEKS[0]} -> ${WEEKS[-1]} (${#WEEKS[@]} weeks)" >&2

run resolve-region KEN --geojson kenya.geojson

CHIRPS_END=$(date -u -d "${WEEKS[-1]} +6 days" +%Y-%m-%d)
run chirps-fetch \
  --start-time "${WEEKS[0]}" --end-time "$CHIRPS_END" \
  --bbox "$BBOX" --workers 8 \
  --output chirps_raw.zarr

run aggregate-temporal \
  --period weekly --method mean --align left \
  --input chirps_raw.zarr --output chirps_weekly.zarr

run convert-to-totals \
  --min-coverage 1.0 \
  --input chirps_weekly.zarr --output chirps_weekly_mm.zarr

# model_key -> dynamical.org dataset id
declare -A DATASET=(
  [gfs]="noaa-gfs-forecast"
  [aifs]="ecmwf-aifs-ens-forecast"
  [ifs]="ecmwf-ifs-ens-forecast-15-day-0-25-degree"
)

week_mae() {  # $1=model_key  $2=verify_start
  local key="$1" start="$2"
  local raw="fcst_${key}_${start}.zarr"
  local weekly="fcst_${key}_${start}_weekly.zarr"
  local mean="fcst_${key}_${start}_mean.zarr"
  local timed="fcst_${key}_${start}_time.zarr"
  local sel="fcst_${key}_${start}_sel.zarr"
  local mm="fcst_${key}_${start}_mm.zarr"
  local plot="fcst_${key}_${start}_plot.zarr"
  local obs="chirps_${key}_${start}.zarr"
  local grid="chirps_${key}_${start}_grid.zarr"
  local mae="mae_${key}_${start}.zarr"
  local clip="mae_${key}_${start}_clip.zarr"
  local out="mae_${key}_${start}_mean.zarr"

  run dynamical-fetch \
    --dataset "${DATASET[$key]}" \
    --date "$start" \
    --variable precipitation_surface \
    --bbox "$BBOX" \
    --output "$raw"

  run aggregate-temporal \
    --period weekly --method mean --align left \
    --input "$raw" --output "$weekly"

  if [[ "$key" == "gfs" ]]; then
    cp -R "$weekly" "$mean"
  else
    run summarize-dim \
      --dim number --method mean \
      --input "$weekly" --output "$mean"
  fi

  run step-to-time --input "$mean" --output "$timed"
  run select --dim time --value "$start" --input "$timed" --output "$sel"
  run convert-to-totals --min-coverage 1.0 --input "$sel" --output "$mm"
  run rename --variable precipitation_surface --to-name precip \
    --input "$mm" --output "$plot"

  run select --dim time --value "$start" \
    --input chirps_weekly_mm.zarr --output "$obs"
  run coarsen --reference-grid "$sel" --input "$obs" --output "$grid"

  run verify --metric mae --variable precip \
    --forecast "$plot" --obs "$grid" --output "$mae"

  run clip-region --geojson kenya.geojson --input "$mae" --output "$clip"
  run summarize-dim \
    --dim latitude --dim longitude --method mean --lat-weighted \
    --input "$clip" --output "$out"
}

for key in gfs aifs ifs; do
  ok=()
  for start in "${WEEKS[@]}"; do
    set +e
    week_mae "$key" "$start"
    status=$?
    set -e
    if [[ $status -eq 0 ]]; then
      ok+=("mae_${key}_${start}_mean.zarr")
    else
      echo "WARNING: skip $key week $start" >&2
    fi
  done
  if (( ${#ok[@]} == 0 )); then
    echo "ERROR: no $key weeks succeeded" >&2
    exit 1
  elif (( ${#ok[@]} == 1 )); then
    cp -R "${ok[0]}" "mae_${key}_series.zarr"
  else
    inputs=()
    for z in "${ok[@]}"; do
      inputs+=(--input "$z")
    done
    run concat --dim time "${inputs[@]}" --output "mae_${key}_series.zarr"
  fi
done

cd ..

START_LABEL=$(date -u -d "${WEEKS[0]}" +'%b %Y')
END_LABEL=$(date -u -d "${WEEKS[-1]}" +'%b %Y')

run plot-timeseries \
  --input intermediate_results/mae_gfs_series.zarr \
  --input intermediate_results/mae_aifs_series.zarr \
  --input intermediate_results/mae_ifs_series.zarr \
  --variable mae \
  --style line \
  --label GFS --label AIFS-ENS --label IFS-ENS \
  --trace 1:color='#1f77b4',marker=o \
  --trace 2:color='#ff7f0e',marker=o \
  --trace 3:color='#2ca02c',marker=o \
  --title "Kenya week-1 rainfall forecast MAE vs CHIRPS (${START_LABEL} - ${END_LABEL})" \
  --ylabel "Mean absolute error" \
  --fontsize 16 \
  --output kenya_week1_forecast_mae_vs_chirps.png
