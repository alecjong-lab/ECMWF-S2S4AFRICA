#!/usr/bin/env bash
# Weekly precip on Monday-start calendar weeks. Day-to-day briefings keep
# the same week bins. Each day is CHIRPS before init and the forecast from
# init through the last valid forecast day, so the transition week can be
# half obs / half forecast. Matching canvases so the slides flip:
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

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"

# Kenya product extent used by the archive weekly/daily maps (N/W/S/E).
BBOX="7/32/-6/43"
# Kenya-extent panel plus a little extra width so long week titles do not collide.
FIGSIZE="17.973,20.000"
PLOT_LAYOUT='{"layout":{"colorbar":{"pad":0.02},"facet":{"wspace":0.04,"hspace":0.02}},"theme":{"rc":{"xtick.labelsize":28,"axes.labelsize":28,"axes.titlesize":14}}}'

mkdir -p intermediate_results
IR=intermediate_results

# Do not inherit DATE_STR: KMSA daily inits lag the briefing date. Pin with
# INIT_OVERRIDE for a local rerun.
INIT="${INIT_OVERRIDE:-$($WS kenya-forecast-fetch --probe-latest precip_downscaled_daily)}"
YEAR="${INIT:0:4}"

# First Monday on or after 1 Sept (this-week of the 7th), through 1 Jan.
SEP_WEEK=$($WS resolve-time this-week --as-of "${YEAR}-09-07" --emit iso)
GRID_START="${SEP_WEEK%%/*}"
GRID_END_EXCL="$((YEAR + 1))-01-01"
CHIRPS_END=$($WS resolve-time now-1d --as-of "$INIT" --emit iso)
CLIM_END=$($WS resolve-time "${YEAR}-12" --emit iso)
CLIM_END="${CLIM_END##*/}"

# Daily rate on a wall-clock time axis, variable renamed to precip.
prep_forecast_daily() {
  local raw="$1" dest="$2" var="$3" reduce_ens="$4"
  local stem="${dest%.zarr}"
  $WS aggregate-temporal --input "$raw" --period daily --method mean \
      --output "${stem}_1d.zarr"
  local cur="${stem}_1d.zarr"
  if [[ "$reduce_ens" == "1" ]]; then
    $WS summarize-dim --input "$cur" --dim number --method mean \
        --output "${stem}_ensmean.zarr"
    cur="${stem}_ensmean.zarr"
  fi
  $WS step-to-time --input "$cur" --output "${stem}_time.zarr"
  $WS rename --input "${stem}_time.zarr" --variable "$var" --to-name precip \
      --output "${stem}_named.zarr"
  $WS unit-convert --input "${stem}_named.zarr" --to-standard --output "$dest"
}

# Put a daily field on a reference grid as precip (mm day-1). Daily
# aggregate last so every piece carries aggregation_coverage (concat
# requires that coord on all inputs).
align_to_grid() {
  local src="$1" dest="$2" var="$3" grid="$4"
  local stem="${dest%.zarr}"
  $WS coarsen --input "$src" --variable "$var" --reference-grid "$grid" \
      --output "${stem}_grid.zarr"
  $WS rename --input "${stem}_grid.zarr" --variable "$var" --to-name precip \
      --output "${stem}_named.zarr"
  $WS unit-convert --input "${stem}_named.zarr" --to-standard \
      --output "${stem}_std.zarr"
  $WS aggregate-temporal --input "${stem}_std.zarr" --period daily --method mean \
      --output "$dest"
}

# CHIRPS (before init) + forecast → Monday weekly totals.
# plot --rows 4 --columns 4 leaves later SOND slots blank.
compose_sond() {
  local fcst="$1" dest_daily="$2" dest_weekly="$3"
  local stem="${dest_weekly%.zarr}"
  local daily="$fcst"

  if [[ "$CHIRPS_END" > "$GRID_START" || "$CHIRPS_END" == "$GRID_START" ]]; then
    align_to_grid "$IR/kenya_chirps_obs.zarr" "${stem}_chirps.zarr" precip "$fcst"
    # Coarsen the forecast onto the obs axis so lat/lon match exactly
    # (coarsen follows the input's lat direction, not the reference's).
    align_to_grid "$fcst" "${stem}_fcst.zarr" precip "${stem}_chirps.zarr"
    $WS concat \
        --input "${stem}_chirps.zarr" --input "${stem}_fcst.zarr" \
        --dim time --output "$dest_daily"
    daily="$dest_daily"
  else
    cp -R "$fcst" "$dest_daily"
    daily="$dest_daily"
  fi

  $WS aggregate-temporal \
      --input "$daily" --period weekly --method mean \
      --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
      --output "${stem}_wk_rate.zarr"
  $WS convert-to-totals \
      --input "${stem}_wk_rate.zarr" --min-coverage 1.0 \
      --output "$dest_weekly"
}

align_clim_to() {
  local src="$1" dest="$2" grid="$3"
  if $WS coarsen --input "$src" --variable precip --reference-grid "$grid" \
      --output "$dest"; then
    return 0
  fi
  $WS downscale \
      --input "$src" --variable precip --reference-grid "$grid" \
      --algorithm linear-interpolation --output "$dest"
}

make_weekly_anomaly() {
  local weekly="$1" dest="$2"
  local stem="${dest%.zarr}"
  align_clim_to "$IR/kenya_chirps_clim_wk_mm.zarr" "${stem}_clim.zarr" "$weekly"
  $WS difference --variable precip \
      --input "$weekly" --input "${stem}_clim.zarr" \
      --output "${stem}_diff.zarr"
  $WS rename --input "${stem}_diff.zarr" \
      --variable precip --to-name precip_anomaly --output "$dest"
}

# Static badge geometry; panel / label / color filled from the weekly time axis.
write_source_patch() {
  local weekly="$1" dest="$2"
  local mondays=() d
  while IFS= read -r d; do
    mondays+=("$d")
  done < <(
    $WS inspect-zarr --input "$weekly" --format json --max-values 0 | python3 -c '
import json, sys
coords = json.load(sys.stdin)["coords"]
axis = next(c for c in coords if c["name"] == "time")
for v in axis["values"]:
    print(str(v)[:10])
'
  )
  local anns="" i monday next color label
  for i in "${!mondays[@]}"; do
    monday="${mondays[$i]}"
    next="${mondays[$((i + 1))]:-}"
    # Sunday < INIT ⇔ next Monday ≤ INIT (weeks in the cube are consecutive).
    if [[ -n "$next" && ( "$next" < "$INIT" || "$next" == "$INIT" ) ]]; then
      color="#1b9e77"; label="OBS"
    elif [[ "$monday" < "$INIT" ]]; then
      color="#d95f02"; label="OBS + FORECAST"
    else
      color="#b22222"; label="FORECAST"
    fi
    [[ -n "$anns" ]] && anns+=","
    anns+="{\"panel\":${i},\"text\":\"${label}\",\"x\":0.5,\"y\":0.04,\"transform\":\"axes\",\"ha\":\"center\",\"va\":\"bottom\",\"fontsize\":12,\"fontweight\":\"bold\",\"color\":\"white\",\"zorder\":10,\"bbox\":{\"facecolor\":\"${color}\",\"edgecolor\":\"none\",\"boxstyle\":\"round,pad=0.28\"}}"
  done
  printf '%s' "${PLOT_LAYOUT%\}},\"annotations\":[${anns}]}" >"$dest"
}

plot_weekly() {
  $WS plot --input "$1" --variable precip --bbox "$BBOX" \
      --rows 4 --columns 4 --figsize "$FIGSIZE" --fontsize 22 \
      --colormap ppt_week --title "$2" --cbar-label "Weekly rainfall [mm]" \
      --patch "$4" --output "$3"
}

plot_weekly_anomaly() {
  $WS plot --input "$1" --variable precip_anomaly --bbox "$BBOX" \
      --rows 4 --columns 4 --figsize "$FIGSIZE" --fontsize 22 \
      --colormap ppt_anomaly --title "$2" \
      --cbar-label "Weekly rainfall anomaly [mm]" \
      --patch "$4" --output "$3"
}

fetch_dynamical_daily() {
  local dataset="$1" stem="$2"
  $WS dynamical-fetch --dataset "$dataset" --date "$INIT" --bbox "$BBOX" \
      -v precipitation_surface --output "$IR/${stem}_raw.zarr"
  prep_forecast_daily "$IR/${stem}_raw.zarr" "$IR/${stem}_daily.zarr" \
      precipitation_surface 1
  compose_sond "$IR/${stem}_daily.zarr" \
      "$IR/${stem}_sond_daily.zarr" "$IR/${stem}_wk.zarr"
}

# ---- KMSA daily downscale ------------------------------------------------
$WS kenya-forecast-fetch \
    --dataset precip_downscaled_daily \
    --date "$INIT" --bbox "$BBOX" -v tp \
    --output "$IR/kenya_tp_ds_daily.zarr"
prep_forecast_daily \
    "$IR/kenya_tp_ds_daily.zarr" \
    "$IR/kenya_tp_ds_daily_precip.zarr" \
    tp 0

if [[ "$CHIRPS_END" > "$GRID_START" || "$CHIRPS_END" == "$GRID_START" ]]; then
  $WS chirps-fetch \
      --start-time "$GRID_START" --end-time "$CHIRPS_END" \
      --bbox "$BBOX" --workers 8 \
      --output "$IR/kenya_chirps_obs.zarr"
fi

compose_sond \
    "$IR/kenya_tp_ds_daily_precip.zarr" \
    "$IR/kenya_sond_daily.zarr" \
    "$IR/kenya_sond_weekly.zarr"
write_source_patch "$IR/kenya_sond_weekly.zarr" "$IR/kenya_sond_weekly.patch.json"
plot_weekly \
    "$IR/kenya_sond_weekly.zarr" \
    "Kenya weekly precip (CHIRPS obs → forecast)" \
    kenya_daily_downscaled_precip.png \
    "$IR/kenya_sond_weekly.patch.json"

fetch_dynamical_daily ecmwf-aifs-ens-forecast kenya_aifs_daily
write_source_patch "$IR/kenya_aifs_daily_wk.zarr" "$IR/kenya_aifs_daily_wk.patch.json"
plot_weekly \
    "$IR/kenya_aifs_daily_wk.zarr" \
    "Kenya weekly precip (CHIRPS obs → AIFS-ENS)" \
    kenya_aifs_daily_precip.png \
    "$IR/kenya_aifs_daily_wk.patch.json"

fetch_dynamical_daily noaa-gefs-forecast-35-day kenya_gefs_daily
write_source_patch "$IR/kenya_gefs_daily_wk.zarr" "$IR/kenya_gefs_daily_wk.patch.json"
plot_weekly \
    "$IR/kenya_gefs_daily_wk.zarr" \
    "Kenya weekly precip (CHIRPS obs → GEFS)" \
    kenya_gefs_daily_precip.png \
    "$IR/kenya_gefs_daily_wk.patch.json"

# ---- CHIRPS weekly climatology, then field − mean ------------------------
$WS clim-fetch \
    --dataset chirps --variable precip \
    --start-time "$GRID_START" --end-time "$CLIM_END" \
    --bbox "$BBOX" \
    -o "$IR/kenya_chirps_clim_daily.zarr"
$WS rename \
    --input "$IR/kenya_chirps_clim_daily.zarr" \
    --variable precip_avg --to-name precip \
    --output "$IR/kenya_chirps_clim_named.zarr"
$WS aggregate-temporal \
    --input "$IR/kenya_chirps_clim_named.zarr" \
    --period weekly --method mean \
    --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
    --output "$IR/kenya_chirps_clim_wk_rate.zarr"
$WS convert-to-totals \
    --input "$IR/kenya_chirps_clim_wk_rate.zarr" \
    --variable precip \
    --output "$IR/kenya_chirps_clim_wk_mm.zarr"

make_weekly_anomaly "$IR/kenya_sond_weekly.zarr" "$IR/kenya_sond_weekly_anom.zarr"
plot_weekly_anomaly \
    "$IR/kenya_sond_weekly_anom.zarr" \
    "Kenya weekly precip anomaly (CHIRPS obs → forecast)" \
    kenya_daily_downscaled_precip_anomaly.png \
    "$IR/kenya_sond_weekly.patch.json"

make_weekly_anomaly "$IR/kenya_aifs_daily_wk.zarr" "$IR/kenya_aifs_daily_wk_anom.zarr"
plot_weekly_anomaly \
    "$IR/kenya_aifs_daily_wk_anom.zarr" \
    "Kenya weekly precip anomaly (CHIRPS obs → AIFS-ENS)" \
    kenya_aifs_daily_precip_anomaly.png \
    "$IR/kenya_aifs_daily_wk.patch.json"

make_weekly_anomaly "$IR/kenya_gefs_daily_wk.zarr" "$IR/kenya_gefs_daily_wk_anom.zarr"
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
    --output "$IR/kenya_sond_weekly_onset.zarr"

CHIRPS_LATEST=$($WS chirps-fetch --probe-latest)
CHIRPS_TIME=$($WS resolve-time last-40d --as-of "$CHIRPS_LATEST")
$WS chirps-fetch \
    $CHIRPS_TIME --bbox "$BBOX" --workers 8 \
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
    --rows 4 --columns 4 \
    --figsize "$FIGSIZE" \
    --fontsize 22 \
    --title "Kenya downscaled weekly P(ICPAC onset)" \
    --patch "$PLOT_LAYOUT" \
    --output kenya_daily_downscaled_onset.png
