"""Backfill inclusive LiveWhale calendar ranges with adaptive cap detection."""
import argparse
import logging
from datetime import date, timedelta

from aggiepulse.ingest._common import fetch, land

URL = 'https://calendar.tamu.edu/live/json/events/start_date/{}/end_date/{}/'


def run(start: date = date(2026, 8, 1), end: date | None = None):
    end = end or date.today() + timedelta(days=30)
    if start > end:
        raise ValueError('start must be <= end')
    files, events = [], []

    def chunk(first, last):
        url = URL.format(first, last)
        payload = fetch(url).json()
        if not isinstance(payload, list):
            raise TypeError(f'Expected LiveWhale array: {url}')
        if len(payload) >= 1000:
            logging.getLogger(__name__).warning('LiveWhale cap reached: %s through %s (%d)', first, last, len(payload))
            if first < last:
                middle = first + (last - first) // 2
                chunk(first, middle)
                chunk(middle + timedelta(days=1), last)
                return
            logging.getLogger(__name__).error('Single-day cap: coverage may be incomplete for %s', first)
        files.append(land('events', payload, rows=True,
                          query={'query_start': str(first), 'query_end': str(last),
                                 'query_url': url, 'cap_reached': len(payload) >= 1000}))
        events.extend(payload)

    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=6), end)
        chunk(cursor, last)
        cursor = last + timedelta(days=1)
    print(f'Events: {len(events)} records, {start} through {end}')
    for field in ['event_types', 'event_types_campus']:
        print(field, sorted({str(v) for event in events for v in (event.get(field) or [])}))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--range', nargs=2, type=date.fromisoformat, metavar=('START', 'END'))
    args = parser.parse_args()
    run(*args.range) if args.range else run()


if __name__ == '__main__':
    main()
