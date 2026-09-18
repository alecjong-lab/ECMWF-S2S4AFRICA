#!/usr/bin/env bash
# Combined 6-week precip forecast: ECMWF IFS-ENS (medium-range ensemble mean,
# 0.25 deg, credential-free via dynamical.org) covers weeks 1-2, ECMWF S2S
# ensemble mean fills weeks 3-6, one 6-panel heatmap.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"

# IFS-ENS via dynamical.org is credential-free; ECMWF_DATASTORES_KEY is still
# needed for the S2S fetch below (CI and run_skills.sh already inject it), or
# it falls back to a public mirror instead of the real ECMWF source.
: "${ECMWF_DATASTORES_KEY:?ECMWF_DATASTORES_KEY not set - check .env}"

ENS_DATASET="ecmwf-ifs-ens-forecast-15-day-0-25-degree"
ENS_WEEKS=2

COUNTRY="${COUNTRY:-KEN}"
GEOJSON="intermediate_results/${COUNTRY,,}.geojson"
mkdir -p intermediate_results

# Country bbox + boundary polygon. resolve-region prints "N/W/S/E" last.
BBOX=$($WS resolve-region "$COUNTRY" --geojson "$GEOJSON" | tail -n1)

# ---------------------------------------------------------------- init dates
# ENS_INIT_OVERRIDE / S2S_INIT_OVERRIDE: pin an older init locally, e.g. to
# skip an embargo window. Neither is read anywhere in .github/.
ENS_INIT="${ENS_INIT_OVERRIDE:-$($WS dynamical-fetch --probe-latest "$ENS_DATASET")}"  # YYYY-MM-DD, 00 UTC
S2S_INIT="${S2S_INIT_OVERRIDE:-$($WS ecmwf-fetch --probe-latest)}"

# time-dim length of a zarr, via inspect-zarr's JSON output.
zarr_time_len() {
  $WS inspect-zarr --input "$1" --format json \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['dims']['time'])"
}

# --------------------------------------------------------------- 1. ENS fetch
$WS dynamical-fetch \
    --dataset "$ENS_DATASET" \
    --date "$ENS_INIT" --bbox "$BBOX" -v tp \
    --output intermediate_results/ens_raw.zarr

$WS step-to-time \
    --input intermediate_results/ens_raw.zarr \
    --output intermediate_results/ens_time.zarr

# Ensemble mean over `number`, keeping lat/lon (this is a map, not a series).
$WS summarize-dim \
    --input intermediate_results/ens_time.zarr \
    --dim number --method mean \
    --output intermediate_results/ens_ensmean.zarr

$WS aggregate-temporal \
    --input intermediate_results/ens_ensmean.zarr \
    --period weekly --method mean --align left \
    --output intermediate_results/ens_wk.zarr

$WS convert-to-totals \
    --input intermediate_results/ens_wk.zarr \
    --min-coverage 1.0 \
    --output intermediate_results/ens_tot.zarr

# ENS is a 15-day forecast (always 00 UTC), so 2 full weeks is normally
# available; cap to what's actually there just in case.
ENS_WEEKS_AVAIL=$(zarr_time_len intermediate_results/ens_tot.zarr)
[ "$ENS_WEEKS" -gt "$ENS_WEEKS_AVAIL" ] && ENS_WEEKS=$ENS_WEEKS_AVAIL

# select with a single --index drops the time dim entirely (needed by
# concat below), so only trim when actually shrinking (avail > ENS_WEEKS).
ENS_SRC=intermediate_results/ens_tot.zarr
if [ "$ENS_WEEKS" -lt "$ENS_WEEKS_AVAIL" ]; then
  ENS_IDX_ARGS=()
  for ((i = 0; i < ENS_WEEKS; i++)); do ENS_IDX_ARGS+=(--index "$i"); done
  $WS select \
      --input intermediate_results/ens_tot.zarr \
      --dim time "${ENS_IDX_ARGS[@]}" \
      --output intermediate_results/ens_weeks.zarr
  ENS_SRC=intermediate_results/ens_weeks.zarr
fi

$WS rename \
    --input "$ENS_SRC" \
    --variable precipitation_surface --to-name precip \
    --output intermediate_results/ens_weeks_precip.zarr

# -------------------------------------------------------------- 2. S2S fetch
$WS ecmwf-fetch \
    --date "$S2S_INIT" --bbox "$BBOX" -v tp \
    --output intermediate_results/s2s_raw.zarr

$WS step-to-time \
    --input intermediate_results/s2s_raw.zarr \
    --output intermediate_results/s2s_time.zarr

# Ensemble mean over `number`, keeping lat/lon (this is a map, not a series).
$WS summarize-dim \
    --input intermediate_results/s2s_time.zarr \
    --dim number --method mean \
    --output intermediate_results/s2s_ensmean.zarr

$WS aggregate-temporal \
    --input intermediate_results/s2s_ensmean.zarr \
    --period weekly --method mean --align left \
    --output intermediate_results/s2s_wk.zarr

$WS convert-to-totals \
    --input intermediate_results/s2s_wk.zarr \
    --min-coverage 1.0 \
    --output intermediate_results/s2s_tot.zarr

# Fill the rest of the 6 panels from S2S, starting right after ENS's
# coverage (index ENS_WEEKS..5).
S2S_WEEKS_AVAIL=$(zarr_time_len intermediate_results/s2s_tot.zarr)
S2S_NEEDED=$((6 - ENS_WEEKS))
[ "$S2S_NEEDED" -gt "$S2S_WEEKS_AVAIL" ] && S2S_NEEDED=$S2S_WEEKS_AVAIL
S2S_IDX_ARGS=()
for ((i = ENS_WEEKS; i < ENS_WEEKS + S2S_NEEDED; i++)); do S2S_IDX_ARGS+=(--index "$i"); done

$WS select \
    --input intermediate_results/s2s_tot.zarr \
    --dim time "${S2S_IDX_ARGS[@]}" \
    --output intermediate_results/s2s_weeks.zarr

$WS rename \
    --input intermediate_results/s2s_weeks.zarr \
    --variable tp --to-name precip \
    --output intermediate_results/s2s_weeks_precip.zarr

# S2S is coarser than ENS; put it on the ENS grid so concat lines up.
$WS downscale \
    --input intermediate_results/s2s_weeks_precip.zarr \
    --algorithm linear-interpolation \
    --reference-grid intermediate_results/ens_weeks_precip.zarr \
    --output intermediate_results/s2s_weeks_ds.zarr

# ------------------------------------------------------------- 3. combine + plot
TOTAL_WEEKS=$((ENS_WEEKS + S2S_NEEDED))

$WS concat \
    --input intermediate_results/ens_weeks_precip.zarr \
    --input intermediate_results/s2s_weeks_ds.zarr \
    --dim time \
    --output intermediate_results/combined_weeks.zarr

$WS plot \
    --layer heatmap:intermediate_results/combined_weeks.zarr::variable=precip \
    --layer outline:"$GEOJSON" \
    --mask-geojson "$GEOJSON" \
    --rows 2 --columns 3 \
    --label 'Precip (mm/week)' --label '' \
    --title "Weekly Rainfall Forecast - ${COUNTRY}"$'\n'"ECMWF IFS-ENS wks 1-${ENS_WEEKS} + ECMWF S2S wks $((ENS_WEEKS + 1))-${TOTAL_WEEKS}" \
    --figsize 18,15 --fontsize 32 \
    --output ECMWF_raw_plot.png
