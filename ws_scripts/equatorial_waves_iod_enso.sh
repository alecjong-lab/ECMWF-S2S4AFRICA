#!/usr/bin/env bash
# Reproduce the four IOD/ENSO figures.
set -eo pipefail
# shellcheck source=./_skills_core_redirect.sh
source "$(dirname "${BASH_SOURCE[0]}")/_skills_core_redirect.sh"

SKILLS="git+https://github.com/rhiza-research/chc-skills@main"

# --- Observations ---
uvx --from "$SKILLS" chc-skills iod-enso-fetch \
  --index iod --format figure --product observation \
  --output iod_observed.png

uvx --from "$SKILLS" chc-skills iod-enso-fetch \
  --index enso --format figure --product observation \
  --output enso_observed.png

# --- Forecasts (latest archive issue) ---
uvx --from "$SKILLS" chc-skills iod-enso-fetch \
  --index iod --format figure --product forecast \
  --output iod_forecast.png

uvx --from "$SKILLS" chc-skills iod-enso-fetch \
  --index enso --format figure --product forecast \
  --output enso_forecast.png