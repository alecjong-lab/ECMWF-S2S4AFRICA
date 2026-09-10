"""Upload a Kenya briefing pptx to Google Drive via rclone and share it.

Uses the same gdrive remote as the subscriber-sheet fetch (secrets.RCLONE_CONFIG).
Prints GitHub Actions outputs: drive_url=... and drive_file_id=...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def rclone(*args, check=True):
    return subprocess.run(
        ["rclone", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def rclone_token(remote="gdrive"):
    # rclone copy refreshes the token; read it back from the config rclone just wrote.
    dump = rclone("config", "dump")
    remotes = json.loads(dump.stdout)
    token_raw = remotes[remote]["token"]
    token = json.loads(token_raw) if isinstance(token_raw, str) else token_raw
    access = token.get("access_token")
    if not access:
        raise SystemExit("rclone gdrive remote has no access_token")
    return access


def drive_request(method, url, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Drive API {method} {url} failed ({exc.code}): {detail}") from exc


def share_reader(file_id, email, token):
    url = (
        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
        "?sendNotificationEmail=false&supportsAllDrives=true"
    )
    try:
        drive_request(
            "POST",
            url,
            token,
            {"role": "reader", "type": "user", "emailAddress": email},
        )
        return "shared"
    except RuntimeError as exc:
        text = str(exc)
        if "already" in text.lower() or "403" in text:
            return "exists"
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Local pptx path")
    parser.add_argument(
        "--dest",
        default="gdrive:S2S_briefings/Kenya/",
        help="rclone destination folder, including remote prefix",
    )
    parser.add_argument(
        "--emails",
        default="",
        help="Comma-separated addresses to grant reader access",
    )
    parser.add_argument("--remote", default="gdrive")
    args = parser.parse_args()

    local = args.file
    if not os.path.isfile(local):
        raise SystemExit(f"briefing file not found: {local}")

    dest = args.dest if args.dest.endswith("/") else f"{args.dest}/"
    remote_path = f"{dest}{os.path.basename(local)}"

    copy = rclone("copy", local, dest)
    if copy.returncode != 0:
        sys.stderr.write(copy.stderr)
        raise SystemExit(f"rclone copy failed ({copy.returncode})")

    listing = rclone("lsjson", "--files-only", remote_path)
    entries = json.loads(listing.stdout or "[]")
    if not entries:
        raise SystemExit(f"rclone uploaded but could not find {remote_path}")
    file_id = entries[0]["ID"]

    token = rclone_token(args.remote)
    meta = drive_request(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        "?fields=id,webViewLink,webContentLink&supportsAllDrives=true",
        token,
    )
    drive_url = meta.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"

    emails = [e.strip() for e in args.emails.split(",") if e.strip()]
    for email in emails:
        status = share_reader(file_id, email, token)
        print(f"share {email}: {status}", file=sys.stderr)

    print(f"drive_file_id={file_id}")
    print(f"drive_url={drive_url}")


if __name__ == "__main__":
    main()
