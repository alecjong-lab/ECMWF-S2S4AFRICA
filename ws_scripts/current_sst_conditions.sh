#!/usr/bin/env bash
# Global OISST v2.1 SST with Nino 3.4 and IOD monitoring boxes.
set -eo pipefail

S="git+https://github.com/rhiza-research/forecasting-skills@dev"
run() { uvx --from "$S" forecasting-skills "$@"; }

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

END=$(run oisst-fetch --probe-latest)
TITLE_DATE=$(pydate "$END" "%-d %b %Y")
TITLE_DATE="${TITLE_DATE/Sep /Sept }"

run oisst-fetch \
  --start-time "$END" --end-time "$END" \
  --output oisst_latest.zarr

run select \
  --dim time --index 0 \
  --input oisst_latest.zarr \
  --output sst_map.zarr

run plot \
  --colormap RdYlBu_r \
  --figsize 18,9 \
  --style heatmap \
  --title "Global SST — ${TITLE_DATE} (NOAA OISST v2.1) with IOD West/East and Niño 3.4 boxes" \
  --draw-box 10/50/-10/70 \
  --draw-box 0/90/-10/110 \
  --draw-box 5/-170/-5/-120 \
  --variable sst \
  --input sst_map.zarr \
  --output sst_global_oisst_nino_iod.png
