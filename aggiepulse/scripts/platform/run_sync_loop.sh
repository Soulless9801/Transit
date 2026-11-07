#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="/opt/homebrew/bin:$PATH"
trap 'exit 0' INT TERM
while true; do
  uv run python scripts/platform/sync_to_volume.py "$@" || echo 'Sync failed; retrying next cycle' >&2
  sleep 300 &
  wait $!
done
