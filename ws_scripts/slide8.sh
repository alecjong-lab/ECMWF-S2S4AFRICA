#!/usr/bin/env bash
set -eo pipefail

SKILLS="git+https://github.com/rhiza-research/forecasting-skills@dev"

uvx --from "$SKILLS" forecasting-skills chirps-fetch \
  --bbox 17.998307/21.887843/-26.742192/51.13387 \
  --start-time 2026-08-04 --end-time 2026-08-31 --workers 8 \
  --output step1.zarr

uvx --from "$SKILLS" forecasting-skills aggregate-temporal \
  --align left --end-time 2026-09-01 --method mean --period weekly \
  --input step1.zarr --output step2.zarr

uvx --from "$SKILLS" forecasting-skills convert-to-totals \
  --min-coverage 1.0 --input step2.zarr --output step3.zarr

uvx --from "$SKILLS" forecasting-skills plot \
  --columns 4 --rows 1 --fontsize 13 --pair-on time --style heatmap \
  --title 'CHIRPS weekly rainfall totals, East Africa (4 weeks ending 2026-08-31)' \
  --variable precip --input step3.zarr \
  --output chirps_east_africa_weekly_rainfall.png

set -eo pipefail

SKILLS="git+https://github.com/rhiza-research/forecasting-skills@dev"
CLIM_SKILLS="git+https://github.com/rhiza-research/forecasting-skills@mohini/skills"

# --- climatology baseline (mohini/skills) ---
uvx --from "$CLIM_SKILLS" forecasting-skills clim-fetch \
  --dataset chirps --variable precip --window 7 --align left \
  --start-time 2026-08-04 --end-time 2026-08-25 \
  --bbox 17.998307/21.887843/-26.742192/51.13387 \
  --output chirps_clim_7d.zarr

uvx --from "$CLIM_SKILLS" forecasting-skills rename \
  --variable precip_avg --to-name precip \
  --input chirps_clim_7d.zarr --output chirps_clim_7d_renamed.zarr

# --- observations (dev) ---
uvx --from "$SKILLS" forecasting-skills chirps-fetch \
  --bbox 17.998307/21.887843/-26.742192/51.13387 \
  --start-time 2026-08-04 --end-time 2026-08-31 --workers 8 \
  --output step1.zarr

uvx --from "$SKILLS" forecasting-skills aggregate-temporal \
  --align left --end-time 2026-09-01 --method mean --period weekly \
  --input step1.zarr --output step2.zarr

# --- anomaly (dev) ---
uvx --from "$SKILLS" forecasting-skills difference \
  --variable precip --input step2.zarr --input chirps_clim_7d_renamed.zarr \
  --output step3.zarr

uvx --from "$SKILLS" forecasting-skills convert-to-totals \
  --min-coverage 1.0 --input step3.zarr --output step4.zarr

uvx --from "$SKILLS" forecasting-skills rename \
  --to-name precip_anomaly --variable precip --input step4.zarr --output step5.zarr

uvx --from "$SKILLS" forecasting-skills plot \
  --columns 4 --rows 1 --fontsize 13 --pair-on time --style heatmap \
  --title 'CHIRPS weekly rainfall anomaly vs climatology, East Africa (4 weeks ending 2026-08-31)' \
  --variable precip_anomaly --input step5.zarr \
  --output chirps_east_africa_weekly_anomaly.png