#!/usr/bin/env bash
set -e

APP_DIR="/home/n1og/skydiving-dashboard"
cd "$APP_DIR"

export DISPLAY=:0
export XAUTHORITY=/home/n1og/.Xauthority

echo "Stopping old processes..."
pkill -f dz_feed_server.py 2>/dev/null || true
pkill -f chromium 2>/dev/null || true

echo "Starting server..."
python3 dz_feed_server.py --host 127.0.0.1 --port 8765 > server.log 2>&1 &
SERVER_PID=$!

echo "Waiting for server..."
for i in {1..40}; do
    if curl -s http://127.0.0.1:8765/status.json >/dev/null; then
        echo "Server is ready"
        break
    fi
    sleep 0.25
done

echo "Launching Dashboard..."
chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --incognito \
  http://127.0.0.1:8765/dashboard.html > browser.log 2>&1 &

disown