"""Publish the latest successful local poll; never contact TripSpark."""
from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import re
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aggiepulse.collector.parse import flatten_vehicles
from aggiepulse.common.paths import data_root

LIVE_PATH = '/Volumes/transit/raw/live/latest_vehicles.json'
LOG = logging.getLogger('publish_live')


def successful_records(path):
    """Keep complete records even while the collector appends a gzip member."""
    try:
        with gzip.open(path, 'rt') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row.get('http_status') == 200 and row.get('payload') is not None and not row.get('error'):
                        yield row
                except (ValueError, TypeError):
                    continue
    except (OSError, EOFError, UnicodeError):
        return


def newest_poll(landing: Path):
    best = None
    # Filename windows order the search; examine all overlapping host/hour files.
    paths = sorted((landing / 'tripspark_vehicles').rglob('vehicles_*.jsonl.gz'),
                   key=lambda p: p.name, reverse=True)
    for path in paths:
        match = re.match(r'vehicles_(\d{8}T\d{2}(?:\d{2})?)_', path.name)
        if match and best:
            start = datetime.strptime(match[1], '%Y%m%dT%H' if len(match[1]) == 11 else '%Y%m%dT%H%M').replace(tzinfo=UTC)
            if start + timedelta(hours=1) <= datetime.fromisoformat(best['ingest_ts']):
                break
        for row in successful_records(path):
            try:
                stamp = datetime.fromisoformat(row['ingest_ts'])
                if stamp.tzinfo is None:
                    continue
                if best is None or stamp > datetime.fromisoformat(best['ingest_ts']):
                    best = row
            except (KeyError, ValueError, TypeError):
                continue
    if best is None:
        raise ValueError('No successful local vehicle poll')
    return best


def newest_base(landing: Path):
    best = None
    for path in sorted((landing / 'tripspark_base').rglob('*.gz'), reverse=True):
        for row in successful_records(path):
            if best is None or row['ingest_ts'] > best['ingest_ts']:
                best = row
        if best is not None:
            return best['payload']
    return {}


def snapshot(landing: Path, now=None):
    now = now or datetime.now(UTC)
    poll = newest_poll(landing)
    base = newest_base(landing)
    routes = {r['key']: r for r in base.get('routes', [])}
    directions = {(r['key'], d.get('direction', {}).get('key')): d for r in base.get('routes', []) for d in r.get('directionList', [])}
    rows = []
    for parsed in flatten_vehicles(poll['payload'], poll['ingest_ts'], base):
        row = dict(parsed)
        gps = row.pop('ts_gps_utc')
        metadata = routes.get(row['route_key'], {})
        row.update(ts_gps=gps.isoformat() if gps else None,
                   gps_age_s=max(0, (now - gps).total_seconds()) if gps else None,
                   route=row['route_short_name'], route_id=row['route_key'],
                   direction=row['direction_key'],
                   route_color=directions.get((row['route_key'], row['direction_key']), {}).get('lineColor') or metadata.get('color'),
                   data_origin='observed', provenance='MEASURED · APC')
        if row['lat'] is not None and row['lon'] is not None:
            rows.append(row)
    return {'snapshot_ts': now.isoformat(), 'poll_ingest_ts': poll['ingest_ts'],
            'poll_id': poll.get('poll_id'), 'vehicles': rows}


def publish_once(landing: Path, client, *, now=None):
    value = snapshot(landing, now)
    client.files.upload(LIVE_PATH, io.BytesIO(json.dumps(value, allow_nan=False, separators=(',', ':')).encode()), overwrite=True)
    return value


def ensure_volume(client):
    from databricks.sdk.errors import AlreadyExists, NotFound
    from databricks.sdk.service.catalog import VolumeType

    try:
        client.volumes.read('transit.raw.live')
    except NotFound:
        try:
            client.volumes.create('transit', 'raw', 'live', VolumeType.MANAGED)
        except AlreadyExists:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--landing-root', type=Path, default=data_root() / 'landing')
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--profile', default='transit')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    if not 0 < args.interval < float('inf'):
        parser.error('--interval must be finite and positive')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    client = None
    while not stop.is_set():
        started = time.monotonic()
        try:
            if client is None:
                from databricks.sdk import WorkspaceClient
                from databricks.sdk.core import Config
                candidate = WorkspaceClient(config=Config(profile=args.profile, http_timeout_seconds=15, retry_timeout_seconds=15))
                ensure_volume(candidate)
                client = candidate
            value = publish_once(args.landing_root, client)
            age = (datetime.now(UTC) - datetime.fromisoformat(value['poll_ingest_ts'])).total_seconds()
            LOG.info('published vehicles=%s poll_age=%.1fs', len(value['vehicles']), age)
        except Exception as exc:  # noqa: BLE001 - retry transient failures next cycle
            LOG.warning('publish_failed %s: %s', type(exc).__name__, str(exc).replace('\n', ' ')[:200])
            if args.once:
                return 1
        if args.once:
            return 0
        stop.wait(max(0, args.interval - (time.monotonic() - started)))
    return 0
