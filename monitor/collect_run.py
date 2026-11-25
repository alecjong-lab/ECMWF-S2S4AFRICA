"""Collect everything needed to judge one day's "Daily ECMWF Download" run.

Run by .github/workflows/daily_run_monitor.yml before Claude reviews the run.
Writes into OUT_DIR (default monitor_out/):

  summary.md          deterministic overview (also the email body if Claude fails)
  run.json            run metadata (or the reason there is no run today)
  jobs.json           jobs and steps with their conclusions
  failed_steps.json   steps that failed, incl. continue-on-error ones the run
                      conclusion hides
  annotations.json    ::error:: / ::warning:: annotations per job
  history.json        failed steps of the previous runs, to tell new from chronic
  bucket_counts.json  objects uploaded per country/date to the public bucket
  logs/<job>.log      raw job logs
  log_errors.txt      error/traceback/warning lines from the logs with context

Needs `gh` on PATH and GH_TOKEN (actions: read, checks: read). Every section is
best effort: a failed API call is recorded in the output rather than raised,
so the report still goes out.
"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "alecjong-lab/ECMWF-S2S4AFRICA")
WORKFLOW_FILE = os.environ.get("MONITORED_WORKFLOW", "daily_download2.0.yml")
OUT_DIR = os.environ.get("OUT_DIR", "monitor_out")
RUN_ID = os.environ.get("RUN_ID", "").strip()
BUCKET = "africa-forecasting-data"
HISTORY_RUNS = 7
COUNTRIES = ("Namibia,Botswana,Kenya,Zambia,Madagascar,Angola,Ghana,Senegal,"
             "Ethiopia,Zimbabwe,Malawi,Great_Horn,Somalia").split(",")
ERROR_RE = re.compile(
    r"##\[error\]|##\[warning\]|Traceback \(most recent call last\)|"
    r"\b\w*(Error|Exception)\b:|\bFAILED\b|\bfatal:|No such file|"
    r"exit code [1-9]|Killed|out of memory|timed out|HTTP Error [45]\d\d",
    re.IGNORECASE)
MAX_ERROR_LINES = 1500
# ANSI CSI/OSC sequences and stray control characters (job logs carry colour codes).
ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def gh_api(path, raw=False, attempts=3):
    """Call the GitHub API through gh; return parsed JSON (or text if raw).

    Raw responses (job logs) contain terminal escape sequences, which gh refuses
    to print without --allow-escape-sequences; they are stripped afterwards so
    the saved logs and the error grep see plain text.
    """
    cmd = ["gh", "api", path] + (["--allow-escape-sequences"] if raw else [])
    last = ""
    for _ in range(attempts):
        proc = subprocess.run(cmd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            return ESCAPE_RE.sub("", proc.stdout) if raw else json.loads(proc.stdout)
        last = proc.stderr.strip()
    raise RuntimeError(f"gh api {path} failed: {last}")


def write(name, data):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(data, str):
            fh.write(data)
        else:
            json.dump(data, fh, indent=2, default=str)


def pick_run():
    """The run to review: RUN_ID if given, else the newest run created today (UTC)."""
    if RUN_ID:
        return gh_api(f"repos/{REPO}/actions/runs/{RUN_ID}"), None
    runs = gh_api(f"repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=10")["workflow_runs"]
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    todays = [r for r in runs if r["created_at"].startswith(today)]
    if not todays:
        last = runs[0]["created_at"] if runs else "never"
        return None, (f"No '{WORKFLOW_FILE}' run was created today ({today} UTC). "
                      f"Most recent run: {last}. The external cron that dispatches "
                      f"the workflow may have failed.")
    return todays[0], None


def failed_steps(jobs):
    out = []
    for job in jobs:
        for step in job.get("steps", []):
            if step.get("conclusion") in ("failure", "cancelled", "timed_out"):
                out.append({"job": job["name"], "step": step["name"],
                            "conclusion": step["conclusion"]})
        if job.get("conclusion") in ("failure", "cancelled", "timed_out"):
            out.append({"job": job["name"], "step": "(whole job)",
                        "conclusion": job["conclusion"]})
    return out


def history(current_id):
    runs = gh_api(f"repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page={HISTORY_RUNS + 3}")["workflow_runs"]
    out = []
    for run in [r for r in runs if r["id"] != current_id][:HISTORY_RUNS]:
        try:
            jobs = gh_api(f"repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
            fails = failed_steps(jobs)
        except Exception as exc:  # noqa: BLE001 - history is optional
            fails = f"unavailable: {exc}"
        out.append({"run_id": run["id"], "created_at": run["created_at"],
                    "conclusion": run["conclusion"], "failed_steps": fails})
    return out


def bucket_count(prefix):
    """Number of objects under a prefix in the public bucket (no auth)."""
    n, token = 0, None
    while True:
        q = {"prefix": prefix, "fields": "items(name),nextPageToken", "maxResults": "1000"}
        if token:
            q["pageToken"] = token
        url = (f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o?"
               + urllib.parse.urlencode(q))
        with urllib.request.urlopen(url, timeout=60) as resp:
            page = json.load(resp)
        n += len(page.get("items", []))
        token = page.get("nextPageToken")
        if not token:
            return n


def bucket_counts(run_date):
    """Counts for the dates today's run writes (run-1 = 0.4 deg, run-2 = ECMWF)
    and run-3, which yesterday's run wrote, as a baseline."""
    dates = [(run_date - dt.timedelta(days=k)).isoformat() for k in (1, 2, 3)]
    out = {"note": "run-1 is usually the 0.4 deg date, run-2 the ECMWF date; "
                   "run-3 was written by yesterday's run and is the baseline.",
           "dates": dates, "plots": {}, "data": {}}
    for d in dates:
        try:
            out["data"][d] = bucket_count(f"data/{d}/")
        except Exception as exc:  # noqa: BLE001
            out["data"][d] = f"error: {exc}"
        for c in COUNTRIES + ["olr_waves"]:
            try:
                out["plots"].setdefault(c, {})[d] = bucket_count(f"plots/{c}/{d}/")
            except Exception as exc:  # noqa: BLE001
                out["plots"].setdefault(c, {})[d] = f"error: {exc}"
    return out


def grab_logs(jobs):
    os.makedirs(os.path.join(OUT_DIR, "logs"), exist_ok=True)
    errors = []
    for job in jobs:
        safe = re.sub(r"[^\w.-]+", "_", job["name"])[:80]
        try:
            text = gh_api(f"repos/{REPO}/actions/jobs/{job['id']}/logs", raw=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"=== {job['name']}: logs unavailable ({exc})")
            continue
        write(f"logs/{safe}.log", text)
        lines = text.splitlines()
        hits = [i for i, line in enumerate(lines) if ERROR_RE.search(line)]
        if not hits:
            continue
        errors.append(f"\n=== {job['name']}  (logs/{safe}.log, {len(hits)} matching lines)")
        shown = set()
        for i in hits:
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                if j not in shown:
                    shown.add(j)
                    errors.append(f"{j + 1}: {lines[j][:400]}")
    if len(errors) > MAX_ERROR_LINES:
        errors = errors[:MAX_ERROR_LINES] + [f"... truncated, grep logs/ for the rest"]
    write("log_errors.txt", "\n".join(errors) or "(no matching lines)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    summary = []
    try:
        run, missing = pick_run()
    except Exception as exc:  # noqa: BLE001
        run, missing = None, f"Could not query workflow runs: {exc}"
    if run is None:
        write("run.json", {"missing": missing})
        write("summary.md", f"STATUS: BROKEN\n\n{missing}\n")
        print(missing)
        return

    write("run.json", run)
    run_date = dt.date.fromisoformat(run["created_at"][:10])
    summary += [f"Run {run['id']} ({run['html_url']})",
                f"created {run['created_at']}, status {run['status']}, "
                f"conclusion {run['conclusion']}, branch {run['head_branch']}"]

    jobs = []
    try:
        jobs = gh_api(f"repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
        write("jobs.json", jobs)
        fails = failed_steps(jobs)
        write("failed_steps.json", fails)
        summary.append(f"\n{len(jobs)} jobs, {len(fails)} failed steps:")
        summary += [f"- {f['job']} / {f['step']}: {f['conclusion']}" for f in fails]
    except Exception as exc:  # noqa: BLE001
        summary.append(f"jobs unavailable: {exc}")

    anns = {}
    for job in jobs:
        try:
            anns[job["name"]] = [
                {"level": a["annotation_level"], "message": a["message"]}
                for a in gh_api(f"repos/{REPO}/check-runs/{job['id']}/annotations")]
        except Exception as exc:  # noqa: BLE001
            anns[job["name"]] = f"unavailable: {exc}"
    write("annotations.json", anns)

    grab_logs(jobs)

    try:
        write("history.json", history(run["id"]))
    except Exception as exc:  # noqa: BLE001
        write("history.json", {"error": str(exc)})

    try:
        write("bucket_counts.json", bucket_counts(run_date))
    except Exception as exc:  # noqa: BLE001
        write("bucket_counts.json", {"error": str(exc)})

    status = "OK" if run["conclusion"] == "success" and not failed_steps(jobs) else "ATTENTION"
    if run["status"] != "completed":
        status = "ATTENTION"
        summary.insert(0, "The run has not finished yet.")
    elif run["conclusion"] not in ("success", None):
        status = "BROKEN"
    write("summary.md", f"STATUS: {status}\n\n" + "\n".join(summary) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    sys.exit(main())
