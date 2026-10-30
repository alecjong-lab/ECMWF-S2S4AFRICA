#!/usr/bin/env bash
set -eo pipefail
# shellcheck source=./_skills_core_redirect.sh
source "$(dirname "${BASH_SOURCE[0]}")/_skills_core_redirect.sh"

uvx --from git+https://github.com/rhiza-research/chc-skills@main \
  chc-skills africa-itf \
  --location africa \
  --output itcz_africa_latest.png