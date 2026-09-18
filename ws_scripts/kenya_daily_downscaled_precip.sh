#!/usr/bin/env bash
# Weekly precip on Monday-start calendar weeks. Day-to-day briefings keep
# the same week bins. The canvas is a static September–December 4×4 (first
# four Mondays in each month). Each day is CHIRPS before init and the
# forecast from init through the last valid forecast day, so the
# transition week can be half obs / half forecast. Weeks after the
# forecast stay blank. Matching canvases so the slides flip:
#   kenya_daily_downscaled_precip[_anomaly]  — CHIRPS-resolution S2S ensemble-mean
#   kenya_aifs_daily_precip[_anomaly]        — dynamical.org AIFS-ENS (0.25°)
#   kenya_gefs_daily_precip[_anomaly]        — dynamical.org GEFS 35-day (0.25°)
# Also writes kenya_daily_downscaled_onset (ICPAC onset on the same daily
# composite, last/max value in each Monday week) plus a CHIRPS "already
# occurred" overlay.
#
# Output stems must stay as above so the briefing template pictures
# (and ai_weather_briefing.py) still match these slides.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

# kenya-forecast-fetch, dynamical-fetch, clim-fetch, and plot all come from
# weather-skills @dev.
WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"

# Kenya product extent used by the archive weekly/daily maps (N/W/S/E).
BBOX="7/32/-6/43"
COLS=4
ROWS=4
# Kenya-extent panel plus a little extra width so long week titles do not collide.
FIGSIZE="17.973,20.000"
PLOT_LAYOUT='{"layout":{"colorbar":{"pad":0.02},"facet":{"wspace":0.04,"hspace":0.02}},"theme":{"rc":{"xtick.labelsize":28,"axes.labelsize":28}}}'

mkdir -p intermediate_results
IR=intermediate_results

# Do not inherit DATE_STR: KMSA daily inits lag the briefing date. Pin with
# INIT_OVERRIDE for a local rerun.
INIT="${INIT_OVERRIDE:-$($WS kenya-forecast-fetch --probe-latest precip_downscaled_daily)}"
YEAR="${INIT:0:4}"

# First four Mondays of Sept–Dec (static 4×4 briefing slots).
WEEKS=()
for month in 09 10 11 12; do
  first="${YEAR}-${month}-01"
  dow=$(pydate "$first" %u)
  add=$(( (8 - dow) % 7 ))
  monday=$(pydate "$first +${add} days" %Y-%m-%d)
  n=0
  while [[ $(pydate "$monday" %m) == "$month" && $n -lt 4 ]]; do
    WEEKS+=("$monday")
    monday=$(pydate "$monday +7 days" %Y-%m-%d)
    n=$((n + 1))
  done
done
GRID_START="${WEEKS[0]}"
GRID_END_EXCL=$(pydate "${WEEKS[15]} +7 days" %Y-%m-%d)
CHIRPS_END=$(pydate "$INIT -1 days" %Y-%m-%d)

# Reindex onto the static 16 Monday slots. Missing weeks stay all-NaN so
# plot --rows 4 --columns 4 keeps blank panels instead of shrinking.
pad_to_sond() {
  local src="$1" dest="$2"
  uv run --python 3.12 --with xarray --with zarr --with numpy - \
    "$src" "$dest" "${WEEKS[@]}" <<'PY'
import sys
import numpy as np
import xarray as xr

src, dest, *weeks = sys.argv[1:]
ds = xr.open_zarr(src).load()
axis = np.array(weeks, dtype="datetime64[ns]")
ds.reindex(time=axis).to_zarr(dest, mode="w")
PY
}

last_time() {
  $WS inspect-zarr --input "$1" --format json --max-values 0 | python3 -c '
import json, sys
coords = json.load(sys.stdin)["coords"]
axis = next(c for c in coords if c["name"] == "time")
print(str(axis["values"][-1])[:10])
'
}

week_value_flags() {
  $WS inspect-zarr --input "$1" --format json --max-values 0 | python3 -c '
import json, sys
coords = json.load(sys.stdin)["coords"]
axis = next(c for c in coords if c["name"] == "time")
for v in axis["values"]:
    print(str(v)[:10])
'
}

# Daily rate on a wall-clock time axis, variable renamed to precip.
prep_forecast_daily() {
  local raw="$1" dest="$2" var="$3" reduce_ens="$4"
  local stem="${dest%.zarr}"
  $WS aggregate-temporal \
    --input "$raw" --period daily --method mean \
    --output "${stem}_1d.zarr"
  local cur="${stem}_1d.zarr"
  if [[ "$reduce_ens" == "1" ]]; then
    $WS summarize-dim \
      --input "$cur" --dim number --method mean \
      --output "${stem}_ensmean.zarr"
    cur="${stem}_ensmean.zarr"
  fi
  $WS step-to-time --input "$cur" --output "${stem}_time.zarr"
  $WS rename \
    --input "${stem}_time.zarr" --variable "$var" --to-name precip \
    --output "${stem}_named.zarr"
  $WS unit-convert \
    --input "${stem}_named.zarr" --to-standard \
    --output "$dest"
}

# Put a daily field on a reference grid as precip (mm day-1). Daily
# aggregate last so every piece carries aggregation_coverage (concat
# requires that coord on all inputs).
align_to_grid() {
  local src="$1" dest="$2" var="$3" grid="$4"
  local stem="${dest%.zarr}"
  $WS coarsen \
    --input "$src" --variable "$var" --reference-grid "$grid" \
    --output "${stem}_grid.zarr"
  $WS rename \
    --input "${stem}_grid.zarr" --variable "$var" --to-name precip \
    --output "${stem}_named.zarr"
  $WS unit-convert \
    --input "${stem}_named.zarr" --to-standard \
    --output "${stem}_std.zarr"
  $WS aggregate-temporal \
    --input "${stem}_std.zarr" --period daily --method mean \
    --output "$dest"
}

# CHIRPS (before init) + forecast. Monday weekly totals; later 4×4 slots stay empty.
compose_sond() {
  local fcst="$1" dest_daily="$2" dest_weekly="$3"
  local stem="${dest_weekly%.zarr}"
  LAST_FCST=$(last_time "$fcst")

  local chirps_z="" fcst_z="$fcst" grid_ref=""
  if [[ "$CHIRPS_END" > "$GRID_START" || "$CHIRPS_END" == "$GRID_START" ]]; then
    align_to_grid \
      "$IR/kenya_chirps_obs.zarr" "${stem}_chirps.zarr" precip "$fcst"
    chirps_z="${stem}_chirps.zarr"
    grid_ref="$chirps_z"
  fi
  # Coarsen the forecast onto the obs axis so lat/lon match exactly
  # (coarsen follows the input's lat direction, not the reference's).
  if [[ -n "$grid_ref" ]]; then
    align_to_grid "$fcst" "${stem}_fcst.zarr" precip "$grid_ref"
    fcst_z="${stem}_fcst.zarr"
  fi
  local parts=()
  [[ -n "$chirps_z" ]] && parts+=("$chirps_z")
  parts+=("$fcst_z")

  local concat_in=()
  local p
  for p in "${parts[@]}"; do
    concat_in+=(--input "$p")
  done
  if [[ ${#parts[@]} -ge 2 ]]; then
    $WS concat "${concat_in[@]}" --dim time --output "$dest_daily"
  else
    dest_daily="$fcst"
  fi

  $WS aggregate-temporal \
    --input "$dest_daily" --period weekly --method mean \
    --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
    --output "${stem}_wk_rate.zarr"
  $WS convert-to-totals \
    --input "${stem}_wk_rate.zarr" --min-coverage 1.0 \
    --output "${stem}_wk_mm.zarr"
  pad_to_sond "${stem}_wk_mm.zarr" "$dest_weekly"
}

week_source() {
  local mon="$1"
  local sun mon_s sun_s init_s last_s
  sun=$(pydate "$mon +6 days" %Y-%m-%d)
  mon_s=$(pydate "$mon" %s)
  sun_s=$(pydate "$sun" %s)
  init_s=$(pydate "$INIT" %s)
  last_s=$(pydate "$LAST_FCST" %s)
  if (( sun_s < init_s )); then
    echo obs
  elif (( sun_s > last_s )); then
    echo ""
  elif (( mon_s < init_s )); then
    echo obs+forecast
  else
    echo forecast
  fi
}

# Colored OBS / FORECAST badges via plot --patch annotations.
write_source_patch() {
  local dest="$1"
  local anns="" i=0 src color label last_s
  last_s=$(pydate "$LAST_FCST" %s)
  for monday in "${WEEKS[@]}"; do
    if (( $(pydate "$monday" %s) <= last_s )); then
      src=$(week_source "$monday")
      case "$src" in
        obs) color="#1b9e77"; label="OBS" ;;
        obs+forecast) color="#d95f02"; label="OBS + FORECAST" ;;
        forecast) color="#b22222"; label="FORECAST" ;;
        *) src="" ;;
      esac
      if [[ -n "$src" ]]; then
        [[ -n "$anns" ]] && anns+=","
        anns+="{\"panel\":${i},\"text\":\"${label}\",\"x\":0.5,\"y\":0.04,\"transform\":\"axes\",\"ha\":\"center\",\"va\":\"bottom\",\"fontsize\":12,\"fontweight\":\"bold\",\"color\":\"white\",\"zorder\":10,\"bbox\":{\"facecolor\":\"${color}\",\"edgecolor\":\"none\",\"boxstyle\":\"round,pad=0.28\"}}"
      fi
    fi
    i=$((i + 1))
  done
  printf '%s' "${PLOT_LAYOUT%\}},\"annotations\":[${anns}]}" >"$dest"
}

plot_weekly() {
  local input="$1" title="$2" output="$3" patch="$4"
  $WS plot \
    --input "$input" \
    --variable precip \
    --bbox "$BBOX" \
    --rows "$ROWS" --columns "$COLS" \
    --figsize "$FIGSIZE" \
    --fontsize 22 \
    --colormap ppt_week \
    --title "$title" \
    --cbar-label "Weekly rainfall [mm]" \
    --patch "$patch" \
    --output "$output"
}

plot_weekly_anomaly() {
  local input="$1" title="$2" output="$3" patch="$4"
  $WS plot \
    --input "$input" \
    --variable precip_anomaly \
    --bbox "$BBOX" \
    --rows "$ROWS" --columns "$COLS" \
    --figsize "$FIGSIZE" \
    --fontsize 22 \
    --colormap ppt_anomaly \
    --title "$title" \
    --cbar-label "Weekly rainfall anomaly [mm]" \
    --patch "$patch" \
    --output "$output"
}

# Put clim onto the weekly cube's exact lat/lon (coarsen if equal/coarser,
# downscale if the weekly grid is finer).
align_clim_to() {
  local src="$1" dest="$2" grid="$3"
  if $WS coarsen \
      --input "$src" --variable precip --reference-grid "$grid" \
      --output "$dest"; then
    return 0
  fi
  $WS downscale \
    --input "$src" --variable precip --reference-grid "$grid" \
    --algorithm linear-interpolation \
    --output "$dest"
}

# Weekly total minus CHIRPS climatological weekly total (physical mm anomaly).
make_weekly_anomaly() {
  local weekly="$1" dest="$2"
  local stem="${dest%.zarr}"
  align_clim_to \
    "$IR/kenya_chirps_clim_wk_mm.zarr" \
    "${stem}_clim.zarr" \
    "$weekly"
  $WS difference \
    --variable precip \
    --input "$weekly" --input "${stem}_clim.zarr" \
    --output "${stem}_diff.zarr"
  $WS rename \
    --input "${stem}_diff.zarr" \
    --variable precip --to-name precip_anomaly \
    --output "$dest"
}

fetch_dynamical_daily() {
  local dataset="$1" stem="$2"
  $WS dynamical-fetch \
    --dataset "$dataset" \
    --date "$INIT" \
    --bbox "$BBOX" \
    -v precipitation_surface \
    --output "$IR/${stem}_raw.zarr"
  prep_forecast_daily \
    "$IR/${stem}_raw.zarr" \
    "$IR/${stem}_daily.zarr" \
    precipitation_surface \
    1
  compose_sond \
    "$IR/${stem}_daily.zarr" \
    "$IR/${stem}_sond_daily.zarr" \
    "$IR/${stem}_wk.zarr"
}

$WS kenya-forecast-fetch \
    --dataset precip_downscaled_daily \
    --date "$INIT" --bbox "$BBOX" -v tp \
    --output "$IR/kenya_tp_ds_daily.zarr"
prep_forecast_daily \
    "$IR/kenya_tp_ds_daily.zarr" \
    "$IR/kenya_tp_ds_daily_precip.zarr" \
    tp \
    0

if [[ "$CHIRPS_END" > "$GRID_START" || "$CHIRPS_END" == "$GRID_START" ]]; then
  $WS chirps-fetch \
    --start-time "$GRID_START" --end-time "$CHIRPS_END" \
    --bbox "$BBOX" \
    --output "$IR/kenya_chirps_obs.zarr"
fi

compose_sond \
    "$IR/kenya_tp_ds_daily_precip.zarr" \
    "$IR/kenya_sond_daily.zarr" \
    "$IR/kenya_sond_weekly.zarr"
write_source_patch "$IR/kenya_sond_weekly.patch.json"
plot_weekly \
    "$IR/kenya_sond_weekly.zarr" \
    "Kenya weekly precip (CHIRPS obs → forecast)" \
    kenya_daily_downscaled_precip.png \
    "$IR/kenya_sond_weekly.patch.json"

fetch_dynamical_daily ecmwf-aifs-ens-forecast kenya_aifs_daily
write_source_patch "$IR/kenya_aifs_daily_wk.patch.json"
plot_weekly \
    "$IR/kenya_aifs_daily_wk.zarr" \
    "Kenya weekly precip (CHIRPS obs → AIFS-ENS)" \
    kenya_aifs_daily_precip.png \
    "$IR/kenya_aifs_daily_wk.patch.json"

fetch_dynamical_daily noaa-gefs-forecast-35-day kenya_gefs_daily
write_source_patch "$IR/kenya_gefs_daily_wk.patch.json"
plot_weekly \
    "$IR/kenya_gefs_daily_wk.zarr" \
    "Kenya weekly precip (CHIRPS obs → GEFS)" \
    kenya_gefs_daily_precip.png \
    "$IR/kenya_gefs_daily_wk.patch.json"

# One CHIRPS weekly climatology for the same Monday SOND slots, then
# field − mean for each forecast canvas.
CLIM_END=$(pydate "$GRID_END_EXCL -1 days" %Y-%m-%d)
$WS clim-fetch \
    --dataset chirps --variable precip \
    --window 7 --align left \
    --start-time "$GRID_START" --end-time "$CLIM_END" \
    --bbox "$BBOX" \
    -o "$IR/kenya_chirps_clim_wk_rate.zarr"
WEEK_VALUES=()
for monday in "${WEEKS[@]}"; do
  WEEK_VALUES+=(--value "$monday")
done
$WS select \
    --input "$IR/kenya_chirps_clim_wk_rate.zarr" \
    --dim time "${WEEK_VALUES[@]}" \
    --output "$IR/kenya_chirps_clim_wk_rate_sond.zarr"
$WS rename \
    --input "$IR/kenya_chirps_clim_wk_rate_sond.zarr" \
    --variable precip_avg --to-name precip \
    --output "$IR/kenya_chirps_clim_wk_rate_named.zarr"
$WS convert-to-totals \
    --input "$IR/kenya_chirps_clim_wk_rate_named.zarr" \
    --variable precip \
    --output "$IR/kenya_chirps_clim_wk_mm.zarr"

make_weekly_anomaly \
    "$IR/kenya_sond_weekly.zarr" \
    "$IR/kenya_sond_weekly_anom.zarr"
plot_weekly_anomaly \
    "$IR/kenya_sond_weekly_anom.zarr" \
    "Kenya weekly precip anomaly (CHIRPS obs → forecast)" \
    kenya_daily_downscaled_precip_anomaly.png \
    "$IR/kenya_sond_weekly.patch.json"

make_weekly_anomaly \
    "$IR/kenya_aifs_daily_wk.zarr" \
    "$IR/kenya_aifs_daily_wk_anom.zarr"
plot_weekly_anomaly \
    "$IR/kenya_aifs_daily_wk_anom.zarr" \
    "Kenya weekly precip anomaly (CHIRPS obs → AIFS-ENS)" \
    kenya_aifs_daily_precip_anomaly.png \
    "$IR/kenya_aifs_daily_wk.patch.json"

make_weekly_anomaly \
    "$IR/kenya_gefs_daily_wk.zarr" \
    "$IR/kenya_gefs_daily_wk_anom.zarr"
plot_weekly_anomaly \
    "$IR/kenya_gefs_daily_wk_anom.zarr" \
    "Kenya weekly precip anomaly (CHIRPS obs → GEFS)" \
    kenya_gefs_daily_precip_anomaly.png \
    "$IR/kenya_gefs_daily_wk.patch.json"

# Cumulative P(onset) on the blended daily cube, then the last (max) value
# in each Monday week. Overlay CHIRPS of the last 40 published days: cells
# where onset has already occurred.
$WS indicator \
    --input "$IR/kenya_sond_daily.zarr" \
    --output "$IR/kenya_sond_daily_onset_p.zarr" \
    --rule icpac-onset -v precip \
    --cumulative --probability
$WS aggregate-temporal \
    --input "$IR/kenya_sond_daily_onset_p.zarr" \
    --period weekly --method max \
    --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
    --output "$IR/kenya_sond_weekly_onset_all.zarr"
ONSET_WEEKS=()
while IFS= read -r d; do
  ONSET_WEEKS+=(--value "$d")
done < <(week_value_flags "$IR/kenya_sond_weekly_wk_mm.zarr")
$WS select \
    --input "$IR/kenya_sond_weekly_onset_all.zarr" \
    --dim time "${ONSET_WEEKS[@]}" \
    --output "$IR/kenya_sond_weekly_onset_complete.zarr"
pad_to_sond \
    "$IR/kenya_sond_weekly_onset_complete.zarr" \
    "$IR/kenya_sond_weekly_onset.zarr"

CHIRPS_LATEST=$($WS chirps-fetch --probe-latest)
CHIRPS_TIME=$($WS resolve-time last-40d --as-of "$CHIRPS_LATEST")
$WS chirps-fetch \
    $CHIRPS_TIME \
    --bbox "$BBOX" \
    --output "$IR/kenya_chirps_40d.zarr"
$WS indicator \
    --input "$IR/kenya_chirps_40d.zarr" \
    --output "$IR/kenya_chirps_onset_any.zarr" \
    --rule icpac-onset -v precip \
    --detect any

$WS plot \
    --layer "heatmap:$IR/kenya_sond_weekly_onset.zarr::variable=probability,colormap={\"colors\":[\"#ffffff\",\"#deebf7\",\"#c6dbef\",\"#9ecae1\",\"#6baed6\",\"#4292c6\",\"#2171b5\",\"#08519c\",\"#08306b\",\"#021530\"],\"bounds\":[0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1]}" \
    --layer "heatmap:$IR/kenya_chirps_onset_any.zarr::variable=indicator,colormap={\"colors\":[\"#00000000\",\"#2ca25f\"],\"bounds\":[0,0.5,1.5]}" \
    --independent-scale \
    --label "P(onset)" \
    --label "already occurred" \
    --bbox "$BBOX" \
    --rows "$ROWS" --columns "$COLS" \
    --figsize "$FIGSIZE" \
    --fontsize 22 \
    --title "Kenya downscaled weekly P(ICPAC onset)" \
    --patch "$PLOT_LAYOUT" \
    --output kenya_daily_downscaled_onset.png
