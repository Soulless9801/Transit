"""Daily batch entrypoint: failures are isolated and reported with a failing exit code."""
import argparse
import logging

from aggiepulse.ingest import (
    events_livewhale,
    gtfs,
    ntd,
    weather_nws,
    weather_openmeteo,
)


def run_all() -> dict[str, dict]:
    results = {}
    for source in [gtfs, events_livewhale, weather_openmeteo, weather_nws, ntd]:
        name = source.__name__.rsplit('.', 1)[-1]
        try:
            files = source.run()
            results[name] = {'status': 'OK', 'files': len(files),
                             'bytes': sum(p.stat().st_size for p in files)}
        except Exception as exc:
            logging.getLogger(__name__).exception('%s failed', name)
            results[name] = {'status': f'FAILED: {exc}', 'files': 0, 'bytes': 0}
    print(f'{"source":24} {"files":>6} {"bytes":>12} status')
    for name, result in results.items():
        print(f'{name:24} {result["files"]:6} {result["bytes"]:12} {result["status"]}')
    return results


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    results = run_all()
    raise SystemExit(any(row['status'] != 'OK' for row in results.values()))


if __name__ == '__main__':
    main()
