#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9222}"
REOPEN_AFTER_QUIT="${CODEX_MONITOR_REOPEN_AFTER_QUIT:-1}"
if [[ "${2:-}" == "--no-reopen-after-quit" ]]; then
  REOPEN_AFTER_QUIT="0"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/port_utils.sh"

REQUESTED_PORT="${PORT}"
PORT="$(codex_monitor_resolve_port "${REQUESTED_PORT}")"

wait_for_codex_if_needed() {
  if [[ "${REOPEN_AFTER_QUIT}" != "0" ]] || codex_monitor_codex_running; then
    return 0
  fi
  echo "Codex is not running; waiting instead of reopening it."
  until codex_monitor_codex_running; do
    sleep 5
  done
}

wait_for_codex_if_needed
if ! codex_monitor_devtools_available "${PORT}"; then
  "${SCRIPT_DIR}/reopen_codex_with_debug.sh" "${PORT}"
  codex_monitor_wait_for_devtools "${PORT}" 30 || true
fi

while true; do
  python3 "${SCRIPT_DIR}/context_token_injector.py" --port "${PORT}" || true
  sleep 3
  if ! curl -fsS "http://127.0.0.1:${PORT}/json" >/dev/null 2>&1; then
    wait_for_codex_if_needed
    PORT="$(codex_monitor_resolve_port "${REQUESTED_PORT}")"
    "${SCRIPT_DIR}/reopen_codex_with_debug.sh" "${PORT}" || true
    codex_monitor_wait_for_devtools "${PORT}" 30 || true
    sleep 3
  fi
done
