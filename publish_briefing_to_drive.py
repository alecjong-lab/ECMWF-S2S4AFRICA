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


DEFAULT_EDITORS = "genevieve@rhizaresearch.org"


def _split_emails(raw):
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def _permission_for_email(file_id, email, token):
    listed = drive_request(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
        "?fields=permissions(id,emailAddress,role,type)&supportsAllDrives=true",
        token,
    )
    want = email.lower()
    for perm in listed.get("permissions") or []:
        if (perm.get("emailAddress") or "").lower() == want:
            return perm
    return None


def share_user(file_id, email, token, role):
    url = (
        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
        "?sendNotificationEmail=false&supportsAllDrives=true"
    )
    try:
        drive_request(
            "POST",
            url,
            token,
            {"role": role, "type": "user", "emailAddress": email},
        )
        return "shared"
    except RuntimeError as exc:
        text = str(exc)
        if "already" not in text.lower() and "403" not in text and "(400)" not in text:
            raise
        perm = _permission_for_email(file_id, email, token)
        if not perm:
            if "already" in text.lower() or "403" in text:
                return "exists"
            raise
        if perm.get("role") == role:
            return "exists"
        drive_request(
            "PATCH",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions/{perm['id']}"
            "?supportsAllDrives=true",
            token,
            {"role": role},
        )
        return "updated"


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
        help="Comma-separated addresses to grant commenter access",
    )
    parser.add_argument(
        "--editors",
        default=DEFAULT_EDITORS,
        help="Comma-separated addresses to grant editor (writer) access",
    )
    parser.add_argument("--remote", default="gdrive")
    args = parser.parse_args()

    local = args.file
    if not os.path.isfile(local):
        raise SystemExit(f"briefing file not found: {local}")

    dest = args.dest if args.dest.endswith("/") else f"{args.dest}/"
    filename = os.path.basename(local)
    remote_path = f"{dest}{filename}"

    mkdir = rclone("mkdir", dest, check=False)
    if mkdir.returncode != 0:
        print(mkdir.stderr or mkdir.stdout or "", file=sys.stderr)

    copy = rclone("copyto", "-v", local, remote_path, check=False)
    if copy.returncode != 0:
        sys.stderr.write(copy.stderr or copy.stdout or "")
        raise SystemExit(f"rclone copy failed ({copy.returncode})")
    if copy.stderr:
        print(copy.stderr, file=sys.stderr)

    file_id = None
    listing = rclone("lsjson", "--files-only", dest, check=False)
    if listing.returncode == 0:
        for entry in json.loads(listing.stdout or "[]"):
            if entry.get("Name") == filename and entry.get("ID"):
                file_id = entry["ID"]
                break
    if not file_id:
        listing = rclone("lsjson", "--files-only", remote_path, check=False)
        entries = json.loads(listing.stdout or "[]") if listing.returncode == 0 else []
        if entries and entries[0].get("ID"):
            file_id = entries[0]["ID"]
    if not file_id:
        raise SystemExit(f"rclone uploaded but could not find an ID for {remote_path}")

    # Print outputs before share/metadata so a later API error cannot blank the email.
    drive_url = f"https://drive.google.com/file/d/{file_id}/view"
    print(f"drive_file_id={file_id}")
    print(f"drive_url={drive_url}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"drive_file_id={file_id}\n")
            fh.write(f"drive_url={drive_url}\n")

    try:
        token = rclone_token(args.remote)
        meta = drive_request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}"
            "?fields=id,webViewLink,webContentLink&supportsAllDrives=true",
            token,
        )
        if meta.get("webViewLink"):
            drive_url = meta["webViewLink"]
            print(f"drive_url={drive_url}")
            if gh_out:
                with open(gh_out, "a") as fh:
                    fh.write(f"drive_url={drive_url}\n")
    except Exception as exc:
        print(f"WARNING: Drive metadata lookup failed ({exc})", file=sys.stderr)
        token = None
        try:
            token = rclone_token(args.remote)
        except Exception:
            pass

    editors = _split_emails(args.editors)
    emails = _split_emails(args.emails)
    editor_set = {e.lower() for e in editors}
    commenters = [e for e in emails if e.lower() not in editor_set]
    if token:
        for email in editors:
            try:
                status = share_user(file_id, email, token, "writer")
                print(f"share editor {email}: {status}", file=sys.stderr)
            except Exception as exc:
                print(f"WARNING: share editor {email} failed ({exc})", file=sys.stderr)
        for email in commenters:
            try:
                status = share_user(file_id, email, token, "commenter")
                print(f"share commenter {email}: {status}", file=sys.stderr)
            except Exception as exc:
                print(f"WARNING: share commenter {email} failed ({exc})", file=sys.stderr)
    elif editors or emails:
        print("WARNING: no Drive token; skipped sharing", file=sys.stderr)


if __name__ == "__main__":
    main()
