#!/usr/bin/env bash
# Start with: nohup ./scripts/collector/run_backup_collector.sh >/dev/null 2>&1 &
# AGGIEPULSE_DATA_ROOT is honored; arguments are forwarded to the collector.
set -uo pipefail
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PACKAGE_ROOT" || exit 1
UV_BIN="${UV_BIN:-uv}"
ROOT_DATA="$("$UV_BIN" run python -c 'from aggiepulse.common.paths import data_root; print(data_root())')" || exit 1
LOG_DIR="$ROOT_DATA/logs"
mkdir -p "$LOG_DIR" || exit 1
exec >>"$LOG_DIR/collector_backup_$(hostname -s).log" 2>&1
STOP=0
CHILD_PID=""
CAFFEINE_PID=""
stop_collector() {
    STOP=1
    if [[ -n "$CHILD_PID" ]]; then kill -TERM "$CHILD_PID" 2>/dev/null || true; fi
}
cleanup() {
    if [[ -n "$CAFFEINE_PID" ]]; then kill "$CAFFEINE_PID" 2>/dev/null || true; fi
}
trap stop_collector INT TERM
trap cleanup EXIT
if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i -w "$$" &
    CAFFEINE_PID=$!
fi
while [[ "$STOP" -eq 0 ]]; do
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) starting backup collector"
    "$UV_BIN" run python -m aggiepulse.collector.poll_tripspark "$@" &
    CHILD_PID=$!
    wait "$CHILD_PID"
    EXIT_CODE=$?
    if [[ "$STOP" -ne 0 ]]; then
        wait "$CHILD_PID" 2>/dev/null || true
        break
    fi
    CHILD_PID=""
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) collector exited status=$EXIT_CODE; restarting in 5s"
    sleep 5 &
    CHILD_PID=$!
    wait "$CHILD_PID" || true
    CHILD_PID=""
done
