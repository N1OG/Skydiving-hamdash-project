#!/usr/bin/env python3
"""
Skydiving Dashboard - Local JSON Feed Server (REV)

What this server provides (stable API for dashboard.html):
  GET  /status.json
  GET  /dzs.json
  GET  /config.json
  GET  /snapshot.json
  POST /set_dz         {"code": "<dz_code>"}
  GET  /profiles.json
  POST /set_profile    {"profile_id": "<id>"}
  POST /save_profile   {"profile_id": "<id>", "name": "...", "limits": {...}}
  POST /delete_profile {"profile_id": "<id>"}  (refuses to delete built-ins)

Design rules enforced:
- No runtime geocoding; DZ metadata must be curated.
- If required live data is missing/stale, dashboard should show it and default NO-GO.
- Winds aloft come from Open-Meteo by coordinates (airport lat/lon).

This file is intentionally "boring": stable endpoints, defensive parsing, explicit error payloads.

Run:
  python dz_feed_server_REV.py --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent

# Prefer ./config/ but fall back to BASE_DIR if running from a flat folder.
CONFIG_DIR = BASE_DIR / "config"
if not CONFIG_DIR.exists():
    CONFIG_DIR = BASE_DIR

def _select_dz_profiles_path(config_dir: Path, base_dir: Path) -> Path:
    """Find dz_profiles.json in the most likely locations.

    Priority:
      1) <project>/config/dz_profiles.json
      2) <project>/dz_profiles.json
      3) <project>/config/dz_profiles.json (even if config_dir==base_dir)

    This prevents the "empty list" situation when the file is simply in a different folder.
    """
    candidates = [
        config_dir / "dz_profiles.json",
        base_dir / "dz_profiles.json",
        (base_dir / "config") / "dz_profiles.json",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    # Default (so error messages are deterministic)
    return config_dir / "dz_profiles.json"

DZ_PROFILES_PATH = _select_dz_profiles_path(CONFIG_DIR, BASE_DIR)
ACTIVE_DZ_PATH = CONFIG_DIR / "active_dz.json"
JUMP_PROFILES_PATH = CONFIG_DIR / "jump_profiles.json"
MANUAL_OVERRIDES_PATH = CONFIG_DIR / "manual_overrides.json"

DEFAULT_LIMITS = {
    "max_surface_wind_kt": 19,
    "max_gust_spread_kt": 9,
    "max_aloft_14k_kt": 60,
    "max_shear_14k_kt": 20,
    # directional sensitivity can be added later; keep place-holder for UI compatibility
    "max_dir_change_deg": 120,
    "max_gust_kt": 0,
    "min_temp_f": 0,
    "max_temp_f": 200
    }

BUILTIN_JUMP_PROFILES = {
    "student": {
        "name": "Student",
        "limits": {
            "max_surface_wind_kt": 14,
            "max_gust_kt": 18,
            "min_temp_f": 32,
            "max_temp_f": 95,
            "max_gust_spread_kt": 6,
            "max_aloft_14k_kt": 45,
            "max_shear_14k_kt": 12,
            "max_dir_change_deg": 90,
        },
    },
    "a_license": {
        "name": "A-License (conservative)",
        "limits": {
            "max_surface_wind_kt": 19,
            "max_gust_kt": 25,
            "min_temp_f": 30,
            "max_temp_f": 95,
            "max_gust_spread_kt": 9,
            "max_aloft_14k_kt": 60,
            "max_shear_14k_kt": 20,
            "max_dir_change_deg": 120,
        },
    },
    "experienced": {
        "name": "Experienced (1k+)",
        "limits": {
            "max_surface_wind_kt": 25,
            "max_gust_spread_kt": 12,
            "max_aloft_14k_kt": 80,
            "max_shear_14k_kt": 25,
            "max_dir_change_deg": 140,
        },
    },
}


def c_to_f(c: Optional[float]) -> Optional[int]:
    """Celsius → Fahrenheit (rounded int)."""
    if c is None:
        return None
    try:
        return int(round((float(c) * 9.0 / 5.0) + 32.0))
    except Exception:
        return None


def _safe_read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _http_get_text(url: str, timeout: float = 12.0, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = int(getattr(r, "status", 200))
            body = r.read().decode("utf-8", errors="replace")
            return status, body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(e.code), body
    except Exception:
        return 0, ""


def _http_get_json(url: str, timeout: float = 12.0, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    if params:
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)
        query_params.update({k: [str(v)] for k, v in params.items()})
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        url = urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))
    status, txt = _http_get_text(url, timeout=timeout, headers=headers)
    if status != 200:
        return status, None
    try:
        return status, json.loads(txt)
    except Exception:
        return status, None


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def build_radar_image_url(station: str, state: str = "") -> str:
    """Return a radar loop image URL.

    If a per-site NEXRAD station is provided, we use it. If coverage is a problem (common in far-north
    New England), we fall back to an NWS regional mosaic loop based on state.
    """
    st = (station or "").strip().upper()
    if st:
        return f"https://radar.weather.gov/ridge/standard/{urllib.parse.quote(st)}_loop.gif"

    region_by_state = {
        # NWS RIDGE regional mosaics
        "ME": "northeast", "NH": "northeast", "VT": "northeast", "MA": "northeast", "RI": "northeast", "CT": "northeast",
        "NY": "northeast", "NJ": "northeast", "PA": "northeast",
        "FL": "southeast", "GA": "southeast", "AL": "southeast", "MS": "southeast", "SC": "southeast", "NC": "southeast", "TN": "southeast",
        "IL": "central", "IN": "central", "OH": "central", "MI": "central", "WI": "central", "MN": "central", "IA": "central", "MO": "central",
        "TX": "southcentral", "OK": "southcentral", "AR": "southcentral", "LA": "southcentral",
        "CA": "pacnorthwest",
    }
    region = region_by_state.get((state or "").strip().upper(), "")
    if region:
        return f"https://radar.weather.gov/ridge/standard/{region}_loop.gif"
    return ""


def build_windy_iframe_src(lat: float, lon: float, zoom: int = 10) -> str:
    params = {
        "lat": f"{lat:.5f}",
        "lon": f"{lon:.5f}",
        "zoom": str(zoom),
        "level": "surface",
        "overlay": "wind",
        "product": "ecmwf",
    }
    return "https://embed.windy.com/embed2.html?" + urllib.parse.urlencode(params)


def fetch_metar_from_aviationweather(icao: str) -> Dict[str, Any]:
    """
    Fetch METAR in a way that does NOT get browser-style 403s.

    Strategy (most reliable first):
      1) NWS TGFTP station text file (public, simple, rarely blocked)
         https://tgftp.nws.noaa.gov/data/observations/metar/stations/KAAA.TXT
      2) AviationWeather ADDS dataserver XML (structured)
         https://(www.)aviationweather.gov/adds/dataserver_current/httpparam

    Output fields are stable for the dashboard:
      ok, station, raw, obs_time_iso, wind_dir_deg, wind_speed_kt, wind_gust_kt,
      temp_c, dewpoint_c, temp_f, dewpoint_f, altim_in_hg, error
    """
    import xml.etree.ElementTree as ET

    icao = (icao or "").strip().upper()
    out: Dict[str, Any] = {
        "source": None,
        "ok": False,
        "station": icao,
        "raw": None,
        "obs_time": None,          # ISO if available
        "fetched_unix": int(time.time()),
        "wind_dir_deg": None,
        "wind_speed_kt": None,
        "wind_gust_kt": None,
        "temp_c": None,
        "dewpoint_c": None,
        "temp_f": None,
        "dewpoint_f": None,
        "altim_in_hg": None,
        "error": None,
    }
    if not icao:
        out["error"] = "missing ICAO"
        return out

    # ---- (1) TGFTP plain text ----
    # Format:
    #   line1: YYYY/MM/DD HH:MM
    #   line2: METAR ...
    tgftp_url = f"https://tgftp.nws.noaa.gov/data/observations/metar/stations/{urllib.parse.quote(icao)}.TXT"
    status, txt = _http_get_text(
        tgftp_url,
        timeout=10.0,
        headers={
            "User-Agent": "SkydivingDashboard/1.0",
            "Accept": "text/plain,*/*",
        },
    )
    if status == 200 and txt:
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if len(lines) >= 2:
            ts = lines[0]
            raw = lines[1]
            out["source"] = "tgftp.nws.noaa.gov"
            out["raw"] = raw
            # Parse timestamp line "YYYY/MM/DD HH:MM" as UTC (NWS serves UTC)
            try:
                dt = _dt.datetime.strptime(ts, "%Y/%m/%d %H:%M").replace(tzinfo=_dt.timezone.utc)
                out["obs_time"] = dt.isoformat().replace("+00:00", "Z")
            except Exception:
                out["obs_time"] = None

            # Lightweight METAR parsing for wind/temp/dew/altim (same logic as before).
            tokens = raw.split()
            wind_dir = wind_spd = wind_gust = None
            temp_c = dew_c = altim = None

            # Wind token: 18012G18KT or 00000KT or VRB05KT
            for t in tokens:
                if t.endswith("KT") and (len(t) >= 5):
                    mm = re.fullmatch(r"(VRB|\d{3})(\d{2,3})(G(\d{2,3}))?KT", t)
                    if mm:
                        wd = mm.group(1)
                        wind_dir = None if wd == "VRB" else int(wd)
                        wind_spd = int(mm.group(2))
                        wind_gust = int(mm.group(4)) if mm.group(4) else None
                        break

            # Temp/dew token: 18/12 or M02/M05
            for t in tokens:
                mm = re.fullmatch(r"(M?\d{2})/(M?\d{2})", t)
                if mm:
                    def _parse_td(s: str) -> int:
                        return -int(s[1:]) if s.startswith("M") else int(s)
                    temp_c = _parse_td(mm.group(1))
                    dew_c = _parse_td(mm.group(2))
                    break

            # Altimeter: A2992
            for t in tokens:
                mm = re.fullmatch(r"A(\d{4})", t)
                if mm:
                    altim = round(int(mm.group(1)) / 100.0, 2)
                    break

            out.update({
                "wind_dir_deg": wind_dir,
                "wind_speed_kt": wind_spd,
                "wind_gust_kt": wind_gust,
                "temp_c": temp_c,
                "dewpoint_c": dew_c,
                "altim_in_hg": altim,
            })
            out["temp_f"] = c_to_f(out["temp_c"])
            out["dewpoint_f"] = c_to_f(out["dewpoint_c"])

            # Consider OK if we at least got a raw METAR.
            out["ok"] = True
            return out

    # ---- (2) ADDS XML fallback ----
    params = {
        "dataSource": "metars",
        "requestType": "retrieve",
        "format": "xml",
        "stationString": icao,
        "hoursBeforeNow": "2",
        "mostRecentForEachStation": "true",
    }

    # Try both hostnames; some networks/proxies behave differently.
    adds_urls = [
        "https://aviationweather.gov/adds/dataserver_current/httpparam?" + urllib.parse.urlencode(params),
        "https://www.aviationweather.gov/adds/dataserver_current/httpparam?" + urllib.parse.urlencode(params),
    ]

    last_status = 0
    last_body = ""
    for url in adds_urls:
        status, body = _http_get_text(
            url,
            timeout=12.0,
            headers={
                "User-Agent": "SkydivingDashboard/1.0",
                "Accept": "application/xml,text/xml,*/*",
            },
        )
        last_status, last_body = status, body
        if status == 200 and body:
            try:
                root = ET.fromstring(body)
            except Exception:
                continue

            metar_el = None
            data_el = root.find(".//data")
            if data_el is not None:
                metar_el = data_el.find("METAR")
            if metar_el is None:
                continue

            def _get_text(tag: str) -> Optional[str]:
                el = metar_el.find(tag)
                if el is None or el.text is None:
                    return None
                return el.text.strip()

            out["source"] = "aviationweather.gov/adds"
            out["raw"] = _get_text("raw_text")
            out["obs_time"] = _get_text("observation_time")
            out["wind_dir_deg"] = _to_float(_get_text("wind_dir_degrees"))
            out["wind_speed_kt"] = _to_float(_get_text("wind_speed_kt"))
            out["wind_gust_kt"] = _to_float(_get_text("wind_gust_kt"))
            out["temp_c"] = _to_float(_get_text("temp_c"))
            out["dewpoint_c"] = _to_float(_get_text("dewpoint_c"))
            out["altim_in_hg"] = _to_float(_get_text("altim_in_hg"))
            out["temp_f"] = c_to_f(out["temp_c"])
            out["dewpoint_f"] = c_to_f(out["dewpoint_c"])

            # OK if we got an obs time and at least one numeric field.
            if out["obs_time"] and (out["wind_speed_kt"] is not None or out["temp_c"] is not None or out["altim_in_hg"] is not None):
                out["ok"] = True
                return out

            out["error"] = "metar missing expected fields"
            return out

    out["error"] = f"metar fetch failed (HTTP {last_status})"
    return out

    params = {
        "dataSource": "metars",
        "requestType": "retrieve",
        "format": "xml",
        "stationString": icao,
        # give us a little buffer; many stations are 55–60 min cadence
        "hoursBeforeNow": "2",
        "mostRecentForEachStation": "true",
    }
    url = "https://aviationweather.gov/adds/dataserver_current/httpparam?" + urllib.parse.urlencode(params)

    status, txt = _http_get_text(url, timeout=12.0, headers={"User-Agent": "SkydivingDashboard/1.0"})
    if status != 200 or not txt:
        out["error"] = f"metar fetch failed (HTTP {status})"
        return out

    try:
        root = ET.fromstring(txt)
    except Exception:
        out["error"] = "metar parse failed (bad XML)"
        return out

    # ADDS structure: <data><METAR>...</METAR></data>
    metar_el = None
    data_el = root.find(".//data")
    if data_el is not None:
        metar_el = data_el.find("METAR")
    if metar_el is None:
        out["error"] = "no METAR in response"
        return out

    def _get_text(tag: str) -> Optional[str]:
        el = metar_el.find(tag)
        if el is None or el.text is None:
            return None
        return el.text.strip()

    raw = _get_text("raw_text")
    if raw:
        out["raw"] = raw

    out["obs_time"] = _get_text("observation_time")

    # Numeric fields
    out["wind_dir_deg"] = _to_float(_get_text("wind_dir_degrees"))
    out["wind_speed_kt"] = _to_float(_get_text("wind_speed_kt"))
    out["wind_gust_kt"] = _to_float(_get_text("wind_gust_kt"))
    out["temp_c"] = _to_float(_get_text("temp_c"))
    out["dewpoint_c"] = _to_float(_get_text("dewpoint_c"))
    out["altim_in_hg"] = _to_float(_get_text("altim_in_hg"))

    out["temp_f"] = c_to_f(out["temp_c"])
    out["dewpoint_f"] = c_to_f(out["dewpoint_c"])

    # Treat as OK if we got an obs time and at least one wind/temp/alt field.
    if out["obs_time"] and (out["wind_speed_kt"] is not None or out["temp_c"] is not None or out["altim_in_hg"] is not None):
        out["ok"] = True
        return out

    out["error"] = "metar missing expected fields"
    return out

    # Use the dataserver with XML? We'll keep it super simple: plain text endpoint.
    # Note: If a network blocks NOAA, this will fail visibly (by design).
    url = "https://aviationweather.gov/metar/data?ids=" + urllib.parse.quote(icao) + "&format=raw&date=0&hours=0"
    status, txt = _http_get_text(url, timeout=12.0, headers={"User-Agent": "SkydivingDashboard/1.0"})
    if status != 200 or not txt:
        out["error"] = f"metar fetch failed (HTTP {status})"
        return out

    # AviationWeather returns HTML; METAR appears in <code> or in pre blocks.
    raw = None
    for pat in [r"<code>(.*?)</code>", r"<pre[^>]*>(.*?)</pre>"]:
        m = re.search(pat, txt, re.DOTALL | re.IGNORECASE)
        if m:
            raw = m.group(1)
            break
    if raw:
        raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        out["error"] = "metar not found in response"
        return out

    # Very lightweight METAR parsing for: wind dir/speed/gust, temp/dew, altimeter, obs time token.
    # This is not a full METAR parser; we keep failure modes explicit.
    tokens = raw.split()
    wind_dir = wind_spd = wind_gust = None
    temp_c = dew_c = altim = None
    obs_time = None

    # Obs time token like 271651Z
    for t in tokens:
        if re.fullmatch(r"\d{6}Z", t):
            obs_time = t
            break

    # Wind token: 18012G18KT or 00000KT or VRB05KT
    for t in tokens:
        if t.endswith("KT") and (len(t) >= 5):
            m = re.fullmatch(r"(VRB|\d{3})(\d{2,3})(G(\d{2,3}))?KT", t)
            if m:
                wd = m.group(1)
                wind_dir = None if wd == "VRB" else int(wd)
                wind_spd = int(m.group(2))
                wind_gust = int(m.group(4)) if m.group(4) else None
                break

    # Temp/dew token: 18/12 or M02/M05
    for t in tokens:
        m = re.fullmatch(r"(M?\d{2})/(M?\d{2})", t)
        if m:
            def _parse_td(s: str) -> int:
                return -int(s[1:]) if s.startswith("M") else int(s)
            temp_c = _parse_td(m.group(1))
            dew_c = _parse_td(m.group(2))
            break

    # Altimeter: A2992
    for t in tokens:
        m = re.fullmatch(r"A(\d{4})", t)
        if m:
            altim = round(int(m.group(1)) / 100.0, 2)
            break

    out.update({
        "ok": True,
        "raw": raw,
        "wind_dir_deg": wind_dir,
        "wind_speed_kt": wind_spd,
        "wind_gust_kt": wind_gust,
        "temp_c": temp_c,
        "temp_f": c_to_f(temp_c) if temp_c is not None else None,
        "dewpoint_c": dew_c,
        "dewpoint_f": c_to_f(dew_c) if dew_c is not None else None,
        "altim_in_hg": altim,
        "obs_time": obs_time,
        "fetched_unix": int(time.time()),
    })
    return out


# ---- Open-Meteo winds aloft (component interpolation) ----

def _dirspd_to_uv(dir_deg: float, spd_kt: float) -> Tuple[float, float]:
    # Meteorological direction: direction FROM which wind blows.
    # Convert to u/v (east/north) components (knots).
    # u = -spd * sin(dir), v = -spd * cos(dir)
    ang = math.radians(dir_deg % 360.0)
    u = -spd_kt * math.sin(ang)
    v = -spd_kt * math.cos(ang)
    return u, v


def _uv_to_dirspd(u: float, v: float) -> Tuple[float, float]:
    # Invert above. Return (dir_deg_from, spd)
    spd = math.hypot(u, v)
    if spd <= 1e-9:
        return 0.0, 0.0
    # dir = atan2(-u, -v)
    ang = math.degrees(math.atan2(-u, -v)) % 360.0
    return ang, spd


def fetch_open_meteo_aloft(lat: float, lon: float) -> Dict[str, Any]:
    """
    Winds/temps 0..15000 ft each 1000 ft.

    We use Open-Meteo pressure-level data with geopotential heights so the altitude mapping
    isn't a hand-wavy "pressure_to_m" guess. We interpolate u/v and temp vs altitude.
    """
    out: Dict[str, Any] = {"source": "Open-Meteo", "ok": False, "status": 0, "error": None, "valid_time_iso": None, "levels": []}

    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        out["error"] = "invalid lat/lon"
        return out

    pressure_levels = [1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750, 725, 700, 650, 600]
    base_url = "https://api.open-meteo.com/v1/forecast"
    now_utc = _dt.datetime.now(_dt.timezone.utc)

    # Ask for geopotential height and T at pressure levels + 10m winds and 2m temp as surface anchor.
    hourly_fields = ["wind_speed_10m", "wind_direction_10m", "temperature_2m"]
    for p in pressure_levels:
        hourly_fields += [
            f"geopotential_height_{p}hPa",
            f"wind_speed_{p}hPa",
            f"wind_direction_{p}hPa",
            f"temperature_{p}hPa",
        ]

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(hourly_fields),
        "wind_speed_unit": "kn",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "forecast_days": 1,
    }

    status, data = _http_get_json(base_url, timeout=12.0, headers={"User-Agent": "SkydivingDashboard/1.0"}, params=params)
    out["status"] = int(status)

    if status != 200 or not isinstance(data, dict):
        out["error"] = f"fetch failed (HTTP {status})"
        return out

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not isinstance(times, list) or not times:
        out["error"] = "missing hourly.time"
        return out

    # Pick the "current" hour for the DZ location.
    # We request timezone=auto, so Open-Meteo returns hourly.time in local wall-clock time.
    # If utc_offset_seconds is missing, we derive it from the returned timezone name.
    utc_offset = 0
    tz_name = data.get("timezone")
    try:
        utc_offset = int(data.get("utc_offset_seconds") or 0)
    except Exception:
        utc_offset = 0
    if utc_offset == 0 and isinstance(tz_name, str) and tz_name and tz_name.upper() != "UTC":
        try:
            from zoneinfo import ZoneInfo
            utc_offset = int((_dt.datetime.now(ZoneInfo(tz_name)).utcoffset() or _dt.timedelta()).total_seconds())
        except Exception:
            pass

    now_local = (now_utc + _dt.timedelta(seconds=utc_offset)).replace(tzinfo=None)
    target = now_local.replace(minute=0, second=0, microsecond=0)
    parsed: List[Tuple[int, _dt.datetime]] = []
    for i, t in enumerate(times):
        try:
            dt_local = _dt.datetime.fromisoformat(str(t).replace("Z", ""))
            parsed.append((i, dt_local))
        except Exception:
            continue
    if not parsed:
        out["error"] = "unparseable hourly.time"
        return out

    idx = None
    for i, dt_local in parsed:
        if dt_local == target:
            idx = i
            break

    if idx is None:
        past = [(i, dt) for (i, dt) in parsed if dt <= target]
        if past:
            idx = max(past, key=lambda x: x[1])[0]
        else:
            future = [(i, dt) for (i, dt) in parsed if dt > target]
            idx = min(future, key=lambda x: x[1])[0] if future else parsed[0][0]

    out["valid_time_iso"] = str(times[idx])
    out["timezone"] = data.get("timezone")

    def _series(key: str) -> Optional[List[Any]]:
        v = hourly.get(key)
        return v if isinstance(v, list) else None

    # Surface anchor
    ws10 = _series("wind_speed_10m")
    wd10 = _series("wind_direction_10m")
    t2 = _series("temperature_2m")

    points: List[Tuple[float, float, float, float]] = []  # (ft, u, v, tempF)
    if ws10 and wd10 and idx < len(ws10) and idx < len(wd10):
        try:
            spd = float(ws10[idx])
            direc = float(wd10[idx])
            u, v = _dirspd_to_uv(direc, spd)
            tf = float(t2[idx]) if (t2 and idx < len(t2)) else float("nan")
            points.append((0.0, u, v, tf))
        except Exception:
            pass

    # Pressure-level points (use geopotential heights to map to ft)
    for p in pressure_levels:
        z = _series(f"geopotential_height_{p}hPa")
        ws = _series(f"wind_speed_{p}hPa")
        wd = _series(f"wind_direction_{p}hPa")
        tp = _series(f"temperature_{p}hPa")
        if not (z and ws and wd and idx < len(z) and idx < len(ws) and idx < len(wd)):
            continue
        try:
            z_m = float(z[idx])
            ft = z_m * 3.28084
            spd = float(ws[idx])
            direc = float(wd[idx])
            u, v = _dirspd_to_uv(direc, spd)
            tf = float(tp[idx]) if (tp and idx < len(tp)) else float("nan")
            points.append((ft, u, v, tf))
        except Exception:
            continue

    if len(points) < 2:
        out["error"] = "no aloft profile returned (blocked or missing data)"
        return out

    points.sort(key=lambda r: r[0])

    def _interp(ft_target: float) -> Tuple[float, float, float]:
        # linear interpolate u,v,tempF vs ft
        if ft_target <= points[0][0]:
            return points[0][1], points[0][2], points[0][3]
        if ft_target >= points[-1][0]:
            return points[-1][1], points[-1][2], points[-1][3]
        lo = points[0]
        hi = points[-1]
        for i in range(1, len(points)):
            if points[i][0] >= ft_target:
                lo = points[i - 1]
                hi = points[i]
                break
        ft0, u0, v0, t0 = lo
        ft1, u1, v1, t1 = hi
        if ft1 == ft0:
            return u0, v0, t0
        t = (ft_target - ft0) / (ft1 - ft0)
        u = u0 + t * (u1 - u0)
        v = v0 + t * (v1 - v0)
        tf = t0 + t * (t1 - t0) if (t0 == t0 and t1 == t1) else float("nan")
        return u, v, tf

    levels = []
    for ft in range(0, 15001, 1000):
        u, v, tf = _interp(float(ft))
        d, s = _uv_to_dirspd(u, v)
        temp_f = None
        if tf == tf:
            temp_f = round(tf, 1)
        levels.append({
            "level_ft": int(ft),
            "dir_deg": int(round(d)) % 360,
            "speed_kt": float(round(s, 1)),
            "temp_f": temp_f,
        })

    out["ok"] = True
    out["levels"] = levels
    return out


def _profile_is_complete(p: Dict[str, Any]) -> Tuple[bool, List[str]]:
    missing = []
    if not (p.get("icao") and isinstance(p.get("icao"), str) and p.get("icao").strip()):
        missing.append("icao")
    if p.get("lat") is None or p.get("lon") is None:
        missing.append("lat/lon")
    return (len(missing) == 0), missing


def _load_jump_profiles() -> Dict[str, Any]:
    user = _safe_read_json(JUMP_PROFILES_PATH, default={})
    if not isinstance(user, dict):
        user = {}
    # built-ins first; user can override by id if they really want to
    out = dict(BUILTIN_JUMP_PROFILES)
    for k, v in user.items():
        if isinstance(v, dict):
            out[k] = v
    return out


def _ensure_files() -> None:
    # Ensure minimum files exist so first run doesn't explode.
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not ACTIVE_DZ_PATH.exists():
        _safe_write_json(ACTIVE_DZ_PATH, {"active_code": ""})
    if not JUMP_PROFILES_PATH.exists():
        _safe_write_json(JUMP_PROFILES_PATH, {})

@dataclass
class Store:
    profiles: Dict[str, Dict[str, Any]]
    active: Dict[str, Any]

    @property
    def active_code(self) -> str:
        return (self.active.get("code") or "").strip()

    @property
    def active_profile_id(self) -> str:
        return (self.active.get("active_profile_id") or "a_license").strip() or "a_license"

    def set_active(self, code: str) -> None:
        code = (code or "").strip()
        if not code or code not in self.profiles:
            return
        self.active["code"] = code
        self.active["updated"] = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _safe_write_json(ACTIVE_DZ_PATH, self.active)

    def set_active_profile(self, profile_id: str) -> None:
        profile_id = (profile_id or "").strip()
        if not profile_id:
            return
        self.active["active_profile_id"] = profile_id
        self.active["updated"] = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _safe_write_json(ACTIVE_DZ_PATH, self.active)

    def get_limits(self) -> Dict[str, Any]:
        # 1) defaults
        lim = dict(DEFAULT_LIMITS)

        # 2) jump profile limits
        jp = _load_jump_profiles()
        prof = jp.get(self.active_profile_id) if isinstance(jp, dict) else None
        if isinstance(prof, dict) and isinstance(prof.get("limits"), dict):
            lim.update(prof["limits"])

        # 3) per-user overrides in active_dz.json (wins)
        if isinstance(self.active.get("limits_overrides"), dict):
            lim.update(self.active["limits_overrides"])

        return lim


_ensure_files()

STORE = Store(
    profiles=_safe_read_json(DZ_PROFILES_PATH, default={}),
    active=_safe_read_json(ACTIVE_DZ_PATH, default={"code": "", "active_profile_id": "a_license", "limits_overrides": {}}),
)


class Handler(BaseHTTPRequestHandler):
    server_version = "dzfeed/locked-fixed"

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _send_cors(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self._send(204, b"", content_type="text/plain; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        # hot-reload config each request (cheap and prevents "restart to see fixes")
        STORE.profiles = _safe_read_json(DZ_PROFILES_PATH, default={})
        STORE.active = _safe_read_json(ACTIVE_DZ_PATH, default={"code": "", "active_profile_id": "a_license", "limits_overrides": {}})


        # Serve the dashboard from the same origin (avoids file:// + CORS issues)
        if path in ("/", "/index.html", "/dashboard", "/dashboard.html"):
            dash_path = (Path(__file__).resolve().parent / "dashboard.html")
            if dash_path.exists():
                body = dash_path.read_bytes()
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self._send_json(404, {"ok": False, "error": "dashboard.html not found next to server"})
                return

        # If someone hits an alternate filename directly, serve it if present.
        if path in ("/dashboard_LOCKED.html", "/dashboard_FINAL.html", "/dashboard_FINAL2.html", "/dashboard_LOCKED.html"):
            alt = (Path(__file__).resolve().parent / path.lstrip("/"))
            if alt.exists():
                body = alt.read_bytes()
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        if path == "/favicon.ico":
            self._send(204, b"", content_type="text/plain; charset=utf-8")
            return

        if path == "/status.json":
            code = STORE.active_code
            prof = STORE.profiles.get(code) if code else None
            self._send_json(200, {
                "ok": True,
                "active_code": code,
                "active_name": (prof or {}).get("name") if isinstance(prof, dict) else None,
                "profiles_loaded": len(STORE.profiles) if isinstance(STORE.profiles, dict) else 0,
                "server_time_unix": int(time.time()),
                "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00","Z"),
                "config_dir": str(CONFIG_DIR),
                "dz_profiles_path": str(DZ_PROFILES_PATH),
                "dz_profiles_exists": DZ_PROFILES_PATH.exists(),
                "active_dz_path": str(ACTIVE_DZ_PATH),
                "active_dz_exists": ACTIVE_DZ_PATH.exists(),
                "jump_profiles_path": str(JUMP_PROFILES_PATH),
                "jump_profiles_exists": JUMP_PROFILES_PATH.exists(),
            })
            return

        if path == "/config.json":
            self._send_json(200, {
                "ok": True,
                "active": STORE.active,
                "limits": STORE.get_limits(),
            })
            return

        if path == "/dzs.json":
            dzs = []
            counts = {"complete": 0, "incomplete": 0}
            for code, p in sorted((STORE.profiles or {}).items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
                if not isinstance(p, dict):
                    continue
                ok, missing = _profile_is_complete(p)
                if ok:
                    counts["complete"] += 1
                else:
                    counts["incomplete"] += 1

                dzs.append({
                    "code": code,
                    "name": p.get("name") or code,
                    "label": p.get("label") or p.get("name") or code,
                    "state": p.get("state") or "",
                    "city": p.get("city") or "",
                    "country": p.get("country") or "",
                    "lat": p.get("lat"),
                    "lon": p.get("lon"),
                    "icao": p.get("icao") or "",
                    "radar": p.get("radar_station") or p.get("radar") or "",
                    "runway_heading_deg": p.get("runway_heading_deg"),
                    "burble_dz_id": p.get("burble_dz_id"),
                    "burble_public_url": p.get("burble_public_url"),
                    "complete": ok,
                    "missing": missing,
                })

            # Important: we include incomplete DZs so users can see the full list,
            # but the UI and snapshot will make the incompleteness explicit and default NO-GO.
            self._send_json(200, {"ok": True, "dzs": dzs, "counts": counts})
            return

        if path == "/profiles.json":
            jp = _load_jump_profiles()
            items = []
            for pid, obj in sorted(jp.items(), key=lambda kv: kv[0]):
                if not isinstance(obj, dict):
                    continue
                items.append({
                    "profile_id": pid,
                    "name": obj.get("name") or pid,
                    "limits": obj.get("limits") if isinstance(obj.get("limits"), dict) else {},
                    "builtin": pid in BUILTIN_JUMP_PROFILES,
                })
            self._send_json(200, {"ok": True, "active_profile_id": STORE.active_profile_id, "profiles": items})
            return

        if path == "/set_dz":
            code = ""
            if "code" in q and q["code"]:
                code = q["code"][0]
            if code:
                STORE.set_active(code)
            self._send_json(200, {"ok": True, "active_code": STORE.active_code})
            return

        if path == "/snapshot.json":
            code = STORE.active_code
            prof = STORE.profiles.get(code) if code else None
            if not isinstance(prof, dict):
                self._send_json(200, {"ok": False, "error": "No active DZ set (or not found)", "active_code": code})
                return

            dz_ok, dz_missing = _profile_is_complete(prof)

            lat = prof.get("lat")
            lon = prof.get("lon")
            icao = prof.get("icao")
            radar_station = prof.get("radar_station") or prof.get("radar") or ""

            metar = fetch_metar_from_aviationweather(str(icao or ""))
            aloft = fetch_open_meteo_aloft(float(lat), float(lon)) if (dz_ok and lat is not None and lon is not None) else {
                "ok": False, "source": "Open-Meteo", "error": f"incomplete DZ metadata ({', '.join(dz_missing)})", "levels": []
            }

            self._send_json(200, {
                "ok": True,
                "server_time_unix": int(time.time()),
                "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00","Z"),
                "active_code": code,
                "profile": {
                    "code": code,
                    "name": prof.get("name") or code,
                    "label": prof.get("label") or prof.get("name") or code,
                    "icao": icao,
                    "lat": lat,
                    "lon": lon,
                    "runway_heading_deg": prof.get("runway_heading_deg"),
                    "burble_dz_id": prof.get("burble_dz_id"),
                    "burble_public_url": prof.get("burble_public_url"),
                    "radar_station": radar_station,
                },
                "dz_metadata_ok": dz_ok,
                "dz_metadata_missing": dz_missing,
                "limits": STORE.get_limits(),
                "active_profile_id": STORE.active_profile_id,
                "metar": metar,
                "aloft": aloft,
                "radar": {
                    "station": radar_station,
                    "image_url": build_radar_image_url(radar_station, prof.get('state','')),
                },
                "windy": {
                    "iframe_src": build_windy_iframe_src(float(lat), float(lon)) if (lat is not None and lon is not None) else ""
                },
            })
            return

        self._send_json(404, {"ok": False, "error": f"unknown path {path}"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            n = int(self.headers.get("Content-Length", "0"))
        except Exception:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            payload = {}

        # hot-reload
        STORE.profiles = _safe_read_json(DZ_PROFILES_PATH, default={})
        STORE.active = _safe_read_json(ACTIVE_DZ_PATH, default={"code": "", "active_profile_id": "a_license", "limits_overrides": {}})

        if path == "/set_dz":
            code = (payload.get("code") or "").strip()
            STORE.set_active(code)
            self._send_json(200, {"ok": True, "active_code": STORE.active_code})
            return

        if path == "/set_profile":
            pid = (payload.get("profile_id") or "").strip()
            if not pid:
                self._send_json(400, {"ok": False, "error": "missing profile_id"})
                return
            STORE.set_active_profile(pid)
            self._send_json(200, {"ok": True, "active_profile_id": STORE.active_profile_id})
            return

        if path == "/save_profile":
            pid = (payload.get("profile_id") or "").strip()
            name = (payload.get("name") or "").strip() or pid
            limits = payload.get("limits")
            if not pid:
                self._send_json(400, {"ok": False, "error": "missing profile_id"})
                return
            if not isinstance(limits, dict):
                self._send_json(400, {"ok": False, "error": "limits must be an object"})
                return

            user = _safe_read_json(JUMP_PROFILES_PATH, default={})
            if not isinstance(user, dict):
                user = {}
            # sanitize to numeric ints where expected
            clean_limits = {}
            for k, v in limits.items():
                if k not in DEFAULT_LIMITS:
                    continue
                try:
                    clean_limits[k] = int(v)
                except Exception:
                    # allow max_dir_change_deg, etc; still ints
                    pass
            user[pid] = {"name": name, "limits": clean_limits}
            _safe_write_json(JUMP_PROFILES_PATH, user)
            self._send_json(200, {"ok": True, "saved": pid})
            return

        if path == "/delete_profile":
            pid = (payload.get("profile_id") or "").strip()
            if not pid:
                self._send_json(400, {"ok": False, "error": "missing profile_id"})
                return
            if pid in BUILTIN_JUMP_PROFILES:
                self._send_json(400, {"ok": False, "error": "cannot delete builtin profile"})
                return
            user = _safe_read_json(JUMP_PROFILES_PATH, default={})
            if not isinstance(user, dict):
                user = {}
            if pid in user:
                user.pop(pid, None)
                _safe_write_json(JUMP_PROFILES_PATH, user)
            self._send_json(200, {"ok": True, "deleted": pid})
            return

        self._send_json(404, {"ok": False, "error": f"unknown path {path}"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}  (config: {CONFIG_DIR})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()