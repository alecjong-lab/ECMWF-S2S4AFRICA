#!/usr/bin/env python3
"""
Generate lightweight markdown index files mirroring the bucket's folder
structure, plus a top-level ARTIFACTS.md with the most recent entries.

Run this AFTER uploading files to the bucket in the GH Actions workflow.
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


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Label used for plot files that sit directly in the date folder (no
# variable subfolder) — this is the main/default variable for that category.
DEFAULT_PLOT_VARIABLE = "Precipitation"

# Recognized timespan folder names (case-insensitive) and their display
# order in the generated markdown.
TIMESPAN_DISPLAY = {
    "weekly": "Weekly",
    "dekadal": "Dekadal",
    "monthly": "Monthly",
}
TIMESPAN_ORDER = list(TIMESPAN_DISPLAY.keys())
# Label used when a plot path has no recognized timespan folder (legacy
# paths from before the timespan level was introduced).
NO_TIMESPAN_LABEL = "Other"


def parse_object_path(object_path: str):
    """
    Handles:

      data/<date>/filename
          (domain-wide, no country)

      plots/<country>/<date>/<timespan>/filename
      plots/<country>/<date>/<timespan>/<variable>/filename
          (timespan is one of "weekly", "dekadal", "monthly", case-insensitive;
           variable folder is optional — files directly under the timespan
           folder are treated as the default/main variable, e.g. precip)

      plots/<country>/<date>/filename
      plots/<country>/<date>/<variable>/filename
          (legacy paths with no timespan folder, still supported)

    Returns a dict: {category, country, date, timespan, variable, object_path}
    or None if the path doesn't match any known layout. `country`, `timespan`,
    and `variable` may be None.
    """
    parts = Path(object_path).parts
    if not parts:
        return None

    category = parts[0]

    if category == "data":
        # data/<date>/filename...
        if len(parts) < 3 or not DATE_RE.match(parts[1]):
            return None
        return {
            "category": category,
            "country": None,
            "date": parts[1],
            "timespan": None,
            "variable": None,
            "object_path": object_path,
        }

    if category == "plots":
        # plots/<country>/<date>/...
        if len(parts) < 4 or not DATE_RE.match(parts[2]):
            return None
        country = parts[1]
        date_str = parts[2]

        remainder = parts[3:-1]  # folder levels between date and the filename
        timespan = None
        variable = None

        if remainder and remainder[0].lower() in TIMESPAN_DISPLAY:
            timespan = remainder[0].lower()
            if len(remainder) >= 2:
                variable = remainder[1]
            else:
                variable = DEFAULT_PLOT_VARIABLE
        elif remainder:
            # Legacy layout: no timespan folder, first remaining part is variable
            variable = remainder[0]
        else:
            # File sits directly in the date folder: no timespan, no variable
            variable = DEFAULT_PLOT_VARIABLE

        return {
            "category": category,
            "country": country,
            "date": date_str,
            "timespan": timespan,
            "variable": variable,
            "object_path": object_path,
        }

    return None


def main():
    args = parse_args()
    repo_root = Path(args.repo_root)

    with open(args.uploaded_list) as f:
        object_paths = [line.strip() for line in f if line.strip()]

    # Group files by (category, country, date)
    groups = defaultdict(list)
    for obj_path in object_paths:
        parsed = parse_object_path(obj_path)
        if parsed is None:
            print(f"Skipping unrecognized path format: {obj_path}")
            continue
        key = (parsed["category"], parsed["country"], parsed["date"])
        groups[key].append(parsed)

    # Write per-folder index.md files
    for (category, country, date_str), entries in groups.items():
        folder = repo_root / category
        if country:
            folder = folder / country
        folder = folder / date_str
        folder.mkdir(parents=True, exist_ok=True)
        index_path = folder / "index.md"

        title = f"{country.capitalize()} — {date_str}" if country else f"Domain-wide — {date_str}"
        lines = [f"# {title}\n"]

        if category == "plots":
            # Group entries by timespan, then by variable within each timespan.
            by_timespan = defaultdict(lambda: defaultdict(list))
            for e in entries:
                timespan_key = e["timespan"] or NO_TIMESPAN_LABEL
                by_timespan[timespan_key][e["variable"]].append(e["object_path"])

            def timespan_sort_key(t):
                if t in TIMESPAN_ORDER:
                    return (0, TIMESPAN_ORDER.index(t))
                return (1, t)  # NO_TIMESPAN_LABEL (or anything unrecognized) sorts last

            timespan_order_present = sorted(by_timespan.keys(), key=timespan_sort_key)

            for timespan_key in timespan_order_present:
                timespan_label = TIMESPAN_DISPLAY.get(timespan_key, timespan_key)
                lines.append(f"## {timespan_label}\n")

                by_variable = by_timespan[timespan_key]
                variable_order = sorted(
                    by_variable.keys(),
                    key=lambda v: (v != DEFAULT_PLOT_VARIABLE, v.lower()),
                )
                for variable in variable_order:
                    lines.append(f"### {variable}\n")
                    for obj_path in sorted(by_variable[variable]):
                        fname = Path(obj_path).name
                        lines.append(f"- [{fname}]({public_url(args.bucket, obj_path)})")
                    lines.append("")
        else:
            # data/ category: no variable/timespan subfolders, just list everything.
            for e in sorted(entries, key=lambda x: x["object_path"]):
                fname = Path(e["object_path"]).name
                lines.append(f"- [{fname}]({public_url(args.bucket, e['object_path'])})")

        index_path.write_text("\n".join(lines).rstrip() + "\n")
        print(f"Wrote {index_path}")

    # Update top-level ARTIFACTS.md: one entry per (category, country, date),
    # newest first, capped at top_level_max, linking to the folder's index.md on GitHub
    # (not the bucket directly, since index.md is the human-friendly entry point)
    top_level_path = repo_root / args.top_level_file
    existing_lines = []
    if top_level_path.exists():
        existing_lines = top_level_path.read_text().splitlines()

    new_entries = []
    for (category, country, date_str) in sorted(groups.keys(), key=lambda k: k[2], reverse=True):
        if country:
            rel_path = f"{category}/{country}/{date_str}/index.md"
            label = f"{country.capitalize()} {category} — {date_str}"
        else:
            rel_path = f"{category}/{date_str}/index.md"
            label = f"Domain-wide {category} — {date_str}"
        new_entries.append(f"- [{label}]({rel_path})")

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
