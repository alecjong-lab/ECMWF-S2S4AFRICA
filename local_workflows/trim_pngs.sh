# Trims the blank left and top margins off a PNG down to its real content
# (plus a small pad) — right and bottom are left untouched on purpose, since
# the provenance stamp lives in the bottom-right corner and would otherwise
# anchor a naive crop out to the far edge on that side. Source this and call
# `trim_png PATH [PAD_PX]` (pad defaults to 20). Safe to call on any PNG;
# a fully blank image is left alone.
trim_png() {
  local path="$1" pad="${2:-20}"
  python3 - "$path" "$pad" <<'PY'
import sys
from PIL import Image
import numpy as np

path, pad = sys.argv[1], int(sys.argv[2])
img = Image.open(path).convert("RGB")
arr = np.array(img)
non_white = np.any(arr != 255, axis=2)
cols = np.where(non_white.any(axis=0))[0]
rows = np.where(non_white.any(axis=1))[0]
if len(cols) == 0 or len(rows) == 0:
    sys.exit(0)  # blank image, nothing to trim
left = max(int(cols.min()) - pad, 0)
top = max(int(rows.min()) - pad, 0)
img.crop((left, top, img.width, img.height)).save(path)
PY
}

# Calls trim_png on every *.png directly inside a directory (non-recursive).
trim_pngs_in_dir() {
  local dir="$1" pad="${2:-20}"
  local f
  for f in "$dir"/*.png; do
    [ -e "$f" ] && trim_png "$f" "$pad"
  done
}
