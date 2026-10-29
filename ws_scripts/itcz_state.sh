#!/usr/bin/env bash
set -eo pipefail
export UV_OVERRIDE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/uv_overrides_chc.txt"

uvx --from git+https://github.com/rhiza-research/chc-skills@dev \
  chc-skills africa-itf \
  --location africa \
  --output itcz_africa_latest.png