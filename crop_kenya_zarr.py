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


def crop_zarr(src: Path, dest: Path) -> bool:
    """Crop a single zarr store to Kenya's bbox and write it to dest.

    Returns True if the cropped store was written, False if the crop produced
    an empty (zero-length lat/lon) result and was skipped.
    """
    ds = xr.open_zarr(src)
    try:
        cropped = ds.sel(
            longitude=slice(KENYA_LON1, KENYA_LON2),
            latitude=slice(KENYA_LAT1, KENYA_LAT2),
        )

        if any(t == 0 for t in [len(cropped.longitude), len(cropped.latitude)]):
            print(
                f"WARNING: crop of {src} produced an empty lat/lon selection, skipping.",
                file=sys.stderr,
            )
            return False

        # ds was opened with xr.open_zarr(), so its variables carry dask chunk
        # boundaries inherited from the source store's encoding["chunks"]. After
        # .sel() with a slice, those dask chunk boundaries generally no longer
        # align with the inherited encoding chunks, and to_zarr(..., safe_chunks=True)
        # (the default) can raise on the mismatch. Drop the inherited chunk encoding so
        # xarray/dask picks fresh, self-consistent chunks for the smaller cropped array.
        for var in cropped.variables.values():
            var.encoding.pop("chunks", None)
            var.encoding.pop("preferred_chunks", None)

        cropped.to_zarr(dest, mode="w", consolidated=True)
        return True
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

    succeeded = 0
    for src in zarr_stores:
        dest = args.output / src.name
        print(f"Cropping {src} -> {dest}")
        try:
            if crop_zarr(src, dest):
                succeeded += 1
        except Exception as exc:
            print(f"WARNING: failed to crop {src}: {exc}", file=sys.stderr)

    if succeeded == 0:
        print(
            f"ERROR: all {len(zarr_stores)} zarr store(s) under {args.data_dir} "
            "failed to crop; no Kenya data was written.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
