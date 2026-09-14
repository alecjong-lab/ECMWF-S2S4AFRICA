"""Upload a Kenya briefing pptx as a shared Google Slides deck and share it.

Uses the GitHub Actions service account via Application Default Credentials
(``google-github-actions/auth`` + rclone ``--drive-env-auth``), converts the
pptx into ``application/vnd.google-apps.presentation``, and writes the Slides
edit URL into
https://drive.google.com/drive/folders/1YE-91Uhx1E8Nx3aSk1CU1SS-B2dD-3ho
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


DEFAULT_FOLDER_ID = "1YE-91Uhx1E8Nx3aSk1CU1SS-B2dD-3ho"
DEFAULT_EDITORS = "genevieve@rhizaresearch.org"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def rclone(*args, check=True):
    if not shutil.which("rclone"):
        raise SystemExit("rclone is not on PATH")
    return subprocess.run(
        ["rclone", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def drive_flags(folder_id):
    return (
        "--drive-env-auth",
        "--drive-scope",
        "drive",
        "--drive-root-folder-id",
        folder_id,
        # Convert the uploaded pptx into a native Google Slides deck.
        "--drive-import-formats",
        "pptx",
        "--drive-export-formats",
        "pptx",
    )


def slides_url(file_id):
    return f"https://docs.google.com/presentation/d/{file_id}/edit"


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
        raise SystemExit(
            "No Drive access token for sharing. Authenticate as "
            "alec-github-action@rhiza-shared.iam.gserviceaccount.com "
            f"(google-github-actions/auth or gcloud). ({exc})"
        ) from exc
    token = (proc.stdout or "").strip()
    if not token:
        raise SystemExit("gcloud auth print-access-token returned empty")
    return token


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


def _listing_entries(path, flags):
    listing = rclone("lsjson", "--files-only", path, *flags, check=False)
    if listing.returncode != 0:
        return []
    try:
        return json.loads(listing.stdout or "[]")
    except json.JSONDecodeError:
        return []


def upload_pptx(local, folder_id):
    filename = os.path.basename(local)
    title = os.path.splitext(filename)[0]
    flags = drive_flags(folder_id)
    copy = rclone("copyto", "-v", local, f":drive:{filename}", *flags, check=False)
    if copy.returncode != 0:
        sys.stderr.write(copy.stderr or copy.stdout or "")
        raise SystemExit(f"rclone copy failed ({copy.returncode})")
    if copy.stderr:
        print(copy.stderr, file=sys.stderr)

    names = {filename, title}
    entries = _listing_entries(f":drive:{filename}", flags)
    if not entries:
        entries = [
            e
            for e in _listing_entries(":drive:", flags)
            if e.get("Name") in names or e.get("Name", "").startswith(title)
        ]
    if not entries or not entries[0].get("ID"):
        raise SystemExit(f"rclone uploaded but could not find an ID for {filename}")
    file_id = entries[0]["ID"]
    return file_id, slides_url(file_id)


def resolve_slides_link(file_id, drive_url, token):
    meta = drive_request(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        "?fields=id,name,mimeType,webViewLink&supportsAllDrives=true",
        token,
    )
    mime = meta.get("mimeType") or ""
    if mime and mime != SLIDES_MIME:
        print(
            f"WARNING: uploaded as {mime}, expected {SLIDES_MIME}",
            file=sys.stderr,
        )
    title = os.path.splitext(meta.get("name") or "")[0]
    if title and meta.get("name") != title:
        try:
            drive_request(
                "PATCH",
                f"https://www.googleapis.com/drive/v3/files/{file_id}"
                "?supportsAllDrives=true",
                token,
                {"name": title},
            )
        except Exception as exc:
            print(f"WARNING: rename to {title} failed ({exc})", file=sys.stderr)
    return meta.get("webViewLink") or drive_url


def write_outputs(file_id, drive_url):
    print(f"drive_file_id={file_id}")
    print(f"drive_url={drive_url}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"drive_file_id={file_id}\n")
            fh.write(f"drive_url={drive_url}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Local pptx path")
    parser.add_argument(
        "--folder-id",
        default=DEFAULT_FOLDER_ID,
        help="Google Drive folder id to upload into",
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
    args = parser.parse_args()

    local = args.file
    if not os.path.isfile(local):
        raise SystemExit(f"briefing file not found: {local}")

    file_id, drive_url = upload_pptx(local, args.folder_id)
    # Write the Slides URL before share/metadata so a later API error
    # cannot blank the email.
    write_outputs(file_id, drive_url)

    editors = _split_emails(args.editors)
    emails = _split_emails(args.emails)
    editor_set = {e.lower() for e in editors}
    commenters = [e for e in emails if e.lower() not in editor_set]
    try:
        token = drive_token()
    except SystemExit as exc:
        if editors or emails:
            print(f"WARNING: {exc}; skipped sharing", file=sys.stderr)
        return

    try:
        drive_url = resolve_slides_link(file_id, drive_url, token)
        write_outputs(file_id, drive_url)
    except Exception as exc:
        print(f"WARNING: Drive metadata lookup failed ({exc})", file=sys.stderr)

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


if __name__ == "__main__":
    main()
