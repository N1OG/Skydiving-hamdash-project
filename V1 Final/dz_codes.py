
"""dz_codes.py - Dropzone resolver for Maggie

What it does
- Resolves short DZ codes (CSC, SDC, SNE, etc.) from text (backward compatible).
- Optionally loads a full DZ directory (generated from Fourway) to resolve DZ *names*
  and provide Burble dz_id + public manifest URL.

Files it can load (optional)
- config/dz_codes.json           : overrides/extends short code -> location
- config/dz_profiles.json        : DZ directory keyed by slug (see build script)

This module stays conservative:
- Codes only match as standalone tokens.
- Name matching prefers the *longest* DZ name contained in the text.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional, Any

# Minimal built-in defaults (can be extended via config/dz_codes.json)
_DEFAULT_DZ_MAP: Dict[str, str] = {
    "SDC": "Zephyrhills, FL",
    "SNE": "Lebanon, ME",
    "CSC": "Rochelle, IL",
    "PALATKA": "Palatka, FL",
    "SDBAMA": "Falkville, AL",
}

_CODE_RE = re.compile(r"\b([A-Z]{2,6})\b")


def _candidate_paths(*parts: str):
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.abspath(os.path.join(here, *parts)),
        os.path.abspath(os.path.join(os.getcwd(), *parts)),
    ]


def _load_json_file(candidates) -> Optional[Any]:
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def _load_code_overrides() -> Dict[str, str]:
    data = _load_json_file(
        _candidate_paths("config", "dz_codes.json")
        + _candidate_paths("dz_codes.json")
    )
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in data.items():
        if not k or not v:
            continue
        out[str(k).upper().strip()] = str(v).strip()
    return out


def _load_profiles() -> Dict[str, Any]:
    data = _load_json_file(
        _candidate_paths("config", "dz_profiles.json")
        + _candidate_paths("dz_profiles.json")
    )
    return data if isinstance(data, dict) else {}


# Build maps
_DZ_MAP: Dict[str, str] = dict(_DEFAULT_DZ_MAP)
_DZ_MAP.update(_load_code_overrides())

_DZ_PROFILES: Dict[str, Any] = _load_profiles()

# Precompute label lookup for conservative "contains" matching
# Prefer longest label first to avoid partial matches.
_LABELS_DESC = sorted(
    ((k, (v.get("label") or "").strip()) for k, v in _DZ_PROFILES.items()),
    key=lambda kv: len(kv[1]),
    reverse=True,
)


def resolve_dz_code(text: str) -> Optional[str]:
    """Return a matched DZ short code like 'SDC' if present."""
    if not text:
        return None
    up = text.upper()
    for m in _CODE_RE.finditer(up):
        code = m.group(1)
        if code in _DZ_MAP:
            return code
    return None


def resolve_dz_location(text: str) -> Optional[str]:
    """Return a human-ish location string from dz_codes.json (legacy)."""
    code = resolve_dz_code(text)
    if not code:
        return None
    return _DZ_MAP.get(code)


def resolve_dz_profile_key(text: str) -> Optional[str]:
    """Resolve a DZ profile key from text by matching the DZ label (Fourway list).

    Matching rule:
    - case-insensitive substring
    - returns the *longest* matching label

    Example:
    - "Heading to Skydive Alabama" -> key for Skydive Alabama
    """
    if not text or not _LABELS_DESC:
        return None
    t = " ".join(text.lower().split())
    for key, label in _LABELS_DESC:
        if not label:
            continue
        lab = " ".join(label.lower().split())
        if lab and lab in t:
            return key
    return None


def resolve_dz_profile(text: str) -> Optional[Dict[str, Any]]:
    key = resolve_dz_profile_key(text)
    if not key:
        return None
    prof = _DZ_PROFILES.get(key)
    return prof if isinstance(prof, dict) else None


def resolve_burble_dz_id(text: str) -> Optional[int]:
    prof = resolve_dz_profile(text)
    if not prof:
        return None
    dz_id = prof.get("burble_dz_id")
    return int(dz_id) if isinstance(dz_id, int) else None


def resolve_burble_public_url(text: str) -> Optional[str]:
    prof = resolve_dz_profile(text)
    if not prof:
        return None
    url = prof.get("burble_public_url")
    return str(url) if url else None
