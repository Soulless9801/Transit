"""Stage ingest on local disk, then publish complete immutable Volume files."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from aggiepulse.common.paths import data_root


def publish(staging: Path, destination: Path) -> list[Path]:
    """Use sequential writes only: no gzip seek, append, rename, or fsync on FUSE."""
    published = []
    for source in sorted((staging / 'landing').rglob('*')):
        if not source.is_file():
            continue
        target = destination / source.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        # GTFS's sidecar must reference the published ZIP rather than local staging.
        if source.name.endswith('.metadata.json'):
            metadata = json.loads(source.read_text())
            zip_path = metadata.get('payload', {}).get('zip_path')
            if zip_path:
                metadata['payload']['zip_path'] = str(destination / Path(zip_path).relative_to(staging))
            with target.open('xb') as output:
                output.write(json.dumps(metadata).encode())
        else:
            with source.open('rb') as input_stream, target.open('xb') as output:
                shutil.copyfileobj(input_stream, output)
        published.append(target)
        print(f'published {target} ({target.stat().st_size} bytes)', flush=True)
    return published


def hourly_weather() -> int:
    from aggiepulse.ingest import weather_nws
    from aggiepulse.ingest._common import fetch, land
    from aggiepulse.ingest.weather_openmeteo import HOURLY

    def forecast():
        url = 'https://api.open-meteo.com/v1/forecast'
        params = {'latitude': 30.61, 'longitude': -96.34, 'hourly': HOURLY,
                  'timezone': 'UTC', 'forecast_days': 16}
        land('weather_openmeteo', fetch(url, params=params).json(), kind='forecast',
             query={'query_url': url, 'query_params': params})

    failures = 0
    for operation in (forecast, weather_nws.run):
        try:
            operation()
        except (requests.RequestException, ValueError, OSError) as exc:
            failures += 1
            print(f'{operation.__name__}: {exc}', file=sys.stderr, flush=True)
    return int(failures > 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['all', 'hourly-weather'])
    parser.add_argument('--data-root', type=Path, default=data_root())
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='aggiepulse-ingest-') as temporary:
        staging = Path(temporary)
        environment = {**os.environ, 'AGGIEPULSE_DATA_ROOT': str(staging)}
        if args.command == 'all':
            result = subprocess.run([sys.executable, '-m', 'aggiepulse.ingest', 'all'],
                                    env=environment, check=False).returncode
        else:
            previous = os.environ.get('AGGIEPULSE_DATA_ROOT')
            os.environ['AGGIEPULSE_DATA_ROOT'] = str(staging)
            try:
                result = hourly_weather()
            finally:
                if previous is None:
                    os.environ.pop('AGGIEPULSE_DATA_ROOT', None)
                else:
                    os.environ['AGGIEPULSE_DATA_ROOT'] = previous
        files = publish(staging, args.data_root)
        print(json.dumps({'published_files': len(files), 'exit_code': result}))
        return result


if __name__ == '__main__':
    raise SystemExit(main())
