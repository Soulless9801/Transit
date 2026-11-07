import gzip
import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest

from aggiepulse.ingest import _common, events_livewhale, gtfs, ntd, run_all
from aggiepulse.ingest.parsers import (
    parse_events,
    parse_ntd,
    parse_nws,
    parse_openmeteo,
)

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(source):
    return json.loads((FIXTURES / f'{source}.json').read_text())


def test_real_parsers():
    events = parse_events([fixture('events')])
    assert str(events.start_ts.dtype).startswith('datetime64[')
    assert str(events.start_ts.dt.tz) == 'UTC'
    assert str(events.id.dtype) == 'string'
    assert str(events.is_all_day.dtype) == 'boolean'
    assert isinstance(events.campus.iloc[0], list)
    weather = parse_openmeteo(fixture('weather_openmeteo'))
    assert len(weather) == 2
    assert str(weather.weather_code.dtype) == 'Int64'
    nws = parse_nws(fixture('weather_nws'))
    assert len(nws) == 2
    monthly = parse_ntd(fixture('ntd'))
    assert monthly.upt.iloc[0] == int(fixture('ntd')['payload'][0]['upt'])
    assert monthly.ntd_id.iloc[0] == '60278'


def test_missing_fields_and_units():
    frame = parse_nws({'payload': {'features': [{'properties': {
        'timestamp': 'bad', 'precipitationLastHour': {'value': 0.001, 'unitCode': 'wmoUnit:m'}}}]}})
    assert frame.precip_mm.iloc[0] == 1
    assert pd.isna(frame.ts.iloc[0])
    assert pd.isna(frame.temp_c.iloc[0])
    assert parse_events([]).empty
    assert parse_ntd({'payload': []}).empty
    assert parse_openmeteo({'payload': {}}).empty
    event = parse_events([{'payload': {'id': '001', 'title': 'A &amp; B', 'is_canceled': '0'}}])
    assert event.id.iloc[0] == '001'
    assert event.title.iloc[0] == 'A & B'
    assert not event.is_canceled.iloc[0]


def test_append_only(tmp_path, monkeypatch):
    monkeypatch.setenv('AGGIEPULSE_DATA_ROOT', str(tmp_path))
    first = _common.land('events', [{'id': 1}], rows=True, query={'query_start': '2026-01-01'})
    second = _common.land('events', [{'id': 2}], rows=True)
    assert first != second
    with gzip.open(first, 'rt') as f:
        row = json.loads(f.readline())
    assert row['payload']['id'] == 1
    assert row['ingest_ts'].endswith('+00:00')
    assert row['query_start'] == '2026-01-01'


def test_gtfs_strings_and_latest(tmp_path, monkeypatch):
    monkeypatch.setenv('AGGIEPULSE_DATA_ROOT', str(tmp_path))
    with pytest.raises(FileNotFoundError):
        gtfs.latest_gtfs_zip()
    target = _common.path('gtfs', 'feed.zip', _common.stamp())
    with ZipFile(target, 'w') as archive:
        archive.writestr('stops.txt', 'stop_id,stop_name\n001,Test\n')
    assert gtfs.latest_gtfs_zip() == target
    assert gtfs.load_gtfs(target)['stops'].stop_id.iloc[0] == '001'


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_event_cap_splits_without_overlap(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('AGGIEPULSE_DATA_ROOT', str(tmp_path))
    calls = []

    def fetch(url):
        calls.append(url)
        return Response([{'id': 1}] * (1000 if len(calls) == 1 else 1))

    monkeypatch.setattr(events_livewhale, 'fetch', fetch)
    files = events_livewhale.run(date(2026, 1, 1), date(2026, 1, 2))
    assert len(files) == 2
    assert 'cap reached' in caplog.text
    assert 'start_date/2026-01-02/end_date/2026-01-02' in calls[-1]


def test_ntd_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv('AGGIEPULSE_DATA_ROOT', str(tmp_path))
    offsets = []

    def fetch(url, params):
        offsets.append(params['$offset'])
        return Response(fixture('ntd')['payload'] if len(offsets) == 1 else [])

    monkeypatch.setattr(ntd, 'fetch', fetch)
    assert len(ntd.run(page_size=2)) == 2
    assert offsets == [0, 2]


def test_batch_continues(monkeypatch):
    def fail():
        raise RuntimeError('offline')

    for module in [run_all.gtfs, run_all.events_livewhale, run_all.weather_openmeteo,
                   run_all.weather_nws, run_all.ntd]:
        monkeypatch.setattr(module, 'run', list)
    monkeypatch.setattr(run_all.gtfs, 'run', fail)
    results = run_all.run_all()
    assert results['gtfs']['status'].startswith('FAILED')
    assert results['ntd']['status'] == 'OK'
