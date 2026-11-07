"""Interchangeable local landing and SQL warehouse vehicle access."""
import gzip
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


def flatten_vehicles(payload, ingest_ts=None, base_data=None):
    """Search nested vendor envelopes, carrying route/direction context downward."""
    # Prefer the collector's verified vendor parser for its native envelope.
    if isinstance(payload, list) and any(isinstance(r, dict) and 'vehiclesByDirections' in r for r in payload):
        try:
            from aggiepulse.collector.parse import flatten_vehicles as collector_flatten
        except ImportError:
            pass
        else:
            now = datetime.now(timezone.utc)
            parsed = collector_flatten(payload, ingest_ts or now, base_data)
            return [{**r, 'route': r.get('route_short_name'), 'route_id': r.get('route_key'),
                     'direction': r.get('direction_key'),
                     'ts_gps': r['ts_gps_utc'].isoformat() if r.get('ts_gps_utc') else None,
                     'gps_age_s': max(0, (now - r['ts_gps_utc']).total_seconds()) if r.get('ts_gps_utc') else None,
                     'provenance': 'MEASURED · APC'}
                    for r in parsed if r.get('lat') is not None and r.get('lon') is not None]
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
            for key, target in [('routeShortName', 'route'), ('routeName', 'route'), ('routeKey', 'route_id'), ('directionKey', 'direction'), ('directionName', 'direction_name')]:
                if key in node:
                    context[target] = node[key]
            lat, lon = node.get('lat', node.get('latitude')), node.get('lon', node.get('longitude'))
            if lat is not None and lon is not None and any(k in node for k in ('name', 'vehicleId', 'passengersOnboard')):
                def number(value):
                    try:
                        parsed = float(value) if value is not None else None
                        return parsed if parsed is not None and math.isfinite(parsed) else None
                    except (ValueError, TypeError):
                        return None
                onboard = number(node.get('passengersOnboard', node.get('onboard')))
                capacity = number(node.get('passengerCapacity', node.get('capacity')))
                gps = node.get('lastGpsDate', node.get('ts_gps'))
                age = None
                try:
                    stamp = datetime.fromisoformat(gps.replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=ZoneInfo('America/Chicago'))
                    age = max(0, (datetime.now(timezone.utc) - stamp).total_seconds())
                except (ValueError, TypeError, AttributeError):
                    pass
                if number(lat) is not None and number(lon) is not None:
                    rows.append({**context, 'vehicle_id': str(node.get('vehicleId', node.get('name', 'unknown'))), 'lat': number(lat), 'lon': number(lon), 'onboard': onboard, 'capacity': capacity, 'load_factor': onboard / capacity if onboard is not None and capacity and capacity > 0 else None, 'ts_gps': gps, 'gps_age_s': age, 'provenance': 'MEASURED · APC'})
            else:
                for value in node.values():
                    walk(value, context)
    walk(payload, {})
    return rows


class LocalBackend:
    def latest(self):
        files = sorted((ROOT / 'data/landing/tripspark_vehicles').glob('date=*/**/*.jsonl.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
        # Use the newest successful snapshot, never resurrect disappeared buses.
        for path in files:
            latest = None
            try:
                with gzip.open(path, 'rt') as stream:
                    for line in stream:
                        try:
                            record = json.loads(line)
                            if record.get('http_status') == 200 and record.get('payload') is not None and not record.get('error'):
                                latest = record
                        except ValueError:
                            continue
            except (OSError, EOFError):
                pass  # An actively appended gzip may have an unfinished final member.
            if latest is not None:
                base = None
                base_files = sorted((ROOT / 'data/landing/tripspark_base').rglob('*.jsonl.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
                for base_path in base_files[:1]:
                    try:
                        with gzip.open(base_path, 'rt') as stream:
                            for line in stream:
                                record = json.loads(line)
                                if record.get('payload'):
                                    base = record['payload']
                    except (OSError, EOFError, ValueError):
                        pass
                return flatten_vehicles(latest['payload'], latest.get('ingest_ts'), base)
        return []


class DatabricksBackend:
    # TODO: install databricks-sql-connector and use App service-principal OAuth.
    # sql.connect(server_hostname=os.environ['DATABRICKS_HOST'],
    #   http_path='/sql/1.0/warehouses/' + os.environ['DATABRICKS_WAREHOUSE_ID'],
    #   credentials_provider=...) -> cursor.execute(QUERY)
    QUERY = 'SELECT * FROM silver.vehicle_latest'

    def latest(self):
        raise NotImplementedError('TODO: wire SQL warehouse authentication and silver.vehicle_latest query')


class VehicleCache:
    def __init__(self):
        self.value, self.expires, self.mode = [], 0, None

    def latest(self):
        mode = os.getenv('AGGIEPULSE_BACKEND', 'local')
        if time.monotonic() >= self.expires or mode != self.mode:
            if mode not in ('local', 'databricks'):
                raise ValueError('AGGIEPULSE_BACKEND must be local or databricks')
            self.value = (DatabricksBackend() if mode == 'databricks' else LocalBackend()).latest()
            self.expires, self.mode = time.monotonic() + 15, mode
        return self.value
