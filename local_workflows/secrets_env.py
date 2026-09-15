"""Load local env files using the same names as GitHub Actions repo secrets.

GitHub does not expose secret values via `gh secret`. CI injects
``${{ secrets.NAME }}`` as ``NAME``. Locally the values live in ``.env`` /
``test/.env``.
"""
from __future__ import annotations

from pathlib import Path

_PLACEHOLDERS = frozenset(
    {"", "TEMP", "CHANGE_ME", "replace-me", "your-cdsapi-key-here"}
)


def _is_placeholder(value: str | None) -> bool:
    v = (value or "").strip().strip("'").strip('"')
    return v in _PLACEHOLDERS or v.startswith("your-")


def load_env_file(env: dict, path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if _is_placeholder(value):
            continue
        if key not in env or _is_placeholder(env.get(key)):
            env[key] = value


def apply_ci_aliases(env: dict) -> None:
    key = env.get("CDSAPI_KEY", "")
    if _is_placeholder(key):
        env.pop("CDSAPI_KEY", None)
        return
    env.setdefault("ECMWF_DATASTORES_KEY", key)
    env.setdefault("ECMWF_DATASTORES_URL", "https://ecds.ecmwf.int/api")


def load_repo_secrets(env: dict, repo_root: Path) -> dict:
    """Fill ``env`` from ``test/.env`` then repo-root ``.env`` (root wins)."""
    load_env_file(env, repo_root / "test" / ".env")
    load_env_file(env, repo_root / ".env")
    apply_ci_aliases(env)
    return env
