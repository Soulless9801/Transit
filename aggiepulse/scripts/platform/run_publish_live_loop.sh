#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="/opt/homebrew/bin:$PATH"
CHILD_PID=""
trap 'if [[ -n "$CHILD_PID" ]]; then kill -TERM "$CHILD_PID" 2>/dev/null || true; wait "$CHILD_PID" || true; fi; exit 0' INT TERM
while true; do
  uv run --with databricks-sdk python scripts/platform/publish_live.py "$@" &
  CHILD_PID=$!
  wait "$CHILD_PID" || echo 'Live publisher exited; restarting in 5s' >&2
  sleep 5 &
  CHILD_PID=$!
  wait "$CHILD_PID" || true
done
