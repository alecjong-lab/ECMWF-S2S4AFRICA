#!/usr/bin/env bash
# Kenya weekly rainfall totals + anomaly, 16 Monday SOND weeks.
# CHIRPS obs, a hybrid obs+forecast transition week, latest-init forecast
# for complete lead weeks, all-NaN (never zeros) for the rest.
# Matching canvases so the slides flip:
#   kenya_daily_downscaled_precip[_anomaly]  — KMSA daily downscale
#   kenya_aifs_daily_precip[_anomaly]        — dynamical.org AIFS-ENS
#   kenya_gefs_daily_precip[_anomaly]        — dynamical.org GEFS 35-day
#
# Output stems must stay as above so the briefing template pictures
# (and ai_weather_briefing.py) still match these slides.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

S="uvx --from git+https://github.com/rhiza-research/forecasting-skills@dev forecasting-skills"

# Kenya product extent used by the week-1 MAE slide (N/W/S/E).
BBOX=5.506/33.893569/-4.67677/41.855083
# 4×4 SOND canvas. First Monday on/after 7 Sept + 16 weeks (2026: 7 Sep–27 Dec).
SOND_WEEKS=16

mkdir -p intermediate_results
IR=intermediate_results

# Do not inherit DATE_STR: KMSA daily inits lag the briefing date. Pin with
# INIT_OVERRIDE for a local rerun. Last line / YYYY-MM-DD so uvx chatter
# cannot poison date compares or --as-of.
INIT="${INIT_OVERRIDE:-$($S kenya-forecast-fetch --probe-latest precip_downscaled_daily | tail -n1)}"
INIT="${INIT:0:10}"
YEAR="${INIT:0:4}"

SEP_WEEK=$($S resolve-time this-week --as-of "${YEAR}-09-07" --emit iso | tail -n1)
GRID_START="${SEP_WEEK%%/*}"
GRID_END_EXCL=$(pydate "${GRID_START} +$((SOND_WEEKS * 7)) days" %Y-%m-%d)
GRID_END=$(pydate "${GRID_END_EXCL} -1 days" %Y-%m-%d)

CHIRPS_END="${CHIRPS_END_OVERRIDE:-$($S chirps-fetch --probe-latest | tail -n1)}"
CHIRPS_END="${CHIRPS_END:0:10}"
INIT_MINUS_1=$(pydate "${INIT} -1 days" %Y-%m-%d)
if [[ "$CHIRPS_END" > "$INIT_MINUS_1" ]]; then
  CHIRPS_END="$INIT_MINUS_1"
fi

if [[ "$CHIRPS_END" < "$GRID_START" ]]; then
  echo "CHIRPS ($CHIRPS_END) has not reached SOND start $GRID_START" >&2
  exit 1
fi

MONDAYS=()
ORDER=()
for ((i = 0; i < SOND_WEEKS; i++)); do
  d=$(pydate "${GRID_START} +$((i * 7)) days" %Y-%m-%d)
  MONDAYS+=("$d")
  ORDER+=(--value "$d")
done

month_abb() {
  case "${1:5:2}" in
    01) echo Jan ;; 02) echo Feb ;; 03) echo Mar ;; 04) echo Apr ;;
    05) echo May ;; 06) echo Jun ;; 07) echo Jul ;; 08) echo Aug ;;
    09) echo Sep ;; 10) echo Oct ;; 11) echo Nov ;; 12) echo Dec ;;
  esac
}

week_label() {
  local n="$1" mon="$2" sun a b
  sun=$(pydate "${mon} +6 days" %Y-%m-%d)
  a=$(month_abb "$mon")
  b=$(month_abb "$sun")
  if [[ "$a" == "$b" ]]; then
    echo "W${n}  ${a} ${mon:8:2}-${sun:8:2}"
  else
    echo "W${n}  ${a} ${mon:8:2}-${b} ${sun:8:2}"
  fi
}

zarr_dates() {
  # inspect-zarr --format json: coords is a list of {name, values, ...}.
  $S inspect-zarr --input "$1" --format json --max-values 0 \
    | python3 -c '
import json, sys
coords = json.load(sys.stdin)["coords"]
time = next(c for c in coords if c["name"] == "time")
for v in time["values"]:
    print(str(v)[:10])
'
}

zarr_last_date() { zarr_dates "$1" | tail -n1; }

zarr_has_times() { [[ -n "$(zarr_dates "$1" 2>/dev/null | head -n1)" ]]; }

# KIND[i] = obs | hybrid | forecast | na from CHIRPS_END + last forecast day.
classify_weeks() {
  local fcst_last="$1" mon sun
  KINDS=()
  for mon in "${MONDAYS[@]}"; do
    sun=$(pydate "${mon} +6 days" %Y-%m-%d)
    if [[ "$sun" < "$CHIRPS_END" || "$sun" == "$CHIRPS_END" ]]; then
      KINDS+=("obs")
    elif [[ "$mon" < "$CHIRPS_END" || "$mon" == "$CHIRPS_END" ]]; then
      KINDS+=("hybrid")
    elif [[ -n "$fcst_last" && ( "$sun" < "$fcst_last" || "$sun" == "$fcst_last" ) ]]; then
      KINDS+=("forecast")
    else
      KINDS+=("na")
    fi
  done
}

kind_mondays() {
  local want="$1" i
  for i in "${!KINDS[@]}"; do
    if [[ "${KINDS[$i]}" == "$want" ]]; then
      echo "${MONDAYS[$i]}"
    fi
  done
}

# Shared panel styling. Overlay fontsize is pinned at 10 so it does NOT scale
# with --fontsize. Annotation x/y are DATA coords (lon/lat), not axes fractions.
write_patch() {
  local dest="$1" i comma="" text color
  {
    echo '{'
    echo '  "theme": {"rc": {"axes.facecolor": "#9e9e9e", "axes.titlesize": 20, "figure.titlesize": 24,'
    echo '                   "axes.labelsize": 18, "xtick.labelsize": 15, "ytick.labelsize": 15}},'
    echo '  "layout": {"colorbar": {"labelsize": 22, "ticksize": 17, "labelpad": 16}},'
    echo '  "annotations": ['
    for i in "${!KINDS[@]}"; do
      case "${KINDS[$i]}" in
        obs) text=obs; color="#c8e6c9" ;;
        hybrid) text="obs+forecast"; color="#ffe0b2" ;;
        forecast) text=forecast; color="#bbdefb" ;;
        *) text="not available"; color="white" ;;
      esac
      printf '%s    {"text": "%s", "panel": %d, "x": 37.9, "y": 4.8, "ha": "center", "va": "center", "fontsize": 10, "color": "black", "zorder": 12, "bbox": {"facecolor": "%s", "edgecolor": "black", "alpha": 0.9, "boxstyle": "round,pad=0.3"}}' \
        "$comma" "$text" "$i" "$color"
      comma=$',\n'
    done
    echo
    echo '  ]'
    echo '}'
  } >"$dest"
}

plot_sond() {
  local input="$1" output="$2" title="$3" cbar="$4" patch="$5" cmap="$6"
  local titles=() i
  for i in "${!MONDAYS[@]}"; do
    titles+=(--subplot-title "$(week_label $((i + 1)) "${MONDAYS[$i]}")")
  done
  $S plot -i "$input" -o "$output" \
    --rows 4 --columns 4 --fontsize 20 \
    --colormap "$cmap" \
    --title "$title" \
    --cbar-label "$cbar" \
    "${titles[@]}" \
    --patch "$patch"
}

# Put a field on the CHIRPS grid. KMSA is ~0.05° (coarsen / half-cell
# realign); AIFS/GEFS are 0.25° so coarsen refuses and we downscale.
align_to_chirps() {
  local src="$1" dest="$2" grid="$3"
  if $S coarsen --reference-grid "$grid" -i "$src" -o "$dest"; then
    return 0
  fi
  $S downscale --reference-grid "$grid" --algorithm linear-interpolation \
      -i "$src" -o "$dest"
}

# Hybrid week: CHIRPS through CHIRPS_END + Monday-of-week init for the rest.
# That older init covers the days the latest init has already stepped past.
build_hybrid() {
  local src="$1" var="$2" dest="$3"
  local mon sun obs_end fcst_start d
  mon=$(kind_mondays hybrid | head -n1)
  [[ -n "$mon" ]] || return 0
  sun=$(pydate "${mon} +6 days" %Y-%m-%d)
  obs_end="$CHIRPS_END"
  fcst_start=$(pydate "${obs_end} +1 days" %Y-%m-%d)

  $S chirps-fetch --bbox $BBOX --start-time "$mon" --end-time "$obs_end" \
      --workers 8 -o "$IR/${src}_hyb_obs_raw.zarr"
  # Daily agg stamps aggregation_coverage so concat with the forecast days
  # does not fail (AIFS/GEFS already carry that coord).
  $S aggregate-temporal --period daily --method mean \
      -i "$IR/${src}_hyb_obs_raw.zarr" -o "$IR/${src}_hyb_obs.zarr"

  case "$src" in
    kmsa)
      if ! $S kenya-forecast-fetch --dataset precip_downscaled_daily \
          --date "$mon" -v tp -o "$IR/${src}_hyb_raw.zarr"; then
        $S kenya-forecast-fetch --dataset precip_downscaled_daily \
            --date "$INIT" -v tp -o "$IR/${src}_hyb_raw.zarr"
      fi
      $S step-to-time -i "$IR/${src}_hyb_raw.zarr" -o "$IR/${src}_hyb_time.zarr"
      $S clip-region --bbox $BBOX -i "$IR/${src}_hyb_time.zarr" -o "$IR/${src}_hyb_ken.zarr"
      align_to_chirps "$IR/${src}_hyb_ken.zarr" "$IR/${src}_hyb_grid.zarr" \
          "$IR/${src}_hyb_obs.zarr"
      $S rename -v tp --to-name precip \
          -i "$IR/${src}_hyb_grid.zarr" -o "$IR/${src}_hyb_ren.zarr"
      $S aggregate-temporal --period daily --method mean \
          -i "$IR/${src}_hyb_ren.zarr" -o "$IR/${src}_hyb_named.zarr"
      ;;
    *)
      local ds=ecmwf-aifs-ens-forecast
      [[ "$src" == gefs ]] && ds=noaa-gefs-forecast-35-day
      if ! $S dynamical-fetch --dataset "$ds" --date "$mon" --bbox $BBOX \
          -v precipitation_surface -o "$IR/${src}_hyb_raw.zarr"; then
        $S dynamical-fetch --dataset "$ds" --date "$INIT" --bbox $BBOX \
            -v precipitation_surface -o "$IR/${src}_hyb_raw.zarr"
      fi
      $S aggregate-temporal --period daily --method mean \
          -i "$IR/${src}_hyb_raw.zarr" -o "$IR/${src}_hyb_1d.zarr"
      $S summarize-dim --dim number --method mean \
          -i "$IR/${src}_hyb_1d.zarr" -o "$IR/${src}_hyb_ens.zarr"
      $S step-to-time -i "$IR/${src}_hyb_ens.zarr" -o "$IR/${src}_hyb_time.zarr"
      $S rename -v precipitation_surface --to-name precip \
          -i "$IR/${src}_hyb_time.zarr" -o "$IR/${src}_hyb_ren.zarr"
      $S unit-convert --to-standard \
          -i "$IR/${src}_hyb_ren.zarr" -o "$IR/${src}_hyb_std.zarr"
      align_to_chirps "$IR/${src}_hyb_std.zarr" "$IR/${src}_hyb_named.zarr" \
          "$IR/${src}_hyb_obs.zarr"
      ;;
  esac

  local days=()
  d="$fcst_start"
  while [[ "$d" < "$sun" || "$d" == "$sun" ]]; do
    days+=(--value "$d")
    d=$(pydate "${d} +1 days" %Y-%m-%d)
  done
  $S select --dim time "${days[@]}" \
      -i "$IR/${src}_hyb_named.zarr" -o "$IR/${src}_hyb_fcst.zarr"
  $S concat --dim time \
      -i "$IR/${src}_hyb_obs.zarr" -i "$IR/${src}_hyb_fcst.zarr" \
      -o "$IR/${src}_hyb_daily.zarr"
  $S aggregate-temporal --period weekly --method mean --align left \
      --start-time "$mon" --end-time "$(pydate "${mon} +7 days" %Y-%m-%d)" \
      -i "$IR/${src}_hyb_daily.zarr" -o "$IR/${src}_hyb_wk_rate.zarr"
  $S convert-to-totals -i "$IR/${src}_hyb_wk_rate.zarr" -o "$dest"
}

# 16-week totals: complete CHIRPS weeks + hybrid + complete forecast weeks +
# all-NaN gaps. Forecast is coarsened onto the CHIRPS grid (half-cell lon
# offset) so concat/difference keep every cell.
compose_sond() {
  local src="$1" fcst_daily="$2" var="$3" dest="$4"
  local stem="${dest%.zarr}"
  local fcst_last
  fcst_last=$(zarr_last_date "$fcst_daily")
  classify_weeks "$fcst_last"

  local pieces=()

  if [[ -n "$(kind_mondays obs)" ]]; then
    $S convert-to-totals --min-coverage 1.0 \
        -i "$IR/chirps_wk_rate.zarr" -o "$IR/${src}_obs_totals.zarr"
    pieces+=(-i "$IR/${src}_obs_totals.zarr")
  fi

  if [[ -n "$(kind_mondays hybrid)" ]]; then
    build_hybrid "$src" "$var" "$IR/${src}_hyb_totals.zarr"
    pieces+=(-i "$IR/${src}_hyb_totals.zarr")
  fi

  local first_fcst last_fcst_mon
  first_fcst=$(kind_mondays forecast | head -n1)
  last_fcst_mon=$(kind_mondays forecast | tail -n1)
  if [[ -n "$first_fcst" ]]; then
    $S aggregate-temporal --period weekly --method mean --align left \
        --start-time "$first_fcst" \
        --end-time "$(pydate "${last_fcst_mon} +7 days" %Y-%m-%d)" \
        -i "$fcst_daily" -o "$IR/${src}_fcst_wk_rate.zarr"
    align_to_chirps "$IR/${src}_fcst_wk_rate.zarr" "$IR/${src}_fcst_wk_grid.zarr" \
        "$IR/chirps_wk_rate.zarr"
    if [[ "$var" != precip ]]; then
      $S rename -v "$var" --to-name precip \
          -i "$IR/${src}_fcst_wk_grid.zarr" -o "$IR/${src}_fcst_wk_named.zarr"
    else
      rm -rf "$IR/${src}_fcst_wk_named.zarr"
      cp -R "$IR/${src}_fcst_wk_grid.zarr" "$IR/${src}_fcst_wk_named.zarr"
    fi
    $S convert-to-totals --min-coverage 1.0 \
        -i "$IR/${src}_fcst_wk_named.zarr" -o "$IR/${src}_fcst_totals.zarr"
    pieces+=(-i "$IR/${src}_fcst_totals.zarr")
  fi

  local na_vals=() na
  for na in $(kind_mondays na); do
    na_vals+=(--value "$na")
  done
  if (( ${#na_vals[@]} > 0 )); then
    $S select --dim time "${na_vals[@]}" \
        -i "$IR/nan_wk.zarr" -o "$IR/${src}_nan_gaps_rate.zarr"
    $S convert-to-totals --min-coverage 0.0 \
        -i "$IR/${src}_nan_gaps_rate.zarr" -o "$IR/${src}_nan_gaps.zarr"
    pieces+=(-i "$IR/${src}_nan_gaps.zarr")
  fi

  if (( ${#pieces[@]} == 0 )); then
    echo "ERROR: $src produced no SOND weeks" >&2
    exit 1
  elif (( ${#pieces[@]} == 1 )); then
    $S select --dim time "${ORDER[@]}" "${pieces[@]}" -o "$dest"
  else
    $S concat --dim time "${pieces[@]}" -o "${stem}_unsorted.zarr"
    $S select --dim time "${ORDER[@]}" -i "${stem}_unsorted.zarr" -o "$dest"
  fi

  write_patch "${stem}.patch.json"
}

make_anomaly() {
  local weekly="$1" dest="$2"
  $S difference -v precip -i "$weekly" -i "$IR/clim_wk_named.zarr" -o "$dest"
}

prep_dynamical_daily() {
  local dataset="$1" stem="$2"
  $S dynamical-fetch --dataset "$dataset" --date "$INIT" --bbox $BBOX \
      -v precipitation_surface -o "$IR/${stem}_raw.zarr"
  $S aggregate-temporal --period daily --method mean \
      -i "$IR/${stem}_raw.zarr" -o "$IR/${stem}_1d.zarr"
  $S summarize-dim --dim number --method mean \
      -i "$IR/${stem}_1d.zarr" -o "$IR/${stem}_ensmean.zarr"
  $S step-to-time -i "$IR/${stem}_ensmean.zarr" -o "$IR/${stem}_time.zarr"
  $S rename -v precipitation_surface --to-name precip \
      -i "$IR/${stem}_time.zarr" -o "$IR/${stem}_named.zarr"
  $S unit-convert --to-standard -i "$IR/${stem}_named.zarr" -o "$IR/${stem}_daily.zarr"
}

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  return 0
fi

# --- shared CHIRPS grid + climatology + all-NaN week template --------------
$S chirps-fetch --bbox $BBOX --start-time "$GRID_START" --end-time "$CHIRPS_END" \
    --workers 8 -o "$IR/chirps_obs.zarr"
# end-time is exclusive; bins walk backward from it. Must be a Monday
# (GRID_END_EXCL) so every week is Monday-labeled. CHIRPS_END+1 is mid-week
# and would shift the whole axis (e.g. 2026-09-09 instead of 2026-09-07).
$S aggregate-temporal --period weekly --method mean --align left \
    --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
    -i "$IR/chirps_obs.zarr" -o "$IR/chirps_wk_rate.zarr"

$S clim-fetch --dataset chirps -v precip --bbox $BBOX \
    --start-time "$GRID_START" --end-time "$GRID_END" -o "$IR/clim_daily.zarr"
$S aggregate-temporal --period weekly --method mean --align left -v precip_avg \
    --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
    -i "$IR/clim_daily.zarr" -o "$IR/clim_wk_rate.zarr"
$S convert-to-totals -v precip_avg \
    -i "$IR/clim_wk_rate.zarr" -o "$IR/clim_wk_totals.zarr"
$S rename -v precip_avg --to-name precip \
    -i "$IR/clim_wk_totals.zarr" -o "$IR/clim_wk_named.zarr"

# One weekly time only: std is 0/0 -> all-NaN on the exact CHIRPS grid.
# Never fabricate zeros for unpublished weeks.
$S aggregate-temporal --period weekly --method mean --align left \
    --start-time "$GRID_START" --end-time "$(pydate "${GRID_START} +7 days" %Y-%m-%d)" \
    -i "$IR/chirps_obs.zarr" -o "$IR/chirps_w1_rate.zarr"
$S summarize-dim --dim time --method std -i "$IR/chirps_w1_rate.zarr" -o "$IR/nan2d.zarr"
$S difference -v precip_avg -i "$IR/clim_daily.zarr" -i "$IR/clim_daily.zarr" \
    -o "$IR/clim_zero.zarr"
$S rename -v precip_avg --to-name precip -i "$IR/clim_zero.zarr" -o "$IR/clim_named.zarr"
$S difference -v precip -i "$IR/clim_named.zarr" -i "$IR/nan2d.zarr" -o "$IR/nan_daily.zarr"
$S aggregate-temporal --period weekly --method mean --align left \
    --start-time "$GRID_START" --end-time "$GRID_END_EXCL" \
    -i "$IR/nan_daily.zarr" -o "$IR/nan_wk.zarr"

# --- KMSA daily downscale --------------------------------------------------
$S kenya-forecast-fetch --dataset precip_downscaled_daily --date "$INIT" \
    -v tp -o "$IR/kmsa_daily.zarr"
$S clip-region --bbox $BBOX -i "$IR/kmsa_daily.zarr" -o "$IR/kmsa_daily_ken.zarr"
$S step-to-time -i "$IR/kmsa_daily_ken.zarr" -o "$IR/kmsa_time.zarr"

compose_sond kmsa "$IR/kmsa_time.zarr" tp "$IR/kenya_sond_weekly.zarr"
plot_sond \
    "$IR/kenya_sond_weekly.zarr" \
    kenya_daily_downscaled_precip.png \
    'Weekly Rainfall Totals - KMSA Downscaled Forecast' \
    'Weekly rainfall total (mm)' \
    "$IR/kenya_sond_weekly.patch.json" \
    ppt_week
make_anomaly "$IR/kenya_sond_weekly.zarr" "$IR/kenya_sond_weekly_anom.zarr"
plot_sond \
    "$IR/kenya_sond_weekly_anom.zarr" \
    kenya_daily_downscaled_precip_anomaly.png \
    'Weekly Rainfall Anomaly vs CHIRPS Clim.' \
    'Weekly rainfall anomaly (mm)' \
    "$IR/kenya_sond_weekly.patch.json" \
    ppt_anom_week

# --- AIFS-ENS / GEFS, same 16-week canvas ---------------------------------
prep_dynamical_daily ecmwf-aifs-ens-forecast kenya_aifs
compose_sond aifs "$IR/kenya_aifs_daily.zarr" precip "$IR/kenya_aifs_wk.zarr"
plot_sond \
    "$IR/kenya_aifs_wk.zarr" \
    kenya_aifs_daily_precip.png \
    'Weekly Rainfall Totals - AIFS-ENS Forecast' \
    'Weekly rainfall total (mm)' \
    "$IR/kenya_aifs_wk.patch.json" \
    ppt_week
make_anomaly "$IR/kenya_aifs_wk.zarr" "$IR/kenya_aifs_wk_anom.zarr"
plot_sond \
    "$IR/kenya_aifs_wk_anom.zarr" \
    kenya_aifs_daily_precip_anomaly.png \
    'Weekly Rainfall Anomaly vs CHIRPS Clim.' \
    'Weekly rainfall anomaly (mm)' \
    "$IR/kenya_aifs_wk.patch.json" \
    ppt_anom_week

prep_dynamical_daily noaa-gefs-forecast-35-day kenya_gefs
compose_sond gefs "$IR/kenya_gefs_daily.zarr" precip "$IR/kenya_gefs_wk.zarr"
plot_sond \
    "$IR/kenya_gefs_wk.zarr" \
    kenya_gefs_daily_precip.png \
    'Weekly Rainfall Totals - GEFS Forecast' \
    'Weekly rainfall total (mm)' \
    "$IR/kenya_gefs_wk.patch.json" \
    ppt_week
make_anomaly "$IR/kenya_gefs_wk.zarr" "$IR/kenya_gefs_wk_anom.zarr"
plot_sond \
    "$IR/kenya_gefs_wk_anom.zarr" \
    kenya_gefs_daily_precip_anomaly.png \
    'Weekly Rainfall Anomaly vs CHIRPS Clim.' \
    'Weekly rainfall anomaly (mm)' \
    "$IR/kenya_gefs_wk.patch.json" \
    ppt_anom_week
