#!/usr/bin/env bash
# Kenya weekly rainfall totals, Aug-Dec:
#   analog years + current-year observed (CHIRPS) + ECMWF S2S ensemble spread & mean
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
INIT=$($WS ecmwf-fetch --probe-latest)      # latest available S2S init

# ---------------------------------------------------------------- 0. inputs
# Analog years for the current season -> 1982 1997 2006 2015 2019 2023
# 1982 and 1997 are NOT fetched: CHIRPS v3.0 final only reaches back to 1998.
$CHC analog-years --date "$TODAY"

# Kenya bbox + boundary polygon
$WS resolve-region KEN --geojson "$GEOJSON"

# ------------------------------------------------- 1. CHIRPS observed years
for Y in 2006 2015 2019 2023; do
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
for Y in 2006 2015 2019 2023 "$CUR_YEAR"; do
  $WS clip-region \
      --input "intermediate_results/chirps_${Y}.zarr" \
      --geojson "$GEOJSON" \
      --output "intermediate_results/clip_${Y}.zarr"

  $WS summarize-dim \
      --input "intermediate_results/clip_${Y}.zarr" \
      --dim latitude --dim longitude --method mean --lat-weighted \
      --output "intermediate_results/mean_${Y}.zarr"

  $WS aggregate-temporal \
      --input "intermediate_results/mean_${Y}.zarr" \
      --period weekly --method mean --align left \
      --output "intermediate_results/wk_${Y}.zarr"

  $WS convert-to-totals \
      --input "intermediate_results/wk_${Y}.zarr" \
      --min-coverage 1.0 \
      --output "intermediate_results/tot_${Y}.zarr"
done

# --------------------------------------------------- 2. ECMWF S2S ensemble
# Needs ECMWF_DATASTORES_URL and ECMWF_DATASTORES_KEY in the environment.
# Real-time S2S is embargoed 2 days; ecmwf-fetch --probe-latest ($INIT)
# already accounts for that embargo, so no extra offset is needed here.
$WS ecmwf-fetch \
    --date "$INIT" --bbox "$BBOX" -v tp \
    --output intermediate_results/s2s_raw.zarr

$WS clip-region \
    --input intermediate_results/s2s_raw.zarr \
    --geojson "$GEOJSON" \
    --output intermediate_results/s2s_clip.zarr

# step-to-time BEFORE the spatial reduction: it requires the lat/lon dims
# to still be present, so reducing first fails.
$WS step-to-time \
    --input intermediate_results/s2s_clip.zarr \
    --output intermediate_results/s2s_time.zarr

# Reduce space but KEEP `number` so members survive as trajectories.
$WS summarize-dim \
    --input intermediate_results/s2s_time.zarr \
    --dim latitude --dim longitude --method mean --lat-weighted \
    --output intermediate_results/s2s_mean2.zarr

$WS aggregate-temporal \
    --input intermediate_results/s2s_mean2.zarr \
    --period weekly --method mean --align left \
    --output intermediate_results/s2s_wk.zarr

$WS convert-to-totals \
    --input intermediate_results/s2s_wk.zarr \
    --min-coverage 1.0 \
    --output intermediate_results/s2s_tot.zarr

# Rename tp -> precip so the forecast shares one axis with the CHIRPS series.
$WS rename \
    --input intermediate_results/s2s_tot.zarr \
    --variable tp --to-name precip \
    --output intermediate_results/s2s_final.zarr

# Ensemble mean: same store as the spread, reduced over `number`.
$WS summarize-dim \
    --input intermediate_results/s2s_final.zarr \
    --dim number --method mean \
    --output intermediate_results/s2s_ensmean.zarr

# ------------------------------------------------------------- 3. the plot
# Analog years: thin colored lines. Current-year CHIRPS: heavy black.
# S2S members: crimson spaghetti (--along number). Ensemble mean on top.
# --trace selectors use full labels so "${CUR_YEAR}" is not ambiguous.
$WS plot-timeseries \
    --input intermediate_results/tot_2006.zarr \
    --input intermediate_results/tot_2015.zarr \
    --input intermediate_results/tot_2019.zarr \
    --input intermediate_results/tot_2023.zarr \
    --input "intermediate_results/tot_${CUR_YEAR}.zarr" \
    --input intermediate_results/s2s_final.zarr \
    --input intermediate_results/s2s_ensmean.zarr \
    --label '2006 (analog)' \
    --label '2015 (analog)' \
    --label '2019 (analog)' \
    --label '2023 (analog)' \
    --label "${CUR_YEAR} observed (CHIRPS)" \
    --label "${CUR_YEAR} ECMWF S2S members" \
    --label 'ECMWF S2S ensemble mean' \
    --variable precip \
    --along number \
    --align-day-of-year \
    --trace "${CUR_YEAR} observed (CHIRPS):color=black,linewidth=3.5,zorder=10" \
    --trace "${CUR_YEAR} ECMWF S2S members:color=crimson,linewidth=0.5,zorder=3" \
    --trace 'ECMWF S2S ensemble mean:color=purple,linewidth=2.5,zorder=8' \
    --trace '2006 (analog):linewidth=1.4' \
    --trace '2015 (analog):linewidth=1.4' \
    --trace '2019 (analog):linewidth=1.4' \
    --trace '2023 (analog):linewidth=1.4' \
    --title "Kenya weekly rainfall totals, Aug-Dec: analog years vs ${CUR_YEAR} + ECMWF S2S ensemble (init ${INIT})" \
    --ylabel 'Weekly rainfall total (mm)' \
    --fontsize 15 \
    --figsize 16,9 \
    --output kenya_weekly_rainfall_analog_years.png