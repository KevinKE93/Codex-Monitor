#!/usr/bin/env python3
"""Inspect Codex Desktop token_count events in local session JSONL files."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any, Iterable


DEFAULT_ROOTS = [
    Path.home() / ".codex" / "sessions",
    Path.home() / ".codex" / "archived_sessions",
]


def comma(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def pct(numerator: int | None, denominator: int | None) -> float | None:
    if not numerator or not denominator:
        return None
    return round((numerator / denominator) * 100, 1)


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def read_jsonl_reverse(path: Path, chunk_size: int = 1024 * 256) -> Iterable[dict[str, Any]]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            buffer = handle.read(read_size) + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]
            for line in reversed(lines[1:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
        if buffer.strip():
            try:
                row = json.loads(buffer)
            except json.JSONDecodeError:
                return
            if isinstance(row, dict):
                yield row


def token_count_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("type") != "event_msg":
        return None
    payload = row.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    return info if isinstance(info, dict) else None


def summarize_session(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    meta: dict[str, Any] = {}
    latest_token_event: dict[str, Any] | None = None
    latest_token_timestamp: str | None = None
    token_events = 0
    last_timestamp: str | None = None

    for row in read_jsonl(path):
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            last_timestamp = timestamp

        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            meta = row["payload"]

        token_info = token_count_payload(row)
        if token_info is not None:
            token_events += 1
            latest_token_event = token_info
            latest_token_timestamp = timestamp if isinstance(timestamp, str) else None

    last_usage = (latest_token_event or {}).get("last_token_usage") or {}
    total_usage = (latest_token_event or {}).get("total_token_usage") or {}
    context_window = (latest_token_event or {}).get("model_context_window")

    latest_context_tokens = as_int(last_usage.get("input_tokens"))
    latest_context_percent = pct(latest_context_tokens, as_int(context_window))

    return {
        "path": str(path),
        "thread_id": meta.get("id") or infer_thread_id(path),
        "cwd": meta.get("cwd"),
        "model_provider": meta.get("model_provider"),
        "created_at": meta.get("timestamp"),
        "updated_at": latest_token_timestamp or last_timestamp,
        "token_events": token_events,
        "context_window": as_int(context_window),
        "latest_context_tokens": latest_context_tokens,
        "latest_context_percent": latest_context_percent,
        "latest_turn_total_tokens": as_int(last_usage.get("total_tokens")),
        "latest_turn_input_tokens": as_int(last_usage.get("input_tokens")),
        "latest_turn_cached_input_tokens": as_int(last_usage.get("cached_input_tokens")),
        "latest_turn_output_tokens": as_int(last_usage.get("output_tokens")),
        "latest_turn_reasoning_tokens": as_int(last_usage.get("reasoning_output_tokens")),
        "session_total_tokens": as_int(total_usage.get("total_tokens")),
        "session_input_tokens": as_int(total_usage.get("input_tokens")),
        "session_cached_input_tokens": as_int(total_usage.get("cached_input_tokens")),
        "session_output_tokens": as_int(total_usage.get("output_tokens")),
        "session_reasoning_tokens": as_int(total_usage.get("reasoning_output_tokens")),
    }


def summarize_session_fast(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    meta: dict[str, Any] = {}
    latest_token_event: dict[str, Any] | None = None
    latest_token_timestamp: str | None = None
    last_timestamp: str | None = None

    for row in read_jsonl(path):
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            last_timestamp = timestamp
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            meta = row["payload"]
            break

    for row in read_jsonl_reverse(path):
        timestamp = row.get("timestamp")
        if last_timestamp is None and isinstance(timestamp, str):
            last_timestamp = timestamp
        token_info = token_count_payload(row)
        if token_info is not None:
            latest_token_event = token_info
            latest_token_timestamp = timestamp if isinstance(timestamp, str) else None
            break

    usage = usage_summary_from_token_info(latest_token_event or {})
    return {
        "path": str(path),
        "thread_id": meta.get("id") or infer_thread_id(path),
        "cwd": meta.get("cwd"),
        "model_provider": meta.get("model_provider"),
        "created_at": meta.get("timestamp"),
        "updated_at": latest_token_timestamp or last_timestamp,
        "token_events": None,
        **usage,
    }


def usage_summary_from_token_info(token_info: dict[str, Any]) -> dict[str, Any]:
    last_usage = token_info.get("last_token_usage") or {}
    total_usage = token_info.get("total_token_usage") or {}
    context_window = token_info.get("model_context_window")
    latest_context_tokens = as_int(last_usage.get("input_tokens"))
    return {
        "context_window": as_int(context_window),
        "latest_context_tokens": latest_context_tokens,
        "latest_context_percent": pct(latest_context_tokens, as_int(context_window)),
        "latest_turn_total_tokens": as_int(last_usage.get("total_tokens")),
        "latest_turn_input_tokens": as_int(last_usage.get("input_tokens")),
        "latest_turn_cached_input_tokens": as_int(last_usage.get("cached_input_tokens")),
        "latest_turn_output_tokens": as_int(last_usage.get("output_tokens")),
        "latest_turn_reasoning_tokens": as_int(last_usage.get("reasoning_output_tokens")),
        "session_total_tokens": as_int(total_usage.get("total_tokens")),
        "session_input_tokens": as_int(total_usage.get("input_tokens")),
        "session_cached_input_tokens": as_int(total_usage.get("cached_input_tokens")),
        "session_output_tokens": as_int(total_usage.get("output_tokens")),
        "session_reasoning_tokens": as_int(total_usage.get("reasoning_output_tokens")),
    }


def parse_session_detail(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    meta: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    pending_assistant_index: int | None = None
    current_turn_index = 0

    for row in read_jsonl(path):
        payload = row.get("payload")
        timestamp = row.get("timestamp")

        if row.get("type") == "session_meta" and isinstance(payload, dict):
            meta = payload
            continue

        if row.get("type") == "response_item" and isinstance(payload, dict):
            if payload.get("type") == "message":
                role = payload.get("role")
                if role in {"user", "assistant"}:
                    text = message_text(payload)
                    if should_skip_message(role, text):
                        continue
                    if role == "user":
                        current_turn_index += 1
                    messages.append(
                        {
                            "timestamp": timestamp,
                            "role": role,
                            "text": text,
                            "token_footer": None,
                            "token_usage": None,
                            "turn_index": current_turn_index if role == "assistant" and current_turn_index else None,
                            "total_turns": None,
                        }
                    )
                    if role == "assistant":
                        pending_assistant_index = len(messages) - 1
            continue

        token_info = token_count_payload(row)
        if token_info is not None and pending_assistant_index is not None:
            usage = usage_summary_from_token_info(token_info)
            messages[pending_assistant_index]["token_usage"] = usage
            messages[pending_assistant_index]["token_footer"] = format_reply_footer(usage)
            pending_assistant_index = None

    for message in messages:
        message["total_turns"] = current_turn_index or None

    return {
        "summary": summarize_session(path),
        "meta": meta,
        "messages": messages,
    }


def message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n\n".join(parts).strip()


def should_skip_message(role: str, text: str) -> bool:
    if role != "user":
        return False
    stripped = text.strip()
    return stripped.startswith("<environment_context>") or stripped.startswith("<permissions instructions>")


def render_markdownish(text: str) -> str:
    escaped = html.escape(text)
    escaped = escaped.replace("\n", "<br>")
    return escaped


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def infer_thread_id(path: Path) -> str:
    name = path.stem
    if "-" not in name:
        return name
    return name.rsplit("-", 1)[-1]


def session_files(paths: Iterable[str], limit: int | None = None) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(os.path.expanduser(raw))
        if path.is_file() and path.suffix == ".jsonl":
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.jsonl"))

    files = sorted(set(files), key=lambda candidate: candidate.stat().st_mtime, reverse=True)
    if limit is not None:
        return files[:limit]
    return files


def format_reply_footer(summary: dict[str, Any]) -> str:
    context = comma(summary.get("latest_context_tokens"))
    window = comma(summary.get("context_window"))
    percent = summary.get("latest_context_percent")
    percent_text = f"{percent:.1f}%" if isinstance(percent, float) else "-"
    return (
        f"context: {context} / {window} ({percent_text}) | "
        f"turn: {comma(summary.get('latest_turn_total_tokens'))} tokens "
        f"(in {comma(summary.get('latest_turn_input_tokens'))}, "
        f"out {comma(summary.get('latest_turn_output_tokens'))}, "
        f"reasoning {comma(summary.get('latest_turn_reasoning_tokens'))}) | "
        f"session: {comma(summary.get('session_total_tokens'))} tokens"
    )


def format_reply_chip(
    summary: dict[str, Any],
    round_index: int | None = None,
    total_rounds: int | None = None,
    user_turn_index: int | None = None,
    user_total_turns: int | None = None,
    assistant_turn_index: int | None = None,
    assistant_total_turns: int | None = None,
) -> str:
    percent = summary.get("latest_context_percent")
    percent_text = f" ({percent:.1f}%)" if isinstance(percent, float) else ""
    chip = (
        f"ctx {comma(summary.get('latest_context_tokens'))}/"
        f"{comma(summary.get('context_window'))}{percent_text} | "
        f"turn token {comma(summary.get('latest_turn_total_tokens'))} | "
        f"total token {comma(summary.get('session_total_tokens'))}"
    )
    user_index = user_turn_index if user_turn_index is not None else round_index
    user_total = user_total_turns if user_total_turns is not None else total_rounds
    assistant_index = assistant_turn_index if assistant_turn_index is not None else round_index
    assistant_total = assistant_total_turns if assistant_total_turns is not None else total_rounds
    if user_index is not None and user_total is not None:
        chip += f"  user rounds {user_index}/{user_total}"
        if assistant_index is not None and assistant_total is not None:
            chip += f", assistant rounds {assistant_index}/{assistant_total}"
    elif assistant_index is not None and assistant_total is not None:
        chip += f"  assistant rounds {assistant_index}/{assistant_total}"
    return chip


def format_hover(summary: dict[str, Any]) -> str:
    lines = [
        f"Session total  {comma(summary.get('session_total_tokens'))}",
        f"Input          {comma(summary.get('session_input_tokens'))}",
        f"Cached input   {comma(summary.get('session_cached_input_tokens'))}",
        f"Output         {comma(summary.get('session_output_tokens'))}",
        f"Reasoning      {comma(summary.get('session_reasoning_tokens'))}",
    ]
    return "\n".join(lines)


def format_percent(value: Any) -> str:
    return f"{value:.1f}%" if isinstance(value, float) else "-"


def context_pressure(value: Any) -> str:
    if not isinstance(value, float):
        return "UNKNOWN"
    if value >= 85:
        return "HIGH"
    if value >= 70:
        return "WATCH"
    return "OK"


def print_table(summaries: list[dict[str, Any]]) -> None:
    headers = ["updated", "thread", "context", "turn", "session", "cwd"]
    rows = []
    for item in summaries:
        rows.append(
            [
                str(item.get("updated_at") or "-")[:19],
                str(item.get("thread_id") or "-")[:12],
                f"{comma(item.get('latest_context_tokens'))}/{comma(item.get('context_window'))}",
                comma(item.get("latest_turn_total_tokens")),
                comma(item.get("session_total_tokens")),
                str(item.get("cwd") or "-"),
            ]
        )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(header)
        for index, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="Session JSONL files or directories. Defaults to ~/.codex/sessions and archived_sessions.",
    )
    parser.add_argument("--limit", type=int, default=20, help="Maximum sessions to inspect.")
    parser.add_argument(
        "--format",
        choices=["table", "json", "hover", "footer"],
        default="table",
        help="Output format.",
    )
    parser.add_argument("--latest", action="store_true", help="Only show the newest matching session.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    paths = args.paths or [str(path) for path in DEFAULT_ROOTS]
    limit = 1 if args.latest else args.limit
    summaries = [summarize_session(path) for path in session_files(paths, limit=limit)]
    summaries = [summary for summary in summaries if summary.get("token_events")]

    if args.format == "json":
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    elif args.format == "hover":
        for index, summary in enumerate(summaries):
            if index:
                print()
            print(format_hover(summary))
    elif args.format == "footer":
        for summary in summaries:
            print(format_reply_footer(summary))
    else:
        print_table(summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
