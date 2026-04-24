from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from .activitywatch import ActivityWatchTransport
from .activitywatch import ActivityWatchError
from .activitywatch import local_day_bounds
from .buckets import RAW_SOURCES
from .buckets import default_host
from .buckets import raw_bucket_id
from .buckets import session_bucket_prefix
from .buckets import session_workspace_host
from .codex import collect_payload as collect_codex_payload
from .codex import find_sessions_dir as find_codex_sessions_dir
from .demo import build_demo_payload
from .opencode import collect_payload as collect_opencode_payload
from .opencode import collect_session_buckets as collect_opencode_session_buckets
from .opencode import find_db as find_opencode_db
from .qoder import collect_payload as collect_qoder_payload
from .qoder import find_projects_dir as find_qoder_projects_dir
from .runner import WATCHER_SOURCES
from .runner import RunConfig
from .runner import configure_logging
from .runner import run_service
from .visualization_server import serve_visualization

KNOWN_COMMANDS = {
    "run",
    "bucket-ids",
    "demo-json",
    "opencode-json",
    "codex-json",
    "qoder-json",
    "qoder-stats",
    "opencode-session-buckets-json",
    "opencode-push",
    "codex-push",
    "qoder-push",
    "opencode-session-buckets-push",
    "opencode-backfill",
    "codex-backfill",
    "qoder-backfill",
    "opencode-session-buckets-backfill",
    "opencode-watch",
    "codex-watch",
    "qoder-watch",
    "opencode-session-buckets-watch",
    "visualize-serve",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aw-watcher-llm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser(
        "run",
        help="run the long-lived watcher service (default when no subcommand is given)",
    )
    _add_host_argument(run)
    run.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=WATCHER_SOURCES,
        help="watch a source (repeat to watch multiple sources, defaults to all supported sources)",
    )
    run.add_argument("--db-path", type=Path)
    run.add_argument("--sessions-dir", type=Path)
    run.add_argument("--qoder-projects-dir", type=Path)
    run.add_argument("--aw-url", default="http://127.0.0.1:5600")
    run.add_argument("--timeout-seconds", type=float, default=10.0)
    run.add_argument("--interval-seconds", type=float, default=15.0)
    run.add_argument("--backfill-days", type=int, default=2)
    run.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    run.add_argument(
        "--enable-session-workspace",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="also push the optional OpenCode per-session workspace buckets",
    )
    _add_include_child_sessions_argument(run)
    run.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="stop after N polling cycles (0 means run forever)",
    )
    run.add_argument("--verbose", action="store_true", help="enable verbose watcher logs")

    bucket_ids = subparsers.add_parser("bucket-ids", help="print recommended bucket ids")
    _add_host_argument(bucket_ids)

    demo_json = subparsers.add_parser("demo-json", help="print demo payload as JSON")
    _add_host_argument(demo_json)
    demo_json.add_argument("--source", choices=RAW_SOURCES, default="opencode")
    demo_json.add_argument("--pretty", action="store_true")

    opencode_json = subparsers.add_parser(
        "opencode-json",
        help="read the local OpenCode SQLite database and print real events",
    )
    _add_host_argument(opencode_json)
    opencode_json.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    opencode_json.add_argument("--db-path", type=Path)
    opencode_json.add_argument("--pretty", action="store_true")

    codex_json = subparsers.add_parser(
        "codex-json",
        help="read local Codex rollout sessions and print real events",
    )
    _add_host_argument(codex_json)
    codex_json.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    codex_json.add_argument("--sessions-dir", type=Path)
    codex_json.add_argument("--pretty", action="store_true")

    qoder_json = subparsers.add_parser(
        "qoder-json",
        help="read local Qoder project transcripts and print real events",
    )
    _add_host_argument(qoder_json)
    qoder_json.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    qoder_json.add_argument("--projects-dir", type=Path)
    qoder_json.add_argument("--pretty", action="store_true")

    qoder_stats = subparsers.add_parser(
        "qoder-stats",
        help="summarize Qoder response coverage and log-based token estimation rates",
    )
    _add_host_argument(qoder_stats)
    qoder_stats.add_argument("--end-date", dest="target_date", type=_parse_date, default=date.today())
    qoder_stats.add_argument("--days", type=int, default=1)
    qoder_stats.add_argument("--projects-dir", type=Path)
    qoder_stats.add_argument("--pretty", action="store_true")

    opencode_session_buckets_json = subparsers.add_parser(
        "opencode-session-buckets-json",
        help="build the optional session-workspace projection for a day",
    )
    _add_host_argument(opencode_session_buckets_json)
    opencode_session_buckets_json.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    opencode_session_buckets_json.add_argument("--db-path", type=Path)
    _add_include_child_sessions_argument(opencode_session_buckets_json)
    opencode_session_buckets_json.add_argument("--pretty", action="store_true")

    opencode_push = subparsers.add_parser(
        "opencode-push",
        help="read real OpenCode events and push them into ActivityWatch",
    )
    _add_host_argument(opencode_push)
    opencode_push.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    opencode_push.add_argument("--db-path", type=Path)
    opencode_push.add_argument("--aw-url", default="http://127.0.0.1:5600")
    opencode_push.add_argument("--timeout-seconds", type=float, default=10.0)
    opencode_push.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_push.add_argument("--pretty", action="store_true")

    codex_push = subparsers.add_parser(
        "codex-push",
        help="read real Codex events and push them into ActivityWatch",
    )
    _add_host_argument(codex_push)
    codex_push.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    codex_push.add_argument("--sessions-dir", type=Path)
    codex_push.add_argument("--aw-url", default="http://127.0.0.1:5600")
    codex_push.add_argument("--timeout-seconds", type=float, default=10.0)
    codex_push.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    codex_push.add_argument("--pretty", action="store_true")

    qoder_push = subparsers.add_parser(
        "qoder-push",
        help="read real Qoder events and push them into ActivityWatch",
    )
    _add_host_argument(qoder_push)
    qoder_push.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    qoder_push.add_argument("--projects-dir", type=Path)
    qoder_push.add_argument("--aw-url", default="http://127.0.0.1:5600")
    qoder_push.add_argument("--timeout-seconds", type=float, default=10.0)
    qoder_push.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    qoder_push.add_argument("--pretty", action="store_true")

    opencode_session_buckets_push = subparsers.add_parser(
        "opencode-session-buckets-push",
        help="push the optional session-workspace projection into ActivityWatch",
    )
    _add_host_argument(opencode_session_buckets_push)
    opencode_session_buckets_push.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    opencode_session_buckets_push.add_argument("--db-path", type=Path)
    _add_include_child_sessions_argument(opencode_session_buckets_push)
    opencode_session_buckets_push.add_argument("--aw-url", default="http://127.0.0.1:5600")
    opencode_session_buckets_push.add_argument("--timeout-seconds", type=float, default=10.0)
    opencode_session_buckets_push.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_session_buckets_push.add_argument("--pretty", action="store_true")

    opencode_backfill = subparsers.add_parser(
        "opencode-backfill",
        help="push a rolling window of OpenCode raw events into ActivityWatch",
    )
    _add_host_argument(opencode_backfill)
    opencode_backfill.add_argument("--end-date", dest="target_date", type=_parse_date, default=date.today())
    opencode_backfill.add_argument("--days", type=int, default=30)
    opencode_backfill.add_argument("--db-path", type=Path)
    opencode_backfill.add_argument("--aw-url", default="http://127.0.0.1:5600")
    opencode_backfill.add_argument("--timeout-seconds", type=float, default=10.0)
    opencode_backfill.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_backfill.add_argument("--pretty", action="store_true")

    codex_backfill = subparsers.add_parser(
        "codex-backfill",
        help="push a rolling window of Codex raw events into ActivityWatch",
    )
    _add_host_argument(codex_backfill)
    codex_backfill.add_argument("--end-date", dest="target_date", type=_parse_date, default=date.today())
    codex_backfill.add_argument("--days", type=int, default=30)
    codex_backfill.add_argument("--sessions-dir", type=Path)
    codex_backfill.add_argument("--aw-url", default="http://127.0.0.1:5600")
    codex_backfill.add_argument("--timeout-seconds", type=float, default=10.0)
    codex_backfill.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    codex_backfill.add_argument("--pretty", action="store_true")

    qoder_backfill = subparsers.add_parser(
        "qoder-backfill",
        help="push a rolling window of Qoder raw events into ActivityWatch",
    )
    _add_host_argument(qoder_backfill)
    qoder_backfill.add_argument("--end-date", dest="target_date", type=_parse_date, default=date.today())
    qoder_backfill.add_argument("--days", type=int, default=30)
    qoder_backfill.add_argument("--projects-dir", type=Path)
    qoder_backfill.add_argument("--aw-url", default="http://127.0.0.1:5600")
    qoder_backfill.add_argument("--timeout-seconds", type=float, default=10.0)
    qoder_backfill.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    qoder_backfill.add_argument("--pretty", action="store_true")

    opencode_session_buckets_backfill = subparsers.add_parser(
        "opencode-session-buckets-backfill",
        help="backfill the optional session-workspace projection for a rolling window",
    )
    _add_host_argument(opencode_session_buckets_backfill)
    opencode_session_buckets_backfill.add_argument("--end-date", dest="target_date", type=_parse_date, default=date.today())
    opencode_session_buckets_backfill.add_argument("--days", type=int, default=30)
    opencode_session_buckets_backfill.add_argument("--db-path", type=Path)
    _add_include_child_sessions_argument(opencode_session_buckets_backfill)
    opencode_session_buckets_backfill.add_argument("--aw-url", default="http://127.0.0.1:5600")
    opencode_session_buckets_backfill.add_argument("--timeout-seconds", type=float, default=10.0)
    opencode_session_buckets_backfill.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_session_buckets_backfill.add_argument("--pretty", action="store_true")

    opencode_watch = subparsers.add_parser(
        "opencode-watch",
        help="run a polling watcher that refreshes today's OpenCode raw events",
    )
    _add_host_argument(opencode_watch)
    opencode_watch.add_argument("--db-path", type=Path)
    opencode_watch.add_argument("--aw-url", default="http://127.0.0.1:5600")
    opencode_watch.add_argument("--timeout-seconds", type=float, default=10.0)
    opencode_watch.add_argument("--interval-seconds", type=float, default=15.0)
    opencode_watch.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_watch.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="stop after N polling cycles (0 means run forever)",
    )
    opencode_watch.add_argument("--pretty", action="store_true")

    codex_watch = subparsers.add_parser(
        "codex-watch",
        help="run a polling watcher that refreshes today's Codex raw events",
    )
    _add_host_argument(codex_watch)
    codex_watch.add_argument("--sessions-dir", type=Path)
    codex_watch.add_argument("--aw-url", default="http://127.0.0.1:5600")
    codex_watch.add_argument("--timeout-seconds", type=float, default=10.0)
    codex_watch.add_argument("--interval-seconds", type=float, default=15.0)
    codex_watch.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    codex_watch.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="stop after N polling cycles (0 means run forever)",
    )
    codex_watch.add_argument("--pretty", action="store_true")

    qoder_watch = subparsers.add_parser(
        "qoder-watch",
        help="run a polling watcher that refreshes today's Qoder raw events",
    )
    _add_host_argument(qoder_watch)
    qoder_watch.add_argument("--projects-dir", type=Path)
    qoder_watch.add_argument("--aw-url", default="http://127.0.0.1:5600")
    qoder_watch.add_argument("--timeout-seconds", type=float, default=10.0)
    qoder_watch.add_argument("--interval-seconds", type=float, default=15.0)
    qoder_watch.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    qoder_watch.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="stop after N polling cycles (0 means run forever)",
    )
    qoder_watch.add_argument("--pretty", action="store_true")

    opencode_session_buckets_watch = subparsers.add_parser(
        "opencode-session-buckets-watch",
        help="poll and refresh the optional session-workspace projection for today's activity",
    )
    _add_host_argument(opencode_session_buckets_watch)
    opencode_session_buckets_watch.add_argument("--db-path", type=Path)
    _add_include_child_sessions_argument(opencode_session_buckets_watch)
    opencode_session_buckets_watch.add_argument("--aw-url", default="http://127.0.0.1:5600")
    opencode_session_buckets_watch.add_argument("--timeout-seconds", type=float, default=10.0)
    opencode_session_buckets_watch.add_argument("--interval-seconds", type=float, default=15.0)
    opencode_session_buckets_watch.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_session_buckets_watch.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="stop after N polling cycles (0 means run forever)",
    )
    opencode_session_buckets_watch.add_argument("--pretty", action="store_true")

    visualize_serve = subparsers.add_parser(
        "visualize-serve",
        help="serve the standalone visualization locally and proxy ActivityWatch API requests",
    )
    visualize_serve.add_argument("--aw-url", default="http://127.0.0.1:5600")
    visualize_serve.add_argument("--bind", default="127.0.0.1")
    visualize_serve.add_argument("--port", type=int, default=8787)
    visualize_serve.add_argument("--open", action="store_true", dest="open_browser")

    raw_argv = argv if argv is not None else sys.argv[1:]
    args = parser.parse_args(_normalize_argv(raw_argv))
    if args.command == "run":
        return _cmd_run(
            host=args.host,
            sources=tuple(args.sources or WATCHER_SOURCES),
            db_path=args.db_path,
            sessions_dir=args.sessions_dir,
            qoder_projects_dir=args.qoder_projects_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            backfill_days=args.backfill_days,
            replace_day=args.replace_day,
            enable_session_workspace=args.enable_session_workspace,
            include_child_sessions=args.include_child_sessions,
            iterations=args.iterations,
            verbose=args.verbose,
        )
    if args.command == "bucket-ids":
        return _cmd_bucket_ids(args.host)
    if args.command == "demo-json":
        return _cmd_demo_json(args.host, args.source, args.pretty)
    if args.command == "opencode-json":
        return _cmd_opencode_json(
            host=args.host,
            target_date=args.target_date,
            db_path=args.db_path,
            pretty=args.pretty,
        )
    if args.command == "codex-json":
        return _cmd_codex_json(
            host=args.host,
            target_date=args.target_date,
            sessions_dir=args.sessions_dir,
            pretty=args.pretty,
        )
    if args.command == "qoder-json":
        return _cmd_qoder_json(
            host=args.host,
            target_date=args.target_date,
            projects_dir=args.projects_dir,
            pretty=args.pretty,
        )
    if args.command == "qoder-stats":
        return _cmd_qoder_stats(
            host=args.host,
            target_date=args.target_date,
            days=args.days,
            projects_dir=args.projects_dir,
            pretty=args.pretty,
        )
    if args.command == "opencode-session-buckets-json":
        return _cmd_opencode_session_buckets_json(
            host=args.host,
            target_date=args.target_date,
            db_path=args.db_path,
            include_child_sessions=args.include_child_sessions,
            pretty=args.pretty,
        )
    if args.command == "opencode-push":
        return _cmd_opencode_push(
            host=args.host,
            target_date=args.target_date,
            db_path=args.db_path,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "codex-push":
        return _cmd_codex_push(
            host=args.host,
            target_date=args.target_date,
            sessions_dir=args.sessions_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "qoder-push":
        return _cmd_qoder_push(
            host=args.host,
            target_date=args.target_date,
            projects_dir=args.projects_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "opencode-session-buckets-push":
        return _cmd_opencode_session_buckets_push(
            host=args.host,
            target_date=args.target_date,
            db_path=args.db_path,
            include_child_sessions=args.include_child_sessions,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "opencode-backfill":
        return _cmd_opencode_backfill(
            host=args.host,
            target_date=args.target_date,
            days=args.days,
            db_path=args.db_path,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "codex-backfill":
        return _cmd_codex_backfill(
            host=args.host,
            target_date=args.target_date,
            days=args.days,
            sessions_dir=args.sessions_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "qoder-backfill":
        return _cmd_qoder_backfill(
            host=args.host,
            target_date=args.target_date,
            days=args.days,
            projects_dir=args.projects_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "opencode-session-buckets-backfill":
        return _cmd_opencode_session_buckets_backfill(
            host=args.host,
            target_date=args.target_date,
            days=args.days,
            db_path=args.db_path,
            include_child_sessions=args.include_child_sessions,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    if args.command == "opencode-watch":
        return _cmd_opencode_watch(
            host=args.host,
            db_path=args.db_path,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            replace_day=args.replace_day,
            iterations=args.iterations,
            pretty=args.pretty,
        )
    if args.command == "codex-watch":
        return _cmd_codex_watch(
            host=args.host,
            sessions_dir=args.sessions_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            replace_day=args.replace_day,
            iterations=args.iterations,
            pretty=args.pretty,
        )
    if args.command == "qoder-watch":
        return _cmd_qoder_watch(
            host=args.host,
            projects_dir=args.projects_dir,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            replace_day=args.replace_day,
            iterations=args.iterations,
            pretty=args.pretty,
        )
    if args.command == "opencode-session-buckets-watch":
        return _cmd_opencode_session_buckets_watch(
            host=args.host,
            db_path=args.db_path,
            include_child_sessions=args.include_child_sessions,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            replace_day=args.replace_day,
            iterations=args.iterations,
            pretty=args.pretty,
        )
    if args.command == "visualize-serve":
        return _cmd_visualize_serve(
            aw_url=args.aw_url,
            bind=args.bind,
            port=args.port,
            open_browser=args.open_browser,
        )
    raise AssertionError("unreachable")


def _add_host_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        default=None,
        help="hostname suffix for bucket ids (defaults to local host, or AW server host for push)",
    )


def _add_include_child_sessions_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include-child-sessions",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="include child/subagent sessions in the session workspace projection",
    )


def _cmd_run(
    *,
    host: str | None,
    sources: tuple[str, ...],
    db_path: Path | None,
    sessions_dir: Path | None,
    qoder_projects_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    interval_seconds: float,
    backfill_days: int,
    replace_day: bool,
    enable_session_workspace: bool,
    include_child_sessions: bool,
    iterations: int,
    verbose: bool,
) -> int:
    if interval_seconds <= 0:
        print("--interval-seconds must be positive", file=sys.stderr)
        return 1
    if backfill_days <= 0:
        print("--backfill-days must be positive", file=sys.stderr)
        return 1
    if iterations < 0:
        print("--iterations must be >= 0", file=sys.stderr)
        return 1
    sources = tuple(dict.fromkeys(sources))
    resolved_host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    configure_logging(verbose=verbose)
    return run_service(
        RunConfig(
            host=resolved_host,
            aw_url=aw_url,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            replace_day=replace_day,
            iterations=iterations,
            backfill_days=backfill_days,
            sources=sources,
            db_path=db_path,
            sessions_dir=sessions_dir,
            qoder_projects_dir=qoder_projects_dir,
            enable_session_workspace=enable_session_workspace,
            include_child_sessions=include_child_sessions,
        )
    )


def _cmd_bucket_ids(host: str) -> int:
    host = _resolve_local_host(host)
    workspace_host = session_workspace_host(host)
    payload = {
        "raw": {source: raw_bucket_id(source, host) for source in RAW_SOURCES},
        "session_workspace_host": workspace_host,
        "session_workspace_prefix": {
            source: session_bucket_prefix(source, workspace_host) for source in RAW_SOURCES
        },
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_demo_json(host: str, source: str, pretty: bool) -> int:
    host = _resolve_local_host(host)
    payload = build_demo_payload(host=host, source=source).to_dict()
    return _print_payload(payload, pretty=pretty)


def _cmd_opencode_json(
    *,
    host: str,
    target_date: date,
    db_path: Path | None,
    pretty: bool,
) -> int:
    host = _resolve_local_host(host)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    payload = collect_opencode_payload(
        host=host,
        target_date=target_date,
        db_path=resolved,
    ).to_dict()
    return _print_payload(payload, pretty=pretty)


def _cmd_codex_json(
    *,
    host: str,
    target_date: date,
    sessions_dir: Path | None,
    pretty: bool,
) -> int:
    host = _resolve_local_host(host)
    resolved = sessions_dir or find_codex_sessions_dir()
    if resolved is None:
        print("no Codex sessions directory found", file=sys.stderr)
        return 1
    payload = collect_codex_payload(
        host=host,
        target_date=target_date,
        sessions_dir=resolved,
    ).to_dict()
    return _print_payload(payload, pretty=pretty)


def _cmd_qoder_json(
    *,
    host: str,
    target_date: date,
    projects_dir: Path | None,
    pretty: bool,
) -> int:
    host = _resolve_local_host(host)
    resolved = projects_dir or find_qoder_projects_dir()
    if resolved is None:
        print("no Qoder projects directory found", file=sys.stderr)
        return 1
    payload = collect_qoder_payload(
        host=host,
        target_date=target_date,
        projects_dir=resolved,
    ).to_dict()
    return _print_payload(payload, pretty=pretty)


def _cmd_qoder_stats(
    *,
    host: str,
    target_date: date,
    days: int,
    projects_dir: Path | None,
    pretty: bool,
) -> int:
    if days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 1
    host = _resolve_local_host(host)
    resolved = projects_dir or find_qoder_projects_dir()
    if resolved is None:
        print("no Qoder projects directory found", file=sys.stderr)
        return 1

    items: list[dict[str, Any]] = []
    session_totals: dict[str, dict[str, Any]] = {}
    for offset in range(days - 1, -1, -1):
        day = target_date - timedelta(days=offset)
        payload = collect_qoder_payload(
            host=host,
            target_date=day,
            projects_dir=resolved,
        )
        day_summary, day_sessions = _summarize_qoder_events(payload.raw_events)
        items.append(
            {
                "date": day.isoformat(),
                **day_summary,
            }
        )
        _merge_qoder_session_totals(session_totals, day_sessions)

    summary = _aggregate_qoder_day_items(items)
    summary["session_count"] = len(session_totals)
    output: dict[str, Any] = {
        "host": host,
        "start_date": (target_date - timedelta(days=days - 1)).isoformat(),
        "end_date": target_date.isoformat(),
        "days": days,
        "projects_dir": str(resolved),
        "summary": summary,
        "items": items,
    }
    if session_totals:
        output["top_missing_sessions"] = sorted(
            session_totals.values(),
            key=lambda item: (
                -item["missing_input_responses"],
                -item["responses"],
                item["session_id"],
            ),
        )[:10]
    return _print_payload(output, pretty=pretty)


def _cmd_opencode_session_buckets_json(
    *,
    host: str,
    target_date: date,
    db_path: Path | None,
    include_child_sessions: bool,
    pretty: bool,
) -> int:
    host = _resolve_session_workspace_local_host(host)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    session_buckets = collect_opencode_session_buckets(
        host=host,
        target_date=target_date,
        db_path=resolved,
        include_child_sessions=include_child_sessions,
    )
    payload = {
        "host": host,
        "date": target_date.isoformat(),
        "db_path": str(resolved),
        "include_child_sessions": include_child_sessions,
        "session_bucket_count": len(session_buckets),
        "session_buckets": [item.to_dict() for item in session_buckets],
    }
    return _print_payload(payload, pretty=pretty)


def _cmd_opencode_push(
    *,
    host: str,
    target_date: date,
    db_path: Path | None,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    payload = collect_opencode_payload(
        host=host,
        target_date=target_date,
        db_path=resolved,
    )
    replace_start = None
    replace_end = None
    if replace_day:
        start, end = local_day_bounds(target_date)
        replace_start = start.isoformat()
        replace_end = end.isoformat()
    transport = ActivityWatchTransport(
        base_url=aw_url,
        timeout_seconds=timeout_seconds,
    )
    summary = transport.push_payload(
        payload,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    output: dict[str, Any] = {
        "aw_url": aw_url,
        "host": host,
        "date": target_date.isoformat(),
        "db_path": str(resolved),
        "replace_day": replace_day,
        "summary": summary.to_dict(),
        "raw_bucket": payload.raw_bucket.to_dict(),
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_codex_push(
    *,
    host: str,
    target_date: date,
    sessions_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = sessions_dir or find_codex_sessions_dir()
    if resolved is None:
        print("no Codex sessions directory found", file=sys.stderr)
        return 1
    payload = collect_codex_payload(
        host=host,
        target_date=target_date,
        sessions_dir=resolved,
    )
    replace_start = None
    replace_end = None
    if replace_day:
        start, end = local_day_bounds(target_date)
        replace_start = start.isoformat()
        replace_end = end.isoformat()
    transport = ActivityWatchTransport(
        base_url=aw_url,
        timeout_seconds=timeout_seconds,
    )
    summary = transport.push_payload(
        payload,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    output: dict[str, Any] = {
        "aw_url": aw_url,
        "host": host,
        "date": target_date.isoformat(),
        "sessions_dir": str(resolved),
        "replace_day": replace_day,
        "summary": summary.to_dict(),
        "raw_bucket": payload.raw_bucket.to_dict(),
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_qoder_push(
    *,
    host: str,
    target_date: date,
    projects_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = projects_dir or find_qoder_projects_dir()
    if resolved is None:
        print("no Qoder projects directory found", file=sys.stderr)
        return 1
    payload = collect_qoder_payload(
        host=host,
        target_date=target_date,
        projects_dir=resolved,
    )
    replace_start = None
    replace_end = None
    if replace_day:
        start, end = local_day_bounds(target_date)
        replace_start = start.isoformat()
        replace_end = end.isoformat()
    transport = ActivityWatchTransport(
        base_url=aw_url,
        timeout_seconds=timeout_seconds,
    )
    summary = transport.push_payload(
        payload,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    output: dict[str, Any] = {
        "aw_url": aw_url,
        "host": host,
        "date": target_date.isoformat(),
        "projects_dir": str(resolved),
        "replace_day": replace_day,
        "summary": summary.to_dict(),
        "raw_bucket": payload.raw_bucket.to_dict(),
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_opencode_session_buckets_push(
    *,
    host: str,
    target_date: date,
    db_path: Path | None,
    include_child_sessions: bool,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    host = _resolve_session_workspace_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    session_buckets = collect_opencode_session_buckets(
        host=host,
        target_date=target_date,
        db_path=resolved,
        include_child_sessions=include_child_sessions,
    )
    replace_start = None
    replace_end = None
    if replace_day:
        start, end = local_day_bounds(target_date)
        replace_start = start.isoformat()
        replace_end = end.isoformat()
    transport = ActivityWatchTransport(
        base_url=aw_url,
        timeout_seconds=timeout_seconds,
    )
    summary = transport.push_bucket_events_batch(
        session_buckets,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    output: dict[str, Any] = {
        "aw_url": aw_url,
        "host": host,
        "date": target_date.isoformat(),
        "db_path": str(resolved),
        "include_child_sessions": include_child_sessions,
        "replace_day": replace_day,
        "summary": summary.to_dict(),
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_opencode_backfill(
    *,
    host: str,
    target_date: date,
    days: int,
    db_path: Path | None,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    if days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 1
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    items: list[dict[str, Any]] = []
    total_inserted = 0
    total_deleted = 0
    for offset in range(days - 1, -1, -1):
        day = target_date - timedelta(days=offset)
        payload = collect_opencode_payload(host=host, target_date=day, db_path=resolved)
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(day)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_payload(
            payload,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        total_inserted += summary.raw_inserted
        total_deleted += summary.raw_deleted
        items.append(
            {
                "date": day.isoformat(),
                "raw_inserted": summary.raw_inserted,
                "raw_deleted": summary.raw_deleted,
            }
        )
    output = {
        "aw_url": aw_url,
        "host": host,
        "days": days,
        "end_date": target_date.isoformat(),
        "db_path": str(resolved),
        "replace_day": replace_day,
        "summary": {
            "raw_inserted": total_inserted,
            "raw_deleted": total_deleted,
        },
        "items": items,
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_codex_backfill(
    *,
    host: str,
    target_date: date,
    days: int,
    sessions_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    if days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 1
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = sessions_dir or find_codex_sessions_dir()
    if resolved is None:
        print("no Codex sessions directory found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    items: list[dict[str, Any]] = []
    total_inserted = 0
    total_deleted = 0
    for offset in range(days - 1, -1, -1):
        day = target_date - timedelta(days=offset)
        payload = collect_codex_payload(host=host, target_date=day, sessions_dir=resolved)
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(day)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_payload(
            payload,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        total_inserted += summary.raw_inserted
        total_deleted += summary.raw_deleted
        items.append(
            {
                "date": day.isoformat(),
                "raw_inserted": summary.raw_inserted,
                "raw_deleted": summary.raw_deleted,
            }
        )
    output = {
        "aw_url": aw_url,
        "host": host,
        "days": days,
        "end_date": target_date.isoformat(),
        "sessions_dir": str(resolved),
        "replace_day": replace_day,
        "summary": {
            "raw_inserted": total_inserted,
            "raw_deleted": total_deleted,
        },
        "items": items,
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_qoder_backfill(
    *,
    host: str,
    target_date: date,
    days: int,
    projects_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    if days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 1
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = projects_dir or find_qoder_projects_dir()
    if resolved is None:
        print("no Qoder projects directory found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    items: list[dict[str, Any]] = []
    total_inserted = 0
    total_deleted = 0
    for offset in range(days - 1, -1, -1):
        day = target_date - timedelta(days=offset)
        payload = collect_qoder_payload(host=host, target_date=day, projects_dir=resolved)
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(day)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_payload(
            payload,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        total_inserted += summary.raw_inserted
        total_deleted += summary.raw_deleted
        items.append(
            {
                "date": day.isoformat(),
                "raw_inserted": summary.raw_inserted,
                "raw_deleted": summary.raw_deleted,
            }
        )
    output = {
        "aw_url": aw_url,
        "host": host,
        "days": days,
        "end_date": target_date.isoformat(),
        "projects_dir": str(resolved),
        "replace_day": replace_day,
        "summary": {
            "raw_inserted": total_inserted,
            "raw_deleted": total_deleted,
        },
        "items": items,
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_opencode_session_buckets_backfill(
    *,
    host: str,
    target_date: date,
    days: int,
    db_path: Path | None,
    include_child_sessions: bool,
    aw_url: str,
    timeout_seconds: float,
    replace_day: bool,
    pretty: bool,
) -> int:
    if days <= 0:
        print("--days must be positive", file=sys.stderr)
        return 1
    host = _resolve_session_workspace_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    items: list[dict[str, Any]] = []
    total_buckets = 0
    total_inserted = 0
    total_deleted = 0
    for offset in range(days - 1, -1, -1):
        day = target_date - timedelta(days=offset)
        session_buckets = collect_opencode_session_buckets(
            host=host,
            target_date=day,
            db_path=resolved,
            include_child_sessions=include_child_sessions,
        )
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(day)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_bucket_events_batch(
            session_buckets,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        total_buckets += summary.bucket_count
        total_inserted += summary.events_inserted
        total_deleted += summary.events_deleted
        items.append(
            {
                "date": day.isoformat(),
                "bucket_count": summary.bucket_count,
                "events_inserted": summary.events_inserted,
                "events_deleted": summary.events_deleted,
            }
        )
    output = {
        "aw_url": aw_url,
        "host": host,
        "days": days,
        "end_date": target_date.isoformat(),
        "db_path": str(resolved),
        "include_child_sessions": include_child_sessions,
        "replace_day": replace_day,
        "summary": {
            "bucket_count": total_buckets,
            "events_inserted": total_inserted,
            "events_deleted": total_deleted,
        },
        "items": items,
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _cmd_opencode_watch(
    *,
    host: str,
    db_path: Path | None,
    aw_url: str,
    timeout_seconds: float,
    interval_seconds: float,
    replace_day: bool,
    iterations: int,
    pretty: bool,
) -> int:
    if interval_seconds <= 0:
        print("--interval-seconds must be positive", file=sys.stderr)
        return 1
    if iterations < 0:
        print("--iterations must be >= 0", file=sys.stderr)
        return 1
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    count = 0
    while True:
        now = datetime.now().astimezone()
        target_date = now.date()
        payload = collect_opencode_payload(host=host, target_date=target_date, db_path=resolved)
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(target_date)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_payload(
            payload,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        output = {
            "timestamp": now.isoformat(),
            "host": host,
            "date": target_date.isoformat(),
            "db_path": str(resolved),
            "replace_day": replace_day,
            "summary": summary.to_dict(),
            "raw_bucket": payload.raw_bucket.to_dict(),
        }
        if pretty:
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(output, ensure_ascii=False))
        count += 1
        if iterations and count >= iterations:
            return 0
        try:
            import time as _time

            _time.sleep(interval_seconds)
        except KeyboardInterrupt:
            return 0


def _cmd_codex_watch(
    *,
    host: str,
    sessions_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    interval_seconds: float,
    replace_day: bool,
    iterations: int,
    pretty: bool,
) -> int:
    if interval_seconds <= 0:
        print("--interval-seconds must be positive", file=sys.stderr)
        return 1
    if iterations < 0:
        print("--iterations must be >= 0", file=sys.stderr)
        return 1
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = sessions_dir or find_codex_sessions_dir()
    if resolved is None:
        print("no Codex sessions directory found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    count = 0
    while True:
        now = datetime.now().astimezone()
        target_date = now.date()
        payload = collect_codex_payload(host=host, target_date=target_date, sessions_dir=resolved)
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(target_date)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_payload(
            payload,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        output = {
            "timestamp": now.isoformat(),
            "host": host,
            "date": target_date.isoformat(),
            "sessions_dir": str(resolved),
            "replace_day": replace_day,
            "summary": summary.to_dict(),
            "raw_bucket": payload.raw_bucket.to_dict(),
        }
        if pretty:
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(output, ensure_ascii=False))
        count += 1
        if iterations and count >= iterations:
            return 0
        try:
            import time as _time

            _time.sleep(interval_seconds)
        except KeyboardInterrupt:
            return 0


def _cmd_qoder_watch(
    *,
    host: str,
    projects_dir: Path | None,
    aw_url: str,
    timeout_seconds: float,
    interval_seconds: float,
    replace_day: bool,
    iterations: int,
    pretty: bool,
) -> int:
    if interval_seconds <= 0:
        print("--interval-seconds must be positive", file=sys.stderr)
        return 1
    if iterations < 0:
        print("--iterations must be >= 0", file=sys.stderr)
        return 1
    host = _resolve_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = projects_dir or find_qoder_projects_dir()
    if resolved is None:
        print("no Qoder projects directory found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    count = 0
    while True:
        now = datetime.now().astimezone()
        target_date = now.date()
        payload = collect_qoder_payload(host=host, target_date=target_date, projects_dir=resolved)
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(target_date)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_payload(
            payload,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        output = {
            "timestamp": now.isoformat(),
            "host": host,
            "date": target_date.isoformat(),
            "projects_dir": str(resolved),
            "replace_day": replace_day,
            "summary": summary.to_dict(),
            "raw_bucket": payload.raw_bucket.to_dict(),
        }
        if pretty:
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(output, ensure_ascii=False))
        count += 1
        if iterations and count >= iterations:
            return 0
        try:
            import time as _time

            _time.sleep(interval_seconds)
        except KeyboardInterrupt:
            return 0


def _cmd_opencode_session_buckets_watch(
    *,
    host: str,
    db_path: Path | None,
    include_child_sessions: bool,
    aw_url: str,
    timeout_seconds: float,
    interval_seconds: float,
    replace_day: bool,
    iterations: int,
    pretty: bool,
) -> int:
    if interval_seconds <= 0:
        print("--interval-seconds must be positive", file=sys.stderr)
        return 1
    if iterations < 0:
        print("--iterations must be >= 0", file=sys.stderr)
        return 1
    host = _resolve_session_workspace_push_host(host, aw_url=aw_url, timeout_seconds=timeout_seconds)
    resolved = db_path or find_opencode_db()
    if resolved is None:
        print("no OpenCode database found", file=sys.stderr)
        return 1
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    count = 0
    while True:
        now = datetime.now().astimezone()
        target_date = now.date()
        session_buckets = collect_opencode_session_buckets(
            host=host,
            target_date=target_date,
            db_path=resolved,
            include_child_sessions=include_child_sessions,
        )
        replace_start = None
        replace_end = None
        if replace_day:
            start, end = local_day_bounds(target_date)
            replace_start = start.isoformat()
            replace_end = end.isoformat()
        summary = transport.push_bucket_events_batch(
            session_buckets,
            replace_start=replace_start,
            replace_end=replace_end,
        )
        output = {
            "timestamp": now.isoformat(),
            "host": host,
            "date": target_date.isoformat(),
            "db_path": str(resolved),
            "include_child_sessions": include_child_sessions,
            "replace_day": replace_day,
            "summary": summary.to_dict(),
        }
        if pretty:
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(output, ensure_ascii=False))
        count += 1
        if iterations and count >= iterations:
            return 0
        try:
            import time as _time

            _time.sleep(interval_seconds)
        except KeyboardInterrupt:
            return 0


def _summarize_qoder_events(events: list[Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    session_totals: dict[str, dict[str, Any]] = {}
    responses = 0
    estimated_input_responses = 0
    official_input_responses = 0
    covered_input_responses = 0
    missing_input_responses = 0
    estimated_input_tokens = 0
    official_input_tokens = 0

    for event in events:
        data = getattr(event, "data", None)
        if not isinstance(data, dict) or data.get("kind") != "response.completed":
            continue
        responses += 1
        session_id = str(data.get("session_id") or "")
        title = str(data.get("title") or "")
        input_tokens = _qoder_stats_int(data.get("input_tokens"))
        estimated = bool(data.get("usage_estimated"))
        covered = input_tokens > 0
        official = covered and not estimated
        missing = not covered

        session_row = session_totals.setdefault(
            session_id,
            {
                "session_id": session_id,
                "title": title,
                "responses": 0,
                "estimated_input_responses": 0,
                "official_input_responses": 0,
                "covered_input_responses": 0,
                "missing_input_responses": 0,
            },
        )
        session_row["responses"] += 1

        if estimated:
            estimated_input_responses += 1
            estimated_input_tokens += input_tokens
            session_row["estimated_input_responses"] += 1
        if official:
            official_input_responses += 1
            official_input_tokens += input_tokens
            session_row["official_input_responses"] += 1
        if covered:
            covered_input_responses += 1
            session_row["covered_input_responses"] += 1
        if missing:
            missing_input_responses += 1
            session_row["missing_input_responses"] += 1

    return (
        {
            "responses": responses,
            "estimated_input_responses": estimated_input_responses,
            "official_input_responses": official_input_responses,
            "covered_input_responses": covered_input_responses,
            "missing_input_responses": missing_input_responses,
            "estimated_input_tokens": estimated_input_tokens,
            "official_input_tokens": official_input_tokens,
            "covered_input_tokens": estimated_input_tokens + official_input_tokens,
            "estimated_input_ratio": _ratio(estimated_input_responses, responses),
            "covered_input_ratio": _ratio(covered_input_responses, responses),
            "missing_input_ratio": _ratio(missing_input_responses, responses),
            "session_count": len(session_totals),
        },
        session_totals,
    )


def _merge_qoder_session_totals(
    totals: dict[str, dict[str, Any]],
    day_sessions: dict[str, dict[str, Any]],
) -> None:
    for session_id, item in day_sessions.items():
        row = totals.setdefault(
            session_id,
            {
                "session_id": session_id,
                "title": item["title"],
                "responses": 0,
                "estimated_input_responses": 0,
                "official_input_responses": 0,
                "covered_input_responses": 0,
                "missing_input_responses": 0,
            },
        )
        if not row["title"] and item["title"]:
            row["title"] = item["title"]
        row["responses"] += item["responses"]
        row["estimated_input_responses"] += item["estimated_input_responses"]
        row["official_input_responses"] += item["official_input_responses"]
        row["covered_input_responses"] += item["covered_input_responses"]
        row["missing_input_responses"] += item["missing_input_responses"]


def _aggregate_qoder_day_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    responses = sum(_qoder_stats_int(item.get("responses")) for item in items)
    estimated_input_responses = sum(_qoder_stats_int(item.get("estimated_input_responses")) for item in items)
    official_input_responses = sum(_qoder_stats_int(item.get("official_input_responses")) for item in items)
    covered_input_responses = sum(_qoder_stats_int(item.get("covered_input_responses")) for item in items)
    missing_input_responses = sum(_qoder_stats_int(item.get("missing_input_responses")) for item in items)
    estimated_input_tokens = sum(_qoder_stats_int(item.get("estimated_input_tokens")) for item in items)
    official_input_tokens = sum(_qoder_stats_int(item.get("official_input_tokens")) for item in items)
    return {
        "responses": responses,
        "estimated_input_responses": estimated_input_responses,
        "official_input_responses": official_input_responses,
        "covered_input_responses": covered_input_responses,
        "missing_input_responses": missing_input_responses,
        "estimated_input_tokens": estimated_input_tokens,
        "official_input_tokens": official_input_tokens,
        "covered_input_tokens": estimated_input_tokens + official_input_tokens,
        "estimated_input_ratio": _ratio(estimated_input_responses, responses),
        "covered_input_ratio": _ratio(covered_input_responses, responses),
        "missing_input_ratio": _ratio(missing_input_responses, responses),
        "days_with_responses": sum(1 for item in items if _qoder_stats_int(item.get("responses")) > 0),
    }


def _qoder_stats_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _print_payload(payload: dict[str, Any], *, pretty: bool) -> int:
    output = payload
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["run"]
    if argv[0] in {"-h", "--help"}:
        return argv
    if argv[0] in KNOWN_COMMANDS:
        return argv
    return ["run", *argv]


def _resolve_local_host(host: str | None) -> str:
    return host or default_host()


def _resolve_push_host(host: str | None, *, aw_url: str, timeout_seconds: float) -> str:
    if host:
        return host
    transport = ActivityWatchTransport(base_url=aw_url, timeout_seconds=timeout_seconds)
    try:
        info = transport.get_info()
    except ActivityWatchError:
        return default_host()
    resolved = info.get("hostname")
    if isinstance(resolved, str) and resolved:
        return resolved
    return default_host()


def _resolve_session_workspace_local_host(host: str | None) -> str:
    if host:
        return host
    return session_workspace_host(default_host())


def _resolve_session_workspace_push_host(host: str | None, *, aw_url: str, timeout_seconds: float) -> str:
    if host:
        return host
    base_host = _resolve_push_host(None, aw_url=aw_url, timeout_seconds=timeout_seconds)
    return session_workspace_host(base_host)


def _cmd_visualize_serve(
    *,
    aw_url: str,
    bind: str,
    port: int,
    open_browser: bool,
) -> int:
    if port <= 0 or port > 65535:
        print("--port must be between 1 and 65535", file=sys.stderr)
        return 1
    if not aw_url:
        print("--aw-url must not be empty", file=sys.stderr)
        return 1
    viewer_url = f"http://{bind}:{port}/"
    print(
        json.dumps(
            {
                "viewer_url": viewer_url,
                "aw_url": aw_url,
                "bind": bind,
                "port": port,
            },
            ensure_ascii=False,
        )
    )
    if open_browser:
        import webbrowser

        webbrowser.open(viewer_url)
    serve_visualization(bind=bind, port=port, aw_url=aw_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
