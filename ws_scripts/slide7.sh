#!/usr/bin/env bash
set -eo pipefail

DEV="git+https://github.com/rhiza-research/forecasting-skills@dev"

# Kenya boundary polygon (needed by clip-region)
uvx --from "$DEV" forecasting-skills resolve-region KEN \
  --geojson kenya.geojson

uvx --from "$DEV" forecasting-skills chirps-fetch \
  --bbox 5.506/33.893569/-4.67677/41.855083 \
  --start-time 2026-08-04 --end-time 2026-08-31 \
  --workers 8 --output step1.zarr

uvx --from "$DEV" forecasting-skills clip-region \
  --geojson kenya.geojson \
  --input step1.zarr --output step2.zarr

uvx --from "$DEV" forecasting-skills aggregate-temporal \
  --align left --method mean --period weekly \
  --input step2.zarr --output step3.zarr

uvx --from "$DEV" forecasting-skills convert-to-totals \
  --min-coverage 1.0 \
  --input step3.zarr --output step4.zarr

uvx --from "$DEV" forecasting-skills plot \
  --columns 4 --fontsize 13 --pair-on time --style heatmap \
  --title 'CHIRPS weekly rainfall totals — Kenya (4 Aug – 31 Aug 2026)' \
  --variable precip \
  --input step4.zarr --output chirps_kenya_weekly_rainfall.png

DEV="git+https://github.com/rhiza-research/forecasting-skills@dev"
CLIM="git+https://github.com/rhiza-research/forecasting-skills@mohini/skills"

BBOX=5.506/33.893569/-4.67677/41.855083

# --- observation branch (same as script 1, through weekly means) ---
uvx --from "$DEV" forecasting-skills resolve-region KEN \
  --geojson kenya.geojson

uvx --from "$DEV" forecasting-skills chirps-fetch \
  --bbox "$BBOX" \
  --start-time 2026-08-04 --end-time 2026-08-31 \
  --workers 8 --output step1.zarr

uvx --from "$DEV" forecasting-skills clip-region \
  --geojson kenya.geojson \
  --input step1.zarr --output step2.zarr

uvx --from "$DEV" forecasting-skills aggregate-temporal \
  --align left --method mean --period weekly \
  --input step2.zarr --output step3.zarr

# --- climatology branch (mohini/skills) ---
uvx --from "$CLIM" forecasting-skills clim-fetch \
  --dataset chirps --variable precip \
  --window 7 --align left \
  --start-time 2026-08-04 --end-time 2026-08-31 \
  --bbox "$BBOX" \
  -o clim1.zarr

uvx --from "$DEV" forecasting-skills select \
  --dim time \
  --value 2026-08-04 --value 2026-08-11 \
  --value 2026-08-18 --value 2026-08-25 \
  --input clim1.zarr --output clim2.zarr

uvx --from "$DEV" forecasting-skills rename \
  --variable precip_avg --to-name precip \
  --input clim2.zarr --output clim3.zarr

# --- anomaly ---
uvx --from "$DEV" forecasting-skills difference \
  --variable precip \
  --input step3.zarr --input clim3.zarr \
  --output step4.zarr

uvx --from "$DEV" forecasting-skills convert-to-totals \
  --min-coverage 1.0 \
  --input step4.zarr --output step5.zarr

uvx --from "$DEV" forecasting-skills rename \
  --variable precip --to-name precip_anomaly \
  --input step5.zarr --output step6.zarr

uvx --from "$DEV" forecasting-skills plot \
  --columns 4 --fontsize 13 --pair-on time --style heatmap \
  --title 'CHIRPS weekly rainfall anomaly vs climatology — Kenya (4 Aug – 31 Aug 2026)' \
  --variable precip_anomaly \
  --input step6.zarr --output chirps_kenya_weekly_anomaly.png