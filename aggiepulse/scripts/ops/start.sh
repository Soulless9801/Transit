#!/usr/bin/env bash
# Shortcut for: services.sh start [args]
exec "$(dirname "$0")/services.sh" start "$@"
