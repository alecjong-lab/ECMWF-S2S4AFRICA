#!/usr/bin/env bash
# GEFS / AIFS-ENS / ECMWF S2S / KMSA / Cumulus AI vs CHIRPS weekly precip verification over Kenya.
# One verifying Monday week, then week-1…week-4 leads (AIFS may have fewer; ~15-day).
# Writes kenya_{gefs,aifs,ecmwf,kmsa,cumulus}_chirps_{verify_5mm,bias,mae}.png
# Cumulus AI needs GOOGLE_APPLICATION_CREDENTIALS (gs://sheerwater-datalake is private).
set -eo pipefail

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@old-dev forecasting-skills"
run() { $WS "$@"; }

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

mkdir -p intermediate_results
cd intermediate_results

BBOX="5.506/33.893569/-4.67677/41.855083"

# ---------------------------------------------------------------- dynamic dates
# Last complete ISO week (Mon–Sun) that CHIRPS has published and that is
# outside the ~2-day forecast delay, so week-1 inits are actually available.
CHIRPS_LATEST=$(run chirps-fetch --probe-latest)
FCST_REF=$(run resolve-time now-2d --emit iso)
if [[ "$CHIRPS_LATEST" > "$FCST_REF" ]]; then
  ASOF="$FCST_REF"
else
  ASOF="$CHIRPS_LATEST"
fi
# `last-week --as-of X` always excludes the Mon-Sun week that CONTAINS X,
# even when X is that week's own Sunday (the week is already fully
# complete) — nudge as-of forward a day so a fully-elapsed week isn't
# skipped an extra week back. See week1_mae_vs_chirps.sh for the same fix.
ISO=$(run resolve-time last-week --as-of "$(pydate "${ASOF} +1 days" %Y-%m-%d)" --emit iso)
VERIFY_START="${ISO%%/*}"
VERIFY_END="${ISO##*/}"
AGG_END=$(pydate "$VERIFY_END +1 days" %Y-%m-%d)
echo "Verifying week: $VERIFY_START -> $VERIFY_END" >&2

declare -A INIT=(
  [w1]="$VERIFY_START"
  [w2]=$(pydate "$VERIFY_START -7 days" %Y-%m-%d)
  [w3]=$(pydate "$VERIFY_START -14 days" %Y-%m-%d)
  [w4]=$(pydate "$VERIFY_START -21 days" %Y-%m-%d)
)

# ---------------------------------------------------------------- region + CHIRPS
run resolve-region KEN --geojson kenya.geojson

run chirps-fetch \
    --start-time "$VERIFY_START" --end-time "$VERIFY_END" \
    --bbox "$BBOX" --workers 8 \
    --output chirps_raw.zarr

run aggregate-temporal \
    --period weekly --method mean --align left --end-time "$AGG_END" \
    --input chirps_raw.zarr --output chirps_weekly.zarr

run convert-to-totals \
    --min-coverage 1.0 \
    --input chirps_weekly.zarr --output chirps_weekly_mm.zarr

run select \
    --dim time --value "$VERIFY_START" \
    --input chirps_weekly_mm.zarr --output chirps_sel.zarr

# ------------------------------------------- one init per lead, per model
# lead week N  <->  init N-1 weeks before the verifying week
prepare_forecast() {
  local key="$1" W="$2"
  local init="${INIT[$W]}"
  local p="${key}_${W}"

  case "$key" in
    gefs)
      run dynamical-fetch --dataset noaa-gefs-forecast-35-day --date "$init" \
        --variable precipitation_surface --bbox "$BBOX" --output "${p}_raw.zarr"
      run aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_raw.zarr" --output "${p}_wk.zarr"
      run summarize-dim --dim number --method mean \
        --input "${p}_wk.zarr" --output "${p}_mean.zarr"
      run step-to-time --input "${p}_mean.zarr" --output "${p}_time.zarr"
      run select --dim time --value "$VERIFY_START" \
        --input "${p}_time.zarr" --output "${p}_sel.zarr"
      run convert-to-totals --min-coverage 1.0 \
        --input "${p}_sel.zarr" --output "${p}_mm.zarr"
      run rename --variable precipitation_surface --to-name precip \
        --input "${p}_mm.zarr" --output "${p}_plot.zarr"
      ;;
    aifs)
      run dynamical-fetch --dataset ecmwf-aifs-ens-forecast --date "$init" \
        --variable precipitation_surface --bbox "$BBOX" --output "${p}_raw.zarr"
      run aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_raw.zarr" --output "${p}_wk.zarr"
      run summarize-dim --dim number --method mean \
        --input "${p}_wk.zarr" --output "${p}_mean.zarr"
      run step-to-time --input "${p}_mean.zarr" --output "${p}_time.zarr"
      run select --dim time --value "$VERIFY_START" \
        --input "${p}_time.zarr" --output "${p}_sel.zarr"
      run convert-to-totals --min-coverage 0.85 \
        --input "${p}_sel.zarr" --output "${p}_mm.zarr"
      run rename --variable precipitation_surface --to-name precip \
        --input "${p}_mm.zarr" --output "${p}_plot.zarr"
      ;;
    ecmwf)
      run kenya-forecast-fetch --dataset precip --date "$init" -v tp \
        --bbox "$BBOX" --output "${p}_raw.zarr"
      run summarize-dim --dim number --method mean \
        --input "${p}_raw.zarr" --output "${p}_ens.zarr"
      run aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_ens.zarr" --output "${p}_wk.zarr"
      run step-to-time --input "${p}_wk.zarr" --output "${p}_time.zarr"
      run select --dim time --value "$VERIFY_START" \
        --input "${p}_time.zarr" --output "${p}_sel.zarr"
      run convert-to-totals --min-coverage 1.0 \
        --input "${p}_sel.zarr" --output "${p}_mm.zarr"
      run rename --variable tp --to-name precip \
        --input "${p}_mm.zarr" --output "${p}_plot.zarr"
      ;;
    kmsa)
      if run kenya-forecast-fetch --dataset precip_downscaled --date "$init" \
          --bbox "$BBOX" --output "${p}_raw.zarr"; then
        run step-to-time --input "${p}_raw.zarr" --output "${p}_time.zarr"
        run select --dim time --value "$VERIFY_START" \
          --input "${p}_time.zarr" --output "${p}_sel.zarr"
        run convert-to-totals --min-coverage 1.0 \
          --input "${p}_sel.zarr" --output "${p}_mm.zarr"
      else
        run kenya-forecast-fetch --dataset precip_downscaled_daily --date "$init" \
          --bbox "$BBOX" --output "${p}_raw.zarr"
        run aggregate-temporal --period weekly --method mean --align left \
          --input "${p}_raw.zarr" --output "${p}_wk.zarr"
        run step-to-time --input "${p}_wk.zarr" --output "${p}_time.zarr"
        run select --dim time --value "$VERIFY_START" \
          --input "${p}_time.zarr" --output "${p}_sel.zarr"
        run convert-to-totals --min-coverage 0.85 \
          --input "${p}_sel.zarr" --output "${p}_mm.zarr"
      fi
      run rename --variable tp --to-name precip \
        --input "${p}_mm.zarr" --output "${p}_plot.zarr"
      ;;
    cumulus)
      run cumulus-fetch --date "$init" -v tp \
        --bbox "$BBOX" --output "${p}_raw.zarr"
      run aggregate-temporal --period weekly --method mean --align left \
        --input "${p}_raw.zarr" --output "${p}_wk.zarr"
      run summarize-dim --dim number --method mean \
        --input "${p}_wk.zarr" --output "${p}_mean.zarr"
      run step-to-time --input "${p}_mean.zarr" --output "${p}_time.zarr"
      run select --dim time --value "$VERIFY_START" \
        --input "${p}_time.zarr" --output "${p}_sel.zarr"
      run convert-to-totals --min-coverage 1.0 \
        --input "${p}_sel.zarr" --output "${p}_mm.zarr"
      run rename --variable tp --to-name precip \
        --input "${p}_mm.zarr" --output "${p}_plot.zarr"
      ;;
    *)
      echo "ERROR: unknown model $key" >&2
      return 1
      ;;
  esac
}

verify_leads() {
  local key="$1"
  shift
  local W
  for W in "$@"; do
    run verify --metric hits --threshold 5 --variable precip \
      --forecast "${key}_${W}_plot.zarr" --obs "chirps_${key}grid.zarr" \
      --output "${key}_verify_${W}.zarr"
    run verify --metric bias --variable precip \
      --forecast "${key}_${W}_plot.zarr" --obs "chirps_${key}grid.zarr" \
      --output "${key}_bias_${W}.zarr"
    run verify --metric mae --variable precip \
      --forecast "${key}_${W}_plot.zarr" --obs "chirps_${key}grid.zarr" \
      --output "${key}_mae_${W}.zarr"
  done
}

# --------------------------------------------------------------- figures
cd ..

WEEK_LABEL="$VERIFY_START to $(pydate "$VERIFY_END" %m-%d)"

plot_model() {
  local key="$1" pretty="$2" title_name="$3"
  shift 3
  local weeks=("$@")
  local W metric prefix out title
  local pairs=() lead_args=() label_args=(--label CHIRPS)

  for W in "${weeks[@]}"; do
    lead_args+=(--lead "Week ${W#w} (init $(pydate "${INIT[$W]}" '%b %-d'))")
    label_args+=(--label "$pretty")
  done

  for metric in verify bias mae; do
    prefix="$metric"
    out="kenya_${key}_chirps_${metric}.png"
    if [[ "$metric" == verify ]]; then
      prefix=verify
      out="kenya_${key}_chirps_verify_5mm.png"
      title="${title_name} vs CHIRPS precipitation, Kenya, week of $WEEK_LABEL (5 mm threshold)"
    elif [[ "$metric" == bias ]]; then
      title="${title_name} vs CHIRPS precipitation bias, Kenya, week of $WEEK_LABEL"
    else
      title="${title_name} vs CHIRPS precipitation MAE, Kenya, week of $WEEK_LABEL"
    fi

    pairs=()
    for W in "${weeks[@]}"; do
      pairs+=(
        --forecast "intermediate_results/${key}_${W}_plot.zarr"
        --verify "intermediate_results/${key}_${prefix}_${W}.zarr"
      )
    done

    run plot-verify \
      --obs "intermediate_results/chirps_${key}grid.zarr" \
      "${pairs[@]}" \
      --variable precip \
      "${lead_args[@]}" \
      "${label_args[@]}" \
      --mask-geojson intermediate_results/kenya.geojson \
      --fontsize 15 --title "$title" --output "$out"
  done
}

run_model() {
  local key="$1" pretty="$2" title_name="$3"
  local W ok=()
  echo "== $title_name ==" >&2
  for W in w1 w2 w3 w4; do
    if ( cd intermediate_results && prepare_forecast "$key" "$W" ); then
      ok+=("$W")
    else
      echo "WARNING: skip $key $W (init ${INIT[$W]})" >&2
    fi
  done
  if (( ${#ok[@]} == 0 )); then
    echo "WARNING: no $key leads succeeded; skipping figures" >&2
    return 0
  fi
  (
    cd intermediate_results
    run coarsen \
      --reference-grid "${key}_${ok[0]}_plot.zarr" \
      --input chirps_sel.zarr --output "chirps_${key}grid.zarr"
    verify_leads "$key" "${ok[@]}"
  )
  plot_model "$key" "$pretty" "$title_name" "${ok[@]}"
}

run_model gefs    "GEFS ens. mean"       "GEFS" || echo "WARNING: GEFS figures failed" >&2
run_model aifs    "AIFS-ENS mean"        "AIFS-ENS" || echo "WARNING: AIFS figures failed" >&2
run_model ecmwf   "ECMWF S2S ens. mean"  "ECMWF S2S" || echo "WARNING: ECMWF S2S figures failed" >&2
run_model kmsa    "KMSA downscaled"      "KMSA downscaled" || echo "WARNING: KMSA figures failed" >&2
run_model cumulus "Cumulus AI ens. mean" "Cumulus AI" || echo "WARNING: Cumulus AI figures failed" >&2
