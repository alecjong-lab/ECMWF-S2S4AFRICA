#!/usr/bin/env bash
# Probability of above-normal rainfall, weeks 1-6: ECMWF S2S extended range
# (101 members, 46 daily steps) vs lead-matched IFS model climatology, one
# 6-panel overview plus six individual weekly maps.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"

COUNTRY="${COUNTRY:-KEN}"
GEOJSON="intermediate_results/${COUNTRY,,}.geojson"
mkdir -p intermediate_results weekly_above_normal

# Kenya-specific plot extent (W,E,S,N) - narrower than the fetch BBOX so the
# map fills the frame. Only valid for COUNTRY=KEN.
EXTENT="33.5,42.2,-5.2,6.0"
CMAP="brown,wheat,white,lightgreen,green"

# Country bbox + boundary polygon. resolve-region prints "N/W/S/E" last.
BBOX=$($WS resolve-region "$COUNTRY" --geojson "$GEOJSON" | tail -n1)

# INIT_OVERRIDE: pin an older init locally, e.g. to skip an embargo window.
# Not read anywhere in .github/.
INIT="${INIT_OVERRIDE:-$($WS kenya-forecast-fetch --probe-latest precip)}"

# "16-22 Sep 2026" (same month) or "30 Sep - 6 Oct 2026" (crosses a month).
week_label() {
  python3 - "$1" "$2" <<'PY'
import sys
from datetime import datetime
s = datetime.strptime(sys.argv[1], "%Y-%m-%d")
e = datetime.strptime(sys.argv[2], "%Y-%m-%d")
if s.month == e.month:
    print(f"{s.day}-{e.day} {s.strftime('%b %Y')}")
else:
    print(f"{s.day} {s.strftime('%b')} - {e.day} {e.strftime('%b %Y')}")
PY
}

# -------------------------------------------------------------- 1. S2S fetch
# ECMWF S2S extended range, 101 members x 46 daily steps.
$WS kenya-forecast-fetch \
    --dataset precip --date "$INIT" -v tp --bbox "$BBOX" \
    -o intermediate_results/s2s_tp.zarr

# step -> wall-clock time so the daily axis is calendar-aligned.
$WS step-to-time \
    --input intermediate_results/s2s_tp.zarr \
    --output intermediate_results/fc_daily.zarr

SUB="P(above-normal rainfall) | ECMWF S2S 101 members vs IFS model climatology | init ${INIT}"

# ------------------------------------------------------- 2. per-week pipeline
PROB_ZARRS=()
COORDS=()
for i in 1 2 3 4 5 6; do
  START=$(( (i - 1) * 7 ))
  LEAD=$START

  # ---- 2a. slice the forecast into this 7-day week
  IDX_ARGS=()
  for ((d = 0; d < 7; d++)); do IDX_ARGS+=(--index "$((START + d))"); done
  $WS select \
      --input intermediate_results/fc_daily.zarr \
      --output "intermediate_results/w${i}_fc.zarr" --dim time \
      "${IDX_ARGS[@]}"

  WEEK_START=$(pydate "$INIT +${START} days" %Y-%m-%d)
  WEEK_END=$(pydate "$INIT +$((START + 6)) days" %Y-%m-%d)
  COORDS+=("$WEEK_START")

  # ---- 2b. climatology: IFS model clim, lead-matched to this week
  $WS clim-fetch --dataset ecmwf_ifs --variable precip \
      --window 7 --align left --prediction-timedelta "$LEAD" \
      --start-time "$WEEK_START" --end-time "$WEEK_START" --bbox "$BBOX" \
      -o "intermediate_results/w${i}_clim.zarr"

  # ---- 2c. collapse clim's length-1 time axis so it broadcasts over the week
  $WS select -i "intermediate_results/w${i}_clim.zarr" \
      -o "intermediate_results/w${i}_cf.zarr" --dim time --index 0

  # ---- 2d. precip_avg -> tp so difference has a common variable name
  $WS rename -i "intermediate_results/w${i}_cf.zarr" \
      -o "intermediate_results/w${i}_ctp.zarr" --variable precip_avg --to-name tp

  # ---- 2e. per-member anomaly: forecast - lead-matched model clim
  $WS difference \
      -i "intermediate_results/w${i}_fc.zarr" -i "intermediate_results/w${i}_ctp.zarr" \
      -v tp --output "intermediate_results/w${i}_anom.zarr"

  # ---- 2f. count members with a positive 7-day anomaly sum, as an ensemble fraction
  $WS indicator -i "intermediate_results/w${i}_anom.zarr" \
      -o "intermediate_results/w${i}_prob.zarr" --rule "tp sum 7d > 0" --probability --detect any
  PROB_ZARRS+=("intermediate_results/w${i}_prob.zarr")

  # ---- 2g. this week's standalone map
  LABEL=$(week_label "$WEEK_START" "$WEEK_END")
  FILE_END=$(pydate "$WEEK_END" %m-%d)
  $WS plot -i "intermediate_results/w${i}_prob.zarr" \
      -o "weekly_above_normal/week${i}_${WEEK_START}_to_${FILE_END}.png" --variable probability \
      --colormap "$CMAP" --title "Week ${i}: ${LABEL} (lead ${LEAD}d)
$SUB" --mask-geojson "$GEOJSON" --extent "$EXTENT"
done

# ---------------------------------------------------------- 3. combined overview
# `indicator` drops the time dim entirely, and the `concat` skill's --coords
# only ever coerces to int/float/string (never a real date - see its
# `_coerce`), which would leave plot's per-panel titling stuck on the bare
# "time=2026-09-16" fallback instead of a "18-24 Sept '26" range label (that
# nicer label needs an actual datetime64 time coord). Do this concat directly
# in xarray instead, stamping a real datetime64 coord per week.
PATHS_CSV=$(IFS=,; echo "${PROB_ZARRS[*]}")
COORDS_CSV=$(IFS=,; echo "${COORDS[*]}")
python3 - "$PATHS_CSV" "$COORDS_CSV" intermediate_results/prob_all6.zarr <<'PY'
import sys
import numpy as np
import xarray as xr

paths, coords, out = sys.argv[1].split(","), sys.argv[2].split(","), sys.argv[3]
dss = [xr.open_zarr(p).expand_dims(time=[np.datetime64(c)]) for p, c in zip(paths, coords)]
xr.concat(dss, dim="time").to_zarr(out, mode="w", consolidated=True)
PY

# --layer (not -i/--variable) so panel titles render as "dd-dd Mon 'yy"
# (same subplot-titling path as weekly_precip_forecast_hres_s2s.sh) instead
# of the plain "time=YYYY-MM-DD" fallback. --label sets the heatmap layer's
# colorbar label (outline has no colorbar, hence the trailing '').
$WS plot \
    --layer heatmap:intermediate_results/prob_all6.zarr::variable=probability \
    --layer outline:"$GEOJSON" \
    --colormap "$CMAP" --rows 2 --columns 3 \
    --label 'P(above-normal rainfall)' --label '' \
    -o ECMWF_tercile_plot.png \
    --title "Likelihood of above normal rainfall" \
    --mask-geojson "$GEOJSON" --extent "$EXTENT"
