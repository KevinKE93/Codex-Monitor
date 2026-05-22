import importlib.util
import json
import os
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

        self.assertEqual(payload["activeThreadId"], "local:019e4e4a-demo")
        self.assertEqual(payload["selectedThreadId"], "019e4e4a-demo")
        self.assertEqual(payload["summaries"][0]["badge"], "15.5% ctx")
        self.assertIn("local:019e4e4a-demo", payload["summaries"][0]["thread_keys"])
        self.assertIn("Session total  64,016", payload["summaries"][0]["hover"])
        self.assertIn("Cached input   27,904", payload["summaries"][0]["hover"])
        self.assertNotIn("Context  39,949 / 258,400", payload["summaries"][0]["hover"])
        self.assertNotIn("/tmp/project", payload["summaries"][0]["hover"])
        self.assertEqual(len(payload["detail"]["assistantFooters"]), 1)
        self.assertEqual(payload["detail"]["assistantChips"][0], "ctx 39,949/258,400 (15.5%) | turn token 40,416 | total token 64,016  assistant rounds 1/1")

    def test_injector_payload_uses_session_rounds_for_historical_replies(self):
        injector = load_injector()
        rows = [
            {
                "timestamp": "2026-05-22T06:06:02.814Z",
                "type": "session_meta",
                "payload": {"id": "019e4e4a-demo", "cwd": "/tmp/project"},
            },
        ]
        for index in range(1, 4):
            rows.extend(
                [
                    {
                        "timestamp": f"2026-05-22T06:0{index}:10.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": f"assistant reply {index}"}],
                        },
                    },
                    {
                        "timestamp": f"2026-05-22T06:0{index}:11.000Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 10000 * index,
                                    "cached_input_tokens": 5000 * index,
                                    "output_tokens": 100 * index,
                                    "reasoning_output_tokens": 20 * index,
                                    "total_tokens": 10120 * index,
                                },
                                "last_token_usage": {
                                    "input_tokens": 3000 * index,
                                    "cached_input_tokens": 1200 * index,
                                    "output_tokens": 100,
                                    "reasoning_output_tokens": 20,
                                    "total_tokens": 3120 * index,
                                },
                                "model_context_window": 258400,
                            },
                        },
                    },
                ]
            )
        session = self.write_session(rows)

        payload = injector.build_payload([str(session.parent)], limit=10, selected_thread_id="019e4e4a-demo")
        items = payload["detail"]["assistantItems"]

        self.assertEqual([item["roundIndex"] for item in items], [1, 2, 3])
        self.assertEqual([item["totalRounds"] for item in items], [3, 3, 3])
        self.assertTrue(payload["detail"]["assistantChips"][2].endswith("assistant rounds 3/3"))

    def test_rounds_count_user_turns_not_assistant_status_messages(self):
        injector = load_injector()
        session = self.write_session(
            [
                {
                    "timestamp": "2026-05-22T06:06:02.814Z",
                    "type": "session_meta",
                    "payload": {"id": "019e4e4a-demo", "cwd": "/tmp/project"},
                },
                {
                    "timestamp": "2026-05-22T06:06:03.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "first request"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:06:04.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "first assistant status"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:06:05.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 1000},
                            "last_token_usage": {"input_tokens": 400, "total_tokens": 500},
                            "model_context_window": 1000,
                        },
                    },
                },
                {
                    "timestamp": "2026-05-22T06:07:03.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "second request"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:07:04.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "second assistant status"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:07:05.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 2000},
                            "last_token_usage": {"input_tokens": 500, "total_tokens": 600},
                            "model_context_window": 1000,
                        },
                    },
                },
                {
                    "timestamp": "2026-05-22T06:07:06.000Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "second assistant final"}],
                    },
                },
                {
                    "timestamp": "2026-05-22T06:07:07.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 3000},
                            "last_token_usage": {"input_tokens": 600, "total_tokens": 700},
                            "model_context_window": 1000,
                        },
                    },
                },
            ]
        )

        payload = injector.build_payload([str(session.parent)], limit=10, selected_thread_id="019e4e4a-demo")
        items = payload["detail"]["assistantItems"]

        self.assertEqual([item["roundIndex"] for item in items], [1, 2, 2])
        self.assertEqual([item["totalRounds"] for item in items], [2, 2, 2])
        self.assertTrue(payload["detail"]["assistantChips"][0].endswith("user rounds 1/2, assistant rounds 1/3"))
        self.assertTrue(payload["detail"]["assistantChips"][2].endswith("user rounds 2/2, assistant rounds 3/3"))

    def test_injector_payload_prefers_latest_session_over_stale_active_thread(self):
        injector = load_injector()
        tmpdir = pathlib.Path(tempfile.mkdtemp())
        old_session = tmpdir / "rollout-2026-05-20T19-19-12-019e451c-old.jsonl"
        new_session = tmpdir / "rollout-2026-05-22T14-06-01-019e4e4a-new.jsonl"

        def write(path, session_id, replies):
            rows = [
                {
                    "timestamp": "2026-05-22T06:06:02.814Z",
                    "type": "session_meta",
                    "payload": {"id": session_id, "cwd": "/tmp/project"},
                },
            ]
            for index in range(1, replies + 1):
                rows.extend(
                    [
                        {
                            "timestamp": f"2026-05-22T06:{index:02d}:10.000Z",
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": f"reply {index}"}],
                            },
                        },
                        {
                            "timestamp": f"2026-05-22T06:{index:02d}:11.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"total_tokens": 1000 * index},
                                    "last_token_usage": {"input_tokens": 100 * index, "total_tokens": 200 * index},
                                    "model_context_window": 1000,
                                },
                            },
                        },
                    ]
                )
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

        write(old_session, "019e451c-old", 7)
        write(new_session, "019e4e4a-new", 2)
        os.utime(old_session, (1_700_000_000, 1_700_000_000))
        os.utime(new_session, (1_700_000_100, 1_700_000_100))

        payload = injector.build_payload([str(tmpdir)], limit=10, selected_thread_id="local:019e451c-old")

        self.assertEqual(payload["activeThreadId"], "local:019e451c-old")
        self.assertEqual(payload["selectedThreadId"], "019e4e4a-new")
        self.assertEqual(payload["detail"]["thread_id"], "019e4e4a-new")
        self.assertEqual(len(payload["detail"]["assistantItems"]), 2)
        self.assertTrue(payload["detail"]["assistantChips"][1].endswith("assistant rounds 2/2"))


if __name__ == "__main__":
    unittest.main()
