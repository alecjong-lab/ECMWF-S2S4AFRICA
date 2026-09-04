#!/usr/bin/env bash
set -eo pipefail
S="git+https://github.com/rhiza-research/forecasting-skills@dev"
BBOX=1.0/36.5/-3.0/39.0   # from: resolve-region "Kenya OND region"

# OND season (Aug-Dec, current year) + latest available CHIRPS day.
CUR_YEAR=$(date -u +%Y)
SEASON_START="${CUR_YEAR}-08-01"
SEASON_END="${CUR_YEAR}-12-31"
END=$(uvx --from $S forecasting-skills chirps-fetch --probe-latest)

# observed branch
uvx --from $S forecasting-skills chirps-fetch \
  --start-time "$SEASON_START" --end-time "$END" --bbox $BBOX \
  --workers 8 --output chirps_ond.zarr
uvx --from $S forecasting-skills summarize-dim \
  --dim latitude --dim longitude --method mean --lat-weighted \
  --input chirps_ond.zarr --output chirps_ond_mean.zarr

# climatology branch
uvx --from $S forecasting-skills clim-fetch \
  --dataset chirps --variable precip \
  --start-time "$SEASON_START" --end-time "$SEASON_END" --bbox $BBOX \
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
  --label "CHIRPS observed (${CUR_YEAR})" --label 'CHIRPS climatology' \
  --title "Daily rainfall, Kenya OND region (Aug-Dec ${CUR_YEAR}) vs climatology" \
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
  --label "CHIRPS observed (${CUR_YEAR})" --label 'CHIRPS climatology' \
  --title "Weekly rainfall, Kenya OND region (Aug-Dec ${CUR_YEAR}) vs climatology" \
  --ylabel 'Rainfall [mm/week]' --fontsize 16 \
  --output kenya_ond_weekly_rainfall_vs_climatology.png

S="git+https://github.com/rhiza-research/forecasting-skills@dev"
BBOX=1.0/36.5/-3.0/39.0

# weekly climatology with correct weekly std
uvx --from $S forecasting-skills clim-fetch \
  --dataset chirps --variable precip --window 7 --align left \
  --start-time "$SEASON_START" --end-time "$SEASON_END" --bbox $BBOX \
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

# Number of fully-covered observed weeks (left-aligned weekly bins from
# SEASON_START) vs. the total weekly bins the OND climatology season spans —
# both computed from actual day counts so a partial trailing week is dropped
# regardless of how many days $END happens to cover.
OBS_DAYS=$(( ( $(date -u -d "$END" +%s) - $(date -u -d "$SEASON_START" +%s) ) / 86400 + 1 ))
OBS_WEEKS=$(( OBS_DAYS / 7 ))
SEASON_DAYS=$(( ( $(date -u -d "$SEASON_END" +%s) - $(date -u -d "$SEASON_START" +%s) ) / 86400 + 1 ))
SEASON_WEEKS=$(( SEASON_DAYS / 7 ))

OBS_IDX_ARGS=()
for ((i = 0; i < OBS_WEEKS; i++)); do
  OBS_IDX_ARGS+=(--index "$i")
done

uvx --from $S forecasting-skills select \
  --dim time "${OBS_IDX_ARGS[@]}" \
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

ZERO_IDX_ARGS=()
for ((i = OBS_WEEKS; i < SEASON_WEEKS; i++)); do
  ZERO_IDX_ARGS+=(--index "$i")
done

uvx --from $S forecasting-skills select --dim time \
  "${ZERO_IDX_ARGS[@]}" \
  --input zero_weekly.zarr --output zero_weeks17.zarr

uvx --from $S forecasting-skills concat \
  --dim time --input anom_obs4.zarr --input zero_weeks17.zarr \
  --output anom_full.zarr

uvx --from $S forecasting-skills plot-timeseries \
  --input anom_full.zarr --variable precip_anomaly --style bar \
  --label 'CHIRPS standardized anomaly' \
  --title "Weekly standardized rainfall anomaly, Kenya OND region (Aug-Dec ${CUR_YEAR})" \
  --ylabel 'Standardized anomaly [z-score]' --fontsize 16 \
  --output kenya_ond_weekly_standardized_anomaly.png