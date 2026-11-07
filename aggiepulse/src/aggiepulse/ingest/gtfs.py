"""Download TAMU's current static feed and read all GTFS text tables."""
import argparse
import json
from zipfile import ZipFile

import pandas as pd

from aggiepulse.common.paths import data_root
from aggiepulse.ingest._common import fetch, path, stamp

URL = 'https://transport.tamu.edu/webFS/gtfs/google_transit.zip'


def load_gtfs(zip_path) -> dict[str, pd.DataFrame]:
    with ZipFile(zip_path) as archive:
        return {name.rsplit('/', 1)[-1][:-4]: pd.read_csv(archive.open(name), dtype='string')
                for name in archive.namelist() if name.lower().endswith('.txt')}


def latest_gtfs_zip():
    files = sorted((data_root() / 'landing' / 'gtfs').glob('date=*/*.zip'))
    if not files:
        raise FileNotFoundError('No landed GTFS ZIP')
    return files[-1]


def run():
    content = fetch(URL).content
    when = stamp()
    target = path('gtfs', 'google_transit.zip', when)
    with target.open('xb') as output:
        output.write(content)
    # Binary feed stays intact; the sidecar supplies Bronze query provenance.
    with target.with_suffix('.metadata.json').open('x') as output:
        json.dump({'ingest_ts': when.isoformat(), 'query_url': URL,
                   'payload': {'zip_path': str(target)}}, output)
    tables = load_gtfs(target)
    print('feed_info:', tables.get('feed_info'))
    print('GTFS counts:', {name: len(frame) for name, frame in tables.items()})
    return [target]


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    run()


if __name__ == '__main__':
    main()
