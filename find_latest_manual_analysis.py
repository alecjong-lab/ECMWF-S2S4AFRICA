"""Find the most recent "manual_analysis_YYYY-MM-DD" deck in a Drive folder
and print its webViewLink, for inclusion in the daily Kenya briefing email.

Uses the same GitHub Actions service-account access token as
publish_briefing_to_drive.py (google-github-actions/auth,
export_environment_variables: true).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

NAME_RE = re.compile(r"manual_analysis_(\d{4}-\d{2}-\d{2})")


def drive_token():
    for key in ("GOOGLE_OAUTH_ACCESS_TOKEN", "CLOUDSDK_AUTH_ACCESS_TOKEN"):
        token = os.environ.get(key)
        if token:
            return token
    try:
        proc = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"No Drive access token available ({exc})") from exc
    token = (proc.stdout or "").strip()
    if not token:
        raise SystemExit("gcloud auth print-access-token returned empty")
    return token


def drive_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Drive API GET {url} failed ({exc.code}): {detail}") from exc


def find_latest(folder_id, token):
    query = f"'{folder_id}' in parents and name contains 'manual_analysis_' and trashed = false"
    url = (
        "https://www.googleapis.com/drive/v3/files"
        f"?q={urllib.parse.quote(query)}"
        "&fields=files(id,name,webViewLink)"
        "&supportsAllDrives=true&includeItemsFromAllDrives=true"
        "&pageSize=1000"
    )
    result = drive_get(url, token)
    # Sort by the date in the filename (YYYY-MM-DD sorts lexicographically),
    # not by Drive metadata, so this matches what a human would call "latest".
    candidates = []
    for f in result.get("files", []):
        m = NAME_RE.search(f["name"])
        if m:
            candidates.append((m.group(1), f))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[-1][1]


def write_outputs(name, url):
    lines = [f"manual_analysis_name={name}\n", f"manual_analysis_url={url}\n"]
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.writelines(lines)
    print("".join(lines), end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-id", required=True)
    args = parser.parse_args()

    try:
        token = drive_token()
        latest = find_latest(args.folder_id, token)
    except Exception as exc:
        print(f"WARNING: manual analysis lookup failed ({exc})", file=sys.stderr)
        write_outputs("", "")
        return

    if not latest:
        print("WARNING: no manual_analysis_* deck found", file=sys.stderr)
        write_outputs("", "")
        return

    write_outputs(latest["name"], latest.get("webViewLink", ""))


if __name__ == "__main__":
    main()
