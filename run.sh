#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH:-/usr/bin/google-chrome}"
exec ./venv/bin/python main.py "$@"
