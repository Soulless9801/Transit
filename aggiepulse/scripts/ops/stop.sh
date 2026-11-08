#!/usr/bin/env bash
# Shortcut for: services.sh stop [args]
exec "$(dirname "$0")/services.sh" stop "$@"
