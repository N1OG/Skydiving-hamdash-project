#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$DIR"

# Start feed server
python3 dz_feed_server.py --host 127.0.0.1 --port 8765 >/tmp/dz_feed_server.log 2>&1 &
SERVER_PID=$!

sleep 1

# Launch Chromium fullscreen kiosk
if command -v chromium-browser >/dev/null 2>&1; then
  BROWSER="chromium-browser"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER="chromium"
elif command -v google-chrome >/dev/null 2>&1; then
  BROWSER="google-chrome"
else
  echo "No Chromium/Chrome found. Install chromium-browser."
  kill "$SERVER_PID" || true
  exit 1
fi

"$BROWSER" --kiosk --incognito --noerrdialogs --disable-infobars \
  --autoplay-policy=no-user-gesture-required \
  "file://$DIR/dashboard.html" || true

kill "$SERVER_PID" || true
