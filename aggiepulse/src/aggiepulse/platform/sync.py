"""Upload only stable landing files; Volume objects are never overwritten."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aggiepulse.common.paths import data_root

WINDOW = re.compile(r'^vehicles_(\d{8}T\d{2}(?:\d{2})?)_[^/]+\.jsonl\.gz$')


def eligible(path: Path, relative: Path, now: datetime, guard_seconds: float = 120,
             rotate_minutes: int = 5) -> bool:
    if not path.is_file() or path.is_symlink() or any(p.startswith('.') for p in relative.parts):
        return False
    if now.timestamp() - path.stat().st_mtime < guard_seconds:
        return False
    if relative.parts[0] == 'tripspark_vehicles':
        match = WINDOW.match(path.name)
        # Unknown collector names cannot be proven closed.
        if not match:
            return False
        try:
            hourly = len(match[1]) == 11
            start = datetime.strptime(match[1], '%Y%m%dT%H' if hourly else '%Y%m%dT%H%M').replace(tzinfo=UTC)
        except ValueError:
            return False
        if now < start + timedelta(minutes=60 if hourly else rotate_minutes):
            return False
    return True


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'size': path.stat().st_size, 'sha256': digest.hexdigest()}


class CliVolume:
    def __init__(self, profile: str = 'transit', executable: str = 'databricks'):
        self.profile, self.executable = profile, executable

    def copy_verified(self, snapshot: Path, destination: str, expected: dict) -> None:
        remote = 'dbfs:' + destination
        subprocess.run([self.executable, 'fs', 'mkdir',
                        'dbfs:' + destination.rsplit('/', 1)[0],
                        '-p', self.profile], check=True, capture_output=True, text=True)
        # No --overwrite, including after crashes or competing syncs.
        subprocess.run([self.executable, 'fs', 'cp', str(snapshot), remote,
                        '-p', self.profile], check=True, capture_output=True, text=True)
        with tempfile.TemporaryDirectory(prefix='aggiepulse-verify-') as temporary:
            downloaded = Path(temporary) / 'object'
            subprocess.run([self.executable, 'fs', 'cp', remote, str(downloaded),
                            '-p', self.profile], check=True, capture_output=True, text=True)
            if fingerprint(downloaded) != expected:
                raise RuntimeError(f'Immutable Volume conflict: {destination}')


def sync_once(root: Path, volume_root: str, manifest_path: Path, *, now=None,
              guard_seconds: float = 120, rotate_minutes: int = 5, backend=None) -> dict:
    now = now or datetime.now(UTC)
    backend = backend or CliVolume()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    counters = {'uploaded': 0, 'unchanged': 0, 'deferred': 0}
    # Also prevents two loop runners on this laptop from racing the manifest.
    with manifest_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if not eligible(path, relative, now, guard_seconds, rotate_minutes):
                counters['deferred'] += 1
                continue
            key = relative.as_posix()
            before = path.stat()
            with tempfile.TemporaryDirectory(prefix='aggiepulse-upload-') as temporary:
                snapshot = Path(temporary) / path.name
                shutil.copyfile(path, snapshot)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    counters['deferred'] += 1
                    continue
                entry = {'path': key, **fingerprint(snapshot)}
                if key in manifest:
                    if manifest[key] != entry:
                        raise RuntimeError(f'Local file changed after upload: {key}')
                    counters['unchanged'] += 1
                    continue
                backend.copy_verified(snapshot, volume_root.rstrip('/') + '/' + key,
                                      fingerprint(snapshot))
            manifest[key] = entry
            temporary_manifest = manifest_path.with_suffix('.tmp')
            temporary_manifest.write_text(json.dumps(manifest, indent=2) + '\n')
            os.replace(temporary_manifest, manifest_path)
            counters['uploaded'] += 1
            print(f'uploaded {key} ({entry["size"]} bytes)', flush=True)
    print(json.dumps(counters), flush=True)
    return counters


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--landing-root', type=Path, default=data_root() / 'landing')
    parser.add_argument('--volume-root', default='/Volumes/transit/raw/landing')
    parser.add_argument('--manifest', type=Path, default=data_root() / 'platform_sync_manifest.json')
    parser.add_argument('--guard-seconds', type=float, default=120)
    parser.add_argument('--profile', default='transit')
    parser.add_argument('--rotate-minutes', type=int, choices=[1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60], default=5)
    args = parser.parse_args(argv)
    if args.guard_seconds < 120:
        parser.error('--guard-seconds must be at least 120')
    sync_once(args.landing_root, args.volume_root, args.manifest,
              guard_seconds=args.guard_seconds, rotate_minutes=args.rotate_minutes, backend=CliVolume(args.profile))


if __name__ == '__main__':
    main()
