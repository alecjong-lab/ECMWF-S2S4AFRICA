#!/usr/bin/env bash
# OISST SST anomaly, Indian Ocean basin, latest published day, with IOD boxes.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

REPO="git+https://github.com/rhiza-research/forecasting-skills"
# all skills from the dev branch ...
SK="uvx --from $REPO@dev forecasting-skills"
# ... except clim-fetch, from the mohini/skills branch
SK_CLIM="uvx --from $REPO@mohini/skills forecasting-skills"

BBOX="30.0/20.0/-40.0/120.0"   # Indian Ocean basin, from: $SK resolve-region "Indian Ocean"
DAY=$($SK oisst-fetch --probe-latest)
DAY_LABEL=$(pydate "$DAY" '%-d %b %Y')

mkdir -p intermediate_results

# 1. Observed SST for the latest day
$SK oisst-fetch \
  --start-time "$DAY" --end-time "$DAY" \
  --bbox "$BBOX" \
  --output intermediate_results/oisst_io.zarr

# 2. OISST daily climatology for the same day-of-year (same bbox -> identical grid)
$SK_CLIM clim-fetch \
  --dataset oisst --variable sst \
  --start-time "$DAY" --end-time "$DAY" \
  --bbox "$BBOX" \
  --output intermediate_results/oisst_clim.zarr

# 3. Rename sst_avg -> sst so difference can match the variable name
$SK rename \
  --input intermediate_results/oisst_clim.zarr \
  --output intermediate_results/oisst_clim_renamed.zarr \
  --variable sst_avg --to-name sst

# 4. Anomaly = observed - climatological mean
$SK difference \
  --input intermediate_results/oisst_io.zarr \
  --input intermediate_results/oisst_clim_renamed.zarr \
  --variable sst \
  --output intermediate_results/oisst_anomaly.zarr

# 5. Plot, clamped to +/-2 C, with the west (WTIO) and east (SETIO) IOD boxes.
# Output stem must stay sst_global_oisst_nino_iod so the briefing template
# picture (and ai_weather_briefing.py) still match this slide.
$SK plot \
  --layer heatmap:intermediate_results/oisst_anomaly.zarr::variable=sst \
  --label 'SST anom (°C)' \
  --colormap RdBu_r --vmin -2 --vmax 2 \
  --title "SST anomaly · ${DAY_LABEL}" \
  --draw-box 10/50/-10/70 \
  --draw-box 0/90/-10/110 \
  --output sst_global_oisst_nino_iod.png
