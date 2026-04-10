#!/usr/bin/env bash
# Skydiving Dashboard — Pi installer / updater
# Usage:
#   First install : bash install_pi.sh
#   Update        : bash install_pi.sh --update
set -euo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PI_DIR="$APP_DIR/pi"
CONFIG_DIR="$APP_DIR/config"
DESKTOP_DIR="$HOME/Desktop"
LAUNCHER="$DESKTOP_DIR/Skydiving Dashboard.desktop"

# ─── GitHub release info ──────────────────────────────────────────────────────
GITHUB_REPO="N1OG/Skydiving-hamdash-project"
RELEASE_API="https://api.github.com/repos/$GITHUB_REPO/releases/latest"

# ─── User-owned config files — never overwritten on update ───────────────────
# These store the user's active DZ selection and custom jump profiles.
PRESERVE_FILES=(
    "config/active_dz.json"
    "config/jump_profiles.json"
    "config/manual_overrides.json"
)

# ─── Files always replaced on update ─────────────────────────────────────────
UPDATE_FILES=(
    "dashboard.html"
    "dz_feed_server.py"
    "version.json"
    "config/dz_profiles.json"
    "config/dz_list.json"
    "pi/install_pi.sh"
    "pi/start_kiosk.sh"
)

# ──────────────────────────────────────────────────────────────────────────────
do_first_install() {
    echo "=== Skydiving Dashboard — First Install ==="

    echo "Installing dependencies (chromium, python3, curl, unzip)..."
    sudo apt update
    sudo apt install -y python3 chromium-browser curl unzip \
        || sudo apt install -y python3 chromium curl unzip

    mkdir -p "$DESKTOP_DIR"

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

    chmod +x "$PI_DIR/start_kiosk.sh" || true
    chmod +x "$PI_DIR/install_pi.sh"  || true

    echo ""
    echo "Done. Double-click to launch: $LAUNCHER"
    echo "To update in the future, run:  bash $PI_DIR/install_pi.sh --update"
}

# ──────────────────────────────────────────────────────────────────────────────
do_update() {
    echo "=== Skydiving Dashboard — Update ==="

    # Check for required tools
    for cmd in curl unzip python3; do
        if ! command -v "$cmd" &>/dev/null; then
            echo "Installing missing tool: $cmd"
            sudo apt install -y "$cmd"
        fi
    done

    # ── Get latest release info from GitHub ───────────────────────────────────
    echo "Checking latest release on GitHub..."
    RELEASE_JSON=$(curl -fsSL "$RELEASE_API")

    LATEST_TAG=$(echo "$RELEASE_JSON" | python3 -c \
        "import json,sys; print(json.load(sys.stdin)['tag_name'])")

    CURRENT_VERSION="unknown"
    if [ -f "$APP_DIR/version.json" ]; then
        CURRENT_VERSION=$(python3 -c \
            "import json; print(json.load(open('$APP_DIR/version.json')).get('version','unknown'))" \
            2>/dev/null || echo "unknown")
    fi

    echo "  Installed : $CURRENT_VERSION"
    echo "  Latest    : $LATEST_TAG"

    if [ "$CURRENT_VERSION" = "${LATEST_TAG#v}" ]; then
        echo "Already up to date. Nothing to do."
        exit 0
    fi

    # ── Find the Pi zip asset URL ──────────────────────────────────────────────
    ZIP_URL=$(echo "$RELEASE_JSON" | python3 -c "
import json, sys
assets = json.load(sys.stdin)['assets']
for a in assets:
    if 'pi' in a['name'].lower() and a['name'].endswith('.zip'):
        print(a['browser_download_url'])
        break
")

    if [ -z "$ZIP_URL" ]; then
        echo "ERROR: Could not find a Pi zip asset in the latest release."
        echo "  Release: $LATEST_TAG"
        echo "  Check: https://github.com/$GITHUB_REPO/releases/latest"
        exit 1
    fi

    echo "  Downloading: $ZIP_URL"

    # ── Download and extract to a temp directory ───────────────────────────────
    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TMP_DIR"' EXIT

    curl -fsSL -o "$TMP_DIR/release.zip" "$ZIP_URL"
    unzip -q "$TMP_DIR/release.zip" -d "$TMP_DIR/extracted"

    # The zip may have a single top-level folder — find the root inside it
    EXTRACTED_ROOT=$(find "$TMP_DIR/extracted" -maxdepth 1 -mindepth 1 -type d | head -1)
    if [ -z "$EXTRACTED_ROOT" ]; then
        EXTRACTED_ROOT="$TMP_DIR/extracted"
    fi

    # ── Back up user config files ──────────────────────────────────────────────
    echo "Backing up user config files..."
    BACKUP_DIR="$TMP_DIR/user_backup"
    mkdir -p "$BACKUP_DIR/config"
    for rel_path in "${PRESERVE_FILES[@]}"; do
        src="$APP_DIR/$rel_path"
        if [ -f "$src" ]; then
            cp "$src" "$BACKUP_DIR/$rel_path"
            echo "  Preserved: $rel_path"
        fi
    done

    # ── Copy updated files ─────────────────────────────────────────────────────
    echo "Installing updated files..."
    for rel_path in "${UPDATE_FILES[@]}"; do
        src="$EXTRACTED_ROOT/$rel_path"
        dst="$APP_DIR/$rel_path"
        if [ -f "$src" ]; then
            mkdir -p "$(dirname "$dst")"
            cp "$src" "$dst"
            echo "  Updated: $rel_path"
        else
            echo "  WARNING: $rel_path not found in release zip — skipped"
        fi
    done

    # ── Restore user config files ──────────────────────────────────────────────
    echo "Restoring user config files..."
    for rel_path in "${PRESERVE_FILES[@]}"; do
        src="$BACKUP_DIR/$rel_path"
        dst="$APP_DIR/$rel_path"
        if [ -f "$src" ]; then
            cp "$src" "$dst"
            echo "  Restored: $rel_path"
        fi
    done

    # ── Fix permissions ────────────────────────────────────────────────────────
    chmod +x "$PI_DIR/start_kiosk.sh" || true
    chmod +x "$PI_DIR/install_pi.sh"  || true

    # ── Restart the server ─────────────────────────────────────────────────────
    echo "Restarting dashboard server..."
    pkill -f dz_feed_server.py 2>/dev/null || true
    sleep 1
    cd "$APP_DIR"
    nohup python3 dz_feed_server.py --host 127.0.0.1 --port 8765 \
        > "$APP_DIR/server.log" 2>&1 &

    echo ""
    echo "=== Update complete: $CURRENT_VERSION → $LATEST_TAG ==="
    echo "Server restarted. Refresh the dashboard to see changes."
}

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--update" ]; then
    do_update
else
    do_first_install
fi
