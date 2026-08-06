"""
Run plot_s2s.py / plot_gefs.py / dowscale_dekade.py / ai_weather_briefing.py locally
against pre-downloaded fixture data under test/, instead of the real download scripts
or GitHub Actions. Mirrors the relevant steps of .github/workflows/daily_download2.0.yml.

Usage:
    python run_local_test.py --setup-only          # build test/run/ sandbox only
    python run_local_test.py                        # build sandbox + run the full pipeline
    python run_local_test.py --skip plot_gefs,ai_weather_briefing
    python run_local_test.py --date 2026-08-01
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TEST_DIR = REPO_ROOT / "test"
SANDBOX = TEST_DIR / "run"

STAGES = ["plot_s2s", "plot_gefs", "dowscale_dekade", "ai_weather_briefing"]


def resolve_default_date():
    candidates = sorted(
        p.name for p in TEST_DIR.iterdir()
        if p.is_dir() and p.name != "run" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
    )
    if not candidates:
        raise SystemExit("No YYYY-MM-DD fixture folder found under test/. Pass --date explicitly.")
    if len(candidates) > 1:
        raise SystemExit(f"Multiple fixture dates found under test/: {', '.join(candidates)}. Pass --date to pick one.")
    return candidates[0]


def ensure_junction(link: Path, target: Path):
    if os.path.lexists(link):
        return
    target = target.resolve()
    if not target.is_dir():
        raise SystemExit(f"Cannot link {link} -> missing source directory {target}")
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"Failed to create junction {link} -> {target}:\n{result.stdout}{result.stderr}")
    else:
        os.symlink(target, link, target_is_directory=True)


def setup(date):
    fixture_dir = TEST_DIR / date
    if not fixture_dir.is_dir():
        raise SystemExit(f"No fixture folder at {fixture_dir}")

    SANDBOX.mkdir(parents=True, exist_ok=True)

    ensure_junction(SANDBOX / "Kenya_shapes", REPO_ROOT / "Kenya_shapes")
    ensure_junction(SANDBOX / "downscale_data", REPO_ROOT / "downscale_data")
    ensure_junction(SANDBOX / "m-climate", REPO_ROOT / "m-climate")

    (SANDBOX / "prompts").mkdir(exist_ok=True)
    shutil.copy2(REPO_ROOT / "prompts" / "system_prompt.md", SANDBOX / "prompts" / "system_prompt.md")

    (SANDBOX / "data").mkdir(exist_ok=True)
    ensure_junction(SANDBOX / "data" / date, fixture_dir)

    kenya_csv_src = TEST_DIR / "Kenya2026.csv"
    if kenya_csv_src.is_file():
        shutil.copy2(kenya_csv_src, SANDBOX / "data" / "Kenya2026.csv")
    else:
        print(f"[warn] {kenya_csv_src} not found yet — dowscale_dekade.py's Kenya dekadal "
              f"section will fail until you add it there.")

    (SANDBOX / "plots").mkdir(exist_ok=True)
    (SANDBOX / "website").mkdir(exist_ok=True)

    print(f"Sandbox ready at {SANDBOX} (date={date})")


def load_env_file(env, path):
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()


def run(date, skip):
    env = os.environ.copy()
    load_env_file(env, TEST_DIR / ".env")

    env["DATE_STR"] = date
    env["MAIN_PATH"] = str(SANDBOX)
    env["WEBSITE_PATH"] = str(SANDBOX / "website")
    env["COUNTRIES"] = "Kenya"
    env["AI_ACTIVE"] = "True"
    env["DEKADE_COUNTRIES"] = "Kenya"
    env["WEEK_COUNTRIES"] = "Kenya"

    results = {}
    for name in STAGES:
        if name in skip:
            print(f"[skip] {name}")
            continue
        script = REPO_ROOT / f"{name}.py"
        print(f"[run ] {name}.py")
        proc = subprocess.run([sys.executable, str(script)], cwd=SANDBOX, env=env)
        results[name] = proc.returncode
        status = "OK" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
        print(f"       -> {status}")

    print("\nSummary:")
    for name, code in results.items():
        print(f"  {name}: {'OK' if code == 0 else f'exit {code}'}")

    print("\nOutputs:")
    print(f"  raw + derived data:    {TEST_DIR / date}")
    print(f"  plots:                 {SANDBOX / 'plots'}")
    print(f"  promt_unformat*.json:  {SANDBOX}")
    print(f"  digest text:           {SANDBOX / 'prompts' / f'digest_{date}.txt'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="Fixture date (YYYY-MM-DD) under test/. Defaults to the single date folder found there.")
    parser.add_argument("--setup-only", action="store_true", help="Only build the test/run/ sandbox; don't execute any pipeline scripts.")
    parser.add_argument("--skip", default="", help=f"Comma-separated stage names to skip: {','.join(STAGES)}")
    args = parser.parse_args()

    date = args.date or resolve_default_date()
    setup(date)

    if args.setup_only:
        print("Setup only — re-run without --setup-only to execute the pipeline.")
        return

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    run(date, skip)


if __name__ == "__main__":
    main()
