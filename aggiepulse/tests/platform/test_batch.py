import gzip
import json

import pytest

from aggiepulse.platform.batch import publish


def test_publish_finished_gzip_and_rewrite_gtfs_sidecar(tmp_path):
    staging, volume = tmp_path / 'staging', tmp_path / 'volume'
    folder = staging / 'landing' / 'gtfs' / 'date=2026-11-07'
    folder.mkdir(parents=True)
    archive = folder / 'feed.zip'
    archive.write_bytes(b'zip')
    (folder / 'feed.metadata.json').write_text(json.dumps({'payload': {'zip_path': str(archive)}}))
    compressed = folder / 'raw.json.gz'
    compressed.write_bytes(gzip.compress(b'{"payload": []}\n'))
    paths = publish(staging, volume)
    assert len(paths) == 3
    target = volume / compressed.relative_to(staging)
    assert gzip.decompress(target.read_bytes()) == b'{"payload": []}\n'
    sidecar = volume / folder.relative_to(staging) / 'feed.metadata.json'
    assert json.loads(sidecar.read_text())['payload']['zip_path'] == str(volume / archive.relative_to(staging))
    with pytest.raises(FileExistsError):
        publish(staging, volume)


@pytest.mark.parametrize('status', [0, 1])
def test_databricks_launcher_avoids_system_exit(monkeypatch, status):
    import runpy
    from pathlib import Path

    monkeypatch.setattr('aggiepulse.platform.batch.main', lambda: status)
    launcher = Path(__file__).resolve().parents[2] / 'scripts/platform/run_batch.py'
    if status:
        with pytest.raises(RuntimeError, match='Ingest failed'):
            runpy.run_path(str(launcher), run_name='__main__')
    else:
        runpy.run_path(str(launcher), run_name='__main__')
