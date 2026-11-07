import csv
import importlib.util
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pipelines/core'))
from lib.helpers import (
    closed_stop_codes,
    gps_time_utc,
    gtfs_instant,
    gtfs_seconds,
    normalize_event,
    strip_html,
)


def test_dst_folds_match_collector():
    from aggiepulse.collector.parse import gps_time_utc as collector

    for ingest in ['2026-11-01T06:45:00+00:00', '2026-11-01T07:45:00+00:00']:
        assert gps_time_utc('2026-11-01T01:30:00', ingest) == collector('2026-11-01T01:30:00', ingest)
    assert gps_time_utc('2026-11-01T01:30:00', '2026-11-01T05:00:00+00:00') is None
    assert gps_time_utc('2026-03-08T02:30:00', '2026-03-08T09:00:00+00:00') is None
    assert gps_time_utc('broken', '2026-11-01T05:00:00+00:00') is None


def test_gtfs_noon_anchor_and_extended_hours():
    assert gtfs_seconds('25:30:00') == 91800
    assert gtfs_seconds('09:99:00') is None
    assert gtfs_instant('2026-11-01', 0) == datetime(2026, 11, 1, 6, tzinfo=UTC)
    assert gtfs_instant('2026-03-08', 0) == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert gtfs_instant('2026-11-01', 91800) == datetime(2026, 11, 2, 7, 30, tzinfo=UTC)


def test_html_and_closed_stops():
    html = '<div>Bus &amp; shuttle</div><p>Detour</p>'
    assert strip_html(html) == 'Bus & shuttle Detour'
    html = ('The following bus stops will be closed:<ul><li>MSC (ID: 0012)</li></ul>'
            'A temporary stop:<ul><li>Other (ID: 9999)</li></ul>')
    assert closed_stop_codes(html) == ['0012']


@pytest.fixture
def policy():
    # JSON is also the exact normalized representation consumed by the pipeline.
    import subprocess

    raw = subprocess.check_output(['uv', 'run', '--with', 'pyyaml', 'python', '-c',
        'import yaml,json; print(json.dumps(yaml.safe_load(open("seeds/event_filters.yaml"))))'], cwd=ROOT)
    return json.loads(raw)


@pytest.fixture
def venues():
    with (ROOT / 'seeds/venues.csv').open() as stream:
        return list(csv.DictReader(stream))


def event(**changes):
    return {'id': 1, 'title': 'Football vs Tennessee', 'date_utc': '2026-11-14 20:00:00',
            'location_title': 'Kyle Field', 'event_types': ['Sports &amp; Athletics'],
            'event_types_audience': ['Students'], **changes}


def test_event_local_geocode_scoring(policy, venues):
    row = json.loads(normalize_event(event(), venues, policy))
    assert row['reason'] == 'kept'
    assert row['venue_key'] == 'kyle_field'
    assert row['lat'] == 30.60999
    assert row['is_football_home']
    assert row['significance'] > 40


@pytest.mark.parametrize(('changes', 'reason'), [
    ({'title': 'Group Study'}, 'routine service or study availability'),
    ({'title': 'Evening Study Session'}, 'routine service or study availability'),
    ({'is_canceled': 1}, 'canceled'),
    ({'is_online': 1}, 'online'),
    ({'location_title': 'Zoom'}, 'remote attendance'),
    ({'event_types_campus': ['Dallas\u2002']}, 'nonlocal campus'),
    ({'location_title': '', 'event_types_campus': ['Bryan-College Station']}, 'missing local physical evidence'),
])
def test_event_rejections(changes, reason, policy, venues):
    assert json.loads(normalize_event(event(**changes), venues, policy))['reason'] == reason


def test_unknown_size_stays_unknown(policy, venues):
    row = json.loads(normalize_event(event(location_title='MSC', event_types=['Campus Life']), venues, policy))
    assert row['reason'] == 'kept'
    assert row['est_attendance'] is None
    assert row['significance'] is None


def test_zip_extract_is_content_versioned():
    spec = importlib.util.spec_from_file_location('prepare', ROOT / 'scripts/pipeline_core/prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for table in module.TABLES:
            archive.writestr(table + '.txt', 'id,name\n001,A\n')
    version, tables = module.extract_feed(data.getvalue())
    assert len(version) == 23
    assert b'001,A,' in tables['routes']
    assert version == module.extract_feed(data.getvalue())[0]
    with pytest.raises(ValueError, match='Missing GTFS'):
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, 'w'):
            pass
        module.extract_feed(empty.getvalue())


def test_only_matching_weights_participate(policy, venues):
    import math

    row = json.loads(normalize_event(event(event_types=['Academic Calendar', 'Unrecognized'],
        event_types_audience=['Faculty', 'Unrecognized']), venues, policy))
    assert row['significance'] == pytest.approx(math.log1p(102733) * 0.1 * 0.7)
