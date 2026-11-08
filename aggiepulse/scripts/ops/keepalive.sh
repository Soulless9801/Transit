#!/usr/bin/env bash
# Keeps the Render free-tier mirror awake by requesting /api/health every 10 minutes.
# /api/health never queries Databricks, so the SQL warehouse can still auto-stop.
#   keepalive.sh install https://aggiepulse.onrender.com
#   keepalive.sh uninstall | status
set -euo pipefail
LABEL=com.aggiepulse.keepalive
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$ROOT/data/logs/keepalive.log"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
install)
    URL="${2:?Usage: keepalive.sh install https://<service>.onrender.com}"
    URL="${URL%/}"
    [[ "$URL" == https://* ]] || { echo "URL must start with https://" >&2; exit 2; }
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    mkdir -p "$(dirname "$AGENT")" "$(dirname "$LOG")"
    /usr/bin/python3 - "$AGENT" "$URL" "$LOG" "$LABEL" <<'PY'
import plistlib, sys
agent, url, log, label = sys.argv[1:]
ping = ('printf "%s " "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; '
        '/usr/bin/curl -sS -o /dev/null -m 90 -w "%{http_code} %{time_total}s\\n" "$1/api/health"'
        ' || echo failed')
with open(agent, "wb") as f:
    plistlib.dump({
        "Label": label,
        "ProgramArguments": ["/bin/sh", "-c", ping, "keepalive", url],
        "StartInterval": 600,
        "RunAtLoad": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }, f)
PY
    plutil -lint "$AGENT" >/dev/null
    launchctl bootstrap "$DOMAIN" "$AGENT"
    echo "Pinging $URL/api/health every 10 min; log: $LOG"
    ;;
uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$AGENT"
    echo "Keepalive removed"
    ;;
status)
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && echo "loaded" || echo "not loaded"
    tail -n 5 "$LOG" 2>/dev/null || true
    ;;
*)
    echo "Usage: keepalive.sh install <https-url> | uninstall | status" >&2
    exit 2
    ;;
esac
