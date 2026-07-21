#!/usr/bin/env python3
"""
Generate lightweight markdown index files mirroring the bucket's folder
structure, plus a top-level ARTIFACTS.md with the most recent entries.

Run after uploading files to the bucket in the GH Actions workflow.
It expects the list of uploaded object paths (relative to the bucket root,
e.g. "data/kenya/2026-07-09/ECMWF_....grib") as input, either from a file
or stdin, one path per line.

Usage:
    python generate_artifact_index.py \
        --bucket africa-forecasting-data \
        --repo-root . \
        --uploaded-list uploaded_files.txt \
        --top-level-max 30
"""

import argparse
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", required=True, help="GCS bucket name, e.g. africa-forecasting-data")
    p.add_argument("--repo-root", default=".", help="Path to repo root")
    p.add_argument("--uploaded-list", required=True,
                   help="Text file with one uploaded object path per line "
                        "(relative to bucket root), e.g. data/kenya/2026-07-09/file.grib")
    p.add_argument("--top-level-file", default="ARTIFACTS.md")
    p.add_argument("--top-level-max", type=int, default=30,
                   help="Max number of date-folders to keep listed in the top-level file")
    return p.parse_args()


def public_url(bucket: str, object_path: str) -> str:
    return f"https://storage.googleapis.com/{bucket}/{object_path}"


def extract_date_and_group(object_path: str):
    """
    Expects paths like: data/kenya/2026-07-09/filename.grib
                     or: plots/ghana/2026-07-14/filename.png
    Returns (top_category, country, date_str) or None if it doesn't match.
    """
    parts = Path(object_path).parts
    if len(parts) < 4:
        return None
    top_category, country, date_str = parts[0], parts[1], parts[2]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return None
    return top_category, country, date_str


def main():
    args = parse_args()
    repo_root = Path(args.repo_root)

    with open(args.uploaded_list) as f:
        object_paths = [line.strip() for line in f if line.strip()]

    # Group files by (top_category, country, date)
    groups = defaultdict(list)
    for obj_path in object_paths:
        parsed = extract_date_and_group(obj_path)
        if parsed is None:
            print(f"Skipping unrecognized path format: {obj_path}")
            continue
        groups[parsed].append(obj_path)

    # Write per-folder index.md files
    for (top_category, country, date_str), files in groups.items():
        folder = repo_root / top_category / country / date_str
        folder.mkdir(parents=True, exist_ok=True)
        index_path = folder / "index.md"

        lines = [f"# {country.capitalize()} — {date_str}\n"]
        for obj_path in sorted(files):
            fname = Path(obj_path).name
            lines.append(f"- [{fname}]({public_url(args.bucket, obj_path)})")
        index_path.write_text("\n".join(lines) + "\n")
        print(f"Wrote {index_path}")

    # Update top-level ARTIFACTS.md: one entry per (top_category, country, date),
    # newest first, capped at top_level_max, linking to the folder's index.md on GitHub
    # (not the bucket directly, since index.md is the human-friendly entry point)
    top_level_path = repo_root / args.top_level_file
    existing_lines = []
    if top_level_path.exists():
        existing_lines = top_level_path.read_text().splitlines()

    new_entries = []
    for (top_category, country, date_str) in sorted(groups.keys(), key=lambda k: k[2], reverse=True):
        rel_path = f"{top_category}/{country}/{date_str}/index.md"
        new_entries.append(f"- [{country.capitalize()} {top_category} — {date_str}]({rel_path})")

    # Preserve a header if present, then merge new entries on top of old ones,
    # de-duplicate, and cap the list length.
    header = []
    body = existing_lines
    if existing_lines and existing_lines[0].startswith("#"):
        header = [existing_lines[0], ""]
        body = existing_lines[2:] if len(existing_lines) > 1 else []

    merged = new_entries + [line for line in body if line not in new_entries]
    merged = merged[: args.top_level_max]

    if not header:
        header = ["# Recent Artifacts\n"]

    top_level_path.write_text("\n".join(header + merged) + "\n")
    print(f"Updated {top_level_path} with {len(new_entries)} new entries "
          f"(showing {len(merged)} of max {args.top_level_max})")


if __name__ == "__main__":
    main()