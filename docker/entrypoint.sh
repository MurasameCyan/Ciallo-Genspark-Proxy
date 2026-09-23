#!/bin/sh
set -eu

mkdir -p "${DATA_DIR:-/data}"
if command -v Xvfb >/dev/null 2>&1; then
  display="${DISPLAY:-:99}"
  Xvfb "$display" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
  echo "[entrypoint] Xvfb started display=$display"
fi

exec python /app/main.py
