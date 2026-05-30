#!/usr/bin/env python3
"""
Rebel Wireless — Flask application
Serves the website + coverage API endpoint.
Coverage data is loaded from a JSON file for easy end-user editing.
"""

import json
import os
import time
from pathlib import Path

from flask import Flask, jsonify, render_template

app = Flask(__name__)

# Path to coverage data — mounted as a volume in Docker so users can edit it
COVERAGE_DATA_PATH = Path(os.environ.get(
    "COVERAGE_DATA_PATH",
    "/app/coverage-data.json",
))

# Cache with mtime check for auto-reload
_cache = {"mtime": 0, "data": None}


def load_coverage():
    """Load coverage data from JSON file. Auto-reloads if file changed."""
    try:
        mtime = COVERAGE_DATA_PATH.stat().st_mtime
        if mtime != _cache["mtime"]:
            with open(COVERAGE_DATA_PATH) as f:
                raw = json.load(f)
            _cache["mtime"] = mtime
            _cache["data"] = raw
    except (FileNotFoundError, json.JSONDecodeError) as e:
        # Return default data if file doesn't exist or is malformed
        return _default_coverage()
    return _cache["data"]


def _default_coverage():
    """Fallback coverage data if the JSON file is missing."""
    return {
        "areas": [
            {"name": "Downtown Core", "lat": 51.045, "lng": -114.057,
             "status": "active", "service": "WISP + Fiber"},
            {"name": "Beltline", "lat": 51.037, "lng": -114.070,
             "status": "active", "service": "WISP + Fiber"},
            {"name": "East Village", "lat": 51.046, "lng": -114.040,
             "status": "active", "service": "WISP + Fiber"},
            {"name": "Mission", "lat": 51.033, "lng": -114.067,
             "status": "active", "service": "WISP + Fiber"},
            {"name": "Eau Claire", "lat": 51.052, "lng": -114.062,
             "status": "active", "service": "WISP + Fiber"},
        ]
    }


def get_contact_info():
    """Contact details — edit here or override via env vars."""
    return {
        "phone": os.environ.get("REBEL_PHONE", "(587) 205-5550"),
        "email": os.environ.get("REBEL_EMAIL", "hello@rebelwireless.ca"),
        "address": os.environ.get(
            "REBEL_ADDRESS",
            "315 204 1440 52 St NE, Calgary AB T2A 4T8",
        ),
        "hours": "Monday to Friday, 9 AM – 5 PM",
        "location": "Calgary, AB — Downtown + expanding",
    }


@app.route("/api/coverage")
def api_coverage():
    """API endpoint returning coverage area data as JSON."""
    data = load_coverage()
    return jsonify(data)


@app.route("/api/contact")
def api_contact():
    """API endpoint returning contact info as JSON."""
    return jsonify(get_contact_info())


@app.route("/")
def index():
    """Serve the main website page."""
    return render_template("index.html", contact=get_contact_info())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
