from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from .activitywatch import ActivityWatchTransport
from .activitywatch import ActivityWatchError
from .activitywatch import local_day_bounds
from .buckets import RAW_SOURCES
from .buckets import default_host
from .buckets import focus_bucket_id
from .buckets import raw_bucket_id
from .demo import build_demo_payload
from .opencode import collect_payload as collect_opencode_payload
from .opencode import find_db as find_opencode_db


def main() -> int:
    parser = argparse.ArgumentParser(prog="aw-watcher-llm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bucket_ids = subparsers.add_parser("bucket-ids", help="print recommended bucket ids")
    _add_host_argument(bucket_ids)

    demo_json = subparsers.add_parser("demo-json", help="print demo payload as JSON")
    _add_host_argument(demo_json)
    demo_json.add_argument("--source", choices=RAW_SOURCES, default="opencode")
    demo_json.add_argument(
        "--only",
        choices=("all", "raw", "display"),
        default="all",
        help="limit output to one layer",
    )
    demo_json.add_argument("--pretty", action="store_true")

    opencode_json = subparsers.add_parser(
        "opencode-json",
        help="read the local OpenCode SQLite database and print real events",
    )
    _add_host_argument(opencode_json)
    opencode_json.add_argument("--date", dest="target_date", type=_parse_date, default=date.today())
    opencode_json.add_argument("--db-path", type=Path)
    opencode_json.add_argument(
        "--only",
        choices=("all", "raw", "display"),
        default="all",
        help="limit output to one layer",
    )
    opencode_json.add_argument("--pretty", action="store_true")

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
        "--only",
        choices=("all", "raw", "display"),
        default="all",
        help="limit upload to one layer",
    )
    opencode_push.add_argument(
        "--replace-day",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="replace only the same local-day window before inserting",
    )
    opencode_push.add_argument("--pretty", action="store_true")

    args = parser.parse_args()
    if args.command == "bucket-ids":
        return _cmd_bucket_ids(args.host)
    if args.command == "demo-json":
        return _cmd_demo_json(args.host, args.source, args.only, args.pretty)
    if args.command == "opencode-json":
        return _cmd_opencode_json(
            host=args.host,
            target_date=args.target_date,
            db_path=args.db_path,
            only=args.only,
            pretty=args.pretty,
        )
    if args.command == "opencode-push":
        return _cmd_opencode_push(
            host=args.host,
            target_date=args.target_date,
            db_path=args.db_path,
            aw_url=args.aw_url,
            timeout_seconds=args.timeout_seconds,
            only=args.only,
            replace_day=args.replace_day,
            pretty=args.pretty,
        )
    raise AssertionError("unreachable")


def _add_host_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        default=None,
        help="hostname suffix for bucket ids (defaults to local host, or AW server host for push)",
    )


def _cmd_bucket_ids(host: str) -> int:
    host = _resolve_local_host(host)
    payload = {
        "raw": {source: raw_bucket_id(source, host) for source in RAW_SOURCES},
        "display": focus_bucket_id(host),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_demo_json(host: str, source: str, only: str, pretty: bool) -> int:
    host = _resolve_local_host(host)
    payload = build_demo_payload(host=host, source=source).to_dict()
    return _print_payload(payload, only=only, pretty=pretty)


def _cmd_opencode_json(
    *,
    host: str,
    target_date: date,
    db_path: Path | None,
    only: str,
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
    return _print_payload(payload, only=only, pretty=pretty)


def _cmd_opencode_push(
    *,
    host: str,
    target_date: date,
    db_path: Path | None,
    aw_url: str,
    timeout_seconds: float,
    only: str,
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
        only=only,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    output: dict[str, Any] = {
        "aw_url": aw_url,
        "host": host,
        "date": target_date.isoformat(),
        "db_path": str(resolved),
        "only": only,
        "replace_day": replace_day,
        "summary": summary.to_dict(),
        "raw_bucket": payload.raw_bucket.to_dict(),
        "display_bucket": payload.display_bucket.to_dict(),
    }
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _print_payload(payload: dict[str, Any], *, only: str, pretty: bool) -> int:
    if only == "raw":
        output: dict[str, Any] = {
            "raw_bucket": payload["raw_bucket"],
            "raw_events": payload["raw_events"],
        }
    elif only == "display":
        output = {
            "display_bucket": payload["display_bucket"],
            "display_events": payload["display_events"],
        }
    else:
        output = payload
    if pretty:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


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


if __name__ == "__main__":
    raise SystemExit(main())
