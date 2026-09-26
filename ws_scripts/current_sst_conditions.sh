#!/usr/bin/env bash
# OISST SST anomaly, Indian Ocean basin, latest published day, with IOD boxes.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

REPO="git+https://github.com/rhiza-research/forecasting-skills"
# all skills from the dev branch ...
SK="uvx --from $REPO@old-dev forecasting-skills"
# ... except clim-fetch, from the mohini/skills branch
SK_CLIM="uvx --from $REPO@mohini/skills forecasting-skills"

BBOX="30.0/20.0/-40.0/120.0"   # Indian Ocean basin, from: $SK resolve-region "Indian Ocean"

# NOAA PSL OPeNDAP 502s used to crash --probe-latest (HTML page parsed as
# a dataset). Retry, then fall back to now-2d (OISST lags ~1 day).
DAY=""
for attempt in 1 2 3; do
  if DAY=$($SK oisst-fetch --probe-latest) && [[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    break
  fi
  DAY=""
  echo "oisst-fetch --probe-latest failed (attempt ${attempt}/3)" >&2
  sleep $((attempt * 8))
done
if [[ -z "$DAY" ]]; then
  echo "oisst-fetch --probe-latest failed; falling back to now-2d" >&2
  DAY=$($SK resolve-time now-2d --emit iso)
fi
DAY_LABEL=$(pydate "$DAY" '%-d %b %Y')

mkdir -p intermediate_results

# 1. Observed SST for the latest day
fetch_ok=0
for attempt in 1 2 3; do
  if $SK oisst-fetch \
    --start-time "$DAY" --end-time "$DAY" \
    --bbox "$BBOX" \
    --output intermediate_results/oisst_io.zarr; then
    fetch_ok=1
    break
  fi
  echo "oisst-fetch failed (attempt ${attempt}/3); retrying..." >&2
  sleep $((attempt * 8))
done
[[ "$fetch_ok" -eq 1 ]]

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
