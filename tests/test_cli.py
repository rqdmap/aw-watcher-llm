from __future__ import annotations

import unittest

from aw_watcher_llm.cli import _normalize_argv


class NormalizeArgvTests(unittest.TestCase):
    def test_empty_argv_defaults_to_run(self) -> None:
        self.assertEqual(_normalize_argv([]), ["run"])

    def test_help_is_not_rewritten(self) -> None:
        self.assertEqual(_normalize_argv(["--help"]), ["--help"])

    def test_known_subcommand_is_kept(self) -> None:
        self.assertEqual(_normalize_argv(["codex-watch", "--iterations", "1"]), ["codex-watch", "--iterations", "1"])

    def test_qoder_subcommand_is_kept(self) -> None:
        self.assertEqual(_normalize_argv(["qoder-watch", "--iterations", "1"]), ["qoder-watch", "--iterations", "1"])

    def test_run_options_without_subcommand_are_supported(self) -> None:
        self.assertEqual(
            _normalize_argv(["--interval-seconds", "30", "--iterations", "1"]),
            ["run", "--interval-seconds", "30", "--iterations", "1"],
        )


if __name__ == "__main__":
    unittest.main()
