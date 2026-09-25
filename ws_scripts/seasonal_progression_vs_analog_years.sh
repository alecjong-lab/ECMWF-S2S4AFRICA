#!/usr/bin/env bash
# Kenya weekly rainfall: analog years + current-year CHIRPS + KMSA weekly
# downscaled ensemble (101 members) and its national-mean line.
# Observed weeks are 7-day windows strided on Mondays (Mon–Sun).
# The weekly downscale is already weekly — convert-to-totals only.
set -eo pipefail

WS="uvx --from git+https://github.com/rhiza-research/weather-skills@dev forecasting-skills"
IR=intermediate_results
mkdir -p "$IR"

CUR_YEAR=$(date -u +%Y)
END=$($WS chirps-fetch --probe-latest)
# Do not inherit DATE_STR: KMSA/ECMWF inits lag the briefing date. Pin with
# INIT_OVERRIDE for a local rerun.
INIT="${INIT_OVERRIDE:-$($WS kenya-forecast-fetch --probe-latest precip_downscaled)}"
# 1982 and 1997 are intentionally absent: CHIRPS v3.0 sat starts in 1998.
ANALOG_YEARS=(2006 2015 2019 2023)

BBOX=$($WS resolve-region KEN --geojson "$IR/kenya.geojson")

# ---- CHIRPS observed branches -------------------------------------------
for Y in "${ANALOG_YEARS[@]}" "$CUR_YEAR"; do
  chirps_end="${Y}-12-31"
  [[ "$Y" == "$CUR_YEAR" ]] && chirps_end="$END"

  $WS chirps-fetch \
      --start-time "${Y}-08-01" --end-time "$chirps_end" \
      --bbox "$BBOX" --workers 8 \
      --output "$IR/chirps_${Y}.zarr"

  $WS clip-region \
      --input "$IR/chirps_${Y}.zarr" \
      --geojson "$IR/kenya.geojson" \
      --output "$IR/clip_${Y}.zarr"

  $WS aggregate-temporal \
      --input "$IR/clip_${Y}.zarr" \
      --output "$IR/mon_${Y}.zarr" \
      --window 7 --align left --stride Monday

  $WS summarize-dim \
      --input "$IR/mon_${Y}.zarr" \
      --dim latitude --dim longitude --method mean --lat-weighted \
      --output "$IR/monavg_${Y}.zarr"

  # 0.1 keeps partially-covered weeks (trailing CHIRPS / last analog week)
  $WS convert-to-totals \
      --input "$IR/monavg_${Y}.zarr" \
      --min-coverage 0.1 \
      --output "$IR/montot_${Y}.zarr"
done

# ---- KMSA weekly downscaled forecast ------------------------------------
# Already weekly — do not re-aggregate. Keep `number` through the spatial reduce.
$WS kenya-forecast-fetch \
    --dataset precip_downscaled \
    --date "$INIT" -v tp \
    --bbox "$BBOX" \
    --output "$IR/s2s_downscaled.zarr"

$WS clip-region \
    --input "$IR/s2s_downscaled.zarr" \
    --geojson "$IR/kenya.geojson" \
    --output "$IR/clip_s2s.zarr"

$WS step-to-time \
    --input "$IR/clip_s2s.zarr" \
    --output "$IR/s2s_t.zarr"

$WS summarize-dim \
    --input "$IR/s2s_t.zarr" \
    --dim latitude --dim longitude --method mean --lat-weighted \
    --output "$IR/s2s_avg.zarr"

$WS convert-to-totals \
    --input "$IR/s2s_avg.zarr" \
    --output "$IR/s2s_totals.zarr"

$WS rename \
    --input "$IR/s2s_totals.zarr" \
    --variable tp --to-name precip \
    --output "$IR/s2s_final.zarr"

$WS summarize-dim \
    --input "$IR/s2s_final.zarr" \
    --dim number --method mean \
    --output "$IR/s2s_ensmean.zarr"

# +/- 1 std ensemble range, to show spread alongside the mean.
$WS summarize-dim \
    --input "$IR/s2s_final.zarr" \
    --dim number --method std \
    --output "$IR/s2s_std.zarr"

$WS difference \
    --input "$IR/s2s_ensmean.zarr" \
    --input "$IR/s2s_std.zarr" \
    --variable precip \
    --output "$IR/s2s_lower.zarr"

# compute mean - std as 2*mean - (mean - std) to stick to skills only
$WS concat \
    --input "$IR/s2s_ensmean.zarr" \
    --input "$IR/s2s_ensmean.zarr" \
    --dim dup --coords 0,1 \
    --output "$IR/s2s_mean_dup.zarr"

$WS summarize-dim \
    --input "$IR/s2s_mean_dup.zarr" \
    --dim dup --method sum \
    --output "$IR/s2s_mean_x2.zarr"

$WS difference \
    --input "$IR/s2s_mean_x2.zarr" \
    --input "$IR/s2s_lower.zarr" \
    --variable precip \
    --output "$IR/s2s_upper.zarr"

# Analog years: seaborn deep. Observed: black. Members: grey. Mean + spread:
# purple. --align-day-of-year overlays years on calendar-day ticks (e.g. 1
# Oct). Output stem stays kenya_weekly_rainfall_analog_years for the briefing.
$WS plot-timeseries \
    --input "$IR/montot_2006.zarr" \
    --input "$IR/montot_2015.zarr" \
    --input "$IR/montot_2019.zarr" \
    --input "$IR/montot_2023.zarr" \
    --input "$IR/montot_${CUR_YEAR}.zarr" \
    --input "$IR/s2s_final.zarr" \
    --input "$IR/s2s_ensmean.zarr" \
    --input "$IR/s2s_lower.zarr" \
    --input "$IR/s2s_upper.zarr" \
    --label '2006 (analog)' \
    --label '2015 (analog)' \
    --label '2019 (analog)' \
    --label '2023 (analog)' \
    --label "${CUR_YEAR} CHIRPS (observed)" \
    --label "${CUR_YEAR} S2S members (101)" \
    --label "${CUR_YEAR} S2S ensemble mean" \
    --label "${CUR_YEAR} S2S -1\$\\sigma\$" \
    --label "${CUR_YEAR} S2S +1\$\\sigma\$" \
    --variable precip \
    --along number \
    --align-day-of-year \
    --theme weather_skills \
    --title "Kenya weekly rainfall (init ${INIT})" \
    --ylabel 'Weekly total (mm)' \
    --xlabel 'Week starting (Monday)' \
    --fontsize 26 \
    --figsize 20,13 \
    --trace '1:color=#4c72b0,linewidth=2.4' \
    --trace '2:color=#dd8452,linewidth=2.4' \
    --trace '3:color=#55a868,linewidth=2.4' \
    --trace '4:color=#c44e52,linewidth=2.4' \
    --trace "5:color=black,linewidth=4.2,zorder=10" \
    --trace '6:color=#9e9e9e,linewidth=0.8,zorder=2' \
    --trace '7:color=#7b1fa2,linewidth=5.0,zorder=12' \
    --trace '8:color=#7b1fa2,linewidth=1.5,linestyle=dashed,zorder=11' \
    --trace '9:color=#7b1fa2,linewidth=1.5,linestyle=dashed,zorder=11' \
    --output kenya_weekly_rainfall_analog_years.png
