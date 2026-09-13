#!/usr/bin/env bash
set -eo pipefail

uvx --from git+https://github.com/rhiza-research/chc-skills@dev \
  chc-skills africa-itf \
  --location africa \
  --output itcz_africa_latest.png