#!/usr/bin/env bash
# Run at about 06:00 America/Chicago on the laptop while serverless DNS is restricted.
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
status=0
uv run python -m aggiepulse.ingest all || status=$?
# The normal sync loop uploads these files after its two-minute stability guard.
exit "$status"
