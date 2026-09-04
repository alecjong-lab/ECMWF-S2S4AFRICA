#!/usr/bin/env bash
set -eo pipefail
S="git+https://github.com/rhiza-research/forecasting-skills@dev"
BBOX=1.0/36.5/-3.0/39.0   # from: resolve-region "Kenya OND region"

# observed branch
uvx --from $S forecasting-skills chirps-fetch \
  --start-time 2026-08-01 --end-time 2026-08-31 --bbox $BBOX \
  --workers 8 --output chirps_ond.zarr
uvx --from $S forecasting-skills summarize-dim \
  --dim latitude --dim longitude --method mean --lat-weighted \
  --input chirps_ond.zarr --output chirps_ond_mean.zarr

# climatology branch
uvx --from $S forecasting-skills clim-fetch \
  --dataset chirps --variable precip \
  --start-time 2026-08-01 --end-time 2026-12-31 --bbox $BBOX \
  --output clim_ond.zarr
uvx --from $S forecasting-skills summarize-dim \
  --dim latitude --dim longitude --method mean --lat-weighted \
  --variable precip_avg --input clim_ond.zarr --output clim_ond_mean.zarr
uvx --from $S forecasting-skills rename \
  --variable precip_avg --to-name precip \
  --input clim_ond_mean.zarr --output clim_ond_series.zarr

uvx --from $S forecasting-skills plot-timeseries \
  --input chirps_ond_mean.zarr --input clim_ond_series.zarr \
  --variable precip --style bar \
  --trace 2:style=line,color=black,linewidth=2 \
  --label 'CHIRPS observed (2026)' --label 'CHIRPS climatology' \
  --title 'Daily rainfall, Kenya OND region (Aug-Dec 2026) vs climatology' \
  --ylabel 'Rainfall [mm/day]' --fontsize 16 \
  --output kenya_ond_daily_rainfall_vs_climatology.png


S="git+https://github.com/rhiza-research/forecasting-skills@dev"

# observed: weekly means -> weekly totals
uvx --from $S forecasting-skills aggregate-temporal \
  --period weekly --method mean --align left \
  --input chirps_ond_mean.zarr --output chirps_ond_weekly.zarr
uvx --from $S forecasting-skills convert-to-totals \
  --min-coverage 1.0 \
  --input chirps_ond_weekly.zarr --output chirps_ond_weekly_mm.zarr

# climatology: weekly means -> weekly totals
uvx --from $S forecasting-skills aggregate-temporal \
  --period weekly --method mean --align left \
  --input clim_ond_series.zarr --output clim_ond_weekly.zarr
uvx --from $S forecasting-skills convert-to-totals \
  --min-coverage 1.0 \
  --input clim_ond_weekly.zarr --output clim_ond_weekly_mm.zarr

uvx --from $S forecasting-skills plot-timeseries \
  --input chirps_ond_weekly_mm.zarr --input clim_ond_weekly_mm.zarr \
  --variable precip --style bar \
  --trace 2:style=line,color=red,linewidth=2,marker=o \
  --label 'CHIRPS observed (2026)' --label 'CHIRPS climatology' \
  --title 'Weekly rainfall, Kenya OND region (Aug-Dec 2026) vs climatology' \
  --ylabel 'Rainfall [mm/week]' --fontsize 16 \
  --output kenya_ond_weekly_rainfall_vs_climatology.png

S="git+https://github.com/rhiza-research/forecasting-skills@dev"
BBOX=1.0/36.5/-3.0/39.0

# weekly climatology with correct weekly std
uvx --from $S forecasting-skills clim-fetch \
  --dataset chirps --variable precip --window 7 --align left \
  --start-time 2026-08-01 --end-time 2026-12-31 --bbox $BBOX \
  --output clim_ond_w7.zarr

# observed anomaly, per grid cell then area-averaged
uvx --from $S forecasting-skills aggregate-temporal \
  --period weekly --method mean --align left \
  --input chirps_ond.zarr --output chirps_ond_grid_weekly.zarr
uvx --from $S forecasting-skills standardize-anomaly \
  --variable precip --epsilon 0.1 \
  --input chirps_ond_grid_weekly.zarr --climatology clim_ond_w7.zarr \
  --output anom_grid.zarr
uvx --from $S forecasting-skills summarize-dim \
  --dim latitude --dim longitude --method mean --lat-weighted \
  --input anom_grid.zarr --output anom_obs.zarr
# keep only the 4 fully-covered weeks (08-29 was 43% covered)
uvx --from $S forecasting-skills select \
  --dim time --index 0 --index 1 --index 2 --index 3 \
  --input anom_obs.zarr --output anom_obs4.zarr

# zero-fill branch: climatology against itself == 0 everywhere
uvx --from $S forecasting-skills rename \
  --variable precip_avg --to-name precip \
  --input clim_ond_w7.zarr --output clim_w7_renamed.zarr
uvx --from $S forecasting-skills standardize-anomaly \
  --variable precip --epsilon 0.1 \
  --input clim_w7_renamed.zarr --climatology clim_ond_w7.zarr \
  --output zero_grid.zarr
uvx --from $S forecasting-skills summarize-dim \
  --dim latitude --dim longitude --method mean --lat-weighted \
  --input zero_grid.zarr --output zero_series.zarr
# re-aggregate so the coverage coord matches the observed side (concat needs this)
uvx --from $S forecasting-skills aggregate-temporal \
  --period weekly --method mean --align left \
  --input zero_series.zarr --output zero_weekly.zarr
uvx --from $S forecasting-skills select --dim time \
  --index 4 --index 5 --index 6 --index 7 --index 8 --index 9 --index 10 \
  --index 11 --index 12 --index 13 --index 14 --index 15 --index 16 \
  --index 17 --index 18 --index 19 --index 20 \
  --input zero_weekly.zarr --output zero_weeks17.zarr

uvx --from $S forecasting-skills concat \
  --dim time --input anom_obs4.zarr --input zero_weeks17.zarr \
  --output anom_full.zarr

uvx --from $S forecasting-skills plot-timeseries \
  --input anom_full.zarr --variable precip_anomaly --style bar \
  --label 'CHIRPS standardized anomaly' \
  --title 'Weekly standardized rainfall anomaly, Kenya OND region (Aug-Dec 2026)' \
  --ylabel 'Standardized anomaly [z-score]' --fontsize 16 \
  --output kenya_ond_weekly_standardized_anomaly.png