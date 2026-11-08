#!/usr/bin/env bash
# Installs only the daily batch; never touches the collector/sync/publisher loops.
set -euo pipefail
OPS_PACKAGE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OPS_AGENT_PATH="${HOME}/Library/LaunchAgents/com.aggiepulse.daily-ingest.plist"
OPS_DRY_RUN=0
if [[ "${1:-}" == --dry-run ]]; then OPS_DRY_RUN=1; shift; fi
if [[ $# -ne 0 ]]; then echo 'Usage: install_launchd.sh [--dry-run]' >&2; exit 2; fi
# plistlib escapes XML paths, including spaces and ampersands.
OPS_PLIST="$(/usr/bin/python3 - "$OPS_PACKAGE_ROOT" <<'PY'
import plistlib
import sys
root = sys.argv[1]
print(plistlib.dumps({
    'Label': 'com.aggiepulse.daily-ingest',
    'ProgramArguments': ['/bin/bash', root + '/scripts/platform/run_daily_ingest.sh'],
    'WorkingDirectory': root,
    'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
    'StartCalendarInterval': {'Hour': 6, 'Minute': 0},
    'StandardOutPath': root + '/data/logs/daily_ingest.log',
    'StandardErrorPath': root + '/data/logs/daily_ingest.log',
    'RunAtLoad': False,
}).decode())
PY
)"
if [[ "$OPS_DRY_RUN" -eq 1 ]]; then
    printf 'Would install %s and bootstrap gui/%s (06:00 system local time; no immediate run)\n%s\n' "$OPS_AGENT_PATH" "$(id -u)" "$OPS_PLIST"
    exit 0
fi
if launchctl print "gui/$(id -u)/com.aggiepulse.daily-ingest" >/dev/null 2>&1; then
    echo 'Daily ingest agent already loaded; uninstall it before replacing.' >&2
    exit 1
fi
mkdir -p "$(dirname "$OPS_AGENT_PATH")" "$OPS_PACKAGE_ROOT/data/logs"
printf '%s\n' "$OPS_PLIST" > "$OPS_AGENT_PATH"
plutil -lint "$OPS_AGENT_PATH"
launchctl bootstrap "gui/$(id -u)" "$OPS_AGENT_PATH"
echo "Installed $OPS_AGENT_PATH (06:00 system local time; no immediate run)"
