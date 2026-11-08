"""Interchangeable local landing and SQL warehouse vehicle access."""

import gzip
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


def flatten_vehicles(payload, ingest_ts=None, base_data=None):
    """Search nested vendor envelopes, carrying route/direction context downward."""
    # Prefer the collector's verified vendor parser for its native envelope.
    if isinstance(payload, list) and any(
        isinstance(r, dict) and "vehiclesByDirections" in r for r in payload
    ):
        try:
            from aggiepulse.collector.parse import flatten_vehicles as collector_flatten
        except ImportError:
            pass
        else:
            now = datetime.now(UTC)
            parsed = collector_flatten(payload, ingest_ts or now, base_data)
            return [
                {
                    **r,
                    "route": r.get("route_short_name"),
                    "route_id": r.get("route_key"),
                    "direction": r.get("direction_key"),
                    "ts_gps": r["ts_gps_utc"].isoformat()
                    if r.get("ts_gps_utc")
                    else None,
                    "gps_age_s": max(0, (now - r["ts_gps_utc"]).total_seconds())
                    if r.get("ts_gps_utc")
                    else None,
                    "provenance": "MEASURED · APC",
                }
                for r in parsed
                if r.get("lat") is not None and r.get("lon") is not None
            ]
    rows = []

    def walk(node, context):
        if isinstance(node, str):
            try:
                walk(json.loads(node), context)
            except (ValueError, TypeError):
                pass
        elif isinstance(node, list):
            for item in node:
                walk(item, context)
        elif isinstance(node, dict):
            context = dict(context)
            for key, target in [
                ("routeShortName", "route"),
                ("routeName", "route"),
                ("routeKey", "route_id"),
                ("directionKey", "direction"),
                ("directionName", "direction_name"),
            ]:
                if key in node:
                    context[target] = node[key]
            lat, lon = (
                node.get("lat", node.get("latitude")),
                node.get("lon", node.get("longitude")),
            )
            if (
                lat is not None
                and lon is not None
                and any(k in node for k in ("name", "vehicleId", "passengersOnboard"))
            ):

                def number(value):
                    try:
                        parsed = float(value) if value is not None else None
                        return (
                            parsed
                            if parsed is not None and math.isfinite(parsed)
                            else None
                        )
                    except (ValueError, TypeError):
                        return None

                onboard = number(node.get("passengersOnboard", node.get("onboard")))
                capacity = number(node.get("passengerCapacity", node.get("capacity")))
                gps = node.get("lastGpsDate", node.get("ts_gps"))
                age = None
                try:
                    stamp = datetime.fromisoformat(gps)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=ZoneInfo("America/Chicago"))
                    age = max(0, (datetime.now(UTC) - stamp).total_seconds())
                except (ValueError, TypeError, AttributeError):
                    pass
                if number(lat) is not None and number(lon) is not None:
                    rows.append(
                        {
                            **context,
                            "vehicle_id": str(
                                node.get("vehicleId", node.get("name", "unknown"))
                            ),
                            "lat": number(lat),
                            "lon": number(lon),
                            "onboard": onboard,
                            "capacity": capacity,
                            "load_factor": onboard / capacity
                            if onboard is not None and capacity and capacity > 0
                            else None,
                            "ts_gps": gps,
                            "gps_age_s": age,
                            "provenance": "MEASURED · APC",
                        }
                    )
            else:
                for value in node.values():
                    walk(value, context)

    walk(payload, {})
    return rows


class LocalBackend:
    def latest(self):
        files = sorted(
            (ROOT / "data/landing/tripspark_vehicles").glob("date=*/**/*.jsonl.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Use the newest successful snapshot, never resurrect disappeared buses.
        for path in files:
            latest = None
            try:
                with gzip.open(path, "rt") as stream:
                    for line in stream:
                        try:
                            record = json.loads(line)
                            if (
                                record.get("http_status") == 200
                                and record.get("payload") is not None
                                and not record.get("error")
                            ):
                                latest = record
                        except ValueError:
                            continue
            except (OSError, EOFError):
                pass  # An actively appended gzip may have an unfinished final member.
            if latest is not None:
                base = latest_base()
                return flatten_vehicles(
                    latest["payload"], latest.get("ingest_ts"), base
                )
        return []


def latest_base():
    for path in sorted(
        (ROOT / "data/landing/tripspark_base").rglob("*.gz"), reverse=True
    ):
        try:
            with gzip.open(path, "rt") as stream:
                records = [json.loads(line) for line in stream if line.strip()]
            for record in reversed(records):
                if record.get("http_status") == 200:
                    return record.get("payload", {})
        except (OSError, EOFError, ValueError):
            continue
    return {}


class DatabricksBackend:
    # Complete, constant statements: no SQL is ever assembled from runtime values.
    # To add a dataset, add a full statement here; never interpolate input into SQL.
    QUERIES: ClassVar[dict[str, str]] = {
        "vehicles": "SELECT * FROM transit.silver.vehicle_latest LIMIT 10000",
        "events": (
            "SELECT * FROM transit.silver.events"
            " WHERE start_ts BETWEEN current_timestamp()"
            " AND current_timestamp() + INTERVAL 7 DAYS"
            " AND NOT coalesce(is_canceled, false) AND coalesce(significance, 0) >= 5"
            " ORDER BY significance DESC LIMIT 10000"
        ),
        "alerts": "SELECT * FROM transit.silver.service_alerts LIMIT 10000",
        "predictions": "SELECT * FROM transit.gold.predictions_latest LIMIT 10000",
        "replay": (
            "SELECT vehicle_id, route_short_name, direction_key, ts_gps, lat, lon,"
            " onboard, capacity, load_factor FROM transit.silver.vehicle_pings"
            " WHERE data_origin = 'observed' AND lat IS NOT NULL AND lon IS NOT NULL"
            " AND ts_gps >= (SELECT max(ts_gps) FROM transit.silver.vehicle_pings"
            " WHERE data_origin = 'observed') - make_interval(0, 0, 0, 0, :hours)"
            " QUALIFY row_number() OVER (PARTITION BY vehicle_id, date_trunc('minute', ts_gps)"
            " ORDER BY ts_gps DESC, ingest_ts DESC) = 1"
            " ORDER BY ts_gps DESC, vehicle_id LIMIT 10000"
        ),
        "comparison": (
            "SELECT * FROM transit.gold.pred_vs_actual"
            " WHERE actual_data_origin = 'observed' AND actual_p90_load IS NOT NULL"
            " AND actual_n_pings > 0 AND target_window_start + INTERVAL 15 MINUTES"
            " <= current_timestamp()"
            " QUALIFY row_number() OVER (PARTITION BY route_short_name, direction_id,"
            " horizon_min, target_window_start ORDER BY scoring_ts DESC) = 1"
            " ORDER BY target_window_start DESC LIMIT 10000"
        ),
        "route_directions": (
            "SELECT route_short_name, route_id, direction_id, direction_key, shape_id"
            " FROM transit.silver.route_direction_map WHERE is_selected LIMIT 10000"
        ),
        # Scheduled-service context for the freshness chip, so "no buses scheduled"
        # isn't reported as stale data. data_expected skips the first 20 min of service,
        # because the pipeline needs about that long before fresh pings reach silver.
        "service": (
            "SELECT"
            " max(CASE WHEN sched_arrival_ts <= current_timestamp() THEN sched_arrival_ts END)"
            " AS last_scheduled_ts,"
            " min(CASE WHEN sched_arrival_ts > current_timestamp() THEN sched_arrival_ts END)"
            " AS next_scheduled_ts,"
            " count_if(sched_arrival_ts BETWEEN current_timestamp() - INTERVAL 10 MINUTES"
            " AND current_timestamp() + INTERVAL 10 MINUTES) > 0 AS in_service,"
            " count_if(sched_arrival_ts BETWEEN current_timestamp() - INTERVAL 60 MINUTES"
            " AND current_timestamp() - INTERVAL 20 MINUTES) > 0 AS data_expected"
            " FROM transit.silver.schedule_expanded"
            " WHERE sched_arrival_ts BETWEEN current_timestamp() - INTERVAL 2 DAYS"
            " AND current_timestamp() + INTERVAL 7 DAYS"
        ),
        "pipeline": (
            "SELECT max(ts_gps) AS latest_pipeline_gps_ts,"
            " max(ingest_ts) AS latest_pipeline_ingest_ts"
            " FROM transit.silver.vehicle_pings WHERE data_origin = 'observed'"
        ),
        "history": (
            "SELECT * FROM transit.gold.route_dir_15m"
            " WHERE data_origin = 'observed'"
            " ORDER BY window_start DESC LIMIT 10000"
        ),
    }

    def client(self):
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        config = (
            Config(http_timeout_seconds=10, retry_timeout_seconds=10)
            if os.getenv("DATABRICKS_CLIENT_ID")
            else Config(
                profile="transit", http_timeout_seconds=10, retry_timeout_seconds=10
            )
        )
        return WorkspaceClient(config=config)

    def hot_snapshot(self):
        response = self.client().files.download(
            "/Volumes/transit/raw/live/latest_vehicles.json"
        )
        with response.contents as stream:
            return json.load(stream)

    def query(self, dataset, parameters=None):
        statement = self.QUERIES.get(dataset)
        if statement is None:
            raise ValueError(f"Unknown dataset: {dataset!r}")
        client = self.client()
        result = client.statement_execution.execute_statement(
            warehouse_id=os.getenv("DATABRICKS_WAREHOUSE_ID", "3c5b1dbf21774cde"),
            statement=statement,
            parameters=parameters,
            wait_timeout="50s",
        )
        if not result.status or result.status.state.value != "SUCCEEDED":
            raise RuntimeError(
                str(result.status.error if result.status else "SQL query pending")
            )
        columns = result.manifest.schema.columns
        rows = []
        for values in result.result.data_array or []:
            row = {}
            for column, value in zip(columns, values):
                if value is not None:
                    if column.type_name.value in ("ARRAY", "STRUCT", "MAP"):
                        value = json.loads(value)
                    elif column.type_name.value in ("LONG", "INT", "SHORT", "BYTE"):
                        value = int(value)
                    elif column.type_name.value in ("DOUBLE", "FLOAT", "DECIMAL"):
                        value = float(value)
                    elif column.type_name.value == "BOOLEAN":
                        value = value.lower() == "true"
                row[column.name] = value
            rows.append(row)
        return rows

    def latest(self):
        return self.query("vehicles")


class VehicleCache:
    def __init__(self):
        self.value, self.expires, self.mode = [], 0, None

    def latest(self):
        mode = os.getenv("AGGIEPULSE_BACKEND", "local")
        if time.monotonic() >= self.expires or mode != self.mode:
            if mode not in ("local", "databricks"):
                raise ValueError("AGGIEPULSE_BACKEND must be local or databricks")
            self.value = (
                DatabricksBackend() if mode == "databricks" else LocalBackend()
            ).latest()
            self.expires, self.mode = time.monotonic() + 15, mode
        return self.value
