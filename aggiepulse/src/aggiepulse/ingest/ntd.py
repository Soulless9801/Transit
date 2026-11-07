"""NTD monthly ridership, Socrata dataset 8bui-9xvu (date, upt, mode, tos)."""
import argparse

from aggiepulse.ingest._common import fetch, land
from aggiepulse.ingest.parsers import parse_ntd

URL = 'https://data.transportation.gov/resource/8bui-9xvu.json'


def run(page_size: int = 1000):
    if page_size < 1:
        raise ValueError('page_size must be positive')
    files, records = [], []
    offset = 0
    while True:
        params = {'ntd_id': '60278', '$limit': page_size, '$offset': offset,
                  '$order': 'date,mode,tos,:id'}
        payload = fetch(URL, params=params).json()
        if not isinstance(payload, list):
            raise TypeError('Expected Socrata array')
        files.append(land('ntd', payload, query={'query_url': URL, 'query_params': params}))
        records.extend(payload)
        if len(payload) < page_size:
            break
        offset += page_size
    frame = parse_ntd({'payload': records})
    if not frame.empty:
        series = frame.groupby('month')['upt'].sum(min_count=1).sort_index().tail(24)
        print('NTD monthly UPT (sum across modes/service types):\n', series.to_string())
    print(f'NTD: {len(records)} rows')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page-size', type=int, default=1000)
    run(parser.parse_args().page_size)


if __name__ == '__main__':
    main()
