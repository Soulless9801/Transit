"""Cached dataset adapters and schedule-based trip recommendations."""

import csv
import gzip
import json
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .data import ROOT, DatabricksBackend, LocalBackend, latest_base

STATIC = Path(__file__).resolve().parents[1] / "frontend/public/static"


def static(name):
    return json.loads((STATIC / name).read_text())


def distance(a, b):
    return (
        6371000
        * 2
        * math.asin(
            min(
                1,
                math.sqrt(
                    math.sin(math.radians(b[1] - a[1]) / 2) ** 2
                    + math.cos(math.radians(a[1]))
                    * math.cos(math.radians(b[1]))
                    * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
                ),
            )
        )
    )


def affected(event):
    if event.get("lat") is None or event.get("lon") is None:
        return []
    ids = {
        r
        for s in static("stops.geojson")["features"]
        if distance(
            s["geometry"]["coordinates"], [float(event["lon"]), float(event["lat"])]
        )
        <= 600
        for r in s["properties"]["routes"]
    }
    return [
        r["route_short_name"] for r in static("routes.json") if r["route_id"] in ids
    ]


def filter_event(event, rules, venues):
    for rule in rules["drop_rules"]:
        value = event.get(rule["field"]) or ""
        values = value if isinstance(value, list) else [value]
        if any(
            (
                str(v) == str(rule["equals"])
                if "equals" in rule
                else re.search(rule["regex"], str(v), re.IGNORECASE)
            )
            for v in values
        ):
            return None
    venue = next(
        (
            v
            for v in venues
            if any(
                alias.lower() in (event.get("location_title") or "").lower()
                for alias in v["aliases"].split("|")
            )
        ),
        {},
    )
    lat, lon = (
        event.get("location_latitude") or venue.get("lat"),
        event.get("location_longitude") or venue.get("lon"),
    )
    if (
        not lat
        or not lon
        or not (30.50 <= float(lat) <= 30.80 and -96.60 <= float(lon) <= -96.20)
    ):
        return None
    size = venue.get("capacity") or event.get("rsvp_total")
    if not size:
        return None
    weight = max(
        [rules["category_weights"].get(c, 1) for c in (event.get("event_types") or [])]
        or [1]
    )
    significance = math.log1p(float(size)) * weight
    if significance < 5:
        return None
    return {
        "event_id": event.get("id"),
        "title": event.get("title"),
        "start_ts": event.get("date_utc"),
        "end_ts": event.get("date2_utc"),
        "venue_key": venue.get("display_name"),
        "lat": float(lat),
        "lon": float(lon),
        "significance": significance,
        "impact_provenance": "simulated prior",
    }


def local_events():
    import yaml

    seed = ROOT / "seeds"
    if not seed.exists():
        seed = Path(__file__).resolve().parents[1] / "fallback/seeds"
    rules = yaml.safe_load((seed / "event_filters.yaml").read_text())
    venues = list(csv.DictReader((seed / "venues.csv").open()))
    rows = {}
    for path in (ROOT / "data/landing/events").rglob("*.gz"):
        try:
            with gzip.open(path, "rt") as stream:
                for line in stream:
                    payload = json.loads(line).get("payload", {})
                    for event in payload if isinstance(payload, list) else [payload]:
                        row = filter_event(event, rules, venues)
                        if row:
                            rows[row["event_id"]] = row
        except (ValueError, OSError, EOFError):
            continue
    now = datetime.now(UTC)
    return sorted(
        [
            r
            for r in rows.values()
            if r["start_ts"]
            and now
            <= datetime.fromisoformat(r["start_ts"]).replace(tzinfo=UTC)
            <= now + timedelta(days=7)
        ],
        key=lambda r: r["start_ts"],
    )


def local_alerts():
    base = latest_base()
    rows = []
    for alert in base.get("serviceInterruptions", []):
        rows.append(
            {
                **alert,
                "title": alert.get("name", "Service interruption"),
                "text": re.sub("<[^>]+>", " ", alert.get("description", "")),
                "start_ts": alert.get("startDateUtc"),
                "end_ts": alert.get("endDateUtc"),
                "routes": [
                    r["shortName"]
                    for r in base.get("routes", [])
                    if any(
                        str(alert.get("key"))
                        in [
                            str(k)
                            for k in d.get("serviceInterruptionKeys", [])
                            + d.get("upcomingServiceInterruptionKeys", [])
                        ]
                        for d in r.get("directionList", [])
                    )
                ],
            }
        )
    return rows


def mock_predictions():
    return [
        {
            "scoring_ts": datetime.now(UTC).isoformat(),
            "route_short_name": r["route_short_name"],
            "direction_id": str(d),
            "horizon_min": h,
            "pred_p10": 0.1 + i % 5 * 0.06,
            "pred_p50": 0.25 + i % 5 * 0.08 + h / 600,
            "pred_p90": min(1, 0.5 + i % 5 * 0.08 + h / 600),
            "p_crowd": 0.08 + i % 5 * 0.12,
            "top_factors": [
                {
                    "family": "Class change",
                    "feature": "schedule_prior",
                    "value": 1,
                    "contribution": 0.12,
                },
                {
                    "family": "Bunching",
                    "feature": "mock_service",
                    "value": 0,
                    "contribution": -0.04,
                },
            ],
            "model_version": "mock-v0",
            "provenance": "SIMULATED",
        }
        for i, r in enumerate(static("routes.json"))
        for d in (0, 1)
        for h in (15, 30, 60)
    ]


def local_history():
    import numpy as np

    buckets = {}
    from .data import flatten_vehicles

    base = latest_base()
    for path in sorted((ROOT / "data/landing/tripspark_vehicles").rglob("*.gz"))[-48:]:
        try:
            with gzip.open(path, "rt") as stream:
                for line in stream:
                    record = json.loads(line)
                    if record.get("http_status") != 200:
                        continue
                    for row in flatten_vehicles(
                        record["payload"], record["ingest_ts"], base
                    ):
                        if row.get("load_factor") is None or not row.get("route"):
                            continue
                        stamp = datetime.fromisoformat(row["ts_gps"])
                        key = (
                            row["route"],
                            row.get("direction", "0"),
                            stamp.replace(
                                minute=stamp.minute // 15 * 15, second=0, microsecond=0
                            ).isoformat(),
                        )
                        buckets.setdefault(key, {})[
                            (row["vehicle_id"], row["ts_gps"])
                        ] = row["load_factor"]
        except (OSError, EOFError, ValueError):
            continue
    return [
        {
            "route_short_name": k[0],
            "direction_id": k[1],
            "window_start": k[2],
            "p90_load_factor": float(np.quantile(list(v.values()), 0.9)),
            "avg_load_factor": float(np.mean(list(v.values()))),
            "n_pings": len(v),
            "data_origin": "observed",
        }
        for k, v in sorted(buckets.items())
    ]


def timestamp(value):
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def select_vehicles(backend, now=None):
    """Prefer a fresh poll, then fresh Silver GPS, then the packaged snapshot."""
    now = now or datetime.now(UTC)
    errors = []
    try:
        hot = backend.hot_snapshot()
        # Republishing an old poll must never make it appear live.
        ages = [(now - timestamp(hot[k])).total_seconds() for k in ('snapshot_ts', 'poll_ingest_ts')]
        if all(-60 <= age <= 180 for age in ages) and isinstance(hot['vehicles'], list):
            return hot['vehicles'], 'hot-path', None, {k: hot[k] for k in ('snapshot_ts', 'poll_ingest_ts')}
        errors.append('Hot path stale')
    except Exception as exc:  # noqa: BLE001 - keep fallback available on SDK failures
        errors.append('Hot path: ' + str(exc)[:150])
    try:
        rows = backend.query('vehicles')
        fresh = [r for r in rows if r.get('ts_gps') and -60 <= (now - timestamp(r['ts_gps'])).total_seconds() <= 600]
        if fresh:
            return fresh, 'databricks', '; '.join(errors), {}
        errors.append('Silver vehicle_latest empty or stale')
    except Exception as exc:  # noqa: BLE001 - keep fallback available on SDK failures
        errors.append('Silver: ' + str(exc)[:150])
    frozen = Path(__file__).resolve().parents[1] / 'fallback/vehicles.json'
    return json.loads(frozen.read_text()) if frozen.exists() else [], 'local-snapshot', '; '.join(errors), {}


def refresh_vehicle_ages(result, now=None):
    now = now or datetime.now(UTC)
    gps = []
    for row in result['data']:
        if row.get('ts_gps'):
            stamp = timestamp(row['ts_gps'])
            gps.append(stamp)
            row['gps_age_s'] = max(0, (now - stamp).total_seconds())
    result['latest_gps_ts'] = max(gps).isoformat() if gps else None
    reference = result.get('poll_ingest_ts') or result['latest_gps_ts']
    result['age_s'] = max(0, (now - timestamp(reference)).total_seconds()) if reference else None
    return result


class Datasets:
    def __init__(self):
        self.cache = {}

    def get(self, name):
        mode = os.getenv("AGGIEPULSE_BACKEND", "local")
        key = (mode, name)
        if key in self.cache and time.monotonic() < self.cache[key][0]:
            cached = self.cache[key][1]
            return refresh_vehicle_ages(cached) if name == 'vehicles' else cached
        error = None
        rows = None
        metadata = {}
        vehicle_source = None
        if mode == "databricks" and name == "vehicles":
            rows, vehicle_source, error, metadata = select_vehicles(DatabricksBackend())
        elif mode == "databricks":
            try:
                rows = DatabricksBackend().query(name)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:300]
        source = (
            "databricks"
            if rows is not None
            else ("mock" if name == "predictions" else "local")
        )
        source = vehicle_source or source
        if rows is None:
            rows = {
                "vehicles": LocalBackend().latest,
                "events": local_events,
                "alerts": local_alerts,
                "predictions": mock_predictions,
                "history": local_history,
            }[name]()
            if not rows and name != "predictions":
                frozen = (
                    Path(__file__).resolve().parents[1] / "fallback" / (name + ".json")
                )
                if frozen.exists():
                    rows = json.loads(frozen.read_text())
                    source = "local-snapshot"
        for row in rows:
            row["source"] = source
            if name == "predictions" and "mock" in str(row.get("model_version", "")).lower():
                row["source"] = "mock"
                row["provenance"] = "SIMULATED"
            if name in ("vehicles", "predictions"):
                row["route"] = row.get("route_short_name", row.get("route"))
                row["direction"] = row.get("direction_id", row.get("direction"))
            if name == "vehicles":
                if row.get("ts_gps"):
                    row["gps_age_s"] = max(
                        0,
                        (
                            datetime.now(UTC)
                            - timestamp(row["ts_gps"])
                        ).total_seconds(),
                    )
                route = next(
                    (
                        r
                        for r in static("routes.json")
                        if r["route_short_name"] == row.get("route")
                    ),
                    {},
                )
                row.update(
                    route_id=route.get("route_id", row.get("route_id")),
                    route_color=row.get("route_color") or route.get("route_color"),
                    provenance="MEASURED · APC",
                )
            if name == "events":
                row["affected_routes"] = affected(row)
        result = {
            "data": rows,
            "source": source,
            "backend": mode,
            "fallback_reason": error,
            **metadata,
        }
        if name == 'vehicles':
            refresh_vehicle_ages(result)
        self.cache[key] = (time.monotonic() + 15, result)
        return result


def plan(origin, destination, predictions, hour=12):
    stops = {s["properties"]["stop_id"]: s for s in static("stops.geojson")["features"]}
    if origin not in stops or destination not in stops:
        return []
    near = lambda sid: {
        k: distance(v["geometry"]["coordinates"], stops[sid]["geometry"]["coordinates"])
        for k, v in stops.items()
        if distance(v["geometry"]["coordinates"], stops[sid]["geometry"]["coordinates"])
        <= 400
    }
    origins, destinations = near(origin), near(destination)
    results = []
    for pattern in static("planner.json"):
        for i, a in enumerate(pattern["stops"]):
            for j, b in enumerate(pattern["stops"]):
                if j <= i or a not in origins or b not in destinations:
                    continue
                for shift in (-10, 0, 10, 20):
                    horizon = min((15, 30, 60), key=lambda h: abs(h - max(15, 15 + shift)))
                    pred = next(
                        (
                            p
                            for p in predictions
                            if p.get("route", p.get("route_short_name"))
                            == pattern["route"]
                            and str(p.get("direction", p.get("direction_id")))
                            == pattern["direction"]
                            and p["horizon_min"] == horizon
                        ),
                        None,
                    )
                    crowd = pred["pred_p50"] if pred else 0.4
                    wait = (
                        pattern["headways"].get(
                            str((hour + shift // 60) % 24), pattern["headway"]
                        )
                        / 2
                    )
                    ride = (pattern["times"][j] - pattern["times"][i]) / 60
                    walk = (origins[a] + destinations[b]) / 80
                    score = wait + ride + walk + crowd * 20
                    results.append(
                        {
                            "route": pattern["route"],
                            "direction": pattern["direction"],
                            "shift": shift,
                            "wait_min": round(wait, 1),
                            "ride_min": round(ride, 1),
                            "walk_min": round(walk, 1),
                            "crowding": crowd,
                            "score": round(score, 1),
                            "reason": f"Route {pattern['route']}: leave {abs(shift)} min {'earlier' if shift < 0 else 'later'}, {crowd:.0%} predicted load",
                            "source": pred.get("source") if pred else "mock",
                        }
                    )
    unique = {}
    for r in sorted(results, key=lambda r: r["score"]):
        unique.setdefault((r["route"], r["direction"], r["shift"]), r)
    return list(unique.values())[:16]
