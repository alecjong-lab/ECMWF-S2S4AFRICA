#!/usr/bin/env bash
# Kenya weekly rainfall totals + anomaly, 16 Monday SOND weeks.
# CHIRPS obs, a hybrid obs+forecast transition week, latest-init forecast
# for complete lead weeks, all-NaN (never zeros) for the rest.
# Matching canvases so the slides flip:
#   kenya_daily_downscaled_precip[_anomaly]  — KMSA daily downscale
#   kenya_aifs_daily_precip[_anomaly]        — dynamical.org AIFS-ENS
#   kenya_gefs_daily_precip[_anomaly]        — dynamical.org GEFS 35-day
#   kenya_aifs_prob_above                    — AIFS-ENS P(above climatology)
#   kenya_gefs_prob_above                    — GEFS P(above climatology)
#
# Output stems must stay as above so the briefing template pictures
# (and ai_weather_briefing.py) still match these slides. KMSA has no
# ensemble members in precip_downscaled_daily, so it has no prob panel.
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
  local vmin="$7" vmax="$8" var="$9"
  local titles=() i bounds=() src=()
  for i in "${!MONDAYS[@]}"; do
    titles+=(--subplot-title "$(week_label $((i + 1)) "${MONDAYS[$i]}")")
  done
  [[ -n "$vmin" ]] && bounds+=(--vmin "$vmin")
  [[ -n "$vmax" ]] && bounds+=(--vmax "$vmax")

  if [[ -n "$var" ]]; then
    src=(--layer "heatmap:${input}::variable=${var}")
  else
    src=(-i "$input")
  fi
  $S plot "${src[@]}" -o "$output" \
    --rows 4 --columns 4 --fontsize 20 \
    --colormap "$cmap" \
    --title "$title" \
    --cbar-label "$cbar" \
    "${titles[@]}" \
    "${bounds[@]}" \
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
# Guarded per-command (like week_mae()/prepare_forecast() in the other
# ws_scripts) so any failure just drops this piece instead of aborting the
# whole script — under `set -eo pipefail`, calling this as an `if` condition
# would otherwise silently suspend -e for its entire body.
build_hybrid() {
  local src="$1" var="$2" dest="$3"
  local mon sun obs_end fcst_start d
  mon=$(kind_mondays hybrid | head -n1)
  [[ -n "$mon" ]] || return 0
  sun=$(pydate "${mon} +6 days" %Y-%m-%d)
  obs_end="$CHIRPS_END"
  fcst_start=$(pydate "${obs_end} +1 days" %Y-%m-%d)

  $S chirps-fetch --bbox $BBOX --start-time "$mon" --end-time "$obs_end" \
      --workers 8 -o "$IR/${src}_hyb_obs_raw.zarr" || return 1
  # Daily agg stamps aggregation_coverage so concat with the forecast days
  # does not fail (AIFS/GEFS already carry that coord).
  $S aggregate-temporal --period daily --method mean \
      -i "$IR/${src}_hyb_obs_raw.zarr" -o "$IR/${src}_hyb_obs.zarr" || return 1

  case "$src" in
    kmsa)
      if ! $S kenya-forecast-fetch --dataset precip_downscaled_daily \
          --date "$mon" -v tp -o "$IR/${src}_hyb_raw.zarr"; then
        $S kenya-forecast-fetch --dataset precip_downscaled_daily \
            --date "$INIT" -v tp -o "$IR/${src}_hyb_raw.zarr" || return 1
      fi
      $S step-to-time -i "$IR/${src}_hyb_raw.zarr" -o "$IR/${src}_hyb_time.zarr" || return 1
      $S clip-region --bbox $BBOX -i "$IR/${src}_hyb_time.zarr" -o "$IR/${src}_hyb_ken.zarr" || return 1
      align_to_chirps "$IR/${src}_hyb_ken.zarr" "$IR/${src}_hyb_grid.zarr" \
          "$IR/${src}_hyb_obs.zarr" || return 1
      $S rename -v tp --to-name precip \
          -i "$IR/${src}_hyb_grid.zarr" -o "$IR/${src}_hyb_ren.zarr" || return 1
      $S aggregate-temporal --period daily --method mean \
          -i "$IR/${src}_hyb_ren.zarr" -o "$IR/${src}_hyb_named.zarr" || return 1
      ;;
    *)
      local ds=ecmwf-aifs-ens-forecast
      [[ "$src" == gefs ]] && ds=noaa-gefs-forecast-35-day
      if ! $S dynamical-fetch --dataset "$ds" --date "$mon" --bbox $BBOX \
          -v precipitation_surface -o "$IR/${src}_hyb_raw.zarr"; then
        $S dynamical-fetch --dataset "$ds" --date "$INIT" --bbox $BBOX \
            -v precipitation_surface -o "$IR/${src}_hyb_raw.zarr" || return 1
      fi
      $S aggregate-temporal --period daily --method mean \
          -i "$IR/${src}_hyb_raw.zarr" -o "$IR/${src}_hyb_1d.zarr" || return 1
      $S summarize-dim --dim number --method mean \
          -i "$IR/${src}_hyb_1d.zarr" -o "$IR/${src}_hyb_ens.zarr" || return 1
      $S step-to-time -i "$IR/${src}_hyb_ens.zarr" -o "$IR/${src}_hyb_time.zarr" || return 1
      $S rename -v precipitation_surface --to-name precip \
          -i "$IR/${src}_hyb_time.zarr" -o "$IR/${src}_hyb_ren.zarr" || return 1
      $S unit-convert --to-standard \
          -i "$IR/${src}_hyb_ren.zarr" -o "$IR/${src}_hyb_std.zarr" || return 1
      align_to_chirps "$IR/${src}_hyb_std.zarr" "$IR/${src}_hyb_named.zarr" \
          "$IR/${src}_hyb_obs.zarr" || return 1
      ;;
  esac

  local days=() n_days=0
  d="$fcst_start"
  while [[ "$d" < "$sun" || "$d" == "$sun" ]]; do
    days+=(--value "$d")
    n_days=$((n_days + 1))
    d=$(pydate "${d} +1 days" %Y-%m-%d)
  done

  if (( n_days == 1 )); then
    days+=(--value "$d")
  fi
  $S select --dim time "${days[@]}" \
      -i "$IR/${src}_hyb_named.zarr" -o "$IR/${src}_hyb_fcst.zarr" || return 1
  $S concat --dim time \
      -i "$IR/${src}_hyb_obs.zarr" -i "$IR/${src}_hyb_fcst.zarr" \
      -o "$IR/${src}_hyb_daily.zarr" || return 1
  $S aggregate-temporal --period weekly --method mean --align left \
      --start-time "$mon" --end-time "$(pydate "${mon} +7 days" %Y-%m-%d)" \
      -i "$IR/${src}_hyb_daily.zarr" -o "$IR/${src}_hyb_wk_rate.zarr" || return 1
  $S convert-to-totals -i "$IR/${src}_hyb_wk_rate.zarr" -o "$dest" || return 1
}

# Complete forecast weeks, aligned onto the CHIRPS grid and converted to mm
# totals. Guarded per-command for the same reason as build_hybrid().
build_forecast_piece() {
  local src="$1" var="$2" fcst_daily="$3" first="$4" last_mon="$5" dest="$6"
  $S aggregate-temporal --period weekly --method mean --align left \
      --start-time "$first" \
      --end-time "$(pydate "${last_mon} +7 days" %Y-%m-%d)" \
      -i "$fcst_daily" -o "$IR/${src}_fcst_wk_rate.zarr" || return 1
  align_to_chirps "$IR/${src}_fcst_wk_rate.zarr" "$IR/${src}_fcst_wk_grid.zarr" \
      "$IR/chirps_wk_rate.zarr" || return 1
  if [[ "$var" != precip ]]; then
    $S rename -v "$var" --to-name precip \
        -i "$IR/${src}_fcst_wk_grid.zarr" -o "$IR/${src}_fcst_wk_named.zarr" || return 1
  else
    rm -rf "$IR/${src}_fcst_wk_named.zarr"
    cp -R "$IR/${src}_fcst_wk_grid.zarr" "$IR/${src}_fcst_wk_named.zarr" || return 1
  fi
  $S convert-to-totals --min-coverage 1.0 \
      -i "$IR/${src}_fcst_wk_named.zarr" -o "$dest" || return 1
}

# Assemble the SOND weeks for one panel: concat whatever real-data pieces it
# was handed, then blank-fill (via the all-NaN weekly template) any Monday
# that isn't actually present in the result — whether classify_weeks never
# expected data for it, or a piece that was expected came back short or
# failed outright. A data gap degrades to a blank panel, never a crash.
# mode=totals converts the blank weeks to precip totals like the real
# pieces (compose_sond); mode=raw leaves them as the raw template
# (compute_prob_above, where blanks sit alongside a [0,1] probability field).
# target_var, if non-empty, renames the blank piece's variable (nan_wk.zarr
# is always "precip") to match the real pieces' variable — required when a
# panel ends up 100% blank, since then nothing else renames it and the
# downstream plot_sond --layer variable=... lookup would find nothing.
assemble_sond_weeks() {
  local src="$1" dest="$2" mode="$3" target_var="$4"
  shift 4
  local piece_paths=("$@")
  local stem="${dest%.zarr}"

  local data_zarr=""
  if (( ${#piece_paths[@]} == 1 )); then
    data_zarr="${piece_paths[0]}"
  elif (( ${#piece_paths[@]} > 1 )); then
    local concat_args=() p
    for p in "${piece_paths[@]}"; do
      concat_args+=(-i "$p")
    done
    $S concat --dim time "${concat_args[@]}" -o "${stem}_data.zarr"
    data_zarr="${stem}_data.zarr"
  fi

  local covered=()
  [[ -n "$data_zarr" ]] && covered=($(zarr_dates "$data_zarr"))

  # gap_vals is --value/date pairs (for the $S select call below); gap_count
  # is the actual number of blank weeks, kept separate so array length isn't
  # double-counted the way final_paths below deliberately avoids too.
  local gap_vals=() gap_count=0 mon c hit
  for mon in "${MONDAYS[@]}"; do
    hit=""
    for c in "${covered[@]}"; do
      [[ "$c" == "$mon" ]] && { hit=1; break; }
    done
    if [[ -z "$hit" ]]; then
      gap_vals+=(--value "$mon")
      gap_count=$((gap_count + 1))
    fi
  done

  # Bare paths, one per real piece — NOT `-i path` pairs, so
  # ${#final_paths[@]} is an honest piece count (a 2-element `-i path` array
  # would silently double-count and misroute a single real piece into
  # concat, which requires at least two inputs).
  local final_paths=()
  [[ -n "$data_zarr" ]] && final_paths+=("$data_zarr")

  if (( gap_count > 0 )); then
    echo "INFO: $src leaving ${gap_count}/${#MONDAYS[@]} SOND week(s) blank (no data)" >&2
    $S select --dim time "${gap_vals[@]}" \
        -i "$IR/nan_wk.zarr" -o "${stem}_nan_gaps_rate.zarr"
    if [[ "$mode" == totals ]]; then
      $S convert-to-totals --min-coverage 0.0 \
          -i "${stem}_nan_gaps_rate.zarr" -o "${stem}_nan_gaps.zarr"
    else
      rm -rf "${stem}_nan_gaps.zarr"
      cp -R "${stem}_nan_gaps_rate.zarr" "${stem}_nan_gaps.zarr"
    fi
    if [[ -n "$target_var" ]]; then
      $S rename -v precip --to-name "$target_var" \
          -i "${stem}_nan_gaps.zarr" -o "${stem}_nan_gaps_named.zarr"
      final_paths+=("${stem}_nan_gaps_named.zarr")
    else
      final_paths+=("${stem}_nan_gaps.zarr")
    fi
  fi

  if (( ${#final_paths[@]} == 0 )); then
    echo "ERROR: $src produced no SOND weeks" >&2
    exit 1
  elif (( ${#final_paths[@]} == 1 )); then
    $S select --dim time "${ORDER[@]}" -i "${final_paths[0]}" -o "$dest"
  else
    local final_args=() fp
    for fp in "${final_paths[@]}"; do
      final_args+=(-i "$fp")
    done
    $S concat --dim time "${final_args[@]}" -o "${stem}_unsorted.zarr"
    $S select --dim time "${ORDER[@]}" -i "${stem}_unsorted.zarr" -o "$dest"
  fi
}

# 16-week totals: complete CHIRPS weeks + hybrid + complete forecast weeks +
# all-NaN gaps. Forecast is coarsened onto the CHIRPS grid (half-cell lon
# offset) so concat/difference keep every cell. Each source is attempted
# independently; whichever weeks don't come from real data end up blank via
# assemble_sond_weeks, instead of aborting the whole script.
compose_sond() {
  local src="$1" fcst_daily="$2" var="$3" dest="$4"
  local stem="${dest%.zarr}"
  local fcst_last
  fcst_last=$(zarr_last_date "$fcst_daily")
  classify_weeks "$fcst_last"

  local piece_paths=()

  if [[ -n "$(kind_mondays obs)" ]]; then
    if $S convert-to-totals --min-coverage 1.0 \
        -i "$IR/chirps_wk_rate.zarr" -o "$IR/${src}_obs_totals.zarr"; then
      piece_paths+=("$IR/${src}_obs_totals.zarr")
    else
      echo "WARNING: $src obs weeks failed to build; leaving them blank" >&2
    fi
  fi

  if [[ -n "$(kind_mondays hybrid)" ]]; then
    if build_hybrid "$src" "$var" "$IR/${src}_hyb_totals.zarr"; then
      piece_paths+=("$IR/${src}_hyb_totals.zarr")
    else
      echo "WARNING: $src hybrid week failed to build; leaving it blank" >&2
    fi
  fi

  local first_fcst last_fcst_mon
  first_fcst=$(kind_mondays forecast | head -n1)
  last_fcst_mon=$(kind_mondays forecast | tail -n1)
  if [[ -n "$first_fcst" ]]; then
    if build_forecast_piece "$src" "$var" "$fcst_daily" "$first_fcst" "$last_fcst_mon" \
        "$IR/${src}_fcst_totals.zarr"; then
      piece_paths+=("$IR/${src}_fcst_totals.zarr")
    else
      echo "WARNING: $src forecast weeks failed to build; leaving them blank" >&2
    fi
  fi

  assemble_sond_weeks "$src" "$dest" totals "" "${piece_paths[@]}"

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

  # Same daily aggregate, but keep `number` (per-member) for probability panels.
  $S step-to-time -i "$IR/${stem}_1d.zarr" -o "$IR/${stem}_mem_time.zarr"
  $S rename -v precipitation_surface --to-name precip \
      -i "$IR/${stem}_mem_time.zarr" -o "$IR/${stem}_mem_named.zarr"
  $S unit-convert --to-standard \
      -i "$IR/${stem}_mem_named.zarr" -o "$IR/${stem}_mem_daily.zarr"
}

# Probability of above-normal rainfall per SOND week, ensemble members only.
# Reuses the KINDS/MONDAYS classification already set by compose_sond for the
# same stem, and the same clim_daily.zarr used by make_anomaly, so the dates
# and grid logic match the precip/anomaly panels exactly. Forecast weeks get
# a real probability (fraction of members with a positive weekly anomaly);
# every other week (obs, hybrid, na) is the same all-NaN template used
# elsewhere, so only forecast panels ever render.
compute_prob_above() {
  local stem="$1" dest="$2"
  local stem_short="${dest%.zarr}"
  local piece_paths=()

  # Climatology regridded onto the forecast's native grid ONCE, across the
  # whole date range (not per week).
  $S rename -v precip_avg --to-name precip \
      -i "$IR/clim_daily.zarr" -o "${stem_short}_clim_named.zarr"
  align_to_chirps "${stem_short}_clim_named.zarr" \
      "${stem_short}_clim_native.zarr" \
      "$IR/${stem}_mem_daily.zarr"
  $S difference -v precip \
      -i "$IR/${stem}_mem_daily.zarr" -i "${stem_short}_clim_native.zarr" \
      -o "${stem_short}_anom.zarr"

  $S indicator -v precip --rule "precip sum 7d > 0" --probability \
      -i "${stem_short}_anom.zarr" -o "${stem_short}_prob_daily.zarr"

  local fcst_vals=() mon
  for mon in $(kind_mondays forecast); do
    fcst_vals+=(--value "$mon")
  done
  if (( ${#fcst_vals[@]} == 1 )); then
    # A single-value select drops the `time` dim entirely; pad with the
    # next day (only the real Monday values ever survive the
    # final select-by-ORDER below).
    fcst_vals+=(--value "$(pydate "${mon} +1 days" %Y-%m-%d)")
  fi
  if (( ${#fcst_vals[@]} > 0 )); then
    if $S select --dim time "${fcst_vals[@]}" \
        -i "${stem_short}_prob_daily.zarr" -o "${stem_short}_prob_native.zarr" \
        && align_to_chirps "${stem_short}_prob_native.zarr" \
        "${stem_short}_prob.zarr" \
        "$IR/chirps_wk_rate.zarr"; then
      piece_paths+=("${stem_short}_prob.zarr")
    else
      echo "WARNING: $stem forecast probability weeks failed to build; leaving them blank" >&2
    fi
  fi

  # Everything other than a real forecast week (obs, hybrid, na, or a
  # forecast week whose probability came back short) is blank by design —
  # only forecast panels ever render a probability.
  assemble_sond_weeks "$stem" "$dest" raw probability "${piece_paths[@]}"
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

compute_prob_above kenya_aifs "$IR/kenya_aifs_prob.zarr"
plot_sond \
    "$IR/kenya_aifs_prob.zarr" \
    kenya_aifs_prob_above.png \
    'Probability of Above-Normal Rainfall - AIFS-ENS Forecast' \
    'P(above climatology)' \
    "$IR/kenya_aifs_wk.patch.json" \
    "brown,wheat,white,lightgreen,green" 0 1 probability

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

compute_prob_above kenya_gefs "$IR/kenya_gefs_prob.zarr"
plot_sond \
    "$IR/kenya_gefs_prob.zarr" \
    kenya_gefs_prob_above.png \
    'Probability of Above-Normal Rainfall - GEFS Forecast' \
    'P(above climatology)' \
    "$IR/kenya_gefs_wk.patch.json" \
    "brown,wheat,white,lightgreen,green" 0 1 probability
