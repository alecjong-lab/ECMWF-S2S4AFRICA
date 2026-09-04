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
# --along number fans s2s_final's 101 members into one grey legend entry.
# --trace selectors are 1-based --input indices (the token "2026" is
# ambiguous across two labels, so index selectors are required here).
$WS plot-timeseries \
    --input intermediate_results/tot_2006.zarr \
    --input intermediate_results/tot_2015.zarr \
    --input intermediate_results/tot_2019.zarr \
    --input intermediate_results/tot_2023.zarr \
    --input "intermediate_results/tot_${CUR_YEAR}.zarr" \
    --input intermediate_results/s2s_final.zarr \
    --input intermediate_results/s2s_ensmean.zarr \
    --label 2006 \
    --label 2015 \
    --label 2019 \
    --label 2023 \
    --label "${CUR_YEAR} CHIRPS (observed)" \
    --label "ECMWF S2S ${INIT} (101 members)" \
    --label 'ECMWF S2S ensemble mean' \
    --variable precip \
    --along number \
    --align-day-of-year \
    --style line \
    --title "Kenya weekly rainfall totals, Aug-Dec: analog years, ${CUR_YEAR} observed, and ECMWF S2S ensemble" \
    --ylabel 'Weekly rainfall total (mm)' \
    --trace 1:zorder=5 \
    --trace 2:zorder=5 \
    --trace 3:zorder=5 \
    --trace 4:zorder=5 \
    --trace 5:color=black,linewidth=3,marker=o,zorder=10 \
    --trace 6:color=grey,linewidth=0.6,zorder=1 \
    --trace 7:color=purple,linewidth=3,marker=s,zorder=9 \
    --fontsize 16 \
    --output kenya_weekly_rainfall_analog_years.png