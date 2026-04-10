# SKYDIVING WEATHER DECISION DASHBOARD

Local, fullscreen skydiving weather decision dashboard designed for drop zones, manifest areas, and personal jump planning. The dashboard runs a lightweight local data server, aggregates multiple aviation weather sources, and presents a single, at-a-glance operational view intended for continuous display on a dedicated screen.

**This project is designed to reduce cognitive load, not replace judgment.**

---

<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/1bebdd71-a218-477d-a5e9-cfbcac334115" />
---

## Features

- **Fullscreen operational display** — Electron app (Windows) or Chromium kiosk (Raspberry Pi), no browser chrome
- **Surface conditions** via live METAR — wind, gust, gust spread, temp, altimeter, visibility, ceiling, pressure altitude, density altitude
- **Winds aloft table** — speed and direction at 3k, 6k, 9k, 12k, and 15k ft with shear between levels
- **NWS NEXRAD radar loop** — nearest station auto-selected per DZ, refreshes every 5 minutes
- **Windy model overlay** — centered on the DZ, refreshes hourly
- **Jump profile–based GO / NO-GO logic** — per-category breakdown of wind, gust, temp, visibility, clouds, and density altitude
- **96 US drop zones** pre-configured across 36 states and territories
- **Substitute METAR station notes** — when a DZ airport has no weather reporting, the panel shows which nearby station is being used and how far away it is
- **Local-first architecture** — no cloud accounts, no subscriptions, no telemetry
- **Persistent state** — DZ selection, profiles, and limits survive restarts

---

## Drop Zone Coverage

96 drop zones across AL, AZ, CA, CO, CT, FL, GA, HI, IA, ID, IL, IN, MA, MD, MI, MN, MO, MT, NC, ND, NE, NH, NJ, NV, NY, OH, OR, PA, PR, RI, SC, TN, TX, UT, VA, WI.

Every DZ entry is sourced from FAA airport data and includes verified coordinates, NEXRAD radar station assignment, METAR station (or nearest substitute with distance), field elevation, and runway headings. 63 DZs publish their own METAR; the remaining 33 use the nearest reporting station with an on-screen note.

---

## Jump Profiles

Three built-in profiles, all limits fully editable in the dashboard:

| Limit | Student | A-B License | Experienced (C-D) |
|---|---|---|---|
| Max surface wind | 14 kt | 19 kt | 24 kt |
| Max gust | 14 kt | 19 kt | 24 kt |
| Max gust spread | 8 kt | 10 kt | 12 kt |
| Max wind shear ≤14k | 10 kt | 12 kt | 15 kt |
| Max dir change | 90° | 120° | 150° |
| Min temperature | 70°F | 68°F | 50°F |

---

## Architecture

- **`dz_feed_server.py`** — Python HTTP server on port 8765. Fetches METAR (aviationweather.gov), winds aloft (Open-Meteo/ECMWF), and radar. Serves a stable JSON API. Reads all config from `config/`.
- **`dashboard.html`** — Single-file HTML/CSS/JS frontend. Polls the local server and renders all panels. No external runtime dependencies.
- **Electron wrapper** — Starts the Python server automatically and opens the dashboard in a native fullscreen window (Windows).
- **Pi kiosk** — Starts the server and opens Chromium in fullscreen kiosk mode (Raspberry Pi).

---

## Installation

### Windows (Electron App)

1. Download `Skydiving-Dashboard-Setup-x.x.x.exe` from [GitHub Releases](https://github.com/N1OG/Skydiving-hamdash-project/releases)
2. Run the installer (accept any SmartScreen prompts — the app is unsigned)
3. Launch **Skydiving Dashboard** from the Start Menu

**Requirements:** Windows 10/11 (64-bit) · Python 3.9+ on PATH · Internet connection

On launch the app starts the local feed server and opens the dashboard fullscreen. DZ selection and jump profiles are saved automatically.

### Raspberry Pi (Kiosk)

1. Download the Pi zip from [GitHub Releases](https://github.com/N1OG/Skydiving-hamdash-project/releases) and extract it
2. Run the installer:

```bash
bash pi/install_pi.sh
```

3. Double-click the **Skydiving Dashboard** icon on the desktop to launch

**Requirements:** Raspberry Pi 3B+ or newer · Raspberry Pi OS with desktop · Internet connection

#### Updating the Pi

From v1.2.2 onwards, one command handles everything — downloads the latest release, replaces app files, preserves your DZ selection and jump profiles, and restarts the server:

```bash
bash ~/skydiving-dashboard/pi/install_pi.sh --update
```

The following files are **never overwritten** on update:
- `config/active_dz.json` — your selected DZ
- `config/jump_profiles.json` — your custom limits
- `config/manual_overrides.json` — any manual overrides

---

## Configuration & Data

```
skydiving-dashboard/
  dashboard.html              # Frontend — entire UI in one file
  dz_feed_server.py           # Backend — Python weather server
  version.json                # Current build version
  config/
    dz_profiles.json          # 96 DZ definitions (coords, METAR, radar)
    dz_list.json              # DZ selector list
    active_dz.json            # ← saved: selected DZ and active profile
    jump_profiles.json        # ← saved: jump limit profiles
    manual_overrides.json     # ← saved: manual overrides
  pi/
    install_pi.sh             # Pi installer and updater
    start_kiosk.sh            # Kiosk launch script
  electron-wrapper/           # Windows Electron packaging
```

When run via Electron on Windows, writable runtime data is stored in:
```
%APPDATA%\Skydiving Dashboard\runtime\
```

---

## Release History

| Version | Changes |
|---|---|
| **v1.2.2** | Fixed coordinates for 12 DZs where lat/lon pointed to the METAR station instead of the actual DZ (affected Windy centering and winds aloft). Fixed 3 invalid NEXRAD radar codes (Ranch: KALY→KENX, Northstar: KARX→KMPX, Seneca Lake: KBUF→KBGM). Added self-updating Pi installer (`--update` flag). |
| **v1.2.1** | Station card in METAR panel now shows the bare ICAO code only; full substitute-station note stays in the observation line. |
| **v1.2.0** | Substitute METAR station distance notes in the METAR panel. Fixed electron-builder `repository` field and GitHub Actions Node.js 24 compatibility. |
| **v1.1.20** | Fixed Windy refresh interval (now 1 hour). Radar cache-busts on every 5-minute METAR refresh. Fixed null-ceiling type coercion in go/no-go logic. |
| **v1.0.x** | Initial release. |

---

## Project Status

- Actively developed
- Stable for daily operational use
- Designed for long-running kiosk deployments

Feedback from jumpers, instructors, pilots, and S&TAs is welcome. If your DZ is missing or has incorrect data, please [open an issue](https://github.com/N1OG/Skydiving-hamdash-project/issues).

---

## Disclaimer

This dashboard is a decision-support tool only. It does not replace drop zone policies, S&TA authority, instructor or pilot judgment, or real-time situational awareness.

**Always defer to local rules and actual conditions.**
