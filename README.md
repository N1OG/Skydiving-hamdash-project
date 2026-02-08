# SKYDIVING WEATHER DECISION DASHBOARD

Local, fullscreen skydiving weather decision dashboard designed for dropzones, manifest areas, and personal jump planning.

The dashboard runs a lightweight local data server, aggregates multiple aviation weather sources, and presents a single, at-a-glance operational view intended for continuous display on a dedicated screen.

This project is designed to **reduce cognitive load**, not replace judgment.

---
<img width="1920" height="1080" alt="Screenshot 2026-02-08 142009" src="https://github.com/user-attachments/assets/3cbd47db-52a5-4c39-a51b-ac83fc7c8d00" />
<img width="477" height="580" alt="Screenshot 2026-02-08 164851" src="https://github.com/user-attachments/assets/b37cdeaf-3f69-45dc-bf93-a312c1cb19a8" />


---

## Features
- **Fullscreen operational dashboard** (Electron app, no browser chrome).
- **Surface conditions** via METAR with trend awareness.
- **Winds aloft table** from 0–15,000 ft (1,000 ft resolution).
- **Radar visualization** (NWS).
- **Surface wind visualization** for quick pattern recognition.
- **Jump profile–based GO / NO-GO logic** (user configurable).
- **Local-first architecture** (no cloud accounts, no telemetry).
- **Persistent state** (DZ selection, profiles, overrides survive restarts).

---

## Architecture Overview

The system is intentionally simple and explicit:

- **Python feed server**
  - Fetches and normalizes weather data.
  - Exposes a small, stable JSON API.
  - Runs entirely on the local machine.

- **Dashboard UI**
  - Single fullscreen display.
  - Polls the local server.
  - Designed for continuous visibility on a TV or kiosk.

- **Electron wrapper (primary launch method)**
  - Starts the Python server automatically.
  - Opens the dashboard in a native fullscreen window.
  - Stores runtime data in a writable per-user directory.

---

## Prerequisites

- **Windows 10 / 11**
- **Python 3.9+** available on PATH  
  (required for the local feed server)
- Internet access for weather sources

No cloud services, subscriptions, or accounts are required.

---

## Recommended Usage (Electron App)

The Electron application is the **official and supported** way to run the dashboard.

### Install & Run
1. Download the latest installer from **GitHub Releases**
2. Run the installer
3. Launch **Skydiving Dashboard** from the Start Menu

On launch, the application will:
- Start the local feed server
- Open the dashboard fullscreen
- Persist DZ selection and profiles automatically

No browser interaction is required.

---

## Configuration & Data Storage

When run via Electron, all writable runtime data is stored here:
%APPDATA%\Skydiving Dashboard\runtime\


This includes:
- `config/active_dz.json`
- `config/jump_profiles.json`
- User-adjusted limits and selections

Bundled configuration files are treated as **read-only defaults** and are copied to the runtime directory on first launch.

---

## Development / Manual Run (Advanced)

Manual execution is supported for development and testing, but **not recommended** for end users.


python dz_feed_server.py --host 127.0.0.1 --port 8765

Then open dashboard.html in a browser.

This mode bypasses Electron’s persistence and window management.

Project Status

Actively developed

Stable for daily operational use

Designed for long-running kiosk deployments

Feedback from jumpers, instructors, pilots, and S&TAs is welcome.

Disclaimer

This dashboard is a decision-support tool only.

It does not replace:

Dropzone policies

S&TA authority

Instructor or pilot judgment

Real-time situational awareness

# Always defer to local rules and actual conditions.


