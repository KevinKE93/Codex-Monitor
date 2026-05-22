# Codex Monitor

Codex Monitor is a local, read-only overlay for Codex Desktop. It shows context-window usage and token consumption inside the existing Codex window without patching the app bundle or copying session data into this repository.

![Codex Monitor demo](assets/codex-monitor-demo.svg)

## Author

Built by KevinKE.

- GitHub: [KevinKE93](https://github.com/KevinKE93)

## Features

- Draggable `Monitor` panel inside Codex Desktop.
- Per-response chip with context usage, turn token, cumulative session token, and current visible-session round.
- Sidebar hover panel with session-level total, input, cached input, output, and reasoning tokens.
- Token display unit switcher: raw, K, and M. The default unit is K.
- Collapsed Monitor keeps the compact title and expand button while hiding unit controls.
- Local-only operation through Chrome DevTools Protocol.

## Safety Boundary

Codex Monitor does not modify:

- `Codex.app`
- `app.asar`
- Codex session JSONL files
- Codex settings or authentication

It reads local Codex session logs and injects temporary DOM elements into a Codex renderer launched with a local DevTools port.

## Usage

Launch Codex with a local DevTools port:

```bash
./scripts/reopen_codex_with_debug.sh 9222
```

Inject the monitor into the current Codex window:

```bash
./run_once.sh 9222
```

Run it again after restarting Codex. The injected UI keeps itself updated while the current page is active.

## CLI Inspection

```bash
python3 ./scripts/context_token_inspector.py --latest --format footer
python3 ./scripts/context_token_inspector.py --latest --format hover
python3 ./scripts/context_token_inspector.py --limit 20 --format table
```

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ./tests/test_context_token_inspector.py
```

## Repository Privacy

This repository contains only source code, tests, and a synthetic demo image. It does not include local Codex session data, generated logs, marketplace metadata, plugin manifests, screenshots of private conversations, or conversation transcripts.

## License

MIT. See [LICENSE](LICENSE).
