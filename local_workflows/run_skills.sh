#!/usr/bin/env bash
# Minimal runner: just the ws_scripts/*.sh weather-skills calls, then bake
# them into the AI briefing pptx. A focused slice of run_local.sh (which
# still exists, unchanged, for the full pipeline+uploads) — for the
# iterate-on-one-visualization-then-see-it-in-the-briefing loop.
#
# Usage:
#   ./local_workflows/run_skills.sh --list                        # show step names
#   ./local_workflows/run_skills.sh all                           # run every step
#   ./local_workflows/run_skills.sh ws_current_sst_conditions      # run just one
#   ./local_workflows/run_skills.sh ai_briefing --force            # rerun even if done
#   ./local_workflows/run_skills.sh --from ws_seasonal_progression # that step onward
#
# Shares .env / test/.env (same names as GitHub Actions repo secrets), and the
# .local_run/state/<date>__<country>/*.done markers, with run_local.sh — a step
# one of them already ran shows as done to the other too.
set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# check for python and pip
PYTHON="$(command -v python || command -v python3)"
PIP="$(command -v pip || command -v pip3)"

DATE_STR="${DATE_STR:-$(python3 -c "from datetime import datetime, timedelta; print((datetime.today() - timedelta(days=2)).strftime('%Y-%m-%d'))")}"
COUNTRY="${COUNTRY:-Kenya}"
export DATE_STR
export MAIN_PATH="${REPO_ROOT}/"
export BRIEFING_TEMPLATE_ID="${BRIEFING_TEMPLATE_ID:-1zSp3C35PqDfMKbT8WtEcxoG2EoyIAJA5}"

# shellcheck source=./load_secrets.sh
source "$(dirname "${BASH_SOURCE[0]}")/load_secrets.sh"

STATE_DIR="${REPO_ROOT}/.local_run/state/${DATE_STR}__${COUNTRY}"
mkdir -p "$STATE_DIR"

STEPS=(
  ws_recent_rainfall_observations_kenya
  ws_recent_rainfall_observations_eastern_africa
  ws_recent_observations_station_data
  ws_seasonal_progression
  ws_seasonal_progression_vs_analog_years
  ws_equatorial_waves_mjo
  ws_equatorial_waves_iod_enso
  ws_itcz_state
  ws_equatorial_waves_analyses_and_forecasts
  ws_current_sst_conditions
  ws_last_weeks_forecast_verification
  collect_briefing_plots
  ai_briefing
)
# timeseries_verification.sh is commented out in daily_download2.0.yml
# itself, so left out here too, matching run_local.sh.

_ws_script() {
  ( cd ws_scripts && bash "$1" )
}
step_ws_recent_rainfall_observations_kenya() { _ws_script recent_rainfall_observations_kenya.sh; }
step_ws_recent_rainfall_observations_eastern_africa() { _ws_script recent_rainfall_observations_eastern_africa.sh; }
step_ws_recent_observations_station_data() { _ws_script recent_observations_station_data.sh; }
step_ws_seasonal_progression() { _ws_script seasonal_progression.sh; }
step_ws_seasonal_progression_vs_analog_years() {
  : "${CDSAPI_KEY:?CDSAPI_KEY not set — GitHub secret CDSAPI_KEY; put it in .env (gh cannot export the value)}"
  _ws_script seasonal_progression_vs_analog_years.sh
}
step_ws_equatorial_waves_mjo() { _ws_script equatorial_waves_mjo.sh; }
step_ws_equatorial_waves_iod_enso() { _ws_script equatorial_waves_iod_enso.sh; }
step_ws_itcz_state() { _ws_script itcz_state.sh; }
step_ws_equatorial_waves_analyses_and_forecasts() { _ws_script equatorial_waves_analyses_and_forecasts.sh; }
step_ws_current_sst_conditions() { _ws_script current_sst_conditions.sh; }
step_ws_last_weeks_forecast_verification() { _ws_script last_weeks_forecast_verification.sh; }

step_collect_briefing_plots() {
  mkdir -p "plots/briefing/${DATE_STR}"
  mv ws_scripts/*.png "plots/briefing/${DATE_STR}/" 2>/dev/null || true
}

step_ai_briefing() {
  "$PIP" install -q -r requirements.txt
  # GOOGLE_API_KEY optional (placeholder narration if unset), BRIEFING_TEMPLATE_PATH
  # optional (local pptx instead of the Drive fetch) — see ai_weather_briefing.py.
  # BRIEFING_ALLOW_MISSING_PICTURES=1: save with placeholders for whatever this
  # skills-only run didn't produce (the core forecast plots), rather than refusing.
  BRIEFING_ALLOW_MISSING_PICTURES="${BRIEFING_ALLOW_MISSING_PICTURES:-1}" \
    "$PYTHON" ai_weather_briefing.py
}

# ── Runner (same shape as run_local.sh) ──────────────────────────────────
usage() { echo "Usage: $0 [--list] [--from STEP] [STEP|all] [--force]"; }
list_steps() { printf '%s\n' "${STEPS[@]}"; }

run_one() {
  local step="$1" force="$2"
  local marker="${STATE_DIR}/${step}.done"
  if [ -f "$marker" ] && [ "$force" != "1" ]; then
    echo "== $step: skipped (already done — pass --force to rerun) =="
    return 0
  fi
  echo "== $step =="
  "step_${step}"
  touch "$marker"
}

main() {
  local force=0 from="" target=""
  local args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --list) list_steps; exit 0 ;;
      --force) force=1 ;;
      --from) from="$2"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  target="${args[0]:-}"

  if [ -n "$from" ]; then
    local started=0
    for s in "${STEPS[@]}"; do
      if [ "$s" = "$from" ]; then started=1; fi
      if [ "$started" = "1" ]; then run_one "$s" "$force"; fi
    done
    return
  fi

  if [ -z "$target" ] || [ "$target" = "all" ]; then
    for s in "${STEPS[@]}"; do run_one "$s" "$force"; done
    return
  fi

  for s in "${STEPS[@]}"; do
    if [ "$s" = "$target" ]; then run_one "$s" "$force"; return; fi
  done
  echo "Unknown step: $target" >&2
  usage
  exit 1
}

main "$@"
