import gzip
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from aggiepulse.platform.live import LIVE_PATH, newest_poll, publish_once, snapshot


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt') as stream:
        stream.write(json.dumps(value) + '\n')


def landing(tmp_path):
    from pathlib import Path
    fixtures = Path(__file__).resolve().parents[1] / 'collector/fixtures'
    payload = json.loads((fixtures / 'vehicles.json').read_text())
    base = json.loads((fixtures / 'routes.json').read_text())
    poll = {'ingest_ts': '2026-11-07T20:30:00+00:00', 'http_status': 200, 'payload': payload}
    path = tmp_path / 'tripspark_vehicles/date=2026-11-07/vehicles_20261107T2030_host.jsonl.gz'
    write(path, poll)
    write(tmp_path / 'tripspark_base/date=2026-11-07/base_20261107T2000.json.gz', {**poll, 'payload': base})
    return path, poll


def test_publish_native_parser_and_sdk_overwrite(tmp_path):
    _, poll = landing(tmp_path)
    client = MagicMock()
    value = publish_once(tmp_path, client, now=datetime(2026, 11, 7, 20, 30, 15, tzinfo=UTC))
    assert value['poll_ingest_ts'] == poll['ingest_ts']
    assert len(value['vehicles']) == 3
    assert all(r['route_short_name'] and r['ts_gps'] and r['data_origin'] == 'observed' for r in value['vehicles'])
    assert all(r['route_color'] for r in value['vehicles'])
    args, kwargs = client.files.upload.call_args
    assert args[0] == LIVE_PATH and kwargs['overwrite'] is True
    assert json.loads(args[1].getvalue()) == value


def test_failed_poll_and_partial_gzip_keep_success(tmp_path):
    path, poll = landing(tmp_path)
    with gzip.open(path, 'at') as stream:
        stream.write(json.dumps({**poll, 'http_status': 503, 'payload': None}) + '\n')
    with path.open('ab') as stream:
        stream.write(b'\x1f\x8b\x08\x00')
    assert newest_poll(tmp_path) == poll
    assert snapshot(tmp_path)['poll_ingest_ts'] == poll['ingest_ts']


def test_empty_success_does_not_resurrect_buses(tmp_path):
    path, poll = landing(tmp_path)
    with gzip.open(path, 'at') as stream:
        stream.write(json.dumps({**poll, 'ingest_ts': '2026-11-07T20:30:30+00:00', 'payload': []}) + '\n')
    assert snapshot(tmp_path)['vehicles'] == []


def test_loop_retries_transient_failures(monkeypatch, tmp_path):
    import sys
    from types import ModuleType

    from aggiepulse.platform import live

    sdk = ModuleType('databricks.sdk')
    sdk.WorkspaceClient = MagicMock()
    core = ModuleType('databricks.sdk.core')
    core.Config = MagicMock()
    monkeypatch.setitem(sys.modules, 'databricks', ModuleType('databricks'))
    monkeypatch.setitem(sys.modules, 'databricks.sdk', sdk)
    monkeypatch.setitem(sys.modules, 'databricks.sdk.core', core)
    stop = MagicMock()
    stop.is_set.side_effect = [False, False, True]
    monkeypatch.setattr(live.threading, 'Event', lambda: stop)
    monkeypatch.setattr(live.signal, 'signal', MagicMock())
    monkeypatch.setattr(live, 'ensure_volume', MagicMock())
    publish = MagicMock(side_effect=[OSError('transient upload error'), {'poll_ingest_ts': datetime.now(UTC).isoformat(), 'vehicles': []}])
    monkeypatch.setattr(live, 'publish_once', publish)
    assert live.main(['--landing-root', str(tmp_path)]) == 0
    assert publish.call_count == 2
    sdk.WorkspaceClient.assert_called_once_with(config=core.Config.return_value)
    core.Config.assert_called_once_with(profile='transit', http_timeout_seconds=15, retry_timeout_seconds=15)
    assert stop.wait.call_count == 2
