#!/usr/bin/env bash
# Global OISST v2.1 SST with Nino 3.4 and IOD monitoring boxes.
set -eo pipefail

S="git+https://github.com/rhiza-research/forecasting-skills@dev"
run() { uvx --from "$S" forecasting-skills "$@"; }

END=$(run oisst-fetch --probe-latest)

run oisst-fetch \
  --start-time "$END" --end-time "$END" \
  --output oisst_latest.zarr

run plot \
  --style heatmap \
  --variable sst \
  --colormap RdBu_r \
  --extent=-180,180,-90,90 \
  --draw-box 5/-170/-5/-120 \
  --draw-box 10/50/-10/70 \
  --draw-box 0/90/-10/110 \
  --fontsize 16 \
  --title "Global SST with Nino 3.4 and IOD Monitoring Boxes - OISST v2.1, ${END}" \
  --ylabel "Daily Sea Surface Temperature [°C]" \
  --input oisst_latest.zarr \
  --output sst_global_oisst_nino_iod.png
