# Load the same env-var names GitHub Actions injects from repo secrets.
#
# In CI, `env: CDSAPI_KEY: ${{ secrets.CDSAPI_KEY }}` (and friends) already
# populate the environment — this is a no-op then.
# Locally, GitHub will not return secret *values* via `gh secret`; they have
# to live in `.env` or `test/.env` under those same names.
#
# Sourced, not executed. Safe to source more than once.

_secrets_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  case "$(basename "$here")" in
    local_workflows|ws_scripts) (cd "$here/.." && pwd) ;;
    *) (cd "$here" && pwd) ;;
  esac
}

_secrets_is_placeholder() {
  local v="${1:-}"
  v="${v#\'}"
  v="${v%\'}"
  v="${v#\"}"
  v="${v%\"}"
  case "$v" in
    "" | TEMP | CHANGE_ME | replace-me | your-* | "<"*">") return 0 ;;
    *) return 1 ;;
  esac
}

_secrets_apply_ci_aliases() {
  if _secrets_is_placeholder "${CDSAPI_KEY:-}"; then
    unset CDSAPI_KEY
  else
    export CDSAPI_KEY
    export ECMWF_DATASTORES_KEY="${ECMWF_DATASTORES_KEY:-$CDSAPI_KEY}"
    export ECMWF_DATASTORES_URL="${ECMWF_DATASTORES_URL:-https://ecds.ecmwf.int/api}"
  fi
}

load_repo_secrets() {
  local root="${1:-$(_secrets_root)}"
  set -a
  # Legacy local file first; repo-root `.env` wins on conflicts.
  # shellcheck disable=SC1091
  [ -f "$root/test/.env" ] && . "$root/test/.env"
  # shellcheck disable=SC1091
  [ -f "$root/.env" ] && . "$root/.env"
  set +a
  _secrets_apply_ci_aliases
}

load_repo_secrets
