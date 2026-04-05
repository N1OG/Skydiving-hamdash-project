#!/usr/bin/env bash
# Skydiving Dashboard — Raspberry Pi Installer
# Run with: curl -fsSL <URL>/install_pi.sh | bash
# Or:       bash install_pi.sh
#
# What this does:
#   1. Downloads the latest release zip from GitHub
#   2. Wipes any previous installation (clean slate)
#   3. Installs Python 3 and Chromium if not already present
#   5. Creates a desktop launcher
#   6. Starts the dashboard immediately
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RELEASE_URL="https://github.com/N1OG/Skydiving-hamdash-project/releases/latest/download/SkydivingDashboard-RaspberryPi-Kiosk.zip"
INSTALL_DIR="$HOME/skydiving-dashboard"
DISPLAY_VAR=":0"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo ""; echo ">>> $*"; }
ok()    { echo "    OK: $*"; }
die()   { echo ""; echo "ERROR: $*" >&2; exit 1; }

# ── Step 1: Download latest release ──────────────────────────────────────────
info "Downloading latest release..."
TMP_ZIP="$(mktemp /tmp/skydiving-dashboard-XXXXXX.zip)"
trap 'rm -f "$TMP_ZIP"' EXIT

if command -v curl &>/dev/null; then
    curl -fsSL "$RELEASE_URL" -o "$TMP_ZIP"
elif command -v wget &>/dev/null; then
    wget -q "$RELEASE_URL" -O "$TMP_ZIP"
else
    die "Neither curl nor wget found. Install one and retry."
fi
ok "Downloaded"

# ── Step 2: Wipe old installation ─────────────────────────────────────────────
info "Removing old installation (if any)..."

pkill -f dz_feed_server.py 2>/dev/null && ok "Stopped old server" || true
pkill -f chromium          2>/dev/null && ok "Stopped old chromium" || true
sleep 0.5

rm -rf "$INSTALL_DIR"
ok "Removed $INSTALL_DIR"

# ── Step 3: Extract new release ───────────────────────────────────────────────
info "Extracting release..."
mkdir -p "$INSTALL_DIR"
unzip -q "$TMP_ZIP" -d "$INSTALL_DIR"

# Flatten a single top-level subdirectory if the zip was packaged that way
CONTENTS=("$INSTALL_DIR"/*)
if [ "${#CONTENTS[@]}" -eq 1 ] && [ -d "${CONTENTS[0]}" ]; then
    mv "${CONTENTS[0]}"/* "$INSTALL_DIR/"
    rmdir "${CONTENTS[0]}"
    ok "Flattened zip subdirectory"
fi

chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true
ok "Extracted to $INSTALL_DIR"

# ── Step 4: Install system dependencies ───────────────────────────────────────
info "Installing dependencies (python3, chromium)..."
sudo apt-get update -qq

# Pi OS has used both 'chromium-browser' and 'chromium' across versions
CHROMIUM_PKG="chromium"
apt-cache show chromium-browser &>/dev/null && CHROMIUM_PKG="chromium-browser"

sudo apt-get install -y python3 "$CHROMIUM_PKG"
ok "Dependencies installed"

# ── Step 5: Write start_kiosk.sh with correct runtime paths ───────────────────
info "Writing start script..."
cat > "$INSTALL_DIR/start_kiosk.sh" <<SCRIPT
#!/usr/bin/env bash
set -e

APP_DIR="$INSTALL_DIR"
cd "\$APP_DIR"

export DISPLAY=$DISPLAY_VAR
export XAUTHORITY="$HOME/.Xauthority"

echo "Stopping old processes..."
pkill -f dz_feed_server.py 2>/dev/null || true
pkill -f chromium          2>/dev/null || true
sleep 0.5

echo "Starting server..."
python3 "\$APP_DIR/dz_feed_server.py" --host 127.0.0.1 --port 8765 \
    > "\$APP_DIR/server.log" 2>&1 &

echo "Waiting for server..."
for i in \$(seq 1 40); do
    if curl -s http://127.0.0.1:8765/status.json >/dev/null 2>&1; then
        echo "Server ready"
        break
    fi
    sleep 0.25
done

# Use whichever Chromium binary is installed
CHROMIUM_BIN=""
for bin in chromium-browser chromium; do
    if command -v "\$bin" &>/dev/null; then
        CHROMIUM_BIN="\$bin"
        break
    fi
done
[ -n "\$CHROMIUM_BIN" ] || { echo "ERROR: Chromium not found"; exit 1; }

echo "Launching with \$CHROMIUM_BIN..."
"\$CHROMIUM_BIN" \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --incognito \
  http://127.0.0.1:8765/dashboard.html > "\$APP_DIR/browser.log" 2>&1 &

disown
SCRIPT

chmod +x "$INSTALL_DIR/start_kiosk.sh"
ok "start_kiosk.sh written"

# ── Step 6: Desktop launcher ───────────────────────────────────────────────────
info "Creating desktop launcher..."
DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_DIR/Skydiving Dashboard.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Skydiving Dashboard
Comment=Launch Skydiving Dashboard kiosk
Exec=$INSTALL_DIR/start_kiosk.sh
Icon=utilities-terminal
Terminal=false
Categories=Utility;
DESKTOP

chmod +x "$DESKTOP_DIR/Skydiving Dashboard.desktop"
ok "Desktop launcher created"

# ── Step 8: Launch immediately ─────────────────────────────────────────────────
info "Starting dashboard now..."
bash "$INSTALL_DIR/start_kiosk.sh" || true

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "======================================================="
echo "  Skydiving Dashboard installed successfully!"
echo ""
echo "  Install directory : $INSTALL_DIR"
echo "  Desktop launcher  : $DESKTOP_DIR/Skydiving Dashboard.desktop"
echo ""
echo "  To start manually:"
echo "    bash $INSTALL_DIR/start_kiosk.sh"
echo ""
echo "  To reinstall cleanly, just run this script again."
echo "======================================================="
