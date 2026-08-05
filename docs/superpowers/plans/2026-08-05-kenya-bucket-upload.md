# Kenya-only GCS bucket upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Additionally upload Kenya's public plots and a Kenya-cropped copy of the daily zarr forecast data to a second GCS bucket (`kenya-forecasting-data`), laid out as `<date>/{weekly,dekadal,monthly,data}/`.

**Architecture:** A new standalone script, `crop_kenya_zarr.py`, crops each domain-wide `.zarr` store for a date down to Kenya's bounding box and writes the result into a staging directory. Two new steps in `daily_download2.0.yml` build that staging directory (Kenya plots + cropped zarr) and `gcloud storage rsync` it to the new bucket. Nothing about the existing `africa-forecasting-data` upload changes.

**Tech Stack:** Python (`xarray`, already a dependency), bash, `gcloud storage` (already used elsewhere in the workflow).

## Global Constraints

- No test suite, linter, or formatter is configured in this repo (per CLAUDE.md) — do not invent commands to run them. Verification in this plan is manual: run the script locally against a small synthetic zarr store and inspect the result.
- Kenya's bounding box is `lat1=6, lon1=33, lat2=-5, lon2=42` — copied verbatim from `plot_s2s.py`'s `bboxes["Kenya"]` (`plot_s2s.py:79`). This is a fourth duplication of that box, matching the existing convention documented in CLAUDE.md ("Country bounding boxes ... duplicated across plot_s2s.py, plot_gefs.py, and fetch_dynamical.py").
- `kenya-forecasting-data` bucket and the CI service account's write access to it already exist — no IAM/infra changes are in scope.
- This is strictly additive: no existing step, env var, or output path in `daily_download2.0.yml` changes.
- No `index.md`/`ARTIFACTS.md` generation for the Kenya bucket.

---

### Task 1: `crop_kenya_zarr.py`

**Files:**
- Create: `crop_kenya_zarr.py`

**Interfaces:**
- Produces: a CLI script invoked as `python crop_kenya_zarr.py --data-dir <dir> --output <dir>`. For every `*.zarr` directory directly under `--data-dir`, writes a Kenya-bbox-cropped copy to `<output>/<same-name>.zarr`. Creates `--output` if it doesn't exist. If `--data-dir` doesn't exist or has no `*.zarr` entries, prints a message and exits 0. A failure cropping one store is logged to stderr and does not stop the others.

- [ ] **Step 1: Write the script**

```python
"""Crop domain-wide S2S zarr stores to Kenya's bounding box for the kenya-forecasting-data bucket."""

import argparse
import sys
from pathlib import Path

import xarray as xr

# Kenya bounding box — kept in sync with plot_s2s.py's bboxes["Kenya"] (see CLAUDE.md
# note on bbox duplication across plot_s2s.py / plot_gefs.py / fetch_dynamical.py).
KENYA_LAT1 = 6
KENYA_LON1 = 33
KENYA_LAT2 = -5
KENYA_LON2 = 42


def crop_zarr(src: Path, dest: Path) -> None:
    ds = xr.open_zarr(src)
    try:
        cropped = ds.sel(
            longitude=slice(KENYA_LON1, KENYA_LON2),
            latitude=slice(KENYA_LAT1, KENYA_LAT2),
        )
        cropped.to_zarr(dest, mode="w", consolidated=True)
    finally:
        ds.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not args.data_dir.is_dir():
        print(f"No {args.data_dir} directory found, skipping.")
        return

    zarr_stores = sorted(args.data_dir.glob("*.zarr"))
    if not zarr_stores:
        print(f"No .zarr stores found under {args.data_dir}, skipping.")
        return

    args.output.mkdir(parents=True, exist_ok=True)

    for src in zarr_stores:
        dest = args.output / src.name
        print(f"Cropping {src} -> {dest}")
        try:
            crop_zarr(src, dest)
        except Exception as exc:
            print(f"WARNING: failed to crop {src}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify with a synthetic zarr store**

There's no test suite in this repo, so verify manually with a small synthetic store that mimics the real domain shape (covers Kenya plus extra margin on all sides).

Run this from the repo root:

```bash
python - <<'EOF'
import numpy as np
import xarray as xr

ds = xr.Dataset(
    {"tp": (("latitude", "longitude"), np.arange(21 * 21).reshape(21, 21).astype("float32"))},
    coords={
        "latitude": np.linspace(10, -10, 21),   # descending, matches real domain data
        "longitude": np.linspace(28, 48, 21),   # ascending, matches real domain data
    },
)
ds.to_zarr("scratch_test_data/ECMWF_s2s_precip_test.zarr", mode="w", consolidated=True)
EOF

python crop_kenya_zarr.py --data-dir scratch_test_data --output scratch_test_output

python - <<'EOF'
import xarray as xr

cropped = xr.open_zarr("scratch_test_output/ECMWF_s2s_precip_test.zarr")
lat = cropped.latitude.values
lon = cropped.longitude.values
assert lat.max() <= 6 and lat.min() >= -5, f"latitude out of Kenya bbox: {lat.min()}..{lat.max()}"
assert lon.min() >= 33 and lon.max() <= 42, f"longitude out of Kenya bbox: {lon.min()}..{lon.max()}"
print("OK: cropped store is within Kenya's bounding box")
print(f"latitude {lat.min()}..{lat.max()}, longitude {lon.min()}..{lon.max()}")
EOF
```

Expected: the last block prints `OK: cropped store is within Kenya's bounding box` followed by
a latitude/longitude range inside `-5..6` / `33..42`, with no assertion error.

- [ ] **Step 3: Clean up scratch verification artifacts**

```bash
rm -rf scratch_test_data scratch_test_output
```

- [ ] **Step 4: Commit**

```bash
git add crop_kenya_zarr.py
git commit -m "Add crop_kenya_zarr.py to crop domain zarr stores to Kenya's bbox"
```

---

### Task 2: Wire Kenya bucket upload into the daily workflow

**Files:**
- Modify: `.github/workflows/daily_download2.0.yml:20` (env block)
- Modify: `.github/workflows/daily_download2.0.yml:158-175` (insert new steps after "Upload zarr data to GCS")

**Interfaces:**
- Consumes: `crop_kenya_zarr.py --data-dir <dir> --output <dir>` from Task 1. `steps.ecmwf_date.outputs.date` (already defined at `daily_download2.0.yml:58-60`). `plots/Kenya/<date>/{weekly,dekadal,monthly}/` (already produced by `plot_s2s.py`/`dowscale_dekade.py` — no changes needed there). Existing `gcloud storage` auth from the "Auth to Google Cloud" / "Setup gcloud" steps (`daily_download2.0.yml:132-143`), which already run before this point in the job.

- [ ] **Step 1: Add the `KENYA_BUCKET` env var**

In `.github/workflows/daily_download2.0.yml`, in the job-level `env:` block:

```yaml
    env:
      DATA_BRANCH: main
      WEBSITE_BRANCH: website
      DATA_DIR: website_data
      OUTPUT_DIR: data
      BUCKET: africa-forecasting-data
      KENYA_BUCKET: kenya-forecasting-data
      CDSAPI_KEY: ${{ secrets.CDSAPI_KEY }}
      TAHMO_API_PASSWORD: ${{ secrets.TAHMO_API_PASSWORD }}
      TAHMO_API_USERNAME: ${{ secrets.TAHMO_API_USERNAME }}
      EARTHDATA_PASSWORD: ${{ secrets.EARTHDATA_PASSWORD }}
      EARTHDATA_USERNAME: ${{ secrets.EARTHDATA_USERNAME }}
```

(Only the `KENYA_BUCKET: kenya-forecasting-data` line is new.)

- [ ] **Step 2: Insert the staging and upload steps**

Insert these two steps immediately after the existing "Upload zarr data to GCS" step
(right before "Generate markdown index") in `.github/workflows/daily_download2.0.yml`:

```yaml
      # ── Stage Kenya-only plots + cropped zarr data ────────────────
      - name: Stage Kenya bucket contents
        continue-on-error: true
        working-directory: main_repo
        run: |
          STAGE_DIR="kenya_bucket_staging/${{ steps.ecmwf_date.outputs.date }}"
          mkdir -p "$STAGE_DIR"
          for sub in weekly dekadal monthly; do
            src="plots/Kenya/${{ steps.ecmwf_date.outputs.date }}/$sub"
            if [ -d "$src" ]; then
              mkdir -p "$STAGE_DIR/$sub"
              cp -r "$src"/. "$STAGE_DIR/$sub/"
            fi
          done
          python crop_kenya_zarr.py \
            --data-dir "$OUTPUT_DIR/${{ steps.ecmwf_date.outputs.date }}" \
            --output "$STAGE_DIR/data"

      # ── Upload Kenya-only bucket contents ─────────────────────────
      - name: Upload to Kenya bucket
        continue-on-error: true
        working-directory: main_repo
        run: |
          STAGE_DIR="kenya_bucket_staging/${{ steps.ecmwf_date.outputs.date }}"
          if [ -d "$STAGE_DIR" ]; then
            gcloud storage rsync -r "$STAGE_DIR" "gs://${KENYA_BUCKET}/${{ steps.ecmwf_date.outputs.date }}" --exclude='.*index\.md$'
          else
            echo "No Kenya staging directory found, skipping."
          fi
```

- [ ] **Step 3: Validate the workflow YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/daily_download2.0.yml'))" && echo VALID`
Expected: prints `VALID` with no exception (catches YAML syntax mistakes; GitHub Actions
semantics like `${{ }}` expressions aren't checked by this, but indentation/structure
errors will surface here).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily_download2.0.yml
git commit -m "Upload Kenya-only plots and cropped zarr data to kenya-forecasting-data bucket"
```

---

## Manual end-to-end check (not automatable without CI/GCS access)

This plan's steps verify the script logic and YAML syntax locally. The actual GCS rsync
against the real `kenya-forecasting-data` bucket can only be verified by letting the
workflow run (next scheduled run, or a manual `workflow_dispatch`) and checking
`gs://kenya-forecasting-data/<date>/` for the `weekly/`, `dekadal/`, `monthly/`, and
`data/` folders afterward. Flag this to the user after implementation — it's worth a
`workflow_dispatch` run to confirm before relying on the next scheduled run.
