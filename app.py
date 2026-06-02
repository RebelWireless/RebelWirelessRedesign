#!/usr/bin/env python3
"""
Rebel Wireless — Flask application
Serves the website + UISP integration (status, coverage, lead creation).
"""

import json
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

# ── Setup ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rebel-wireless")

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────
UISP_BASE_URL = os.environ.get("UISP_BASE_URL", "https://uisp.rebelwireless.ca")
UISP_TOKEN = os.environ.get("UISP_API_TOKEN", "")
UISP_TIMEOUT = int(os.environ.get("UISP_TIMEOUT", "10"))

COVERAGE_DATA_PATH = Path(os.environ.get("COVERAGE_DATA_PATH", "/app/coverage-data.json"))

# ── Cache ─────────────────────────────────────────────────────
_cache: dict = {}  # {key: {"data": ..., "ts": float, "ttl": float}}

def cached(key: str, ttl: float = 60):
    """Simple in-memory cache with TTL in seconds."""
    entry = _cache.get(key)
    now = time.time()
    if entry and (now - entry["ts"]) < entry["ttl"]:
        return entry["data"]
    return None

def cache_set(key: str, data, ttl: float = 60):
    _cache[key] = {"data": data, "ts": time.time(), "ttl": ttl}


# ── UISP API Client ───────────────────────────────────────────

def _uisp_headers() -> dict:
    return {
        "x-auth-token": UISP_TOKEN,
        "Accept": "application/json",
    }


def _uisp_get(path: str, timeout: int = UISP_TIMEOUT) -> dict | list | None:
    """GET from UISP API. Returns parsed JSON or None on failure."""
    if not UISP_TOKEN:
        return None
    url = f"{UISP_BASE_URL}{path}"
    try:
        r = requests.get(url, headers=_uisp_headers(), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        log.warning(f"UISP GET {path} failed: {e}")
        return None


def _uisp_post(path: str, payload: dict, timeout: int = UISP_TIMEOUT) -> dict | None:
    """POST to UISP API. Returns parsed JSON or None on failure."""
    if not UISP_TOKEN:
        log.warning("No UISP_API_TOKEN set — cannot POST to UISP")
        return None
    url = f"{UISP_BASE_URL}{path}"
    try:
        r = requests.post(
            url,
            json=payload,
            headers={**_uisp_headers(), "Content-Type": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        log.error(f"UISP POST {path} failed: {e} | payload={payload}")
        return None


# ── UISP Data Fetchers ────────────────────────────────────────

def fetch_network_status() -> dict:
    """Aggregate device + site health from UISP NMS."""
    cached_result = cached("network_status", ttl=30)
    if cached_result:
        return cached_result

    if not UISP_TOKEN:
        return {"available": False, "reason": "No UISP_API_TOKEN configured"}

    sites = _uisp_get("/nms/api/v2.1/sites")
    devices = _uisp_get("/nms/api/v2.1/devices")

    if sites is None or devices is None:
        return {"available": False, "reason": "UISP unreachable"}

    # Sites
    total_sites = len(sites)
    up_sites = sum(1 for s in sites if s.get("status") == "active")

    # Devices
    total_devices = len(devices)
    healthy = sum(1 for d in devices if d.get("status") == "active")
    degraded = sum(1 for d in devices if d.get("status") == "disconnected")
    down = total_devices - healthy - degraded

    result = {
        "available": True,
        "sites": {"total": total_sites, "up": up_sites},
        "devices": {"total": total_devices, "healthy": healthy, "degraded": degraded, "down": down},
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }

    cache_set("network_status", result, ttl=30)
    return result


def fetch_coverage_from_uisp() -> dict | None:
    """Pull site geo data from UISP to render on the coverage map."""
    if not UISP_TOKEN:
        return None

    cached_result = cached("uisp_coverage", ttl=300)  # 5 min
    if cached_result:
        return cached_result

    sites = _uisp_get("/nms/api/v2.1/sites")
    if not sites:
        return None

    areas = []
    for site in sites:
        ident = site.get("identification", {})
        loc = ident.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue

        status = site.get("status", "active")
        area_status = "active" if status == "active" else "coming_soon"

        description = site.get("description") or ""
        service = "WISP + Fiber" if "fiber" in description.lower() else "WISP"

        areas.append({
            "name": site.get("name", "Unknown Site"),
            "lat": lat,
            "lng": lng,
            "status": area_status,
            "service": service,
            "source": "uisp",
        })

    result = {"areas": areas, "source": "uisp", "count": len(areas)}
    cache_set("uisp_coverage", result, ttl=300)
    return result


# ── Lead Storage (fallback when UISP CRM is down) ─────────────

LEAD_LOG_PATH = Path(os.environ.get("LEAD_LOG_PATH", "/app/leads.json"))

def _save_lead_locally(payload: dict):
    """Append lead to a local JSON file as fallback."""
    LEADS: list = []
    if LEAD_LOG_PATH.exists():
        try:
            LEADS = json.loads(LEAD_LOG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    LEADS.append({
        **payload,
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    LEAD_LOG_PATH.write_text(json.dumps(LEADS, indent=2))
    log.info(f"Lead saved locally ({len(LEADS)} total)")


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    contact = get_contact_info()
    status = fetch_network_status()
    return render_template("index.html", contact=contact, network_status=status)


@app.route("/api/coverage")
def api_coverage():
    """Coverage data — prefer UISP, fall back to local JSON."""
    uisp_data = fetch_coverage_from_uisp()
    if uisp_data and uisp_data.get("areas"):
        return jsonify(uisp_data)

    data = load_coverage()
    data["source"] = "local"
    return jsonify(data)


@app.route("/api/network-status")
def api_network_status():
    return jsonify(fetch_network_status())


@app.route("/api/contact")
def api_contact():
    return jsonify(get_contact_info())


@app.route("/api/submit-lead", methods=["POST"])
def api_submit_lead():
    """Accept lead from the website signup form, push to UISP CRM."""
    try:
        return _handle_submit_lead()
    except Exception as e:
        log.exception("Lead submission crashed")
        return jsonify({"ok": False, "error": f"Server error: {e}"}), 500


def _handle_submit_lead():
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    speed = (data.get("speed") or "").strip()
    service_type = (data.get("serviceType") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "Name is required"}), 400

    # Split first/last name
    parts = name.split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    # Build UISP CRM payload
    uisp_payload = {
        "firstName": first_name,
        "lastName": last_name,
        "contacts": [],
        "addresses": [],
        "lead": True,
        "note": f"Website signup — {speed or 'any'} Mbps, {service_type or 'either'} "
                f"| Notes: {notes}" if notes else f"Website signup — {speed or 'any'} Mbps, {service_type or 'either'}",
    }

    if email:
        uisp_payload["contacts"].append({"email": email, "type": "email"})
    if phone:
        uisp_payload["contacts"].append({"phone": phone, "type": "phone"})

    if address:
        uisp_payload["addresses"].append({
            "line1": address,
            "city": "Calgary",
            "province": "AB",
            "country": "CA",
        })

    # Try creating lead in UISP CRM
    crm_result = _uisp_post("/crm/api/v1/clients", uisp_payload)

    if crm_result:
        log.info(f"Lead created in UISP: {name} → client ID {crm_result.get('id', '?')}")
        return jsonify({"ok": True, "source": "uisp", "id": crm_result.get("id")})
    else:
        # Fallback: save locally
        _save_lead_locally(uisp_payload)
        return jsonify({
            "ok": True,
            "source": "local",
            "note": "Lead saved locally — UISP CRM was unreachable. We'll import it manually.",
        })


# ── Contact ───────────────────────────────────────────────────

def get_contact_info():
    return {
        "phone": os.environ.get("REBEL_PHONE", "(587) 205-5550"),
        "email": os.environ.get("REBEL_EMAIL", "hello@rebelwireless.ca"),
        "address": os.environ.get("REBEL_ADDRESS", "315 204 1440 52 St NE, Calgary AB T2A 4T8"),
        "hours": "Monday to Friday, 9 AM – 5 PM",
        "location": "Calgary, AB — Downtown + expanding",
    }


# ── Coverage JSON loader (fallback) ───────────────────────────

_coverage_cache = {"mtime": 0, "data": None}

def load_coverage():
    try:
        mtime = COVERAGE_DATA_PATH.stat().st_mtime
        if mtime != _coverage_cache["mtime"]:
            with open(COVERAGE_DATA_PATH) as f:
                raw = json.load(f)
            _coverage_cache["mtime"] = mtime
            _coverage_cache["data"] = raw
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return _default_coverage()
    return _coverage_cache["data"]


def _default_coverage():
    return {
        "areas": [
            {"name": "Downtown Core", "lat": 51.045, "lng": -114.057, "status": "active", "service": "WISP + Fiber", "source": "local"},
            {"name": "Beltline", "lat": 51.037, "lng": -114.070, "status": "active", "service": "WISP + Fiber", "source": "local"},
            {"name": "East Village", "lat": 51.046, "lng": -114.040, "status": "active", "service": "WISP + Fiber", "source": "local"},
            {"name": "Mission", "lat": 51.033, "lng": -114.067, "status": "active", "service": "WISP + Fiber", "source": "local"},
            {"name": "Eau Claire", "lat": 51.052, "lng": -114.062, "status": "active", "service": "WISP + Fiber", "source": "local"},
        ],
        "source": "default",
    }


# ── Entrypoint ────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
