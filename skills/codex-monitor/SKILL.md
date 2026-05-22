---
name: codex-monitor
description: Start, inspect, or repair the local Codex Monitor overlay for Codex Desktop context-window and token usage.
---

# Codex Monitor

Use this skill when the user asks to show Codex context usage, token usage, session totals, per-reply token chips, sidebar hover metrics, or to restart/repair the Codex Monitor overlay.

## Boundary

Codex Monitor is local and read-only. It does not patch `Codex.app`, `app.asar`, session JSONL files, auth files, or Codex settings. It reads local Codex session logs and injects temporary DOM elements into a Codex renderer launched with a local Chrome DevTools port.

## Install From GitHub

Use the repository URL:

```text
https://github.com/KevinKE93/Codex-Monitor
```

Codex CLI equivalent:

```bash
codex plugin marketplace add https://github.com/KevinKE93/Codex-Monitor --ref main
codex plugin add codex-monitor@codex-monitor
```

The plugin install only makes this skill and the local scripts available. To show the overlay, start the monitor from the installed plugin root.

## Start Monitor

From the plugin root:

```bash
./scripts/start_codex_monitor.sh 9222
```

This reopens Codex with a local DevTools port, injects the monitor, and loops so the overlay is restored after Codex restarts.
The injector refreshes the session payload every 10 seconds by default while the in-page observer handles ordinary UI changes.
For responsiveness, sidebar hover summaries cover the latest 100 sessions while per-message chip details are parsed for the latest 12 sessions by default. Use `--detail-limit` on `context_token_injector.py` if older sessions need chips.
If the requested port is occupied, the scripts automatically choose the next available local port.

## Auto Start

```bash
./scripts/install_launch_agent.sh 9222
```

This installs a macOS LaunchAgent that keeps Codex Monitor running after login, Codex restart, and Codex updates.
In LaunchAgent mode, Monitor waits while Codex is closed; it does not reopen Codex after a normal user quit.

```bash
./scripts/uninstall_launch_agent.sh
```

This stops and removes the LaunchAgent.

## Inject Once

```bash
./run_once.sh 9222
```

Use this when Codex is already running with `--remote-debugging-port=9222`.

## Inspect From CLI

```bash
python3 ./scripts/context_token_inspector.py --latest --format footer
python3 ./scripts/context_token_inspector.py --latest --format hover
python3 ./scripts/context_token_inspector.py --limit 20 --format table
```

## Upgrade Handling

If Codex Desktop upgrades or restarts, injected DOM elements disappear because they are intentionally temporary. Run `./scripts/start_codex_monitor.sh 9222` again, or keep that script running in a terminal so it can reconnect and re-inject after the renderer returns.

## Interpretation

- `context` uses the current request's `last_token_usage.input_tokens` divided by `model_context_window`.
- `turn token` uses the current response's `last_token_usage.total_tokens`.
- `total token` and Monitor `session` use cumulative `total_token_usage` for the current session only.
- `user rounds` counts non-environment user messages in the current session JSONL.
- `assistant rounds` counts assistant messages with token-count records in the current session JSONL.
