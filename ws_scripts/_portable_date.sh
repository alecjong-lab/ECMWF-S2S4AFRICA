# Parses EXPR ("YYYY-MM-DD", "... [+-]N days", or "N days ago") into FORMAT via
# Python's datetime — works on macOS too, unlike GNU-only `date -u -d EXPR +FORMAT`.
pydate() {
  python3 - "$1" "$2" <<'PY'
import re, sys
from datetime import datetime, timedelta, timezone
e, f = sys.argv[1].strip(), sys.argv[2]
if m := re.match(r"(\d+) days? ago", e):
    d = datetime.now(timezone.utc) - timedelta(days=int(m[1]))
elif m := re.match(r"(\d{4}-\d{2}-\d{2})\s*([+-]\d+) days?", e):
    d = datetime.strptime(m[1], "%Y-%m-%d") + timedelta(days=int(m[2]))
else:
    d = datetime.strptime(e, "%Y-%m-%d")
fmt = f.replace("%-d", str(d.day)).replace("%-m", str(d.month))
print(int(d.replace(tzinfo=timezone.utc).timestamp()) if f == "%s" else d.strftime(fmt))
PY
}
