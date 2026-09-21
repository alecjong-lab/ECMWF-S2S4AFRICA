#!/usr/bin/env bash
# Weekly precip on Monday-start calendar weeks. Day-to-day briefings keep
# the same week bins. Each day is CHIRPS before init and the forecast from
# init through the last valid forecast day, so the transition week can be
# half obs / half forecast. Matching canvases so the slides flip:
#   kenya_daily_downscaled_precip[_anomaly]  — CHIRPS-resolution S2S ensemble-mean
#   kenya_aifs_daily_precip[_anomaly]        — dynamical.org AIFS-ENS (0.25°)
#   kenya_gefs_daily_precip[_anomaly]        — dynamical.org GEFS 35-day (0.25°)
#
# Output stems must stay as above so the briefing template pictures
# (and ai_weather_briefing.py) still match these slides.
set -eo pipefail

# shellcheck source=./_portable_date.sh
source "$(dirname "${BASH_SOURCE[0]}")/_portable_date.sh"

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"
WS_PY="uv run --no-project --with xarray --with zarr --with pandas python"

# Kenya product extent used by the archive weekly/daily maps (N/W/S/E).
BBOX="7/32/-6/43"
# Kenya-extent panel plus a little extra width so long week titles do not collide.
FIGSIZE="17.973,20.000"
PLOT_LAYOUT='{"layout":{"colorbar":{"pad":0.02},"facet":{"wspace":0.04,"hspace":0.16}},"theme":{"rc":{"xtick.labelsize":28,"axes.labelsize":28,"axes.titlesize":14}}}'
# 4×4 SOND canvas. First Monday on/after 1 Sept + 16 weeks (2026: 7 Sep–27 Dec).
SOND_WEEKS=16
# Keep the CHIRPS↔forecast transition week (often ~4/7 days) and a nearly
# complete last lead. Empty future weeks are never in the rate cube.
MIN_COVERAGE=0.5

mkdir -p intermediate_results
IR=intermediate_results

# Do not inherit DATE_STR: KMSA daily inits lag the briefing date. Pin with
# INIT_OVERRIDE for a local rerun. Last line / YYYY-MM-DD so uvx chatter
# cannot poison date compares or --as-of.
INIT="${INIT_OVERRIDE:-$($WS kenya-forecast-fetch --probe-latest precip_downscaled_daily | tail -n1)}"
INIT="${INIT:0:10}"
YEAR="${INIT:0:4}"

SEP_WEEK=$($WS resolve-time this-week --as-of "${YEAR}-09-07" --emit iso | tail -n1)
GRID_START="${SEP_WEEK%%/*}"
GRID_END_EXCL=$(pydate "${GRID_START} +$((SOND_WEEKS * 7)) days" %Y-%m-%d)
CHIRPS_END=$($WS resolve-time now-1d --as-of "$INIT" --emit iso | tail -n1)
CHIRPS_END="${CHIRPS_END%%/*}"
CLIM_END=$($WS resolve-time "${YEAR}-12" --emit iso | tail -n1)
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

# Reindex a weekly cube onto the 16 SOND Mondays. Missing weeks stay all-NaN
# so plot always gets a 4×4 season canvas.
pad_sond_weeks() {
  local src="$1"
  $WS_PY - "$src" "$GRID_START" "$SOND_WEEKS" <<'PY'
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import xarray as xr

src, start, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
mondays = pd.to_datetime(
    [date.fromisoformat(start) + timedelta(days=7 * i) for i in range(n)]
)
try:
    ds = xr.open_zarr(src, consolidated=True)
except Exception:
    ds = xr.open_zarr(src, consolidated=False)
ds = ds.assign_coords(time=pd.to_datetime(ds["time"].values)).load()
ds.close()
out = ds.reindex(time=mondays)
tmp = Path(str(src) + ".padtmp")
if tmp.exists():
    shutil.rmtree(tmp)
out.to_zarr(tmp, mode="w", consolidated=True)
dest = Path(src)
if dest.exists():
    shutil.rmtree(dest)
tmp.rename(dest)
print(f"padded {src} -> {n} SOND weeks from {start}", file=sys.stderr)
PY
}

# CHIRPS (before init) + forecast → Monday weekly totals, then pad to 4×4.
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
      --input "${stem}_wk_rate.zarr" --min-coverage "$MIN_COVERAGE" \
      --output "$dest_weekly"
  pad_sond_weeks "$dest_weekly"
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

# Badge only weeks that have finite data. Empty SOND slots stay unlabeled.
write_source_patch() {
  local weekly="$1" dest="$2"
  local mondays=() finite=() line d ok
  while IFS=$'\t' read -r d ok; do
    mondays+=("$d")
    finite+=("$ok")
  done < <(
    $WS_PY - "$weekly" <<'PY'
import sys
import pandas as pd
import xarray as xr

ds = xr.open_zarr(sys.argv[1], consolidated=True)
var = "precip" if "precip" in ds.data_vars else next(iter(ds.data_vars))
other = [d for d in ds[var].dims if d != "time"]
has = ~ds[var].isnull().all(dim=other)
for t, ok in zip(pd.to_datetime(ds["time"].values), has.values):
    print(f"{t.strftime('%Y-%m-%d')}\t{int(bool(ok))}")
PY
  )
  local anns="" shapes="" i monday next color label
  # BBOX is N/W/S/E; grey wash uses W/E/S/N in data coords.
  local west="${BBOX#*/}" east south
  east="${BBOX##*/}"
  west="${west%%/*}"
  south="${BBOX#*/*/}"
  south="${south%%/*}"
  local north="${BBOX%%/*}"
  for i in "${!mondays[@]}"; do
    monday="${mondays[$i]}"
    next="${mondays[$((i + 1))]:-}"
    if [[ "${finite[$i]}" == "1" ]]; then
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
    else
      [[ -n "$anns" ]] && anns+=","
      anns+="{\"panel\":${i},\"text\":\"N/A\",\"x\":0.5,\"y\":0.5,\"transform\":\"axes\",\"ha\":\"center\",\"va\":\"center\",\"fontsize\":20,\"fontweight\":\"bold\",\"color\":\"#5a5a5a\",\"zorder\":12,\"bbox\":{\"facecolor\":\"#d0d0d0\",\"edgecolor\":\"none\",\"boxstyle\":\"round,pad=0.45\"}}"
      [[ -n "$shapes" ]] && shapes+=","
      shapes+="{\"type\":\"rect\",\"panel\":${i},\"x0\":${west},\"x1\":${east},\"y0\":${south},\"y1\":${north},\"facecolor\":\"#c8c8c8\",\"edgecolor\":\"none\",\"fill\":true,\"alpha\":0.62,\"zorder\":8}"
    fi
  done
  printf '%s' "${PLOT_LAYOUT%\}},\"annotations\":[${anns}],\"shapes\":[${shapes}]}" >"$dest"
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
      --colormap ppt_anom_week --title "$2" \
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

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  return 0
fi

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
pad_sond_weeks "$IR/kenya_chirps_clim_wk_mm.zarr"

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
