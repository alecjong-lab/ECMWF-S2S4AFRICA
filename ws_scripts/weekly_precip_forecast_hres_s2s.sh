#!/usr/bin/env bash
# Combined 6-week precip forecast: HRES first (weeks depend on the latest
# cycle - 2 for 00Z/12Z, 1 for 06Z/18Z), ECMWF S2S ensemble mean fills the
# rest, one 6-panel heatmap.
set -eo pipefail

# shellcheck source=../local_workflows/load_secrets.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/local_workflows/load_secrets.sh"
# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

# ---------------------------------------------------------------- skill pins
# weather-skills @dev for everything except HRES fetch, which only exists on
# the mohini/skills branch so far.
WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"
HRES="uvx --from git+https://github.com/rhiza-research/weather-skills@mohini/skills forecasting-skills"

# CDSAPI_KEY/ECMWF_DATASTORES_KEY (from load_secrets.sh) must be set, or both
# fetch tools fall back to a public mirror instead of the real ECMWF source.
: "${ECMWF_DATASTORES_KEY:?ECMWF_DATASTORES_KEY not set - check .env}"

COUNTRY="${COUNTRY:-KEN}"
GEOJSON="intermediate_results/${COUNTRY,,}.geojson"
mkdir -p intermediate_results

# Country bbox + boundary polygon. resolve-region prints "N/W/S/E" last.
BBOX=$($WS resolve-region "$COUNTRY" --geojson "$GEOJSON" | tail -n1)

# ---------------------------------------------------------------- init dates
# HRES_INIT_OVERRIDE / S2S_INIT_OVERRIDE: pin an older init locally, e.g. to
# skip an embargo window. Neither is read anywhere in .github/.
HRES_INIT="${HRES_INIT_OVERRIDE:-$($HRES ecmwf-hres-fetch --probe-latest)}"  # YYYY-MM-DDTHH
HRES_DATE="${HRES_INIT%%T*}"
HRES_RUN=$((10#${HRES_INIT##*T}))
S2S_INIT="${S2S_INIT_OVERRIDE:-$($WS ecmwf-fetch --probe-latest)}"

# time-dim length of a zarr, via inspect-zarr's JSON output.
zarr_time_len() {
  $WS inspect-zarr --input "$1" --format json \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['dims']['time'])"
}

# ------------------------------------------------------------- 1. HRES fetch
$HRES ecmwf-hres-fetch \
    --date "$HRES_DATE" --run "$HRES_RUN" -v tp \
    --bbox "$BBOX" \
    -o intermediate_results/hres_tp.zarr

$WS step-to-time \
    --input intermediate_results/hres_tp.zarr \
    --output intermediate_results/hres_time.zarr

$WS aggregate-temporal \
    --input intermediate_results/hres_time.zarr \
    --period weekly --method mean --align left \
    --output intermediate_results/hres_wk.zarr

# 06Z/18Z cycles only publish 144h (6 days), so week 2 can be short a day;
# 0.85 still requires 6/7 days.
$WS convert-to-totals \
    --input intermediate_results/hres_wk.zarr \
    --min-coverage 0.85 \
    --output intermediate_results/hres_tot.zarr

# Up to 2 full weeks. 00Z/12Z publish 15 days (2 weeks); 06Z/18Z only 144h
# (~1 week), so HRES_WEEKS can come out at 1 depending on the latest cycle.
HRES_WEEKS_AVAIL=$(zarr_time_len intermediate_results/hres_tot.zarr)
HRES_WEEKS=$HRES_WEEKS_AVAIL
[ "$HRES_WEEKS" -gt 2 ] && HRES_WEEKS=2

# select with a single --index drops the time dim entirely (needed by
# concat below), so only trim when actually shrinking (avail > 2).
HRES_SRC=intermediate_results/hres_tot.zarr
if [ "$HRES_WEEKS" -lt "$HRES_WEEKS_AVAIL" ]; then
  HRES_IDX_ARGS=()
  for ((i = 0; i < HRES_WEEKS; i++)); do HRES_IDX_ARGS+=(--index "$i"); done
  $WS select \
      --input intermediate_results/hres_tot.zarr \
      --dim time "${HRES_IDX_ARGS[@]}" \
      --output intermediate_results/hres_weeks.zarr
  HRES_SRC=intermediate_results/hres_weeks.zarr
fi

$WS rename \
    --input "$HRES_SRC" \
    --variable tp --to-name precip \
    --output intermediate_results/hres_weeks_precip.zarr

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

# Fill the rest of the 6 panels from S2S, starting right after HRES's
# coverage (index HRES_WEEKS..5).
S2S_WEEKS_AVAIL=$(zarr_time_len intermediate_results/s2s_tot.zarr)
S2S_NEEDED=$((6 - HRES_WEEKS))
[ "$S2S_NEEDED" -gt "$S2S_WEEKS_AVAIL" ] && S2S_NEEDED=$S2S_WEEKS_AVAIL
S2S_IDX_ARGS=()
for ((i = HRES_WEEKS; i < HRES_WEEKS + S2S_NEEDED; i++)); do S2S_IDX_ARGS+=(--index "$i"); done

$WS select \
    --input intermediate_results/s2s_tot.zarr \
    --dim time "${S2S_IDX_ARGS[@]}" \
    --output intermediate_results/s2s_weeks.zarr

$WS rename \
    --input intermediate_results/s2s_weeks.zarr \
    --variable tp --to-name precip \
    --output intermediate_results/s2s_weeks_precip.zarr

# S2S is coarser than HRES; put it on the HRES grid so concat lines up.
$WS downscale \
    --input intermediate_results/s2s_weeks_precip.zarr \
    --algorithm linear-interpolation \
    --reference-grid intermediate_results/hres_weeks_precip.zarr \
    --output intermediate_results/s2s_weeks_ds.zarr

# ------------------------------------------------------------- 3. combine + plot
TOTAL_WEEKS=$((HRES_WEEKS + S2S_NEEDED))

$WS concat \
    --input intermediate_results/hres_weeks_precip.zarr \
    --input intermediate_results/s2s_weeks_ds.zarr \
    --dim time \
    --output intermediate_results/combined_weeks.zarr

$WS plot \
    --layer heatmap:intermediate_results/combined_weeks.zarr::variable=precip \
    --layer outline:"$GEOJSON" \
    --mask-geojson "$GEOJSON" \
    --rows 2 --columns 3 \
    --label 'Precip (mm/week)' --label '' \
    --title "Weekly Rainfall Forecast - ${COUNTRY}"$'\n'"HRES [w1-w${HRES_WEEKS}] + ECMWF S2S [w$((HRES_WEEKS + 1))-w${TOTAL_WEEKS}]" \
    --figsize 18,15 --fontsize 32 \
    --output ECMWF_raw_plot.png
