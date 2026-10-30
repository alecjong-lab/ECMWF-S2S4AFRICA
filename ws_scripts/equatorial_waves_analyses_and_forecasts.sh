#!/usr/bin/env bash
set -eo pipefail
# shellcheck source=./_skills_core_redirect.sh
source "$(dirname "${BASH_SOURCE[0]}")/_skills_core_redirect.sh"

SKILLS="git+https://github.com/rhiza-research/chc-skills@main"

# 1) Hovmöller diagram — OLR, tropics (15S-15N)
uvx --from "$SKILLS" chc-skills ncics-mjo-png \
  --product hovmoller \
  --variable olr \
  --latitude wide \
  --algorithm cfs \
  --level 200 \
  --days 7 \
  --region africa \
  --wave all \
  --output hovmoller_olr_tropics.png

# 2) Map — OLR, Africa, 7-day average
uvx --from "$SKILLS" chc-skills ncics-mjo-png \
  --product map \
  --variable olr \
  --region africa \
  --days 7 \
  --algorithm cfs \
  --level 200 \
  --latitude wide \
  --wave all \
  --output olr_map_africa.png