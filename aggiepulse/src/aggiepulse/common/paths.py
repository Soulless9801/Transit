"""Shared storage layout.

Local runs write under $AGGIEPULSE_DATA_ROOT (default: <repo>/data).
On Databricks set AGGIEPULSE_DATA_ROOT=/Volumes/aggiepulse/raw so the same
relative layout lands in the Unity Catalog Volume, ready for Auto Loader.

Layout (all raw files are append-only, never rewritten):
  landing/tripspark_vehicles/date=YYYY-MM-DD/<poll batch>.jsonl.gz
  landing/tripspark_base/date=YYYY-MM-DD/<ts>.json.gz
  landing/gtfs/date=YYYY-MM-DD/google_transit.zip
  landing/events/date=YYYY-MM-DD/<query range>.jsonl.gz
  landing/weather_openmeteo/date=YYYY-MM-DD/<kind>_<ts>.json.gz
  landing/weather_nws/date=YYYY-MM-DD/<ts>.json.gz
  landing/ntd/date=YYYY-MM-DD/ntd_60278.json
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_TZ = "America/Chicago"
USER_AGENT = "AggiePulse/0.1 (student hackathon research project; contact transit research team)"


def data_root() -> Path:
    return Path(os.environ.get("AGGIEPULSE_DATA_ROOT", REPO_ROOT / "data"))


def landing_dir(source: str, when: datetime | None = None) -> Path:
    """Partitioned landing directory for a source, created if missing. Partition date is UTC."""
    when = when or datetime.now(timezone.utc)
    d = data_root() / "landing" / source / f"date={when.astimezone(timezone.utc):%Y-%m-%d}"
    d.mkdir(parents=True, exist_ok=True)
    return d
