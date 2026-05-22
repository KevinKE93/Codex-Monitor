#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9222}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/port_utils.sh"

if ! codex_monitor_devtools_available "${PORT}"; then
  PORT="$(codex_monitor_resolve_port "${PORT}")"
  "${SCRIPT_DIR}/scripts/reopen_codex_with_debug.sh" "${PORT}" >/dev/null
  codex_monitor_wait_for_devtools "${PORT}" 30 || true
fi
python3 "${SCRIPT_DIR}/scripts/context_token_injector.py" --port "${PORT}" --once
