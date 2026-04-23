from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any

from .buckets import RAW_BUCKET_TYPE
from .buckets import SESSION_BUCKET_TYPE
from .buckets import raw_bucket_id
from .buckets import session_bucket_id
from .schema import BucketEvents
from .schema import BucketSpec
from .schema import Event
from .schema import WatcherPayload

DEFAULT_GAP_MINUTES = 10
MIN_WORKSPACE_EVENT_DURATION_MS = 1000
MAX_FALLBACK_RESPONSE_DURATION_MS = 60 * 60 * 1000
CLIENT_NAME = "aw-watcher-llm"
SOURCE_NAME = "opencode"
TOOL_NAME = "OpenCode"
OPENCODE_DIR = Path.home() / ".local" / "share" / "opencode"
OPENCODE_PRIMARY_DB = OPENCODE_DIR / "opencode.db"


@dataclass(frozen=True)
class _RawMessage:
    message_id: str
    raw_session_id: str
    parent_session_id: str | None
    session_created_ms: int
    session_title: str | None
    session_directory: str | None
    is_child: bool
    time_created_ms: int
    time_ended_ms: int
    role: str | None
    model_id: str | None
    provider_id: str | None
    agent: str | None
    tokens: dict[str, int]
    cost: float | None

    @property
    def tokens_total(self) -> int:
        return (
            self.tokens["input"]
            + self.tokens["output"]
            + self.tokens["reasoning"]
            + self.tokens["cache_read"]
            + self.tokens["cache_write"]
        )


@dataclass
class _SessionState:
    raw_session_id: str
    parent_session_id: str | None
    root_session_id: str
    is_child: bool
    created_ms: int
    title: str
    project: str | None
    focus_label: str
    model_id: str | None
    provider_id: str | None
    agent: str | None
    messages: list[_RawMessage]


def find_db() -> Path | None:
    if not OPENCODE_PRIMARY_DB.exists():
        return None
    if _can_read(OPENCODE_PRIMARY_DB):
        return OPENCODE_PRIMARY_DB
    return OPENCODE_PRIMARY_DB


def collect_payload(
    *,
    host: str,
    target_date: date,
    db_path: Path | None = None,
) -> WatcherPayload:
    resolved = db_path or find_db()
    if resolved is None:
        raise FileNotFoundError("no OpenCode database found")

    raw_bucket = BucketSpec(
        id=raw_bucket_id(SOURCE_NAME, host),
        type=RAW_BUCKET_TYPE,
        client=CLIENT_NAME,
        hostname=host,
        name="LLM raw events (opencode)",
    )

    sessions, start_ms, end_ms, session_max_created = _load_sessions_for_date(
        resolved,
        target_date=target_date,
    )
    raw_events = _build_raw_events(
        sessions=sessions,
        start_ms=start_ms,
        end_ms=end_ms,
        session_max_created=session_max_created,
    )
    return WatcherPayload(
        raw_bucket=raw_bucket,
        raw_events=raw_events,
    )


def collect_session_buckets(
    *,
    host: str,
    target_date: date,
    db_path: Path | None = None,
    include_child_sessions: bool = False,
) -> list[BucketEvents]:
    resolved = db_path or find_db()
    if resolved is None:
        raise FileNotFoundError("no OpenCode database found")
    sessions, start_ms, end_ms, session_max_created = _load_sessions_for_date(
        resolved,
        target_date=target_date,
    )
    if not include_child_sessions:
        sessions = [session for session in sessions if not session.is_child]
    return _build_session_bucket_payloads(
        host=host,
        sessions=sessions,
        start_ms=start_ms,
        end_ms=end_ms,
        session_max_created=session_max_created,
    )


def _load_sessions_for_date(
    db_path: Path,
    *,
    target_date: date,
) -> tuple[list[_SessionState], int, int, dict[str, int]]:
    connection = _connect_readonly(db_path)
    try:
        start_ms, end_ms = _day_bounds_ms(target_date)
        rows = _query_messages(connection, start_ms, end_ms)
        part_extents = _query_part_extents(
            connection,
            [str(row["message_id"]) for row in rows if row["message_id"]],
        )
        raw_messages = _build_raw_messages(rows, part_extents)
        deduped = _dedupe_fork_messages(raw_messages)
        session_ids = sorted({msg.raw_session_id for msg in deduped})
        session_max_created = _query_session_max_created(connection, session_ids)
    finally:
        connection.close()
    sessions = _group_sessions(deduped)
    return sessions, start_ms, end_ms, session_max_created


def _build_raw_messages(
    rows: list[sqlite3.Row],
    part_extents: dict[str, int],
) -> list[_RawMessage]:
    out: list[_RawMessage] = []
    for row in rows:
        payload = _load_payload(row["data"])
        if payload is None:
            continue
        created_ms = _coerce_int(payload.get("time", {}).get("created"))
        if created_ms is None:
            created_ms = _coerce_int(row["time_created"])
        if created_ms is None:
            continue
        tokens = _extract_tokens(payload)
        ended_ms = _resolve_message_end_ms(
            payload=payload,
            created_ms=created_ms,
            row_time_updated=_coerce_int(row["time_updated"]),
            part_extent=part_extents.get(str(row["message_id"])),
            tokens=tokens,
        )
        out.append(
            _RawMessage(
                message_id=str(row["message_id"]),
                raw_session_id=str(row["session_id"]),
                parent_session_id=_nonempty_str(row["session_parent_id"]),
                session_created_ms=_coerce_int(row["session_created"]) or created_ms,
                session_title=_nonempty_str(row["session_title"]),
                session_directory=_nonempty_str(row["session_directory"]),
                is_child=bool(_nonempty_str(row["session_parent_id"])),
                time_created_ms=created_ms,
                time_ended_ms=ended_ms,
                role=_nonempty_str(payload.get("role")),
                model_id=_nonempty_str(payload.get("modelID")),
                provider_id=_nonempty_str(payload.get("providerID")),
                agent=_nonempty_str(payload.get("agent")),
                tokens=tokens,
                cost=_coerce_float(payload.get("cost")),
            )
        )
    return out


def _group_sessions(messages: list[_RawMessage]) -> list[_SessionState]:
    grouped: dict[str, list[_RawMessage]] = defaultdict(list)
    for msg in messages:
        grouped[msg.raw_session_id].append(msg)

    sessions: dict[str, _SessionState] = {}
    for raw_session_id, session_messages in grouped.items():
        ordered = sorted(session_messages, key=lambda msg: (msg.time_created_ms, msg.message_id))
        first = ordered[0]
        title = first.session_title or "untitled session"
        focus_label = _short_label(title)
        project = _project_name(first.session_directory)
        model_id = _dominant_value(
            [msg.model_id for msg in ordered],
            [msg.tokens_total for msg in ordered],
        )
        provider_id = _dominant_value(
            [msg.provider_id for msg in ordered],
            [msg.tokens_total for msg in ordered],
        )
        agent = _dominant_value(
            [msg.agent for msg in ordered],
            [1 for _ in ordered],
        )
        sessions[raw_session_id] = _SessionState(
            raw_session_id=raw_session_id,
            parent_session_id=first.parent_session_id,
            root_session_id=raw_session_id,
            is_child=first.is_child,
            created_ms=first.session_created_ms,
            title=title,
            project=project,
            focus_label=focus_label,
            model_id=model_id,
            provider_id=provider_id,
            agent=agent,
            messages=ordered,
        )

    for session in sessions.values():
        session.root_session_id = _resolve_root_session_id(session.raw_session_id, sessions)

    return sorted(sessions.values(), key=lambda session: (session.created_ms, session.raw_session_id))


def _resolve_root_session_id(
    session_id: str,
    sessions: dict[str, _SessionState],
) -> str:
    current = session_id
    seen: set[str] = set()
    while True:
        session = sessions.get(current)
        if session is None:
            return current
        parent = session.parent_session_id
        if not parent or parent in seen:
            return current
        if parent not in sessions:
            return parent
        seen.add(current)
        current = parent


def _build_raw_events(
    *,
    sessions: list[_SessionState],
    start_ms: int,
    end_ms: int,
    session_max_created: dict[str, int],
) -> list[Event]:
    events: list[Event] = []
    for session in sessions:
        title = session.focus_label
        if start_ms <= session.created_ms < end_ms:
            events.append(
                Event(
                    timestamp=_iso_from_ms(session.created_ms),
                    duration=0.0,
                    data={
                        "kind": "session.started",
                        "source": SOURCE_NAME,
                        "project": session.project,
                        "session_id": session.raw_session_id,
                        "root_session_id": session.root_session_id,
                        "parent_session_id": session.parent_session_id,
                        "is_child": session.is_child,
                        "app": TOOL_NAME,
                        "title": title,
                        "model": session.model_id,
                        "provider": session.provider_id,
                        "agent": session.agent,
                    },
                )
            )

        last_end_ms = session.created_ms
        for message in session.messages:
            if message.role != "assistant":
                continue
            if message.time_ended_ms <= message.time_created_ms:
                continue
            last_end_ms = max(last_end_ms, message.time_ended_ms)
            events.append(
                Event(
                    timestamp=_iso_from_ms(message.time_ended_ms),
                    duration=(message.time_ended_ms - message.time_created_ms) / 1000.0,
                    data={
                        "kind": "response.completed",
                        "source": SOURCE_NAME,
                        "project": session.project,
                        "session_id": session.raw_session_id,
                        "root_session_id": session.root_session_id,
                        "parent_session_id": session.parent_session_id,
                        "is_child": session.is_child,
                        "message_id": message.message_id,
                        "app": TOOL_NAME,
                        "title": title,
                        "model": message.model_id or session.model_id,
                        "provider": message.provider_id or session.provider_id,
                        "agent": message.agent or session.agent,
                        "input_tokens": message.tokens["input"],
                        "output_tokens": message.tokens["output"],
                        "reasoning_tokens": message.tokens["reasoning"],
                        "cache_read_tokens": message.tokens["cache_read"],
                        "cache_write_tokens": message.tokens["cache_write"],
                        "cost": message.cost,
                    },
                )
            )

        latest_created = session_max_created.get(session.raw_session_id)
        if latest_created is not None and latest_created <= end_ms:
            events.append(
                Event(
                    timestamp=_iso_from_ms(last_end_ms),
                    duration=0.0,
                    data={
                        "kind": "session.ended",
                        "source": SOURCE_NAME,
                        "project": session.project,
                        "session_id": session.raw_session_id,
                        "root_session_id": session.root_session_id,
                        "parent_session_id": session.parent_session_id,
                        "is_child": session.is_child,
                        "app": TOOL_NAME,
                        "title": title,
                        "model": session.model_id,
                        "provider": session.provider_id,
                        "agent": session.agent,
                    },
                )
            )

    events.sort(key=lambda event: (event.timestamp, event.duration, event.data.get("kind", "")))
    return events


def _build_session_bucket_payloads(
    *,
    host: str,
    sessions: list[_SessionState],
    start_ms: int,
    end_ms: int,
    session_max_created: dict[str, int],
) -> list[BucketEvents]:
    bucket_events: list[BucketEvents] = []
    for session in sessions:
        events = _build_session_bucket_events(
            session=session,
            start_ms=start_ms,
            end_ms=end_ms,
            session_max_created=session_max_created,
        )
        if not events:
            continue
        bucket_events.append(
            BucketEvents(
                bucket=BucketSpec(
                    id=session_bucket_id(SOURCE_NAME, host, session.raw_session_id),
                    type=SESSION_BUCKET_TYPE,
                    client=CLIENT_NAME,
                    hostname=host,
                    name=f"LLM session ({SOURCE_NAME}) · {_bucket_label(session.focus_label)}",
                ),
                events=events,
            )
        )
    return sorted(bucket_events, key=lambda item: item.bucket.id)


def _build_session_bucket_events(
    *,
    session: _SessionState,
    start_ms: int,
    end_ms: int,
    session_max_created: dict[str, int],
) -> list[Event]:
    events: list[Event] = []
    for offset, message in enumerate(session.messages):
        index = offset + 1
        next_message_start_ms = None
        if offset + 1 < len(session.messages):
            next_message_start_ms = session.messages[offset + 1].time_created_ms
        event_start_ms = max(start_ms, message.time_created_ms)
        event_end_ms = min(end_ms, _workspace_message_end_ms(message, next_message_start_ms=next_message_start_ms))
        if event_end_ms <= event_start_ms:
            continue
        model = message.model_id or session.model_id
        role = message.role or "unknown"
        events.append(
            Event(
                timestamp=_iso_from_ms(event_start_ms),
                duration=(event_end_ms - event_start_ms) / 1000.0,
                data={
                    "kind": _workspace_message_kind(role),
                    "workspace": "session-buckets",
                    "source": SOURCE_NAME,
                    "project": session.project,
                    "session_id": session.raw_session_id,
                    "root_session_id": session.root_session_id,
                    "parent_session_id": session.parent_session_id,
                    "is_child": session.is_child,
                    "message_id": message.message_id,
                    "message_index": index,
                    "role": role,
                    "app": TOOL_NAME,
                    "title": session.title,
                    "session_title": session.title,
                    "session_label": session.focus_label,
                    "message_title": _workspace_message_title(role, model),
                    "model": model,
                    "provider": message.provider_id or session.provider_id,
                    "agent": message.agent or session.agent,
                    "input_tokens": message.tokens["input"],
                    "output_tokens": message.tokens["output"],
                    "reasoning_tokens": message.tokens["reasoning"],
                    "cache_read_tokens": message.tokens["cache_read"],
                    "cache_write_tokens": message.tokens["cache_write"],
                    "tokens_total": message.tokens_total,
                    "cost": message.cost,
                },
            )
        )
    if events:
        return events
    return _build_session_active_fallback(
        session=session,
        start_ms=start_ms,
        end_ms=end_ms,
        session_max_created=session_max_created,
    )


def _build_session_active_fallback(
    *,
    session: _SessionState,
    start_ms: int,
    end_ms: int,
    session_max_created: dict[str, int],
) -> list[Event]:
    active_start_ms = max(start_ms, session.created_ms)
    latest_created = max(active_start_ms, session_max_created.get(session.raw_session_id, active_start_ms))
    assistant_messages = [
        message
        for message in session.messages
        if message.role == "assistant" and message.time_ended_ms > message.time_created_ms
    ]
    totals = _session_totals(assistant_messages)
    last_response_end_ms = max(
        [message.time_ended_ms for message in assistant_messages],
        default=active_start_ms,
    )
    active_end_ms = max(active_start_ms + MIN_WORKSPACE_EVENT_DURATION_MS, latest_created, last_response_end_ms)
    active_end_ms = min(end_ms, active_end_ms)
    if active_end_ms <= active_start_ms:
        return []
    return [
        Event(
            timestamp=_iso_from_ms(active_start_ms),
            duration=(active_end_ms - active_start_ms) / 1000.0,
            data={
                "kind": "session.active",
                "workspace": "session-buckets",
                "source": SOURCE_NAME,
                "project": session.project,
                "session_id": session.raw_session_id,
                "root_session_id": session.root_session_id,
                "parent_session_id": session.parent_session_id,
                "is_child": session.is_child,
                "app": TOOL_NAME,
                "title": session.title,
                "session_title": session.title,
                "session_label": session.focus_label,
                "model": session.model_id,
                "provider": session.provider_id,
                "agent": session.agent,
                "response_count": len(assistant_messages),
                "input_tokens": totals["input"],
                "output_tokens": totals["output"],
                "reasoning_tokens": totals["reasoning"],
                "cache_read_tokens": totals["cache_read"],
                "cache_write_tokens": totals["cache_write"],
            },
        )
    ]


def _workspace_message_end_ms(
    message: _RawMessage,
    *,
    next_message_start_ms: int | None,
) -> int:
    ended_ms = max(message.time_created_ms, message.time_ended_ms)
    if message.role == "assistant":
        return max(message.time_created_ms + MIN_WORKSPACE_EVENT_DURATION_MS, ended_ms)
    marker_end_ms = message.time_created_ms + MIN_WORKSPACE_EVENT_DURATION_MS
    if next_message_start_ms is not None:
        marker_end_ms = min(marker_end_ms, next_message_start_ms)
    return max(message.time_created_ms, marker_end_ms)


def _workspace_message_kind(role: str) -> str:
    normalized = role.strip().lower() if role else "unknown"
    return f"message.{normalized}"


def _workspace_message_title(role: str, model: str | None) -> str:
    normalized = role.strip().lower() if role else "unknown"
    if normalized == "assistant" and model:
        return f"assistant · {model}"
    return normalized


def _session_totals(messages: list[_RawMessage]) -> dict[str, int]:
    totals = {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
    }
    for message in messages:
        totals["input"] += message.tokens["input"]
        totals["output"] += message.tokens["output"]
        totals["reasoning"] += message.tokens["reasoning"]
        totals["cache_read"] += message.tokens["cache_read"]
        totals["cache_write"] += message.tokens["cache_write"]
    return totals


def _bucket_label(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= 72:
        return compact
    return f"{compact[:69].rstrip()}..."


def _query_messages(connection: sqlite3.Connection, start_ms: int, end_ms: int) -> list[sqlite3.Row]:
    has_time_updated = _column_exists(connection, "message", "time_updated")
    updated_expr = "m.time_updated" if has_time_updated else "m.time_created"
    fallback_updated = "time_updated" if has_time_updated else "time_created"
    join_query = f"""
        SELECT
            m.id AS message_id,
            m.session_id AS session_id,
            COALESCE(s.time_created, m.time_created) AS session_created,
            s.parent_id AS session_parent_id,
            s.title AS session_title,
            s.directory AS session_directory,
            m.time_created AS time_created,
            {updated_expr} AS time_updated,
            m.data AS data
        FROM message m
        LEFT JOIN session s ON s.id = m.session_id
        WHERE m.time_created >= ? AND m.time_created < ?
    """
    fallback_query = f"""
        SELECT
            id AS message_id,
            session_id AS session_id,
            time_created AS session_created,
            NULL AS session_parent_id,
            NULL AS session_title,
            NULL AS session_directory,
            time_created AS time_created,
            {fallback_updated} AS time_updated,
            data AS data
        FROM message
        WHERE time_created >= ? AND time_created < ?
    """
    try:
        return list(connection.execute(join_query, (start_ms, end_ms)))
    except sqlite3.OperationalError:
        return list(connection.execute(fallback_query, (start_ms, end_ms)))


def _query_part_extents(
    connection: sqlite3.Connection,
    message_ids: list[str],
) -> dict[str, int]:
    if not message_ids:
        return {}
    try:
        placeholders = ",".join("?" for _ in message_ids)
        query = (
            "SELECT message_id, MAX(time_updated) AS u, MAX(time_created) AS c "
            "FROM part WHERE message_id IN (" + placeholders + ") GROUP BY message_id"
        )
        rows = connection.execute(query, message_ids).fetchall()
    except sqlite3.OperationalError:
        return {}
    extents: dict[str, int] = {}
    for row in rows:
        updated = _coerce_int(row["u"])
        created = _coerce_int(row["c"])
        candidates = [value for value in (updated, created) if value is not None]
        if candidates:
            extents[str(row["message_id"])] = max(candidates)
    return extents


def _query_session_max_created(
    connection: sqlite3.Connection,
    session_ids: list[str],
) -> dict[str, int]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    query = (
        "SELECT session_id, MAX(time_created) AS latest "
        "FROM message WHERE session_id IN (" + placeholders + ") GROUP BY session_id"
    )
    rows = connection.execute(query, session_ids).fetchall()
    out: dict[str, int] = {}
    for row in rows:
        latest = _coerce_int(row["latest"])
        if latest is not None:
            out[str(row["session_id"])] = latest
    return out


def _resolve_message_end_ms(
    *,
    payload: dict[str, Any],
    created_ms: int,
    row_time_updated: int | None,
    part_extent: int | None,
    tokens: dict[str, int],
) -> int:
    completed = _coerce_int(payload.get("time", {}).get("completed"))
    if completed is not None and completed >= created_ms:
        return completed

    fallback_end = None
    candidates = [value for value in (part_extent, row_time_updated) if value is not None and value >= created_ms]
    if candidates:
        fallback_end = max(candidates)

    finish = _nonempty_str(payload.get("finish"))
    tokens_total = (
        tokens["input"]
        + tokens["output"]
        + tokens["reasoning"]
        + tokens["cache_read"]
        + tokens["cache_write"]
    )
    if tokens_total == 0 and not finish:
        return created_ms
    if fallback_end is not None:
        if fallback_end - created_ms > MAX_FALLBACK_RESPONSE_DURATION_MS:
            return created_ms + MIN_WORKSPACE_EVENT_DURATION_MS
        return fallback_end
    return created_ms


def _dedupe_fork_messages(messages: list[_RawMessage]) -> list[_RawMessage]:
    grouped: dict[tuple[int, str | None], list[_RawMessage]] = defaultdict(list)
    for message in messages:
        grouped[(message.time_created_ms, message.role)].append(message)
    kept: list[_RawMessage] = []
    for group in grouped.values():
        winner = min(group, key=lambda msg: (msg.session_created_ms, msg.raw_session_id))
        kept.append(winner)
    kept.sort(key=lambda msg: (msg.time_created_ms, msg.raw_session_id, msg.message_id))
    return kept


def _can_read(path: Path) -> bool:
    try:
        connection = _connect_readonly(path)
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        connection.close()
        return True
    except sqlite3.Error:
        return False


def _connect_readonly(path: Path) -> sqlite3.Connection:
    errors: list[Exception] = []
    for suffix in ("mode=ro", "mode=ro&immutable=1"):
        try:
            connection = sqlite3.connect(f"file:{path}?{suffix}", uri=True)
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as exc:
            errors.append(exc)
    raise sqlite3.OperationalError(str(errors[-1]))


def _db_activity_key(path: Path) -> tuple[float, int, int]:
    related = [
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ]
    existing = [candidate for candidate in related if candidate.exists()]
    latest_mtime = max(candidate.stat().st_mtime for candidate in existing)
    has_wal = int(related[1].exists())
    effective_size = sum(candidate.stat().st_size for candidate in existing)
    return latest_mtime, has_wal, effective_size


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    return any(row[1] == column for row in rows)


def _load_payload(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_tokens(payload: dict[str, Any]) -> dict[str, int]:
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return {
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache_read": 0,
            "cache_write": 0,
        }
    cache = tokens.get("cache")
    return {
        "input": _coerce_int(tokens.get("input")) or 0,
        "output": _coerce_int(tokens.get("output")) or 0,
        "reasoning": _coerce_int(tokens.get("reasoning")) or 0,
        "cache_read": _coerce_int(cache.get("read") if isinstance(cache, dict) else None) or 0,
        "cache_write": _coerce_int(cache.get("write") if isinstance(cache, dict) else None) or 0,
    }
def _short_label(title: str) -> str:
    compact = " ".join(title.strip().split())
    if " (@" in compact:
        compact = compact.split(" (@", 1)[0].strip()
    if len(compact) > 96:
        return compact[:93].rstrip() + "..."
    return compact or "untitled session"


def _project_name(directory: str | None) -> str | None:
    if not directory:
        return None
    return Path(directory).name or None


def _day_bounds_ms(target_date: date) -> tuple[int, int]:
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(target_date, time.min, tzinfo=local_tz)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _iso_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _dominant_value(values: list[str | None], weights: list[int]) -> str | None:
    scored: Counter[str] = Counter()
    for value, weight in zip(values, weights):
        if value:
            scored[value] += max(weight, 1)
    if not scored:
        return None
    return scored.most_common(1)[0][0]


def _nonempty_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
