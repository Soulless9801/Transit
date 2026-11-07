"""Land recent Easterwood Airport observations as GeoJSON."""
import argparse

from aggiepulse.ingest._common import fetch, land

URL = 'https://api.weather.gov/stations/KCLL/observations'


def run():
    payload = fetch(URL, headers={'Accept': 'application/geo+json'}).json()
    target = land('weather_nws', payload, query={'query_url': URL})
    print(f'NWS: {len(payload.get("features", []))} observations')
    return [target]


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    run()


if __name__ == '__main__':
    main()
