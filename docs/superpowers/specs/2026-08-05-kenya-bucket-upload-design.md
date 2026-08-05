# Kenya-only GCS bucket upload

## Problem

Daily CI already uploads plots and zarr forecast data to `gs://africa-forecasting-data`,
organized as `plots/<Country>/<date>/{weekly,dekadal,monthly}/` and `data/<date>/*.zarr`.
Kenya stakeholders want a second, Kenya-only copy in `gs://kenya-forecasting-data`, laid
out with the date as the top-level folder instead of country-then-date:

```
2026-01-01/
  weekly/
  dekadal/
  monthly/
  data/
```

## Scope

- Additive only — the existing `africa-forecasting-data` upload steps are unchanged.
- Public artifacts only: `plots/Kenya/<date>/{weekly,dekadal,monthly}/` and cropped zarr
  data. Private/gitignored outputs (`private_plots/`, `private_data/`, the PPTX AI
  briefing) are excluded.
- No `index.md`/`ARTIFACTS.md` generation for the Kenya bucket.
- `kenya-forecasting-data` bucket and CI service account write access already exist
  (confirmed by user) — no infra/IAM changes needed.

## Data folder: cropped, not domain-wide

The `.zarr` stores under `data/<date>/` are domain-wide (whole Africa bounding box) —
there is no Kenya-only zarr produced anywhere in the pipeline today. Rather than copy the
full domain data into the Kenya bucket, each zarr store is cropped to Kenya's bounding box
before upload, using the same box already defined in `plot_s2s.py`'s `bboxes["Kenya"]`:
`lat1=6, lon1=33, lat2=-5, lon2=42`.

## Design

### New script: `crop_kenya_zarr.py`

Standalone CI script, same shape as `generate_artifact_index.py` (argument-driven, not
imported by other scripts). Responsibilities:

- Takes `--data-dir` (e.g. `data/<date>`) and `--output` (staging dir) arguments.
- Globs `*.zarr` stores directly under `--data-dir`.
- Opens each with `xr.open_zarr`, subsets with
  `.sel(longitude=slice(lon1, lon2), latitude=slice(lat1, lat2))` — the same slicing
  convention already used elsewhere in `get_ECMWF_functions.py` for this domain's
  descending-latitude / ascending-longitude coordinate order.
- Writes the cropped copy to `<output>/<zarr_name>.zarr` (`mode="w", consolidated=True`,
  matching `gef.combine_to_zarr`'s convention).
- Kenya's bbox is hardcoded as a module-level constant with a comment noting it must be
  kept in sync with the other bbox duplications (`plot_s2s.py`, `plot_gefs.py`,
  `fetch_dynamical.py`), per the existing convention documented in CLAUDE.md.
- Each zarr is processed in its own try/except — a failure on one store is logged and
  skipped rather than aborting the whole script, consistent with this pipeline's
  fault-tolerant style.

### Workflow changes: `.github/workflows/daily_download2.0.yml`

- New job-level env: `KENYA_BUCKET: kenya-forecasting-data`.
- Two new steps, inserted after the existing "Upload zarr data to GCS" step (after GCP
  auth has already run), both `continue-on-error: true`:
  1. **Stage Kenya bucket contents** — create `kenya_bucket_staging/<date>/`, copy
     `plots/Kenya/<date>/{weekly,dekadal,monthly}` into it, then run
     `crop_kenya_zarr.py --data-dir data/<date> --output kenya_bucket_staging/<date>/data`.
  2. **Upload to Kenya bucket** — 
     `gcloud storage rsync -r kenya_bucket_staging/<date> "gs://${KENYA_BUCKET}/<date>"`,
     with the same `--exclude='.*index\.md$'` used for the public plots upload.
- No changes to any existing step, env var, or output path.

## Out of scope

- Cropping/uploading any country other than Kenya.
- Any index/catalog generation for the Kenya bucket.
- Retroactive backfill of past dates into the Kenya bucket.
