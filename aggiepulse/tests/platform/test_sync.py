import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aggiepulse.platform.sync import eligible, sync_once

NOW = datetime(2026, 11, 7, 20, 30, tzinfo=UTC)


def old_file(root, relative, content=b'complete'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (NOW.timestamp() - 300,) * 2)
    return path


@pytest.mark.parametrize(('hour', 'expected'), [('19', True), ('20', False), ('21', False)])
def test_skip_open_or_future_utc_hour(tmp_path, hour, expected):
    relative = Path(f'tripspark_vehicles/date=2026-11-07/vehicles_20261107T{hour}_laptop.jsonl.gz')
    path = old_file(tmp_path, relative)
    assert eligible(path, relative, NOW) is expected


@pytest.mark.parametrize('source', ['tripspark_vehicles', 'events', 'gtfs'])
def test_mtime_guard_for_every_source(tmp_path, source):
    relative = Path(f'{source}/date=2026-11-07/vehicles_20261107T19_host.jsonl.gz')
    path = old_file(tmp_path, relative)
    os.utime(path, (NOW.timestamp() - 119,) * 2)
    assert not eligible(path, relative, NOW)
    os.utime(path, (NOW.timestamp() - 120,) * 2)
    assert eligible(path, relative, NOW)


class Backend:
    def __init__(self):
        self.uploads = []

    def copy_verified(self, snapshot, destination, expected):
        self.uploads.append((snapshot.read_bytes(), destination, expected))


def test_manifest_idempotence_and_immutable_conflict(tmp_path):
    root = tmp_path / 'landing'
    path = old_file(root, 'events/date=2026-11-07/events.json.gz')
    manifest = tmp_path / 'manifest.json'
    backend = Backend()
    args = (root, '/Volumes/transit/raw/landing', manifest)
    assert sync_once(*args, now=NOW, backend=backend)['uploaded'] == 1
    assert sync_once(*args, now=NOW, backend=backend)['unchanged'] == 1
    assert len(backend.uploads) == 1
    assert backend.uploads[0][1].endswith('/events/date=2026-11-07/events.json.gz')
    path.write_bytes(b'changed')
    os.utime(path, (NOW.timestamp() - 300,) * 2)
    with pytest.raises(RuntimeError, match='changed after upload'):
        sync_once(*args, now=NOW, backend=backend)
    assert len(backend.uploads) == 1


def test_sync_defers_open_files(tmp_path):
    root = tmp_path / 'landing'
    old_file(root, 'tripspark_vehicles/date=2026-11-07/vehicles_20261107T20_host.jsonl.gz')
    backend = Backend()
    assert sync_once(root, '/Volumes/transit/raw/landing', tmp_path / 'manifest.json',
                     now=NOW, backend=backend)['deferred'] == 1
    assert not backend.uploads


def test_failed_upload_does_not_advance_manifest(tmp_path):
    class FailingBackend:
        def copy_verified(self, *args):
            raise RuntimeError('network failure')

    root = tmp_path / 'landing'
    old_file(root, 'events/date=2026-11-07/events.json.gz')
    manifest = tmp_path / 'manifest.json'
    with pytest.raises(RuntimeError, match='network failure'):
        sync_once(root, '/Volumes/transit/raw/landing', manifest,
                  now=NOW, backend=FailingBackend())
    assert not manifest.exists()


def test_existing_remote_object_is_verified_without_overwrite(tmp_path, monkeypatch):
    from aggiepulse.platform.sync import CliVolume, fingerprint

    snapshot = tmp_path / 'snapshot'
    snapshot.write_bytes(b'complete')
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[2] == 'cp' and command[3].startswith('dbfs:'):
            Path(command[4]).write_bytes(b'complete')

    monkeypatch.setattr('aggiepulse.platform.sync.subprocess.run', run)
    CliVolume().copy_verified(snapshot, '/Volumes/transit/raw/landing/file', fingerprint(snapshot))
    assert len(commands) == 3
    assert all('--overwrite' not in command for command in commands)


def test_remote_conflict_does_not_get_overwritten(tmp_path, monkeypatch):
    from aggiepulse.platform.sync import CliVolume, fingerprint

    snapshot = tmp_path / 'snapshot'
    snapshot.write_bytes(b'complete')

    def run(command, **kwargs):
        if command[2] == 'cp' and command[3].startswith('dbfs:'):
            Path(command[4]).write_bytes(b'different object')

    monkeypatch.setattr('aggiepulse.platform.sync.subprocess.run', run)
    with pytest.raises(RuntimeError, match='Immutable Volume conflict'):
        CliVolume().copy_verified(snapshot, '/Volumes/transit/raw/landing/file', fingerprint(snapshot))
