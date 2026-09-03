"""
Pull one day's worth of already-downloaded pipeline data from the public
gs://africa-forecasting-data/ bucket and lay it out under test/<date>/ so that
run_local_test.py can use it as fixture data (it only ever exercises Kenya, but
draws on this full, uncropped, all-countries archive rather than the Kenya-only
bucket so every zarr store the pipeline needs -- including ones IndianOceanState.py
reads, like ECMWF_s2s_10wind_alt and ECMWF_s2s_sst, which the Kenya-cropped bucket
doesn't carry -- is available).

daily_download2.0.yml uploads every *.zarr store plus other non-nc/grib/md data
files (e.g. .tif) under local data/<date>/ to gs://africa-forecasting-data/data/<date>/.
That "data/" prefix is stripped back off on download so files land directly under
test/<date>/, matching what plot_s2s.py / plot_gefs.py / dowscale_dekade.py expect
under data/<date>/ locally. Derived files the pipeline writes for itself as it runs
(e.g. data_weekly.nc, data_dekade_Kenya_downscaled.nc) aren't part of this fixture --
they get regenerated fresh each run.

Usage:
    python pull_kenya_test_data.py --date 2026-09-01
    python pull_kenya_test_data.py --date 2026-09-01 --force   # overwrite existing fixture
"""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TEST_DIR = REPO_ROOT / "test"
BUCKET = "africa-forecasting-data"


def resolve_default_date():
    return (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")


def find_gcloud():
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise SystemExit("gcloud CLI not found on PATH. Install the Google Cloud SDK first.")
    return gcloud


def remote_data_exists(gcloud, date):
    result = subprocess.run(
        [gcloud, "storage", "ls", f"gs://{BUCKET}/data/{date}/"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() != ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="Date to pull (YYYY-MM-DD). Defaults to 2 days ago, matching the pipeline's DATE_STR fallback.")
    parser.add_argument("--force", action="store_true", help="Overwrite test/<date>/ if it already exists.")
    args = parser.parse_args()

    date = args.date or resolve_default_date()
    datetime.strptime(date, "%Y-%m-%d")  # validate format

    gcloud = find_gcloud()

    dest = TEST_DIR / date
    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} already exists. Pass --force to overwrite it.")

    print(f"Checking gs://{BUCKET}/data/{date}/ ...")
    if not remote_data_exists(gcloud, date):
        raise SystemExit(f"No data found at gs://{BUCKET}/data/{date}/ — check the date and try again.")

    dest.mkdir(parents=True, exist_ok=True)

    print(f"Downloading gs://{BUCKET}/data/{date}/* -> {dest}")
    print("(this is the full, uncropped, all-countries archive -- expect a larger, slower download than the old Kenya-only bucket)")
    result = subprocess.run(
        [gcloud, "storage", "cp", "-r", f"gs://{BUCKET}/data/{date}/*", str(dest)],
    )
    if result.returncode != 0:
        raise SystemExit(f"gcloud storage cp failed with exit code {result.returncode}")

    print(f"\nDone. Fixture data ready at {dest}")
    print("Top-level contents:")
    for p in sorted(dest.iterdir()):
        print(f"  {p.name}")
    print(f"\nNext: python run_local_test.py --date {date}")


if __name__ == "__main__":
    main()
