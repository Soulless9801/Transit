"""Fetch archived hourly weather and a 16-day forecast for College Station."""
import argparse
from datetime import date

from aggiepulse.ingest._common import fetch, land

HOURLY = 'temperature_2m,apparent_temperature,precipitation,rain,wind_speed_10m,weather_code'


def run(start: date = date(2026, 8, 1), end: date | None = None):
    # The default best-match archive currently supplies data through today.
    end = end or date.today()
    common = {'latitude': 30.61, 'longitude': -96.34, 'hourly': HOURLY, 'timezone': 'UTC'}
    files = []
    for kind, url, extra in [
        ('archive', 'https://archive-api.open-meteo.com/v1/archive',
         {'start_date': str(start), 'end_date': str(end)}),
        ('forecast', 'https://api.open-meteo.com/v1/forecast', {'forecast_days': 16}),
    ]:
        params = {**common, **extra}
        payload = fetch(url, params=params).json()
        files.append(land('weather_openmeteo', payload, kind=kind,
                          query={'query_url': url, 'query_params': params}))
        times = payload.get('hourly', {}).get('time', [])
        print(f'Open-Meteo {kind}: {len(times)} hours {times[:1]} .. {times[-1:]}')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, default=date(2026, 8, 1))
    parser.add_argument('--end', type=date.fromisoformat)
    args = parser.parse_args()
    run(args.start, args.end)


if __name__ == '__main__':
    main()
