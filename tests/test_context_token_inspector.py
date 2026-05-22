import importlib.util
import json
import pathlib
import tempfile
import unittest


PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "context_token_inspector.py"
INJECTOR_PATH = PLUGIN_ROOT / "scripts" / "context_token_injector.py"


def load_module():
    spec = importlib.util.spec_from_file_location("context_token_inspector", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_injector():
    spec = importlib.util.spec_from_file_location("context_token_injector", INJECTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContextTokenInspectorTests(unittest.TestCase):
    def write_session(self, rows):
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        session = tmpdir / "rollout-2026-05-22T14-06-01-019e4e4a-demo.jsonl"
        with session.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return session

    def test_summarizes_latest_context_and_cumulative_usage(self):
        module = load_module()
        session = self.write_session(
            [
                {
                    "timestamp": "2026-05-22T06:06:02.814Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "019e4e4a-demo",
                        "cwd": "/tmp/project",
                        "model_provider": "openai",
                    },
                },
                {
                    "timestamp": "2026-05-22T06:06:39.701Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 62670,
                                "cached_input_tokens": 27904,
                                "output_tokens": 1346,
                                "reasoning_output_tokens": 588,
                                "total_tokens": 64016,
                            },
                            "last_token_usage": {
                                "input_tokens": 39949,
                                "cached_input_tokens": 22400,
                                "output_tokens": 467,
                                "reasoning_output_tokens": 72,
                                "total_tokens": 40416,
                            },
                            "model_context_window": 258400,
                        },
                    },
                },
            ]
        )

        summary = module.summarize_session(session)

        self.assertEqual(summary["thread_id"], "019e4e4a-demo")
        self.assertEqual(summary["cwd"], "/tmp/project")
        self.assertEqual(summary["latest_context_tokens"], 39949)
        self.assertEqual(summary["latest_context_percent"], 15.5)
        self.assertEqual(summary["session_total_tokens"], 64016)
        self.assertEqual(summary["latest_turn_total_tokens"], 40416)

    def test_formats_reply_footer_line(self):
        module = load_module()
        summary = {
            "latest_context_tokens": 39949,
            "context_window": 258400,
            "latest_context_percent": 15.5,
            "latest_turn_total_tokens": 40416,
            "latest_turn_input_tokens": 39949,
            "latest_turn_output_tokens": 467,
            "latest_turn_reasoning_tokens": 72,
            "session_total_tokens": 64016,
        }

        footer = module.format_reply_footer(summary)

        self.assertEqual(
            footer,
            "context: 39,949 / 258,400 (15.5%) | turn: 40,416 tokens "
            "(in 39,949, out 467, reasoning 72) | session: 64,016 tokens",
        )

    def test_parses_messages_and_attaches_next_token_count_to_assistant_reply(self):
        module = load_module()
        session = self.write_session(
            [
                {
                    "timestamp": "2026-05-22T06:06:02.814Z",
                    "type": "session_meta",
                    "payload": {"id": "019e4e4a-demo", "cwd": "/tmp/project"},
                },
                {
                    "timestamp": "2026-05-22T06:06:07.320Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "build this"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:06:39.533Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:06:39.701Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 62670,
                                "cached_input_tokens": 27904,
                                "output_tokens": 1346,
                                "reasoning_output_tokens": 588,
                                "total_tokens": 64016,
                            },
                            "last_token_usage": {
                                "input_tokens": 39949,
                                "output_tokens": 467,
                                "reasoning_output_tokens": 72,
                                "total_tokens": 40416,
                            },
                            "model_context_window": 258400,
                        },
                    },
                },
            ]
        )

        detail = module.parse_session_detail(session)

        self.assertEqual(detail["summary"]["thread_id"], "019e4e4a-demo")
        self.assertEqual(len(detail["messages"]), 2)
        self.assertEqual(detail["messages"][1]["role"], "assistant")
        self.assertEqual(detail["messages"][1]["token_footer"], "context: 39,949 / 258,400 (15.5%) | turn: 40,416 tokens (in 39,949, out 467, reasoning 72) | session: 64,016 tokens")

    def test_injector_payload_keeps_sidebar_and_active_reply_data_compact(self):
        injector = load_injector()
        session = self.write_session(
            [
                {
                    "timestamp": "2026-05-22T06:06:02.814Z",
                    "type": "session_meta",
                    "payload": {"id": "019e4e4a-demo", "cwd": "/tmp/project"},
                },
                {
                    "timestamp": "2026-05-22T06:06:39.533Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:06:39.701Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 62670,
                                "cached_input_tokens": 27904,
                                "output_tokens": 1346,
                                "reasoning_output_tokens": 588,
                                "total_tokens": 64016,
                            },
                            "last_token_usage": {
                                "input_tokens": 39949,
                                "output_tokens": 467,
                                "reasoning_output_tokens": 72,
                                "total_tokens": 40416,
                            },
                            "model_context_window": 258400,
                        },
                    },
                },
            ]
        )

        payload = injector.build_payload([str(session.parent)], limit=10, selected_thread_id="local:019e4e4a-demo")

        self.assertEqual(payload["selectedThreadId"], "local:019e4e4a-demo")
        self.assertEqual(payload["summaries"][0]["badge"], "15.5% ctx")
        self.assertIn("local:019e4e4a-demo", payload["summaries"][0]["thread_keys"])
        self.assertIn("Session total  64,016", payload["summaries"][0]["hover"])
        self.assertIn("Cached input   27,904", payload["summaries"][0]["hover"])
        self.assertNotIn("Context  39,949 / 258,400", payload["summaries"][0]["hover"])
        self.assertNotIn("/tmp/project", payload["summaries"][0]["hover"])
        self.assertEqual(len(payload["detail"]["assistantFooters"]), 1)
        self.assertEqual(payload["detail"]["assistantChips"][0], "ctx 39,949/258,400 (15.5%) | turn token 40,416 | total token 64,016  round 1/1")


if __name__ == "__main__":
    unittest.main()
