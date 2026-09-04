#!/usr/bin/env bash
# GEFS (weeks 1-4) vs CHIRPS precip verification over Kenya
# Verifying week: 2026-08-25 -> 2026-08-31
set -eo pipefail

SKILLS="git+https://github.com/rhiza-research/forecasting-skills@dev"
run() { uvx --from "$SKILLS" forecasting-skills "$@"; }

mkdir -p intermediate_results
cd intermediate_results

BBOX="5.506/33.893569/-4.67677/41.855083"

# ---------------------------------------------------------------- region
run resolve-region KEN --geojson kenya.geojson

# ------------------------------------------------------------- CHIRPS obs
run chirps-fetch \
    --start-time 2026-08-25 --end-time 2026-08-31 \
    --bbox "$BBOX" --workers 8 \
    --output chirps_raw.zarr

run aggregate-temporal \
    --period weekly --method mean --align left --end-time 2026-09-01 \
    --input chirps_raw.zarr --output chirps_weekly.zarr

run convert-to-totals \
    --min-coverage 1.0 \
    --input chirps_weekly.zarr --output chirps_weekly_mm.zarr

run select \
    --dim time --value 2026-08-25 \
    --input chirps_weekly_mm.zarr --output chirps_sel.zarr

# ------------------------------------------- GEFS, one init per lead week
# lead week N  <->  init N-1 weeks before the verifying week
declare -A INIT=( [w1]=2026-08-25 [w2]=2026-08-18 [w3]=2026-08-11 [w4]=2026-08-04 )

for W in w1 w2 w3 w4; do
    run dynamical-fetch \
        --dataset noaa-gefs-forecast-35-day \
        --date "${INIT[$W]}" \
        --variable precipitation_surface \
        --bbox "$BBOX" \
        --output "gefs_${W}.zarr"

    # 3-hourly -> weekly step bins (left-labeled at 0/7/14/21/28 days)
    run aggregate-temporal \
        --period weekly --method mean --align left \
        --input "gefs_${W}.zarr" --output "gefs_${W}_weekly.zarr"

    # 31-member ensemble mean
    run summarize-dim \
        --dim number --method mean \
        --input "gefs_${W}_weekly.zarr" --output "gefs_${W}_mean.zarr"

    # step -> wall-clock valid time, then pick the verifying week
    run step-to-time \
        --input "gefs_${W}_mean.zarr" --output "gefs_${W}_time.zarr"

    run select \
        --dim time --value 2026-08-25 \
        --input "gefs_${W}_time.zarr" --output "gefs_${W}_sel.zarr"

    run convert-to-totals \
        --min-coverage 1.0 \
        --input "gefs_${W}_sel.zarr" --output "gefs_${W}_mm.zarr"

    # match the obs variable name so plot-verify can use one --variable
    run rename \
        --variable precipitation_surface --to-name precip \
        --input "gefs_${W}_mm.zarr" --output "gefs_${W}_plot.zarr"
done

# --------------------------- put CHIRPS 0.05deg onto the GEFS 0.25deg grid
run coarsen \
    --reference-grid gefs_w1_sel.zarr \
    --input chirps_sel.zarr --output chirps_gefsgrid.zarr

# ------------------------------------------------------------- verify x3
for W in w1 w2 w3 w4; do
    run verify --metric hits --threshold 5 --variable precip \
        --forecast "gefs_${W}_plot.zarr" --obs chirps_gefsgrid.zarr \
        --output "verify_${W}.zarr"

    run verify --metric bias --variable precip \
        --forecast "gefs_${W}_plot.zarr" --obs chirps_gefsgrid.zarr \
        --output "bias_${W}.zarr"

    run verify --metric mae --variable precip \
        --forecast "gefs_${W}_plot.zarr" --obs chirps_gefsgrid.zarr \
        --output "mae_${W}.zarr"
done

# --------------------------------------------------------------- figures
cd ..

plot_grid() {   # $1 = verify-zarr prefix, $2 = output png, $3 = title
    run plot-verify \
        --obs intermediate_results/chirps_gefsgrid.zarr \
        --forecast intermediate_results/gefs_w4_plot.zarr --verify "intermediate_results/$1_w4.zarr" \
        --forecast intermediate_results/gefs_w3_plot.zarr --verify "intermediate_results/$1_w3.zarr" \
        --forecast intermediate_results/gefs_w2_plot.zarr --verify "intermediate_results/$1_w2.zarr" \
        --forecast intermediate_results/gefs_w1_plot.zarr --verify "intermediate_results/$1_w1.zarr" \
        --variable precip \
        --lead 'Week 4 (init Aug 4)'  --lead 'Week 3 (init Aug 11)' \
        --lead 'Week 2 (init Aug 18)' --lead 'Week 1 (init Aug 25)' \
        --label 'CHIRPS obs' \
        --label 'GEFS ens. mean' --label 'GEFS ens. mean' \
        --label 'GEFS ens. mean' --label 'GEFS ens. mean' \
        --mask-geojson intermediate_results/kenya.geojson \
        --fontsize 15 --title "$3" --output "$2"
}

plot_grid verify kenya_gefs_chirps_verify_5mm.png \
    'GEFS vs CHIRPS precipitation, Kenya, week of 2026-08-25 to 08-31 (5 mm threshold)'
plot_grid bias   kenya_gefs_chirps_bias.png \
    'GEFS vs CHIRPS precipitation bias, Kenya, week of 2026-08-25 to 08-31'
plot_grid mae    kenya_gefs_chirps_mae.png \
    'GEFS vs CHIRPS precipitation MAE, Kenya, week of 2026-08-25 to 08-31'