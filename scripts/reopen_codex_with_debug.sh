#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9222}"
APP="/Applications/Codex.app"

if [[ ! -d "$APP" ]]; then
  echo "Codex.app not found at $APP" >&2
  exit 1
fi

osascript -e 'tell application "Codex" to quit' >/dev/null 2>&1 || true
sleep 2
open "$APP" --args "--remote-debugging-port=${PORT}" "--remote-allow-origins=http://127.0.0.1:${PORT}"
echo "Reopened Codex with local DevTools port ${PORT}."
echo "Then run:"
echo "  python3 ~/plugins/context-token-inspector/scripts/context_token_injector.py --port ${PORT}"
