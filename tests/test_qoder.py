from __future__ import annotations

import json
import unittest
from datetime import date
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from aw_watcher_llm.qoder import collect_payload


def _timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(text)


class QoderCollectPayloadTests(unittest.TestCase):
    def test_collect_payload_includes_root_and_child_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            projects_dir = Path(tmp)
            project_dir = projects_dir / "-Users-test-project"
            project_dir.mkdir()

            root_session_id = "root-session"
            root_meta = {
                "id": root_session_id,
                "parent_session_id": "",
                "title": "Cache investigation",
                "working_dir": "/tmp/project",
                "created_at": _timestamp_ms("2026-04-02T10:00:00+08:00"),
                "updated_at": _timestamp_ms("2026-04-02T10:00:15+08:00"),
            }
            _write_json(project_dir / f"{root_session_id}-session.json", root_meta)

            root_rows = [
                {
                    "uuid": "row-user-1",
                    "parentUuid": "",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "main-agent",
                    "type": "user",
                    "timestamp": "2026-04-02T10:00:01+08:00",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Investigate the cache issue"}],
                        "id": "user-1",
                    },
                    "isMeta": False,
                },
                {
                    "uuid": "row-assistant-1",
                    "parentUuid": "user-1",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "main-agent",
                    "type": "assistant",
                    "timestamp": "2026-04-02T10:00:05+08:00",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "echo hi"},
                            }
                        ],
                        "id": "assistant-1",
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "cache_read_input_tokens": 3,
                            "cache_creation_input_tokens": 2,
                        },
                    },
                    "isMeta": False,
                },
                {
                    "uuid": "row-result-1",
                    "parentUuid": "assistant-1",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "main-agent",
                    "type": "user",
                    "timestamp": "2026-04-02T10:00:08+08:00",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "content": "ok"}],
                        "id": "result-1",
                    },
                    "toolUseResult": {
                        "end_time": _timestamp_ms("2026-04-02T10:00:08+08:00"),
                    },
                    "isMeta": False,
                },
                {
                    "uuid": "row-assistant-2",
                    "parentUuid": "result-1",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "main-agent",
                    "type": "assistant",
                    "timestamp": "2026-04-02T10:00:12+08:00",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Finished investigating"}],
                        "id": "assistant-2",
                        "usage": {
                            "input_tokens": 101,
                            "output_tokens": 19,
                            "cache_read_input_tokens": 31,
                            "cache_creation_input_tokens": 41,
                        },
                    },
                    "isMeta": False,
                },
            ]
            _write_jsonl(project_dir / f"{root_session_id}.jsonl", root_rows)

            subagent_dir = project_dir / root_session_id / "subagents"
            subagent_dir.mkdir(parents=True)
            child_rows = [
                {
                    "uuid": "child-user-row",
                    "parentUuid": "",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "child-agent",
                    "type": "user",
                    "timestamp": "2026-04-02T11:00:00+08:00",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Check the child flow"}],
                        "id": "child-user-1",
                    },
                    "isMeta": False,
                },
                {
                    "uuid": "child-assistant-row",
                    "parentUuid": "child-user-1",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "child-agent",
                    "type": "assistant",
                    "timestamp": "2026-04-02T11:00:03+08:00",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Child result"}],
                        "id": "child-assistant-1",
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 6,
                        },
                    },
                    "isMeta": False,
                },
            ]
            _write_jsonl(subagent_dir / "agent-child-agent.jsonl", child_rows)

            payload = collect_payload(
                host="test-host",
                target_date=date(2026, 4, 2),
                projects_dir=projects_dir,
            )
            events = payload.raw_events

        self.assertEqual(payload.raw_bucket.id, "aw-watcher-llm-qoder_test-host")
        self.assertEqual(len(events), 7)

        root_started = next(
            event for event in events if event.data["kind"] == "session.started" and event.data["session_id"] == root_session_id
        )
        self.assertEqual(root_started.data["title"], "Cache investigation")
        self.assertEqual(root_started.data["agent"], "main-agent")

        root_response = next(event for event in events if event.data.get("message_id") == "assistant-1")
        self.assertEqual(root_response.data["source"], "qoder")
        self.assertAlmostEqual(root_response.duration, 3.0)
        self.assertEqual(root_response.data["input_tokens"], 11)
        self.assertEqual(root_response.data["cache_write_tokens"], 2)

        final_response = next(event for event in events if event.data.get("message_id") == "assistant-2")
        self.assertEqual(final_response.data["output_tokens"], 19)
        self.assertEqual(final_response.data["cache_read_tokens"], 31)
        self.assertEqual(final_response.data["title"], "Investigate the cache issue")

        child_started = next(
            event
            for event in events
            if event.data["kind"] == "session.started" and event.data["session_id"] == "root-session:agent-child-agent"
        )
        self.assertTrue(child_started.data["is_child"])
        self.assertEqual(child_started.data["root_session_id"], root_session_id)
        self.assertEqual(child_started.data["parent_session_id"], root_session_id)
        self.assertEqual(child_started.data["agent"], "child-agent")

        child_response = next(event for event in events if event.data.get("message_id") == "child-assistant-1")
        self.assertEqual(child_response.data["session_id"], "root-session:agent-child-agent")
        self.assertEqual(child_response.data["title"], "Check the child flow")

    def test_collect_payload_estimates_input_tokens_from_qoder_log(self) -> None:
        with TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            projects_dir = root_dir / "projects"
            projects_dir.mkdir()
            project_dir = projects_dir / "-Users-test-project"
            project_dir.mkdir()
            log_path = root_dir / "qodercli.log"

            root_session_id = "root-session"
            root_meta = {
                "id": root_session_id,
                "parent_session_id": "",
                "title": "Token estimate session",
                "working_dir": "/tmp/project",
                "created_at": _timestamp_ms("2026-04-02T10:00:00+08:00"),
                "updated_at": _timestamp_ms("2026-04-02T11:00:05+08:00"),
            }
            _write_json(project_dir / f"{root_session_id}-session.json", root_meta)

            root_rows = [
                {
                    "uuid": "row-user-1",
                    "parentUuid": "",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "main-agent",
                    "type": "user",
                    "timestamp": "2026-04-02T10:00:01+08:00",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Estimate tokens"}],
                        "id": "user-1",
                    },
                    "isMeta": False,
                },
                {
                    "uuid": "row-assistant-1",
                    "parentUuid": "user-1",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "main-agent",
                    "type": "assistant",
                    "timestamp": "2026-04-02T10:00:05+08:00",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Root answer"}],
                        "id": "assistant-1",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                    "isMeta": False,
                },
            ]
            _write_jsonl(project_dir / f"{root_session_id}.jsonl", root_rows)

            subagent_dir = project_dir / root_session_id / "subagents"
            subagent_dir.mkdir(parents=True)
            child_rows = [
                {
                    "uuid": "child-user-row",
                    "parentUuid": "",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "child-agent",
                    "type": "user",
                    "timestamp": "2026-04-02T11:00:00+08:00",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Child estimate"}],
                        "id": "child-user-1",
                    },
                    "isMeta": False,
                },
                {
                    "uuid": "child-assistant-row",
                    "parentUuid": "child-user-1",
                    "cwd": "/tmp/project",
                    "sessionId": root_session_id,
                    "agentId": "child-agent",
                    "type": "assistant",
                    "timestamp": "2026-04-02T11:00:03+08:00",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Child answer"}],
                        "id": "child-assistant-1",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                    "isMeta": False,
                },
            ]
            _write_jsonl(subagent_dir / "agent-child-agent.jsonl", child_rows)

            log_path.write_text(
                "\n".join(
                    [
                        (
                            "2026-04-02T10:00:04.980+0800\tINFO\tagent/compact.go:311\t"
                            f"current token usage rate: 7.50%, max input tokens: 180000, sessionId: {root_session_id}"
                        ),
                        (
                            "2026-04-02T10:00:06.500+0800\tINFO\tagent/compact.go:311\t"
                            f"current token usage rate: 1.00%, max input tokens: 180000, sessionId: {root_session_id}"
                        ),
                        (
                            "2026-04-02T11:00:02.950+0800\tINFO\tagent/compact.go:311\t"
                            f"current token usage rate: 9.00%, max input tokens: 180000, sessionId: {root_session_id}"
                        ),
                    ]
                )
                + "\n"
            )

            payload = collect_payload(
                host="test-host",
                target_date=date(2026, 4, 2),
                projects_dir=projects_dir,
                log_path=log_path,
            )
            events = payload.raw_events

        root_response = next(event for event in events if event.data.get("message_id") == "assistant-1")
        self.assertEqual(root_response.data["input_tokens"], 13500)
        self.assertTrue(root_response.data["usage_estimated"])
        self.assertEqual(root_response.data["usage_estimate_source"], "qodercli.log.current_token_usage_rate")
        self.assertEqual(root_response.data["usage_estimate_input_rate"], 7.5)
        self.assertEqual(root_response.data["usage_estimate_max_input_tokens"], 180000)

        child_response = next(event for event in events if event.data.get("message_id") == "child-assistant-1")
        self.assertEqual(child_response.data["input_tokens"], 16200)
        self.assertTrue(child_response.data["usage_estimated"])
        self.assertEqual(child_response.data["usage_estimate_input_rate"], 9.0)


if __name__ == "__main__":
    unittest.main()
