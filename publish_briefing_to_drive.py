"""Upload a Kenya briefing pptx to Google Drive and share it.

Uploads with the GitHub Actions service account access token
(google-github-actions/auth), not rclone. Default destination is
https://drive.google.com/drive/folders/1YE-91Uhx1E8Nx3aSk1CU1SS-B2dD-3ho
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


DEFAULT_FOLDER_ID = "1YE-91Uhx1E8Nx3aSk1CU1SS-B2dD-3ho"
DEFAULT_EDITORS = "genevieve@rhizaresearch.org"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


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
            "No Drive access token. Authenticate as "
            "alec-github-action@rhiza-shared.iam.gserviceaccount.com "
            f"(google-github-actions/auth or gcloud). ({exc})"
        ) from exc
    token = (proc.stdout or "").strip()
    if not token:
        raise SystemExit("gcloud auth print-access-token returned empty")
    return token


def drive_request(method, url, token, body=None, content_type="application/json"):
    data = body
    if data is not None and not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
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


def find_existing(folder_id, filename, token):
    escaped = filename.replace("\\", "\\\\").replace("'", "\\'")
    query = (
        f"'{folder_id}' in parents and name = '{escaped}' and trashed = false"
    )
    params = urllib.parse.urlencode(
        {
            "q": query,
            "fields": "files(id,webViewLink)",
            "pageSize": "1",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }
    )
    listed = drive_request(
        "GET",
        f"https://www.googleapis.com/drive/v3/files?{params}",
        token,
    )
    files = listed.get("files") or []
    return files[0] if files else None


def upload_pptx(local, folder_id, token):
    filename = os.path.basename(local)
    with open(local, "rb") as fh:
        payload = fh.read()
    existing = find_existing(folder_id, filename, token)
    if existing:
        url = (
            f"https://www.googleapis.com/upload/drive/v3/files/{existing['id']}"
            "?uploadType=media&supportsAllDrives=true"
            "&fields=id,webViewLink"
        )
        return drive_request("PATCH", url, token, payload, content_type=PPTX_MIME)

    boundary = uuid.uuid4().hex
    metadata = json.dumps(
        {"name": filename, "parents": [folder_id], "mimeType": PPTX_MIME}
    )
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {PPTX_MIME}\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    url = (
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=multipart&supportsAllDrives=true"
        "&fields=id,webViewLink"
    )
    return drive_request(
        "POST",
        url,
        token,
        body,
        content_type=f"multipart/related; boundary={boundary}",
    )


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

    token = drive_token()
    meta = upload_pptx(local, args.folder_id, token)
    file_id = meta.get("id")
    if not file_id:
        raise SystemExit(f"Drive upload returned no file id: {meta}")
    drive_url = (
        meta.get("webViewLink")
        or f"https://drive.google.com/file/d/{file_id}/view"
    )
    write_outputs(file_id, drive_url)

    editors = _split_emails(args.editors)
    emails = _split_emails(args.emails)
    editor_set = {e.lower() for e in editors}
    commenters = [e for e in emails if e.lower() not in editor_set]
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
