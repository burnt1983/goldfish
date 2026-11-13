#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SHARE="${HOME}/.local/share/goldfish"
BIN="${HOME}/.local/bin"
APPS="${HOME}/.local/share/applications"
mkdir -p "$SHARE/fish" "$BIN" "$APPS"
python3 - "$ROOT" "$SHARE" <<'PY'
import base64
import io
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1])
share = Path(sys.argv[2])
parts = sorted((root / "assets").glob("fish.b64.*"))
expected = int((root / "assets" / "parts.txt").read_text().splitlines()[0])
if len(parts) != expected:
    raise SystemExit(f"expected {expected} picture parts, found {len(parts)}")
blob = base64.b64decode("".join(part.read_text() for part in parts))
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tar:
    tar.extractall(share)
PY
if command -v cc >/dev/null 2>&1; then
  cc -shared -fPIC -O2 -o "$SHARE/splat.so" "$ROOT/splat.c" -lm
elif command -v gcc >/dev/null 2>&1; then
  gcc -shared -fPIC -O2 -o "$SHARE/splat.so" "$ROOT/splat.c" -lm
fi
install -m 0755 "$ROOT/goldfish.py" "$SHARE/goldfish.py"
ln -sfn "$SHARE/goldfish.py" "$BIN/puff"
ln -sfn "$SHARE/goldfish.py" "$BIN/goldfish"
echo "Installed."
echo "  Run: puff"
echo "  It does not start at login."
