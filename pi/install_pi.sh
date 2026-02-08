#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
PI_DIR="$APP_DIR/pi"

chmod +x "$PI_DIR/start_kiosk.sh" || true
chmod +x "$PI_DIR/install_pi.sh" || true

echo "Installing dependencies (chromium, python3)..."
sudo apt update
sudo apt install -y python3 chromium-browser || sudo apt install -y python3 chromium

DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"

LAUNCHER="$DESKTOP_DIR/Skydiving Dashboard.desktop"
cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=Skydiving Dashboard
Comment=Launch Skydiving Dashboard (kiosk)
Exec=$PI_DIR/start_kiosk.sh
Icon=utilities-terminal
Terminal=false
Categories=Utility;
EOF

chmod +x "$LAUNCHER"

echo "Done."
echo "You can now double-click: $LAUNCHER"
