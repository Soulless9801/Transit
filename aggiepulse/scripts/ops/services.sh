#!/usr/bin/env bash
# Start, stop and check the laptop-side AggiePulse processes.
#   services.sh start  [name...]   default: collector sync publisher
#   services.sh stop   [--force] [name...]
#   services.sh status
# Names: collector, sync, publisher, keepalive (Render ping; opt-in).
# Each loop already stops gracefully on SIGTERM, so stop finishes the current
# poll/upload before exiting. --force kills anything still running after 60 s.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
LOGS="$ROOT/data/logs"
RENDER_URL="${AGGIEPULSE_PUBLIC_URL:-https://aggiepulse-n5me.onrender.com}"
DEFAULT=(collector sync publisher)

script_for() {
    case "$1" in
    collector) echo "scripts/collector/run_backup_collector.sh" ;;
    sync) echo "scripts/platform/run_sync_loop.sh" ;;
    publisher) echo "scripts/platform/run_publish_live_loop.sh" ;;
    *) return 1 ;;
    esac
}
args_for() {
    case "$1" in
    collector) echo "--interval 30 --base-interval 3600 --rotate-minutes 5" ;;
    sync) echo "--rotate-minutes 5" ;;
    publisher) echo "--interval 30" ;;
    esac
}
# Same log files the loops used when started by hand. The collector also logs to
# collector_backup_<host>.log by itself.
log_for() {
    case "$1" in
    collector) echo "$LOGS/collector_loop.log" ;;
    sync) echo "$LOGS/platform_sync.log" ;;
    publisher) echo "$LOGS/publish_live.log" ;;
    esac
}
# PIDs of the bash wrapper loop for a service (not its children).
pids_for() {
    local script
    script="$(script_for "$1")" || return 0
    pgrep -f "^(/bin/)?bash .*$(basename "$script")" || true
}
keepalive_loaded() { launchctl print "gui/$(id -u)/com.aggiepulse.keepalive" >/dev/null 2>&1; }
check_names() {
    local n
    for n in "$@"; do
        case "$n" in collector | sync | publisher | keepalive) ;; *)
            echo "Unknown service '$n' (use collector, sync, publisher, keepalive)" >&2
            exit 2
            ;;
        esac
    done
}

start() {
    local names=("$@") n pids
    [[ ${#names[@]} -eq 0 ]] && names=("${DEFAULT[@]}")
    check_names "${names[@]}"
    mkdir -p "$LOGS"
    for n in "${names[@]}"; do
        if [[ "$n" == keepalive ]]; then
            scripts/ops/keepalive.sh install "$RENDER_URL"
            continue
        fi
        pids="$(pids_for "$n")"
        if [[ -n "$pids" ]]; then
            echo "$n: already running (pid $(echo $pids))"
            continue
        fi
        # shellcheck disable=SC2046
        nohup bash "$(script_for "$n")" $(args_for "$n") >>"$(log_for "$n")" 2>&1 </dev/null &
        disown
        sleep 1
        pids="$(pids_for "$n")"
        if [[ -n "$pids" ]]; then
            echo "$n: started (pid $(echo $pids))"
        else
            echo "$n: failed to start; see $(log_for "$n")" >&2
        fi
    done
}

stop() {
    local force=0 names=() n pids waiting=() deadline
    for n in "$@"; do
        if [[ "$n" == --force ]]; then force=1; else names+=("$n"); fi
    done
    [[ ${#names[@]} -eq 0 ]] && names=("${DEFAULT[@]}" keepalive)
    check_names "${names[@]}"
    for n in "${names[@]}"; do
        if [[ "$n" == keepalive ]]; then
            if keepalive_loaded; then scripts/ops/keepalive.sh uninstall; fi
            continue
        fi
        pids="$(pids_for "$n")"
        if [[ -z "$pids" ]]; then
            echo "$n: not running"
            continue
        fi
        kill -TERM $pids 2>/dev/null
        echo "$n: stopping (pid $(echo $pids))"
        waiting+=("$n")
    done
    [[ ${#waiting[@]} -eq 0 ]] && return 0
    deadline=$((SECONDS + 60))
    while ((SECONDS < deadline)); do
        local left=()
        for n in "${waiting[@]}"; do [[ -n "$(pids_for "$n")" ]] && left+=("$n"); done
        [[ ${#left[@]} -eq 0 ]] && { echo "All stopped."; return 0; }
        sleep 2
    done
    for n in "${waiting[@]}"; do
        pids="$(pids_for "$n")"
        [[ -z "$pids" ]] && continue
        if [[ "$force" -eq 1 ]]; then
            for p in $pids; do pkill -KILL -P "$p" 2>/dev/null; kill -KILL "$p" 2>/dev/null; done
            echo "$n: force-killed"
        else
            echo "$n: still finishing after 60 s (pid $(echo $pids)); rerun with --force to kill it" >&2
        fi
    done
}

age() {
    # Seconds since an ISO timestamp, via Python for portable parsing.
    /usr/bin/python3 -c 'import sys,datetime as d;print(int((d.datetime.now(d.timezone.utc)-d.datetime.fromisoformat(sys.argv[1].replace("Z","+00:00"))).total_seconds()))' "$1" 2>/dev/null
}

status() {
    local n pids hb last
    for n in "${DEFAULT[@]}"; do
        pids="$(pids_for "$n")"
        if [[ -n "$pids" ]]; then printf "%-10s running (pid %s)\n" "$n" "$(echo $pids)"; else printf "%-10s stopped\n" "$n"; fi
    done
    if keepalive_loaded; then printf "%-10s running (pings %s every 10 min)\n" keepalive "$RENDER_URL"; else printf "%-10s stopped\n" keepalive; fi
    if launchctl print "gui/$(id -u)/com.aggiepulse.daily-ingest" >/dev/null 2>&1; then
        printf "%-10s scheduled daily at 06:00\n" daily
    else
        printf "%-10s not scheduled (scripts/ops/install_launchd.sh)\n" daily
    fi
    hb="$ROOT/data/collector_heartbeat.json"
    if [[ -f "$hb" ]]; then
        last="$(/usr/bin/python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("last_success_ts") or "")' "$hb")"
        [[ -n "$last" ]] && echo "Last successful bus poll: $(age "$last") s ago"
    fi
}

cmd="${1:-}"
shift || true
case "$cmd" in
start) start "$@" ;;
stop) stop "$@" ;;
restart)
    stop "$@"
    names=()
    for n in "$@"; do [[ "$n" == --force ]] || names+=("$n"); done
    start "${names[@]+"${names[@]}"}"
    ;;
status) status ;;
*)
    echo "Usage: services.sh start|stop [--force]|restart|status [collector sync publisher keepalive]" >&2
    exit 2
    ;;
esac
