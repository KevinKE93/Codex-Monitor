#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9222}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"${SCRIPT_DIR}/reopen_codex_with_debug.sh" "${PORT}"

while true; do
  python3 "${SCRIPT_DIR}/context_token_injector.py" --port "${PORT}" --interval 5 || true
  sleep 3
  if ! curl -fsS "http://127.0.0.1:${PORT}/json" >/dev/null 2>&1; then
    "${SCRIPT_DIR}/reopen_codex_with_debug.sh" "${PORT}" || true
    sleep 3
  fi
done
