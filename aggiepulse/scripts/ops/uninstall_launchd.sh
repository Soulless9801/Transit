#!/usr/bin/env bash
set -euo pipefail
OPS_AGENT_PATH="${HOME}/Library/LaunchAgents/com.aggiepulse.daily-ingest.plist"
if [[ "${1:-}" == --dry-run ]]; then
    echo "Would bootout gui/$(id -u)/com.aggiepulse.daily-ingest and remove $OPS_AGENT_PATH"
    exit 0
fi
if [[ $# -ne 0 ]]; then echo 'Usage: uninstall_launchd.sh [--dry-run]' >&2; exit 2; fi
if launchctl print "gui/$(id -u)/com.aggiepulse.daily-ingest" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/com.aggiepulse.daily-ingest"
fi
rm -f "$OPS_AGENT_PATH"
echo 'Removed daily ingest agent; collector/sync/publisher untouched.'
