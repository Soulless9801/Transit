#!/usr/bin/env bash
# Shortcut for: services.sh status [args]
exec "$(dirname "$0")/services.sh" status "$@"
