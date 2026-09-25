#!/usr/bin/env bash
# Kenya week-1 rainfall MAE vs CHIRPS over the last 4 weeks from today.
# Models: AIFS-ENS, ECMWF ENS (IFS 15-day), ECMWF ER (S2S), KMSA downscaled, GEFS, Cumulus AI.
# Writes kenya_week1_mae_vs_chirps_4wk.png (briefing template picture name).
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"

BBOX="5.506/33.893569/-4.67677/41.855083"
N_WEEKS=4

mkdir -p intermediate_results
cd intermediate_results

# ---------------------------------------------------------------- dates
# Last complete week whose Monday init is also outside the ~2-day forecast
# delay. resolve-time last-week is Monday–Sunday.
CHIRPS_LATEST=$($WS chirps-fetch --probe-latest | tail -n1)
CHIRPS_LATEST="${CHIRPS_LATEST:0:10}"
FCST_REF=$($WS resolve-time now-2d --emit iso)
if [[ "$CHIRPS_LATEST" > "$FCST_REF" ]]; then
  ASOF="$FCST_REF"
else
  ASOF="$CHIRPS_LATEST"
fi

LAST_ISO=$($WS resolve-time last-week --as-of "$ASOF" --emit iso)
LAST_SUN="${LAST_ISO##*/}"
LAST_MON="${LAST_ISO%%/*}"

# Walk back 7 days from that Monday. Calling last-week again on the Monday
# (or the Sunday before it) skips a week.
WEEKS=()
start="$LAST_MON"
for ((i = 0; i < N_WEEKS; i++)); do
  WEEKS=("$start" "${WEEKS[@]}")
  start=$($WS resolve-time now-7d --as-of "$start" --emit iso)
done
LAST_WEEK="${WEEKS[$((N_WEEKS - 1))]}"
echo "MAE weeks: ${WEEKS[0]} -> ${LAST_WEEK} ($N_WEEKS complete weeks, as of $ASOF)" >&2

$WS resolve-region KEN --geojson kenya.geojson

CHIRPS_RANGE=$($WS resolve-time last-4w --as-of "$LAST_SUN" --emit iso)
$WS chirps-fetch \
  --start-time "${CHIRPS_RANGE%%/*}" --end-time "${CHIRPS_RANGE##*/}" \
  --bbox "$BBOX" --workers 8 \
  --output chirps_raw.zarr

$WS aggregate-temporal \
  --period weekly --method mean --align left \
  --input chirps_raw.zarr --output chirps_weekly.zarr

$WS convert-to-totals \
  --min-coverage 1.0 \
  --input chirps_weekly.zarr --output chirps_weekly_mm.zarr

# ---------------------------------------------------------------- per model-week
# Already-weekly KMSA is stamped on fetch — convert-to-totals only.
# Daily KMSA / AIFS / IFS-ENS / GEFS / ER: weekly-bin, then totals.
week_mae() {  # $1=key $2=week
  local key="$1" w="$2"
  local p="${key}_${w}"
  local var=tp
  case "$key" in
    aifs|ifs|gefs) var=precipitation_surface ;;
  esac

  case "$key" in
    aifs|ifs)
      local ds=ecmwf-aifs-ens-forecast
      [[ "$key" == ifs ]] && ds=ecmwf-ifs-ens-forecast-15-day-0-25-degree
      $WS dynamical-fetch --dataset "$ds" --date "$w" \
        --variable precipitation_surface --bbox "$BBOX" --output "${p}_raw.zarr" || return 1
      $WS aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_raw.zarr" --output "${p}_wk.zarr" || return 1
      $WS summarize-dim --dim number --method mean \
        --input "${p}_wk.zarr" --output "${p}_mean.zarr" || return 1
      $WS step-to-time --input "${p}_mean.zarr" --output "${p}_time.zarr" || return 1
      $WS convert-to-totals --min-coverage 0.85 \
        --input "${p}_time.zarr" --output "${p}_mm.zarr" || return 1
      ;;
    gefs)
      $WS dynamical-fetch --dataset noaa-gefs-forecast-35-day --date "$w" \
        --variable precipitation_surface --bbox "$BBOX" --output "${p}_raw.zarr" || return 1
      $WS aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_raw.zarr" --output "${p}_wk.zarr" || return 1
      $WS summarize-dim --dim number --method mean \
        --input "${p}_wk.zarr" --output "${p}_mean.zarr" || return 1
      $WS step-to-time --input "${p}_mean.zarr" --output "${p}_time.zarr" || return 1
      $WS convert-to-totals --min-coverage 0.85 \
        --input "${p}_time.zarr" --output "${p}_mm.zarr" || return 1
      ;;
    er)
      $WS kenya-forecast-fetch --dataset precip --date "$w" -v tp \
        --bbox "$BBOX" --output "${p}_raw.zarr" || return 1
      $WS summarize-dim --dim number --method mean \
        --input "${p}_raw.zarr" --output "${p}_ens.zarr" || return 1
      $WS aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_ens.zarr" --output "${p}_wk.zarr" || return 1
      $WS step-to-time --input "${p}_wk.zarr" --output "${p}_time.zarr" || return 1
      $WS convert-to-totals --min-coverage 1.0 \
        --input "${p}_time.zarr" --output "${p}_mm.zarr" || return 1
      ;;
    kmsa)
      if $WS kenya-forecast-fetch --dataset precip_downscaled --date "$w" \
          --bbox "$BBOX" --output "${p}_raw.zarr"; then
        $WS step-to-time --input "${p}_raw.zarr" --output "${p}_time.zarr" || return 1
        $WS convert-to-totals --min-coverage 1.0 \
          --input "${p}_time.zarr" --output "${p}_mm.zarr" || return 1
      else
        $WS kenya-forecast-fetch --dataset precip_downscaled_daily --date "$w" \
          --bbox "$BBOX" --output "${p}_raw.zarr" || return 1
        $WS aggregate-temporal --period weekly --method mean --align left \
          --input "${p}_raw.zarr" --output "${p}_wk.zarr" || return 1
        $WS step-to-time --input "${p}_wk.zarr" --output "${p}_time.zarr" || return 1
        $WS convert-to-totals --min-coverage 0.85 \
          --input "${p}_time.zarr" --output "${p}_mm.zarr" || return 1
      fi
      ;;
    cumulus)
      $WS cumulus-fetch --date "$w" -v tp \
        --bbox "$BBOX" --output "${p}_raw.zarr" || return 1
      $WS aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_raw.zarr" --output "${p}_wk.zarr" || return 1
      $WS summarize-dim --dim number --method mean \
        --input "${p}_wk.zarr" --output "${p}_mean.zarr" || return 1
      $WS step-to-time --input "${p}_mean.zarr" --output "${p}_time.zarr" || return 1
      $WS convert-to-totals --min-coverage 1.0 \
        --input "${p}_time.zarr" --output "${p}_mm.zarr" || return 1
      ;;
    *)
      echo "ERROR: unknown model $key" >&2
      return 1
      ;;
  esac

  $WS rename --variable "$var" --to-name precip \
    --input "${p}_mm.zarr" --output "${p}_named.zarr" || return 1
  $WS select --dim time --value "$w" \
    --input "${p}_named.zarr" --output "${p}_w1.zarr" || return 1
  $WS select --dim time --value "$w" \
    --input chirps_weekly_mm.zarr --output "chirps_${p}.zarr" || return 1
  $WS coarsen --reference-grid "${p}_w1.zarr" \
    --input "chirps_${p}.zarr" --output "chirps_${p}_grid.zarr" || return 1
  $WS verify --metric mae --variable precip \
    --forecast "${p}_w1.zarr" --obs "chirps_${p}_grid.zarr" \
    --output "mae_${p}.zarr" || return 1
  $WS clip-region --geojson kenya.geojson \
    --input "mae_${p}.zarr" --output "mae_${p}_clip.zarr" || return 1
  $WS summarize-dim --dim latitude --dim longitude --method mean --lat-weighted \
    --input "mae_${p}_clip.zarr" --output "mae_${p}_mean.zarr" || return 1
}

for key in aifs ifs er kmsa gefs cumulus; do
  ok=()
  for w in "${WEEKS[@]}"; do
    if week_mae "$key" "$w"; then
      ok+=("mae_${key}_${w}_mean.zarr")
    else
      echo "WARNING: skip $key week $w" >&2
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
    $WS concat --dim time "${inputs[@]}" --output "mae_${key}_series.zarr"
  fi
done

cd ..

# Concat leaves time as 0..n-1. Label each bar with the week start–end.
TICK_LABELS=$(python3 - "${WEEKS[@]}" <<'PY'
import json, sys
from datetime import date, timedelta
months = ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sept","Oct","Nov","Dec")
def fmt(d):
    return f"{d.day} {months[d.month-1]} '{d.year % 100:02d}"
labels = []
for raw in sys.argv[1:]:
    start = date.fromisoformat(raw)
    labels.append(f"{fmt(start)} - {fmt(start + timedelta(days=6))}")
print(json.dumps(labels))
PY
)
TICK_VALUES=$(python3 -c "import json,sys; print(json.dumps(list(range(len(sys.argv)-1))))" "${WEEKS[@]}")
PATCH=$(python3 -c "import json,sys; print(json.dumps({
  'theme': {'rc': {'xtick.labelsize': 12}},
  'axes': {
    'xlabel': '',
    'legend': {'loc': 'upper center', 'bbox_to_anchor': [0.5, -0.22], 'ncol': 4},
    'xticks': {'values': json.loads(sys.argv[1]), 'labels': json.loads(sys.argv[2])},
  }
}))" "$TICK_VALUES" "$TICK_LABELS")

$WS plot-timeseries \
  --input intermediate_results/mae_aifs_series.zarr \
  --input intermediate_results/mae_ifs_series.zarr \
  --input intermediate_results/mae_er_series.zarr \
  --input intermediate_results/mae_kmsa_series.zarr \
  --input intermediate_results/mae_gefs_series.zarr \
  --input intermediate_results/mae_cumulus_series.zarr \
  --variable mae \
  --mark bar --bar-mode grouped \
  --label AIFS --label "ECMWF ENS" --label "ECMWF ER" \
  --label "KMSA downscaled" --label GEFS --label "Cumulus AI" \
  --title "Kenya week-1 rainfall forecast MAE vs CHIRPS · ${WEEKS[0]} – ${LAST_SUN}" \
  --ylabel "MAE (mm / week)" \
  --fontsize 16 --figsize 13,6.5 \
  --patch "$PATCH" \
  --output kenya_week1_mae_vs_chirps_4wk.png
cp -f kenya_week1_mae_vs_chirps_4wk.png kenya_week1_forecast_mae_vs_chirps.png
