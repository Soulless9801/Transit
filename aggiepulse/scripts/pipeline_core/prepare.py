"""Idempotently stage newest GTFS and versioned seeds before every refresh.

Run as a serverless Python job. Local bootstrap: --local uses the transit CLI.
All outputs are immutable; the zip content hash identifies a feed snapshot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

TABLES = ('routes', 'stops', 'trips', 'stop_times', 'shapes', 'calendar_dates', 'feed_info')


def extract_feed(blob):
    version = 'v2_' + hashlib.sha256(blob).hexdigest()[:20]
    result = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for table in TABLES:
            name = next((n for n in archive.namelist() if n.split('/')[-1] == table + '.txt'), None)
            if not name:
                raise ValueError(f'Missing GTFS table: {table}')
            rows = csv.DictReader(io.StringIO(archive.read(name).decode('utf-8-sig')))
            output = io.StringIO()
            fields = ['published_feed_version' if f == 'feed_version' else f for f in rows.fieldnames]
            writer = csv.DictWriter(output, fieldnames=[*fields, 'feed_version'])
            writer.writeheader()
            for row in rows:
                if 'feed_version' in row:
                    row['published_feed_version'] = row.pop('feed_version')
                writer.writerow(dict(row, feed_version=version))
            result[table] = output.getvalue().encode()
    return version, result


def main():
    import yaml

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--root', default='/Volumes/transit/raw')
    parser.add_argument('--seeds', default='seeds')
    args = parser.parse_args()

    def cli(*parts):
        return subprocess.check_output(['/opt/homebrew/bin/databricks', *parts, '-p', 'transit'])

    if args.local:
        cli('api', 'post', '/api/2.0/sql/statements', '--json', json.dumps({
            'warehouse_id': '3c5b1dbf21774cde',
            'statement': 'CREATE VOLUME IF NOT EXISTS transit.raw.seeds', 'wait_timeout': '50s'}))
    else:
        from pyspark.sql import SparkSession

        SparkSession.builder.getOrCreate().sql('CREATE VOLUME IF NOT EXISTS transit.raw.seeds')

    def write_current(path, blob):
        # Human-readable seed aliases are outside the versioned Auto Loader inputs.
        if not args.local:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            return
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / path.name
            source.write_bytes(blob)
            cli('fs', 'mkdirs', 'dbfs:' + str(path.parent))
            cli('fs', 'cp', str(source), 'dbfs:' + str(path), '--overwrite')

    def files(root):
        if not args.local:
            return list(Path(root).rglob('*.zip'))
        result = []
        for row in json.loads(cli('fs', 'ls', 'dbfs:' + root, '--output', 'json')):
            path = root + '/' + row['name']
            if row['is_directory']:
                result.extend(files(path))
            elif path.endswith('.zip'):
                result.append(Path(path))
        return result

    def read(path):
        if not args.local:
            return path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'feed.zip'
            cli('fs', 'cp', 'dbfs:' + str(path), str(target))
            return target.read_bytes()

    def write_once(path, blob):
        if not args.local:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != blob:
                    raise ValueError(f'Immutable output conflict: {path}')
                return
            path.write_bytes(blob)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / path.name
                source.write_bytes(blob)
                cli('fs', 'mkdirs', 'dbfs:' + str(path.parent))
                # Existing identical versioned files require no overwrite.
                result = subprocess.run(['/opt/homebrew/bin/databricks', 'fs', 'cp',
                                         str(source), 'dbfs:' + str(path), '-p', 'transit'],
                                        check=False, capture_output=True, text=True)
                if result.returncode and 'already exists' not in result.stderr.lower():
                    raise RuntimeError(result.stderr)

    newest = max(files(args.root + '/landing/gtfs'), key=lambda p: str(p))
    version, tables = extract_feed(read(newest))
    for table, blob in tables.items():
        write_once(Path(args.root) / 'landing/gtfs_extracted' / version / table / f'{table}.csv', blob)
    seed_dir = Path(args.seeds)
    for source in sorted(seed_dir.glob('*.csv')):
        blob = source.read_bytes()
        revision = hashlib.sha256(blob).hexdigest()[:16]
        write_once(Path(args.root) / 'seeds' / source.stem / f'{revision}.csv', blob)
        write_current(Path(args.root) / 'seeds' / source.name, blob)
    blob = (seed_dir / 'event_filters.yaml').read_bytes()
    revision = hashlib.sha256(blob).hexdigest()[:16]
    write_current(Path(args.root) / 'seeds/event_filters.yaml', blob)
    normalized = json.dumps({'config': json.dumps(yaml.safe_load(blob))}) + '\n'
    write_once(Path(args.root) / 'seeds/event_filters' / f'{revision}.json', normalized.encode())
    print(json.dumps({'feed_version': version, 'tables': list(tables), 'seeds': str(seed_dir)}))


if __name__ == '__main__':
    main()
