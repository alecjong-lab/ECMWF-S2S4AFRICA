#!/usr/bin/env bash
set -eo pipefail

S="uvx --from git+https://github.com/rhiza-research/forecasting-skills@dev forecasting-skills"
mkdir -p intermediate_results

# ---------------------------------------------------------------
# 1. Fetch. Two calls because the first pick of stations for
#    Nairobi / Kisumu / Eldoret returned no data and were dropped;
#    substitutes came from the second call.
# ---------------------------------------------------------------
$S tahmo-fetch \
  --station TA00134 --station TA00072 --station TA00146 \
  --station TA00026 --station TA00811 \
  --start-time 2026-08-05 --end-time 2026-09-03 \
  --output intermediate_results/tahmo_cities.zarr

$S tahmo-fetch \
  --station TA00025 --station TA00715 --station TA00182 --station TA00057 \
  --station TA00171 --station TA00374 --station TA00587 --station TA00807 \
  --station TA00808 --station TA00628 --station TA00274 \
  --start-time 2026-08-05 --end-time 2026-09-03 \
  --output intermediate_results/tahmo_alts.zarr

# ---------------------------------------------------------------
# 2. Rainfall: daily means -> period totals (mm), on both fetches
# ---------------------------------------------------------------
for f in cities alts; do
  $S aggregate-temporal --period daily --variable precip \
    --input  intermediate_results/tahmo_${f}.zarr \
    --output intermediate_results/${f}_daily.zarr
  $S convert-to-totals --variable precip \
    --input  intermediate_results/${f}_daily.zarr \
    --output intermediate_results/${f}_totals.zarr
done

# ---------------------------------------------------------------
# 3. Split one station per city.
#    src = which fetch holds it; totals for precip, raw for t/rh
# ---------------------------------------------------------------
#   Nairobi TA00025 (alts) | Mombasa TA00072 (cities) | Kisumu  TA00171 (alts)
#   Nakuru  TA00026 (cities) | Eldoret TA00274 (alts)
select_city () {  # $1=city  $2=id  $3=alts|cities
  $S select --dim station_id --value "$2" \
    --input  "intermediate_results/$3_totals.zarr" \
    --output "intermediate_results/st_$1.zarr"
  $S select --dim station_id --value "$2" \
    --input  "intermediate_results/tahmo_$3.zarr" \
    --output "intermediate_results/raw_$1.zarr"
}
select_city nairobi TA00025 alts
select_city mombasa TA00072 cities
select_city kisumu  TA00171 alts
select_city nakuru  TA00026 cities
select_city eldoret TA00274 alts

# ---------------------------------------------------------------
# 4. Plot. st_* (totals) for rainfall, raw_* for temp + humidity
# ---------------------------------------------------------------
IN_P=""; IN_R=""
for c in nairobi mombasa kisumu nakuru eldoret; do
  IN_P="$IN_P --input intermediate_results/st_$c.zarr"
  IN_R="$IN_R --input intermediate_results/raw_$c.zarr"
done
LBL="--label Nairobi --label Mombasa --label Kisumu --label Nakuru --label Eldoret"

$S plot-timeseries $IN_P $LBL --variable precip --fontsize 13 \
  --title 'TAHMO daily rainfall, 5 Kenyan cities (5 Aug - 3 Sep 2026)' \
  --ylabel 'Daily rainfall [mm]' --output tahmo_kenya_cities_rainfall.png

$S plot-timeseries $IN_R $LBL --variable temperature --fontsize 13 \
  --title 'TAHMO daily mean temperature, 5 Kenyan cities (5 Aug - 3 Sep 2026)' \
  --ylabel 'Temperature [°C]' --output tahmo_kenya_cities_temperature.png

$S plot-timeseries $IN_R $LBL --variable humidity --fontsize 13 \
  --title 'TAHMO daily mean relative humidity, 5 Kenyan cities (5 Aug - 3 Sep 2026)' \
  --ylabel 'Relative humidity [fraction]' --output tahmo_kenya_cities_humidity.png