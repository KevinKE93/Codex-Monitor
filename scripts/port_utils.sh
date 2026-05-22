#!/usr/bin/env bash

codex_monitor_devtools_available() {
  local port="${1:?port required}"
  curl --max-time 0.4 -fsS "http://127.0.0.1:${port}/json" >/dev/null 2>&1
}

codex_monitor_port_listening() {
  local port="${1:?port required}"
  python3 - "${port}" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.2)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

codex_monitor_find_free_port() {
  local start="${1:-9222}"
  local port
  for ((port=start; port<start+300; port++)); do
    if ! codex_monitor_port_listening "${port}"; then
      echo "${port}"
      return 0
    fi
  done
  python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

codex_monitor_resolve_port() {
  local requested="${1:-9222}"
  if codex_monitor_port_listening "${requested}"; then
    if codex_monitor_devtools_available "${requested}"; then
      echo "${requested}"
      return 0
    fi
    codex_monitor_find_free_port "$((requested + 1))"
    return 0
  fi
  echo "${requested}"
}

codex_monitor_wait_for_devtools() {
  local port="${1:?port required}"
  local attempts="${2:-30}"
  local index
  for ((index=0; index<attempts; index++)); do
    if codex_monitor_devtools_available "${port}"; then
      return 0
    fi
    sleep 1
  done
  return 1
}
