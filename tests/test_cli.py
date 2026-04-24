from __future__ import annotations

import unittest

from aw_watcher_llm.cli import _aggregate_qoder_day_items
from aw_watcher_llm.cli import _normalize_argv
from aw_watcher_llm.cli import _summarize_qoder_events
from aw_watcher_llm.schema import Event


class NormalizeArgvTests(unittest.TestCase):
    def test_empty_argv_defaults_to_run(self) -> None:
        self.assertEqual(_normalize_argv([]), ["run"])

    def test_help_is_not_rewritten(self) -> None:
        self.assertEqual(_normalize_argv(["--help"]), ["--help"])

    def test_known_subcommand_is_kept(self) -> None:
        self.assertEqual(_normalize_argv(["codex-watch", "--iterations", "1"]), ["codex-watch", "--iterations", "1"])

    def test_qoder_subcommand_is_kept(self) -> None:
        self.assertEqual(_normalize_argv(["qoder-watch", "--iterations", "1"]), ["qoder-watch", "--iterations", "1"])

    def test_qoder_stats_subcommand_is_kept(self) -> None:
        self.assertEqual(_normalize_argv(["qoder-stats", "--days", "7"]), ["qoder-stats", "--days", "7"])

    def test_run_options_without_subcommand_are_supported(self) -> None:
        self.assertEqual(
            _normalize_argv(["--interval-seconds", "30", "--iterations", "1"]),
            ["run", "--interval-seconds", "30", "--iterations", "1"],
        )


class QoderStatsSummaryTests(unittest.TestCase):
    def test_summarize_qoder_events_tracks_estimated_official_and_missing(self) -> None:
        events = [
            Event(
                timestamp="2026-04-24T00:00:00+00:00",
                duration=0.0,
                data={"kind": "session.started", "session_id": "s1"},
            ),
            Event(
                timestamp="2026-04-24T00:00:01+00:00",
                duration=1.0,
                data={
                    "kind": "response.completed",
                    "session_id": "s1",
                    "title": "one",
                    "input_tokens": 120,
                },
            ),
            Event(
                timestamp="2026-04-24T00:00:02+00:00",
                duration=1.0,
                data={
                    "kind": "response.completed",
                    "session_id": "s1",
                    "title": "one",
                    "input_tokens": 80,
                    "usage_estimated": True,
                },
            ),
            Event(
                timestamp="2026-04-24T00:00:03+00:00",
                duration=1.0,
                data={
                    "kind": "response.completed",
                    "session_id": "s1",
                    "title": "one",
                    "input_tokens": 0,
                },
            ),
            Event(
                timestamp="2026-04-24T00:00:04+00:00",
                duration=1.0,
                data={
                    "kind": "response.completed",
                    "session_id": "s2",
                    "title": "two",
                    "input_tokens": 40,
                    "usage_estimated": True,
                },
            ),
        ]

        summary, sessions = _summarize_qoder_events(events)

        self.assertEqual(summary["responses"], 4)
        self.assertEqual(summary["official_input_responses"], 1)
        self.assertEqual(summary["estimated_input_responses"], 2)
        self.assertEqual(summary["covered_input_responses"], 3)
        self.assertEqual(summary["missing_input_responses"], 1)
        self.assertEqual(summary["official_input_tokens"], 120)
        self.assertEqual(summary["estimated_input_tokens"], 120)
        self.assertEqual(summary["covered_input_tokens"], 240)
        self.assertEqual(summary["covered_input_ratio"], 0.75)
        self.assertEqual(summary["missing_input_ratio"], 0.25)
        self.assertEqual(summary["session_count"], 2)
        self.assertEqual(sessions["s1"]["missing_input_responses"], 1)
        self.assertEqual(sessions["s2"]["estimated_input_responses"], 1)

        aggregate = _aggregate_qoder_day_items(
            [
                {"responses": 2, "estimated_input_responses": 1, "official_input_responses": 1, "covered_input_responses": 2, "missing_input_responses": 0, "estimated_input_tokens": 80, "official_input_tokens": 120},
                {"responses": 2, "estimated_input_responses": 1, "official_input_responses": 0, "covered_input_responses": 1, "missing_input_responses": 1, "estimated_input_tokens": 40, "official_input_tokens": 0},
            ]
        )
        self.assertEqual(aggregate["responses"], 4)
        self.assertEqual(aggregate["estimated_input_responses"], 2)
        self.assertEqual(aggregate["official_input_responses"], 1)
        self.assertEqual(aggregate["covered_input_responses"], 3)
        self.assertEqual(aggregate["missing_input_responses"], 1)
        self.assertEqual(aggregate["estimated_input_ratio"], 0.5)
        self.assertEqual(aggregate["covered_input_ratio"], 0.75)
        self.assertEqual(aggregate["days_with_responses"], 2)


if __name__ == "__main__":
    unittest.main()
