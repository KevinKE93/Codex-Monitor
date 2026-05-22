#!/usr/bin/env python3
"""Inject Codex context/token stats into the existing Codex Desktop window.

This helper talks to a Codex Desktop renderer through Chrome DevTools Protocol.
It does not modify Codex.app, app.asar, app state, or local session JSONL files.
Codex must be launched with a local --remote-debugging-port first.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import context_token_inspector as inspector


DEFAULT_PORT = 9222


class CDPError(RuntimeError):
    pass


class CDPClient:
    def __init__(self, websocket_url: str, timeout: float = 5.0) -> None:
        self.websocket_url = websocket_url
        self.timeout = timeout
        self.sock = self._connect(websocket_url)
        self.next_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        self._send_json({"id": message_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._recv_json()
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise CDPError(str(message["error"]))
            return message.get("result") or {}
        raise TimeoutError(f"Timed out waiting for CDP response to {method}")

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": False,
            },
        )
        remote = result.get("result") or {}
        if "exceptionDetails" in result:
            raise CDPError(str(result["exceptionDetails"]))
        return remote.get("value")

    def _connect(self, websocket_url: str) -> socket.socket:
        parsed = urllib.parse.urlparse(websocket_url)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise ValueError(f"Unsupported websocket URL: {websocket_url}")
        port = parsed.port or 80
        sock = socket.create_connection((parsed.hostname, port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise CDPError(f"WebSocket handshake failed: {response[:200]!r}")
        return sock

    def _send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.sock.sendall(masked_websocket_frame(data))

    def _recv_json(self) -> dict[str, Any]:
        while True:
            opcode, payload = read_websocket_frame(self.sock)
            if opcode == 1:
                data = json.loads(payload.decode("utf-8"))
                if isinstance(data, dict):
                    return data
            if opcode == 8:
                raise CDPError("WebSocket closed by target")


def masked_websocket_frame(payload: bytes) -> bytes:
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = secrets.token_bytes(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + mask + masked


def read_websocket_frame(sock: socket.socket) -> tuple[int, bytes]:
    first = read_exact(sock, 2)
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b""
    payload = read_exact(sock, length)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def read_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise CDPError("Unexpected WebSocket EOF")
        chunks.extend(chunk)
    return bytes(chunks)


def devtools_targets(port: int) -> list[dict[str, Any]]:
    url = f"http://127.0.0.1:{port}/json"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise CDPError(
            f"Cannot connect to Codex DevTools on 127.0.0.1:{port}. "
            "Launch Codex with --remote-debugging-port first."
        ) from exc
    return data if isinstance(data, list) else []


def select_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [target for target in targets if target.get("type") == "page"]
    codex_pages = [
        target
        for target in pages
        if "Codex" in str(target.get("title") or "")
        or str(target.get("url") or "").startswith("app://")
    ]
    candidates = codex_pages or pages
    for target in candidates:
        if target.get("webSocketDebuggerUrl"):
            return target
    raise CDPError("No debuggable Codex renderer target found")


def runtime_state(client: CDPClient) -> dict[str, Any]:
    expression = r"""
(() => {
  function attr(el, name) { return el && el.getAttribute ? el.getAttribute(name) : null; }
  const activeRow =
    document.querySelector('[data-app-action-sidebar-thread-row][data-app-action-sidebar-thread-active]') ||
    document.querySelector('[data-app-action-sidebar-thread-active]');
  const activeId =
    attr(activeRow, 'data-app-action-sidebar-thread-id') ||
    attr(activeRow && activeRow.querySelector('[data-app-action-sidebar-thread-id]'), 'data-app-action-sidebar-thread-id') ||
    attr(document.querySelector('[data-conversation-id]'), 'data-conversation-id') ||
    attr(document.querySelector('[data-above-composer-conversation-id]'), 'data-above-composer-conversation-id') ||
    null;
  return { href: location.href, title: document.title, activeThreadId: activeId };
})()
"""
    value = client.evaluate(expression)
    return value if isinstance(value, dict) else {}


def build_payload(paths: list[str], limit: int, selected_thread_id: str | None) -> dict[str, Any]:
    files = inspector.session_files(paths, limit=limit)
    summaries = [inspector.summarize_session_fast(path) for path in files]
    summaries = [summary for summary in summaries if summary.get("session_total_tokens")]
    by_thread: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        thread_id = summary.get("thread_id")
        if not thread_id:
            continue
        normalized = normalize_thread_id(str(thread_id))
        by_thread[normalized] = summary
        by_thread[f"local:{normalized}"] = summary
    selected = summaries[0] if summaries else None

    compact_summaries = []
    for summary in summaries:
        compact_summaries.append(
            {
                "thread_id": summary.get("thread_id"),
                "thread_keys": thread_keys(summary.get("thread_id")),
                "cwd": summary.get("cwd"),
                "updated_at": summary.get("updated_at"),
                "latest_context_tokens": summary.get("latest_context_tokens"),
                "context_window": summary.get("context_window"),
                "latest_context_percent": summary.get("latest_context_percent"),
                "latest_turn_total_tokens": summary.get("latest_turn_total_tokens"),
                "latest_turn_input_tokens": summary.get("latest_turn_input_tokens"),
                "latest_turn_cached_input_tokens": summary.get("latest_turn_cached_input_tokens"),
                "latest_turn_output_tokens": summary.get("latest_turn_output_tokens"),
                "latest_turn_reasoning_tokens": summary.get("latest_turn_reasoning_tokens"),
                "session_total_tokens": summary.get("session_total_tokens"),
                "session_input_tokens": summary.get("session_input_tokens"),
                "session_cached_input_tokens": summary.get("session_cached_input_tokens"),
                "session_output_tokens": summary.get("session_output_tokens"),
                "session_reasoning_tokens": summary.get("session_reasoning_tokens"),
                "hover": inspector.format_hover(summary),
                "footer": inspector.format_reply_footer(summary),
                "badge": compact_badge(summary),
            }
        )

    details_by_thread: dict[str, dict[str, Any]] = {}
    detail: dict[str, Any] | None = None
    for summary in summaries:
        if not summary.get("path"):
            continue
        parsed = inspector.parse_session_detail(str(summary["path"]))
        assistant_token_messages = [
            message
            for message in parsed.get("messages", [])
            if message.get("role") == "assistant" and message.get("token_usage")
        ]
        total_rounds = len(assistant_token_messages)
        assistant_items = [
            {
                "footer": message.get("token_footer"),
                "chip": inspector.format_reply_chip(
                    message["token_usage"],
                    user_turn_index=message.get("turn_index"),
                    user_total_turns=message.get("total_turns"),
                    assistant_turn_index=index,
                    assistant_total_turns=total_rounds,
                ),
                "tokenUsage": message["token_usage"],
                "textPrefix": text_prefix(message.get("text")),
                "roundIndex": message.get("turn_index") or index,
                "totalRounds": message.get("total_turns") or total_rounds,
                "userTurnIndex": message.get("turn_index"),
                "userTotalTurns": message.get("total_turns"),
                "assistantTurnIndex": index,
                "assistantTotalTurns": total_rounds,
            }
            for index, message in enumerate(assistant_token_messages, start=1)
        ]
        assistant_chips = [
            item["chip"]
            for item in assistant_items
        ]
        assistant_footers = [
            item["footer"]
            for item in assistant_items
            if item.get("footer")
        ]
        item_detail = {
            "thread_id": summary.get("thread_id"),
            "updated_at": summary.get("updated_at"),
            "footer": inspector.format_reply_footer(summary),
            "assistantFooters": assistant_footers,
            "assistantChips": assistant_chips,
            "assistantItems": assistant_items,
        }
        for key in thread_keys(summary.get("thread_id")):
            details_by_thread[key] = item_detail
        if selected and selected.get("thread_id") == summary.get("thread_id"):
            detail = item_detail

    return {
        "activeThreadId": selected_thread_id,
        "selectedThreadId": (selected or {}).get("thread_id") or selected_thread_id,
        "summaries": compact_summaries,
        "detail": detail,
        "detailsByThread": details_by_thread,
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def compact_badge(summary: dict[str, Any]) -> str:
    percent = summary.get("latest_context_percent")
    if isinstance(percent, float):
        return f"{percent:.1f}% ctx"
    total = summary.get("session_total_tokens")
    if isinstance(total, int):
        return f"{total // 1000}k tok"
    return "tokens"


def text_prefix(value: Any, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def normalize_thread_id(thread_id: str) -> str:
    if thread_id.startswith("local:"):
        return thread_id.removeprefix("local:")
    return thread_id


def thread_keys(thread_id: Any) -> list[str]:
    if not thread_id:
        return []
    normalized = normalize_thread_id(str(thread_id))
    return [normalized, f"local:{normalized}"]


INJECTION_SCRIPT = r"""
(payload => {
  const ROOT_ID = 'codex-context-token-inspector-root';
  const STYLE_ID = 'codex-context-token-inspector-style';
  const FOOTER_ATTR = 'data-context-token-footer';
  const CHIP_ATTR = 'data-context-token-chip';
  const BADGE_ATTR = 'data-context-token-badge';
  const SIDEBAR_HOVER_ATTR = 'data-context-token-sidebar-hover';
  const COLLAPSE_KEY = 'codex-context-token-inspector-collapsed';
  const POSITION_KEY = 'codex-context-token-inspector-position';
  const UNIT_KEY = 'codex-context-token-inspector-unit';
  const UNIT_DEFAULTED_KEY = 'codex-context-token-inspector-unit-defaulted';

  function n(value) {
    return value == null ? '-' : new Intl.NumberFormat().format(value);
  }
  function ensureDefaultUnit() {
    if (!localStorage.getItem(UNIT_DEFAULTED_KEY)) {
      localStorage.setItem(UNIT_KEY, 'k');
      localStorage.setItem(UNIT_DEFAULTED_KEY, 'true');
    }
  }
  function unitMode() {
    const value = localStorage.getItem(UNIT_KEY);
    return ['raw', 'k', 'm'].includes(value) ? value : 'k';
  }
  function token(value) {
    if (value == null || Number.isNaN(Number(value))) return '-';
    const number = Number(value);
    const mode = unitMode();
    if (mode === 'k') {
      const scaled = number / 1000;
      const digits = Math.abs(scaled) >= 1000 ? 0 : Math.abs(scaled) >= 100 ? 1 : 2;
      return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(scaled)}K`;
    }
    if (mode === 'm') return `${(number / 1000000).toFixed(Math.abs(number) >= 10000000 ? 1 : 2)}M`;
    return n(number);
  }
  function pct(value) {
    return typeof value === 'number' ? `${value.toFixed(1)}%` : '-';
  }
  function pressure(value) {
    if (typeof value !== 'number') return 'UNKNOWN';
    if (value >= 85) return 'HIGH';
    if (value >= 70) return 'WATCH';
    return 'OK';
  }
  function remainingContext(item) {
    if (typeof item?.latest_context_tokens !== 'number' || typeof item?.context_window !== 'number') return null;
    return Math.max(item.context_window - item.latest_context_tokens, 0);
  }
  function summaryHover(item) {
    return [
      `Session total  ${token(item.session_total_tokens)}`,
      `Input          ${token(item.session_input_tokens)}`,
      `Cached input   ${token(item.session_cached_input_tokens)}`,
      `Output         ${token(item.session_output_tokens)}`,
      `Reasoning      ${token(item.session_reasoning_tokens)}`,
    ].join('\n');
  }
  function itemChip(item, roundIndex, totalRounds) {
    const usage = item?.tokenUsage || {};
    const userIndex = item.userTurnIndex;
    const userTotal = item.userTotalTurns;
    const assistantIndex = item.assistantTurnIndex || item.roundIndex || roundIndex;
    const assistantTotal = item.assistantTotalTurns || item.totalRounds || totalRounds;
    const turnText = userIndex && userTotal
      ? `user rounds ${userIndex}/${userTotal}, assistant rounds ${assistantIndex}/${assistantTotal}`
      : `assistant rounds ${assistantIndex}/${assistantTotal}`;
    return `ctx ${token(usage.latest_context_tokens)}/${token(usage.context_window)} (${pct(usage.latest_context_percent)}) | ` +
      `turn token ${token(usage.latest_turn_total_tokens)} | ` +
      `total token ${token(usage.session_total_tokens)}  ${turnText}`;
  }
  function itemTitle(item, roundIndex, totalRounds) {
    const usage = item?.tokenUsage || {};
    const userIndex = item.userTurnIndex;
    const userTotal = item.userTotalTurns;
    const assistantIndex = item.assistantTurnIndex || item.roundIndex || roundIndex;
    const assistantTotal = item.assistantTotalTurns || item.totalRounds || totalRounds;
    const lines = [
      `Context: ${token(usage.latest_context_tokens)} / ${token(usage.context_window)} (${pct(usage.latest_context_percent)})`,
      `Turn: ${token(usage.latest_turn_total_tokens)} tokens (in ${token(usage.latest_turn_input_tokens)}, out ${token(usage.latest_turn_output_tokens)}, reasoning ${token(usage.latest_turn_reasoning_tokens)})`,
      `Session: ${token(usage.session_total_tokens)} tokens`,
    ];
    if (userIndex && userTotal) lines.push(`User rounds: ${userIndex}/${userTotal}`);
    lines.push(`Assistant rounds: ${assistantIndex}/${assistantTotal}`);
    return lines.join('\n');
  }
  function rowThreadId(row) {
    return row.getAttribute('data-app-action-sidebar-thread-id') ||
      row.querySelector('[data-app-action-sidebar-thread-id]')?.getAttribute('data-app-action-sidebar-thread-id') ||
      null;
  }
  function normalizeThreadId(threadId) {
    return String(threadId || '').replace(/^local:/, '');
  }
  function threadKeys(threadId) {
    const normalized = normalizeThreadId(threadId);
    return [String(threadId || ''), normalized, `local:${normalized}`].filter(Boolean);
  }
  function activeThreadId() {
    const row = document.querySelector('[data-app-action-sidebar-thread-row][data-app-action-sidebar-thread-active]') ||
      document.querySelector('[data-app-action-sidebar-thread-active]');
    return row ? rowThreadId(row) : null;
  }
  function ensureStyle() {
    document.getElementById(STYLE_ID)?.remove();
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      [${BADGE_ATTR}] {
        display: inline-flex;
        align-items: center;
        width: fit-content;
        max-width: 100%;
        margin-top: 4px;
        padding: 2px 6px;
        border-radius: 6px;
        background: color-mix(in srgb, CanvasText 9%, transparent);
        color: color-mix(in srgb, CanvasText 72%, transparent);
        font: 11px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        pointer-events: none;
      }
      .cti-hud {
        position: fixed;
        right: 14px;
        bottom: 16px;
        z-index: 2147483647;
        width: max-content;
        min-width: 180px;
        max-width: min(760px, calc(100vw - 28px));
        border: 1px solid color-mix(in srgb, CanvasText 16%, transparent);
        border-radius: 8px;
        background: color-mix(in srgb, Canvas 94%, transparent);
        color: CanvasText;
        box-shadow: 0 12px 36px color-mix(in srgb, CanvasText 18%, transparent);
        backdrop-filter: blur(16px);
        font: 12px/1.35 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
        overflow: hidden;
        user-select: none;
      }
      .cti-hud button {
        display: inline-grid;
        place-items: center;
        width: 28px;
        height: 24px;
        border: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
        border-radius: 6px;
        background: color-mix(in srgb, CanvasText 5%, transparent);
        color: inherit;
        font: 15px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        cursor: pointer;
        position: relative;
        z-index: 1;
      }
      .cti-hud-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 7px 9px;
        border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
        font-weight: 650;
        cursor: move;
        touch-action: none;
      }
      [data-cti-title] {
        cursor: pointer;
      }
      .cti-hud-tools {
        display: inline-flex;
        align-items: center;
        gap: 5px;
      }
      .cti-unit-group {
        display: inline-flex;
        align-items: center;
        gap: 2px;
        border: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
        border-radius: 6px;
        padding: 2px;
        background: color-mix(in srgb, CanvasText 4%, transparent);
      }
      .cti-hud .cti-unit-button {
        width: auto;
        min-width: 30px;
        height: 20px;
        padding: 0 6px;
        border: 0;
        border-radius: 4px;
        background: transparent;
        font: 11px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }
      .cti-hud .cti-unit-button[data-active="true"] {
        background: color-mix(in srgb, CanvasText 12%, transparent);
      }
      .cti-hud[data-dragging="true"] {
        transition: none;
        opacity: 0.92;
      }
      .cti-hud-body {
        display: grid;
        gap: 4px;
        padding: 8px 9px;
        color: color-mix(in srgb, CanvasText 78%, transparent);
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        white-space: nowrap;
      }
      .cti-credit {
        margin-top: 3px;
        justify-self: end;
        text-align: right;
        color: color-mix(in srgb, CanvasText 44%, transparent);
        font: 10px/1.2 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
      }
      .cti-hud[data-collapsed="true"] .cti-hud-body { display: none; }
      .cti-hud[data-collapsed="true"] .cti-unit-group { display: none; }
      .cti-reply-footer {
        margin-top: 8px;
        padding: 6px 8px;
        border: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
        border-radius: 6px;
        background: color-mix(in srgb, CanvasText 5%, transparent);
        color: color-mix(in srgb, CanvasText 68%, transparent);
        font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        overflow-wrap: anywhere;
      }
      .cti-reply-chip {
        display: inline-flex;
        align-items: center;
        max-width: min(720px, 70vw);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        padding: 2px 6px;
        border: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
        border-radius: 6px;
        background: color-mix(in srgb, CanvasText 5%, transparent);
        color: color-mix(in srgb, CanvasText 62%, transparent);
        font: 11px/1.25 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }
      .cti-sidebar-tooltip {
        position: fixed;
        z-index: 2147483647;
        max-width: min(420px, calc(100vw - 24px));
        padding: 12px 14px;
        border: 1px solid color-mix(in srgb, CanvasText 14%, transparent);
        border-radius: 8px;
        background: color-mix(in srgb, Canvas 96%, transparent);
        color: CanvasText;
        box-shadow: 0 12px 36px color-mix(in srgb, CanvasText 18%, transparent);
        backdrop-filter: blur(16px);
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        word-break: normal;
        font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        pointer-events: none;
      }
      .cti-sidebar-credit {
        display: block;
        margin-top: 8px;
        text-align: right;
        color: color-mix(in srgb, CanvasText 44%, transparent);
        font: 11px/1.2 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
      }
    `;
    document.head.appendChild(style);
  }
  function cleanOriginalTitle(value) {
    return String(value || '')
      .split(/\n{2,}(?=Context\s)/)[0]
      .replace(/\n?Context\s+[\s\S]*$/m, '')
      .trim();
  }
  function hideSidebarTooltip() {
    document.querySelector('.cti-sidebar-tooltip')?.remove();
  }
  function showSidebarTooltip(row, text) {
    hideSidebarTooltip();
    const tooltip = document.createElement('div');
    tooltip.className = 'cti-sidebar-tooltip';
    const content = document.createElement('div');
    content.textContent = text;
    const credit = document.createElement('span');
    credit.className = 'cti-sidebar-credit';
    credit.textContent = 'Made by Kevin KE';
    tooltip.append(content, credit);
    document.body.appendChild(tooltip);
    const rect = row.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const left = Math.min(window.innerWidth - tooltipRect.width - 12, Math.max(12, rect.right + 8));
    const top = Math.min(window.innerHeight - tooltipRect.height - 12, Math.max(12, rect.top + 30));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }
  function installSidebarHoverDelegation() {
    if (window.__codexContextTokenInspectorSidebarDelegation) return;
    window.__codexContextTokenInspectorSidebarDelegation = true;
    document.addEventListener('mouseover', event => {
      const row = event.target?.closest?.(`[${SIDEBAR_HOVER_ATTR}]`);
      if (!row) return;
      setTimeout(() => showSidebarTooltip(row, row.getAttribute(SIDEBAR_HOVER_ATTR) || ''), 0);
    });
    document.addEventListener('mouseout', event => {
      const row = event.target?.closest?.(`[${SIDEBAR_HOVER_ATTR}]`);
      if (!row) return;
      if (event.relatedTarget && row.contains(event.relatedTarget)) return;
      setTimeout(hideSidebarTooltip, 0);
    });
  }
  function applyStoredHudPosition(root) {
    try {
      const position = JSON.parse(localStorage.getItem(POSITION_KEY) || 'null');
      if (!position || typeof position.left !== 'number' || typeof position.top !== 'number') return;
      root.style.left = `${Math.max(8, Math.min(window.innerWidth - 48, position.left))}px`;
      root.style.top = `${Math.max(8, Math.min(window.innerHeight - 32, position.top))}px`;
      root.style.right = 'auto';
      root.style.bottom = 'auto';
    } catch {}
  }
  function installHudDrag(root) {
    if (root.__ctiDragInstalled) return;
    root.__ctiDragInstalled = true;
    const head = root.querySelector('.cti-hud-head');
    let drag = null;
    const start = event => {
      if (event.target?.closest?.('[data-cti-toggle], [data-cti-unit], [data-cti-title]')) return;
      if (event.button !== undefined && event.button !== 0) return;
      const rect = root.getBoundingClientRect();
      drag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top, x: event.clientX, y: event.clientY, moved: false };
      root.setAttribute('data-dragging', 'true');
      root.style.left = `${rect.left}px`;
      root.style.top = `${rect.top}px`;
      root.style.right = 'auto';
      root.style.bottom = 'auto';
      head.setPointerCapture?.(event.pointerId);
    };
    const move = event => {
      if (!drag) return;
      const width = root.offsetWidth || 48;
      const height = root.offsetHeight || 32;
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, event.clientX - drag.dx));
      const top = Math.max(8, Math.min(window.innerHeight - height - 8, event.clientY - drag.dy));
      if (Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 3) {
        drag.moved = true;
      }
      root.style.left = `${left}px`;
      root.style.top = `${top}px`;
    };
    const end = () => {
      if (!drag) return;
      root.__ctiSuppressToggle = drag.moved;
      drag = null;
      root.removeAttribute('data-dragging');
      const rect = root.getBoundingClientRect();
      localStorage.setItem(POSITION_KEY, JSON.stringify({ left: rect.left, top: rect.top }));
    };
    head.addEventListener('pointerdown', start);
    head.addEventListener('pointermove', move);
    head.addEventListener('pointerup', end);
    head.addEventListener('pointercancel', end);
  }
  function keepTogglePosition(root, update) {
    const toggle = root.querySelector('[data-cti-toggle]');
    const before = toggle.getBoundingClientRect();
    update();
    const after = toggle.getBoundingClientRect();
    if (root.style.left && root.style.left !== 'auto') {
      const currentLeft = Number.parseFloat(root.style.left) || root.getBoundingClientRect().left;
      root.style.left = `${currentLeft + before.left - after.left}px`;
      const rect = root.getBoundingClientRect();
      localStorage.setItem(POSITION_KEY, JSON.stringify({ left: rect.left, top: rect.top }));
    }
  }
  function updateUnitButtons(root) {
    root.querySelectorAll('[data-cti-unit]').forEach(button => {
      button.setAttribute('data-active', String(button.getAttribute('data-cti-unit') === unitMode()));
    });
  }
  function ensureHud() {
    let root = document.getElementById(ROOT_ID);
    if (root) {
      applyStoredHudPosition(root);
      installHudDrag(root);
      updateUnitButtons(root);
      return root;
    }
    root = document.createElement('section');
    root.id = ROOT_ID;
    root.className = 'cti-hud';
    root.setAttribute('data-collapsed', localStorage.getItem(COLLAPSE_KEY) === 'true' ? 'true' : 'false');
    root.innerHTML = `
      <div class="cti-hud-head">
        <span data-cti-title>Monitor</span>
        <div class="cti-hud-tools">
          <div class="cti-unit-group" aria-label="Token unit">
            <button class="cti-unit-button" type="button" data-cti-unit="raw">raw</button>
            <button class="cti-unit-button" type="button" data-cti-unit="k">K</button>
            <button class="cti-unit-button" type="button" data-cti-unit="m">M</button>
          </div>
          <button type="button" data-cti-toggle>−</button>
        </div>
      </div>
      <div class="cti-hud-body" data-cti-body></div>
    `;
    root.querySelectorAll('[data-cti-unit]').forEach(button => {
      button.addEventListener('pointerdown', event => event.stopPropagation());
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        localStorage.setItem(UNIT_KEY, button.getAttribute('data-cti-unit'));
        applyAll(window.__codexContextTokenInspectorPayload);
      });
    });
    const titleButton = root.querySelector('[data-cti-title]');
    titleButton.addEventListener('pointerdown', event => {
      event.stopPropagation();
    });
    titleButton.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (root.getAttribute('data-collapsed') === 'true') {
        toggleHud(root);
      }
    });
    const toggleButton = root.querySelector('[data-cti-toggle]');
    toggleButton.addEventListener('pointerdown', event => {
      event.stopPropagation();
    });
    toggleButton.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (root.__ctiSuppressToggle) {
        root.__ctiSuppressToggle = false;
        return;
      }
      toggleHud(root);
    });
    toggleButton.addEventListener('pointerup', event => {
      if (!root.__ctiSuppressToggle) return;
      event.preventDefault();
      root.__ctiSuppressToggle = false;
    });
    function toggleHud(root) {
      keepTogglePosition(root, () => {
        const next = root.getAttribute('data-collapsed') !== 'true';
        root.setAttribute('data-collapsed', String(next));
        localStorage.setItem(COLLAPSE_KEY, String(next));
        root.querySelector('[data-cti-toggle]').textContent = next ? '+' : '−';
      });
    }
    document.body.appendChild(root);
    applyStoredHudPosition(root);
    installHudDrag(root);
    updateUnitButtons(root);
    return root;
  }
  function applySidebar(summaries) {
    const byThread = new Map();
    summaries.forEach(item => {
      byThread.set(String(item.thread_id), item);
      (item.thread_keys || []).forEach(key => byThread.set(String(key), item));
    });
    document.querySelectorAll('[data-app-action-sidebar-thread-row]').forEach(row => {
      const id = rowThreadId(row);
      const item = byThread.get(String(id));
      if (!item) return;
      const existing = row.getAttribute('data-cti-original-title') || cleanOriginalTitle(row.getAttribute('title') || '');
      if (!row.hasAttribute('data-cti-original-title')) row.setAttribute('data-cti-original-title', existing);
      row.removeAttribute('title');
      row.setAttribute(SIDEBAR_HOVER_ATTR, summaryHover(item));
      if (!row.__ctiSidebarHoverInstalled) {
        row.__ctiSidebarHoverInstalled = true;
        row.addEventListener('mouseenter', () => showSidebarTooltip(row, row.getAttribute(SIDEBAR_HOVER_ATTR) || ''));
        row.addEventListener('mouseleave', hideSidebarTooltip);
        row.addEventListener('blur', hideSidebarTooltip);
      }
    });
  }
  function assistantNodes() {
    const selectors = [
      '[data-content-search-assistant-turn-key]',
      '[data-local-conversation-final-assistant]',
    ];
    const seen = new Set();
    const nodes = [];
    for (const selector of selectors) {
      document.querySelectorAll(selector).forEach(node => {
        const element = node.closest('[data-content-search-assistant-turn-key]') || node;
        if (!seen.has(element)) {
          seen.add(element);
          nodes.push(element);
        }
      });
      if (nodes.length) break;
    }
    return nodes.filter(node => !node.closest(`#${ROOT_ID}`));
  }
  function metadataTargetForAssistant(node) {
    const turn = node.closest('[data-turn-key]');
    if (!turn) return null;
    const candidates = Array.from(turn.querySelectorAll('span, div')).filter(el => {
      if (el.closest(`#${ROOT_ID}`) || el.hasAttribute(CHIP_ATTR)) return false;
      const text = (el.textContent || '').trim();
      return /^Work(?:ing|ed) for /.test(text) || /\b\d{1,2}:\d{2}\s?(?:AM|PM)\b/.test(text);
    });
    return candidates.find(el => /^Work(?:ing|ed) for /.test((el.textContent || '').trim())) ||
      candidates.find(el => /\b\d{1,2}:\d{2}\s?(?:AM|PM)\b/.test((el.textContent || '').trim())) ||
      null;
  }
  function normalizedText(value) {
    return String(value || '')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[`*~]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }
  function visibleItemForNode(node, index, items, used, visibleCount) {
    const nodeText = normalizedText(node.textContent);
    for (let itemIndex = 0; itemIndex < items.length; itemIndex += 1) {
      if (used.has(itemIndex)) continue;
      const prefix = normalizedText(items[itemIndex].textPrefix);
      if (prefix && nodeText.includes(prefix)) {
        used.add(itemIndex);
        return items[itemIndex];
      }
    }
    const fallbackStart = Math.max(0, items.length - visibleCount);
    const fallbackIndex = fallbackStart + index;
    if (items[fallbackIndex] && !used.has(fallbackIndex)) {
      used.add(fallbackIndex);
      return items[fallbackIndex];
    }
    return null;
  }
  function detailCandidates(payload) {
    if (payload.__ctiDetailCandidates) return payload.__ctiDetailCandidates;
    const details = new Map();
    if (payload.detail?.thread_id) details.set(String(payload.detail.thread_id), payload.detail);
    Object.values(payload.detailsByThread || {}).forEach(detail => {
      if (detail?.thread_id) details.set(String(detail.thread_id), detail);
    });
    payload.__ctiDetailCandidates = Array.from(details.values());
    return payload.__ctiDetailCandidates;
  }
  function detailPrefixes(detail) {
    if (detail.__ctiPrefixes) return detail.__ctiPrefixes;
    const items = detail?.assistantItems || [];
    detail.__ctiPrefixes = items
      .map((item, index) => ({ index, prefix: normalizedText(item.textPrefix) }))
      .filter(item => item.prefix.length >= 24);
    return detail.__ctiPrefixes;
  }
  function scoreDetailForNodes(detail, nodes) {
    if (!nodes.length) return 0;
    const prefixes = detailPrefixes(detail);
    if (!prefixes.length) return 0;
    let score = 0;
    const used = new Set();
    for (const node of nodes) {
      const nodeText = normalizedText(node.textContent);
      if (nodeText.length < 24) continue;
      for (const item of prefixes) {
        if (used.has(item.index)) continue;
        const matchScore = textMatchScore(nodeText, item.prefix);
        if (matchScore > 0) {
          used.add(item.index);
          score += matchScore;
          break;
        }
      }
    }
    return score;
  }
  function textChunks(value) {
    return normalizedText(value)
      .split(/[，。！？；：、,.!?;:\n\r()[\]{}<>《》"'“”‘’|]+/)
      .map(chunk => chunk.trim())
      .filter(chunk => chunk.length >= 6);
  }
  function textMatchScore(nodeText, prefix) {
    if (nodeText.includes(prefix)) return 100 + Math.min(prefix.length, 120);
    const nodeHead = nodeText.slice(0, Math.min(120, nodeText.length));
    if (prefix.includes(nodeHead)) return 80 + Math.min(nodeHead.length, 120);
    const prefixHead = prefix.slice(0, Math.min(80, prefix.length));
    if (nodeText.includes(prefixHead)) return 60 + Math.min(prefixHead.length, 80);

    let chunkScore = 0;
    let chunkMatches = 0;
    for (const chunk of textChunks(prefix)) {
      if (nodeText.includes(chunk)) {
        chunkMatches += 1;
        chunkScore += Math.min(chunk.length, 40);
      }
    }
    if (chunkMatches >= 2 || chunkScore >= 18) return chunkScore;

    chunkScore = 0;
    chunkMatches = 0;
    for (const chunk of textChunks(nodeText)) {
      if (prefix.includes(chunk)) {
        chunkMatches += 1;
        chunkScore += Math.min(chunk.length, 40);
      }
    }
    if (chunkMatches >= 2 || chunkScore >= 18) return chunkScore;
    return 0;
  }
  function detailForVisiblePage(payload) {
    const nodes = assistantNodes();
    if (!nodes.length) return null;
    const signature = nodes
      .map(node => normalizedText(node.textContent).slice(0, 180))
      .join('||');
    if (
      payload.__ctiVisibleMatchCache &&
      payload.__ctiVisibleMatchCache.signature === signature &&
      payload.__ctiVisibleMatchCache.threadId
    ) {
      const cached = detailCandidates(payload).find(
        detail => String(detail.thread_id) === String(payload.__ctiVisibleMatchCache.threadId)
      );
      if (cached) return cached;
    }
    let best = null;
    let bestScore = 0;
    for (const detail of detailCandidates(payload)) {
      const score = scoreDetailForNodes(detail, nodes);
      if (score > bestScore) {
        best = detail;
        bestScore = score;
      }
    }
    payload.__ctiVisibleMatchCache = {
      signature,
      threadId: bestScore > 0 ? best?.thread_id : null,
      score: bestScore,
    };
    return bestScore > 0 ? best : null;
  }
  function applyFooters(detail) {
    if (!detail) return;
    const nodes = assistantNodes();
    const items = detail.assistantItems || [];
    const used = new Set();
    nodes.forEach((node, index) => {
      const item = visibleItemForNode(node, index, items, used, nodes.length);
      const text = item?.footer;
      if (!text) return;
      node.querySelector(`[${FOOTER_ATTR}]`)?.remove();
      const sessionRound = item.roundIndex || index + 1;
      const sessionTotalRounds = item.totalRounds || items.length || nodes.length;
      const chipText = itemChip(item, sessionRound, sessionTotalRounds);
      const target = metadataTargetForAssistant(node);
      let chip = null;
      if (target) {
        chip = target.parentElement?.querySelector(`:scope > [${CHIP_ATTR}]`) || null;
      } else {
        chip = node.querySelector(`:scope > [${CHIP_ATTR}]`);
      }
      if (!chip) {
        chip = document.createElement('span');
        chip.className = 'cti-reply-chip';
        chip.setAttribute(CHIP_ATTR, 'true');
        if (target?.parentElement) {
          target.parentElement.appendChild(chip);
        } else {
          node.insertAdjacentElement('afterbegin', chip);
        }
      }
      chip.textContent = chipText;
      chip.setAttribute('title', itemTitle(item, sessionRound, sessionTotalRounds));
    });
  }
  function applyHud(payload, currentDetail = null) {
    const root = ensureHud();
    const body = root.querySelector('[data-cti-body]');
    const currentThreadId = currentDetail?.thread_id || activeThreadId() || payload.activeThreadId || payload.selectedThreadId;
    const selected =
      payload.summaries.find(item => String(item.thread_id) === String(currentDetail?.thread_id)) ||
      payload.summaries.find(item =>
        threadKeys(currentThreadId).some(key => String(item.thread_id) === key || (item.thread_keys || []).includes(key))
      ) ||
      payload.summaries.find(item =>
        threadKeys(activeThreadId() || payload.activeThreadId).some(key => String(item.thread_id) === key || (item.thread_keys || []).includes(key))
      ) ||
      payload.summaries[0];
    if (!selected) {
      body.textContent = 'No token records found.';
      return;
    }
    body.innerHTML = `
      <div>status: ${pressure(selected.latest_context_percent)} | left ${token(remainingContext(selected))}</div>
      <div>context: ${token(selected.latest_context_tokens)} / ${token(selected.context_window)} (${pct(selected.latest_context_percent)})</div>
      <div>turn: ${token(selected.latest_turn_total_tokens)} (in ${token(selected.latest_turn_input_tokens)}, cached ${token(selected.latest_turn_cached_input_tokens)}, out ${token(selected.latest_turn_output_tokens)}, reason ${token(selected.latest_turn_reasoning_tokens)})</div>
      <div>session: ${token(selected.session_total_tokens)} (in ${token(selected.session_input_tokens)}, cached ${token(selected.session_cached_input_tokens)}, out ${token(selected.session_output_tokens)}, reason ${token(selected.session_reasoning_tokens)})</div>
      <div class="cti-credit">Made by Kevin KE</div>
    `;
    const toggle = root.querySelector('[data-cti-toggle]');
    toggle.textContent = root.getAttribute('data-collapsed') === 'true' ? '+' : '−';
    updateUnitButtons(root);
  }
  function detailForCurrentThread(payload) {
    const details = payload.detailsByThread || {};
    for (const key of threadKeys(activeThreadId() || payload.activeThreadId || payload.selectedThreadId)) {
      if (details[key]) return details[key];
    }
    return payload.detail;
  }
  function applyAll(payload) {
    window.__codexContextTokenInspectorApplying = true;
    try {
      payload.activeThreadId = activeThreadId() || payload.activeThreadId;
      applySidebar(payload.summaries || []);
      const currentDetail = detailForVisiblePage(payload) || detailForCurrentThread(payload);
      payload.currentDetailThreadId = currentDetail?.thread_id || null;
      applyHud(payload, currentDetail);
      applyFooters(currentDetail);
    } finally {
      setTimeout(() => { window.__codexContextTokenInspectorApplying = false; }, 0);
    }
  }
  function installObserver(payload) {
    window.__codexContextTokenInspectorPayload = payload;
    if (window.__codexContextTokenInspectorObserver) return;
    let timer = null;
    const observer = new MutationObserver(() => {
      if (window.__codexContextTokenInspectorApplying) return;
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        applyAll(window.__codexContextTokenInspectorPayload);
      }, 450);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    window.__codexContextTokenInspectorObserver = observer;
  }

  window.__codexContextTokenInspectorObserver?.disconnect?.();
  window.__codexContextTokenInspectorObserver = null;
  document.getElementById(ROOT_ID)?.remove();
  ensureDefaultUnit();
  ensureStyle();
  hideSidebarTooltip();
  installSidebarHoverDelegation();
  document.querySelectorAll(`[${BADGE_ATTR}]`).forEach(node => node.remove());
  document.querySelectorAll(`[${FOOTER_ATTR}]`).forEach(node => node.remove());
  document.querySelectorAll(`[${CHIP_ATTR}]`).forEach(node => node.remove());
  installObserver(payload);
  applyAll(payload);
  return {
    ok: true,
    summaries: (payload.summaries || []).length,
    activeThreadId: activeThreadId() || payload.activeThreadId,
    selectedThreadId: payload.selectedThreadId,
    currentDetailThreadId: payload.currentDetailThreadId || null,
    assistantNodes: assistantNodes().length,
  };
})
"""


def inject_once(client: CDPClient, roots: list[str], limit: int) -> Any:
    state = runtime_state(client)
    payload = build_payload(roots, limit, state.get("activeThreadId"))
    expression = f"({INJECTION_SCRIPT})({json.dumps(payload, ensure_ascii=False)})"
    return client.evaluate(expression)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Codex DevTools port.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum recent sessions to inspect.")
    parser.add_argument("--interval", type=float, default=10.0, help="Refresh interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Inject once and exit.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Session JSONL files or directories. Defaults to ~/.codex/sessions and archived_sessions.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    roots = args.paths or [str(path) for path in inspector.DEFAULT_ROOTS]
    target = select_target(devtools_targets(args.port))
    client = CDPClient(str(target["webSocketDebuggerUrl"]))
    try:
        while True:
            result = inject_once(client, roots, args.limit)
            print(json.dumps(result, ensure_ascii=False))
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
