#!/usr/bin/env bash
# Reproduce the four IOD/ENSO figures.
set -eo pipefail

SKILLS="git+https://github.com/rhiza-research/chc-skills@dev"

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