import gzip
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from aggiepulse.collector import poll_tripspark as poll
from aggiepulse.collector.tripspark import TripSparkError


def test_multiple_gzip_members_are_durable_and_readable(tmp_path, monkeypatch):
    path = tmp_path / "vehicles.jsonl.gz"
    sync = MagicMock()
    monkeypatch.setattr(poll.os, "fsync", sync)
    for i in range(3):
        poll.append_gzip_json(path, {"poll_id": i, "payload": [i]})
        with gzip.open(path, "rt") as stream:
            assert [json.loads(line)["poll_id"] for line in stream] == list(
                range(i + 1)
            )
    assert path.read_bytes().count(b"\x1f\x8b") == 3
    assert sync.call_count == 3


def test_heartbeat_atomic_replace(tmp_path):
    path = tmp_path / "collector_heartbeat.json"
    poll.write_heartbeat(path, {"successes": 1})
    poll.write_heartbeat(path, {"successes": 2})
    assert json.loads(path.read_text()) == {"successes": 2}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    ("now", "interval", "expected"), [(60, 30, 90), (61, 30, 90), (125, 30, 150)]
)
def test_epoch_alignment(now, interval, expected):
    assert poll.next_boundary(now, interval) == expected


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 7, 19, 59, 59, tzinfo=UTC).timestamp()

    def is_set(self):
        return False

    def wait(self, seconds):
        self.now += seconds
        return False


def test_poll_rotation_base_refresh_and_heartbeat(
    tmp_path,
    monkeypatch,
    base_payload,
    vehicles_payload,
    capsys,
):
    monkeypatch.setenv("AGGIEPULSE_DATA_ROOT", str(tmp_path))
    clock = Clock()
    monkeypatch.setattr(poll.time, "time", lambda: clock.now)
    monkeypatch.setattr(poll, "utc_now", lambda: datetime.fromtimestamp(clock.now, UTC))
    client = MagicMock()
    client.last_http_status = 200
    client.get_base_data.return_value = base_payload
    client.get_vehicles.return_value = vehicles_payload
    state = poll.run(
        interval=30, base_interval=3600, max_polls=2, stop=clock, client=client
    )
    assert state["successes"] == 2
    assert state["failures"] == 0
    assert state["last_vehicles"] == 3
    assert state["last_total_onboard"] == 19
    assert client.get_base_data.call_count == 2
    paths = sorted(tmp_path.glob("landing/tripspark_vehicles/**/*.gz"))
    assert len(paths) == 2
    records = []
    for path in paths:
        with gzip.open(path, "rt") as stream:
            records.extend(json.loads(line) for line in stream)
    assert len({record["poll_id"] for record in records}) == 2
    assert all(len(record["route_keys"]) == 23 for record in records)
    assert records[0]["payload"] == vehicles_payload
    assert records[0]["error"] is None
    assert len(list(tmp_path.glob("landing/tripspark_base/**/*.gz"))) == 2
    heartbeat = json.loads((tmp_path / "collector_heartbeat.json").read_text())
    assert heartbeat["last_success_ts"] == records[-1]["ingest_ts"]
    assert heartbeat["stopped_ts"]
    client.close.assert_called_once()
    assert capsys.readouterr().err.count("vehicles=3 onboard=19") == 2


def test_failed_vehicle_poll_is_recorded(tmp_path, monkeypatch, base_payload):
    monkeypatch.setenv("AGGIEPULSE_DATA_ROOT", str(tmp_path))
    client = MagicMock()
    client.last_http_status = 200
    client.get_base_data.return_value = base_payload
    client.get_vehicles.side_effect = TripSparkError("unavailable", 503)
    state = poll.run(max_polls=1, client=client)
    assert state["failures"] == 1
    assert state["last_success_ts"] is None
    with gzip.open(state["last_landing_file"], "rt") as stream:
        record = json.loads(stream.readline())
    assert record["payload"] is None
    assert record["error"] == "unavailable"
    assert record["http_status"] == 503


def test_missing_base_data_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("AGGIEPULSE_DATA_ROOT", str(tmp_path))
    client = MagicMock()
    client.get_base_data.side_effect = TripSparkError("metadata unavailable", 403)
    state = poll.run(max_polls=1, client=client)
    assert state["base_failures"] == 1
    assert state["failures"] == 1
    client.get_vehicles.assert_not_called()
    with gzip.open(
        next(tmp_path.glob("landing/tripspark_base/**/*.gz")), "rt"
    ) as stream:
        record = json.load(stream)
    assert record["http_status"] == 403
    assert record["payload"] is None


def test_stop_during_wait_flushes_and_closes(tmp_path, monkeypatch, base_payload):
    import threading

    monkeypatch.setenv("AGGIEPULSE_DATA_ROOT", str(tmp_path))
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set())
    client = MagicMock()
    client.last_http_status = 200
    client.get_base_data.return_value = base_payload
    client.get_vehicles.return_value = []
    state = poll.run(stop=stop, client=client)
    assert state["polls"] == 1
    assert state["successes"] == 1
    assert state["stopped_ts"]
    client.close.assert_called_once()
    with gzip.open(state["last_landing_file"], "rt") as stream:
        assert json.load(stream)["payload"] == []

@pytest.mark.parametrize('minute,stamp', [(0, '20261107T2000'), (4, '20261107T2000'), (5, '20261107T2005'), (59, '20261107T2055')])
def test_five_minute_filenames_and_core_reader(minute, stamp):
    import ast
    import fnmatch
    import re
    from pathlib import Path

    when = datetime(2026, 11, 7, 20, minute, 59, tzinfo=UTC)
    path = poll.vehicles_path(when, 'laptop', 5)
    assert path.name == f'vehicles_{stamp}_laptop.jsonl.gz'
    # Exercise the actual Auto Loader glob and source-host regex without Spark.
    source = (Path(__file__).resolve().parents[2] / 'pipelines/core/core.py').read_text()
    tree = ast.parse(source)
    glob = next(n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'option' and n.args[0].value == 'pathGlobFilter')
    pattern = next(n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'regexp_extract' and isinstance(n.args[0], ast.Constant) and n.args[0].value == '_source_file')
    assert fnmatch.fnmatch(path.name, glob)
    assert re.search(pattern, '/' + path.name)[1] == 'laptop'
    hourly = poll.vehicles_path(when, 'laptop')
    assert hourly.name == 'vehicles_20261107T20_laptop.jsonl.gz'
    assert re.search(pattern, '/' + hourly.name)[1] == 'laptop'


def test_poll_uses_configured_rotation(tmp_path, monkeypatch, base_payload):
    monkeypatch.setenv('AGGIEPULSE_DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(poll, 'utc_now', lambda: datetime(2026, 11, 7, 20, 7, tzinfo=UTC))
    client = MagicMock()
    client.last_http_status = 200
    client.get_base_data.return_value = base_payload
    client.get_vehicles.return_value = []
    state = poll.run(max_polls=1, client=client, rotate_minutes=5)
    assert 'vehicles_20261107T2005_' in state['last_landing_file']
