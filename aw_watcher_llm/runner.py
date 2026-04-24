from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from .activitywatch import ActivityWatchError
from .activitywatch import ActivityWatchTransport
from .activitywatch import local_day_bounds
from .buckets import session_workspace_host
from .codex import collect_payload as collect_codex_payload
from .codex import find_sessions_dir as find_codex_sessions_dir
from .opencode import collect_payload as collect_opencode_payload
from .opencode import collect_session_buckets as collect_opencode_session_buckets
from .opencode import find_db as find_opencode_db
from .qoder import collect_payload as collect_qoder_payload
from .qoder import find_projects_dir as find_qoder_projects_dir

WATCHER_SOURCES = ("opencode", "codex", "qoder")
LOGGER = logging.getLogger("aw-watcher-llm")


@dataclass(frozen=True)
class RunConfig:
    host: str
    aw_url: str = "http://127.0.0.1:5600"
    timeout_seconds: float = 10.0
    interval_seconds: float = 15.0
    replace_day: bool = True
    iterations: int = 0
    backfill_days: int = 2
    sources: tuple[str, ...] = WATCHER_SOURCES
    db_path: Path | None = None
    sessions_dir: Path | None = None
    qoder_projects_dir: Path | None = None
    enable_session_workspace: bool = False
    include_child_sessions: bool = False


class SourceUnavailable(RuntimeError):
    pass


def configure_logging(*, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def run_service(config: RunConfig) -> int:
    transport = ActivityWatchTransport(
        base_url=config.aw_url,
        timeout_seconds=config.timeout_seconds,
    )
    LOGGER.info(
        "starting watcher host=%s sources=%s interval=%.1fs backfill_days=%d session_workspace=%s",
        config.host,
        ",".join(config.sources),
        config.interval_seconds,
        config.backfill_days,
        config.enable_session_workspace,
    )

    warned_missing: set[str] = set()
    successful_syncs = 0
    count = 0

    while True:
        cycle_now = datetime.now().astimezone()
        cycle_dates = _cycle_dates(cycle_now.date(), config.backfill_days)
        cycle_messages: list[str] = []

        for target_date in cycle_dates:
            for source in config.sources:
                try:
                    if source == "opencode":
                        cycle_messages.extend(
                            _sync_opencode_day(
                                transport=transport,
                                host=config.host,
                                workspace_host=session_workspace_host(config.host),
                                target_date=target_date,
                                db_path=config.db_path,
                                replace_day=config.replace_day,
                                enable_session_workspace=config.enable_session_workspace,
                                include_child_sessions=config.include_child_sessions,
                            )
                        )
                    elif source == "codex":
                        cycle_messages.append(
                            _sync_codex_day(
                                transport=transport,
                                host=config.host,
                                target_date=target_date,
                                sessions_dir=config.sessions_dir,
                                replace_day=config.replace_day,
                            )
                        )
                    elif source == "qoder":
                        cycle_messages.append(
                            _sync_qoder_day(
                                transport=transport,
                                host=config.host,
                                target_date=target_date,
                                projects_dir=config.qoder_projects_dir,
                                replace_day=config.replace_day,
                            )
                        )
                    else:
                        raise ValueError(f"unsupported watcher source: {source}")
                except SourceUnavailable as exc:
                    warning_key = f"{source}:{exc}"
                    if warning_key not in warned_missing:
                        LOGGER.warning("%s; watcher will keep retrying", exc)
                        warned_missing.add(warning_key)
                except ActivityWatchError as exc:
                    LOGGER.warning(
                        "sync failed for source=%s date=%s: %s",
                        source,
                        target_date.isoformat(),
                        exc,
                    )
                except KeyboardInterrupt:
                    LOGGER.info("stopping watcher")
                    return 0 if successful_syncs else 1
                except Exception:
                    LOGGER.exception(
                        "unexpected failure for source=%s date=%s",
                        source,
                        target_date.isoformat(),
                    )

        if cycle_messages:
            successful_syncs += len(cycle_messages)
            LOGGER.info("cycle complete %s", " | ".join(cycle_messages))

        count += 1
        if config.iterations and count >= config.iterations:
            return 0 if successful_syncs else 1
        try:
            time.sleep(config.interval_seconds)
        except KeyboardInterrupt:
            LOGGER.info("stopping watcher")
            return 0 if successful_syncs else 1


def _cycle_dates(target_date: date, backfill_days: int) -> tuple[date, ...]:
    return tuple(target_date - timedelta(days=offset) for offset in range(backfill_days - 1, -1, -1))


def _sync_opencode_day(
    *,
    transport: ActivityWatchTransport,
    host: str,
    workspace_host: str,
    target_date: date,
    db_path: Path | None,
    replace_day: bool,
    enable_session_workspace: bool,
    include_child_sessions: bool,
) -> list[str]:
    resolved = db_path or find_opencode_db()
    if resolved is None:
        raise SourceUnavailable("OpenCode database not found")

    replace_start, replace_end = _replace_range(target_date, replace_day)
    payload = collect_opencode_payload(
        host=host,
        target_date=target_date,
        db_path=resolved,
    )
    summary = transport.push_payload(
        payload,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    messages = [
        (
            f"opencode@{target_date.isoformat()} "
            f"raw={summary.raw_inserted}/{summary.raw_deleted}"
        )
    ]

    if not enable_session_workspace:
        return messages

    session_buckets = collect_opencode_session_buckets(
        host=workspace_host,
        target_date=target_date,
        db_path=resolved,
        include_child_sessions=include_child_sessions,
    )
    workspace_summary = transport.push_bucket_events_batch(
        session_buckets,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    messages.append(
        (
            f"opencode-workspace@{target_date.isoformat()} "
            f"buckets={workspace_summary.bucket_count} "
            f"events={workspace_summary.events_inserted}/{workspace_summary.events_deleted}"
        )
    )
    return messages


def _sync_codex_day(
    *,
    transport: ActivityWatchTransport,
    host: str,
    target_date: date,
    sessions_dir: Path | None,
    replace_day: bool,
) -> str:
    resolved = sessions_dir or find_codex_sessions_dir()
    if resolved is None:
        raise SourceUnavailable("Codex sessions directory not found")

    replace_start, replace_end = _replace_range(target_date, replace_day)
    payload = collect_codex_payload(
        host=host,
        target_date=target_date,
        sessions_dir=resolved,
    )
    summary = transport.push_payload(
        payload,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    return f"codex@{target_date.isoformat()} raw={summary.raw_inserted}/{summary.raw_deleted}"


def _sync_qoder_day(
    *,
    transport: ActivityWatchTransport,
    host: str,
    target_date: date,
    projects_dir: Path | None,
    replace_day: bool,
) -> str:
    resolved = projects_dir or find_qoder_projects_dir()
    if resolved is None:
        raise SourceUnavailable("Qoder projects directory not found")

    replace_start, replace_end = _replace_range(target_date, replace_day)
    payload = collect_qoder_payload(
        host=host,
        target_date=target_date,
        projects_dir=resolved,
    )
    summary = transport.push_payload(
        payload,
        replace_start=replace_start,
        replace_end=replace_end,
    )
    return f"qoder@{target_date.isoformat()} raw={summary.raw_inserted}/{summary.raw_deleted}"


def _replace_range(target_date: date, replace_day: bool) -> tuple[str | None, str | None]:
    if not replace_day:
        return None, None
    start, end = local_day_bounds(target_date)
    return start.isoformat(), end.isoformat()
