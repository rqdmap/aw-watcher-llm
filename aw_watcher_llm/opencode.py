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

from .buckets import DISPLAY_BUCKET_TYPE
from .buckets import RAW_BUCKET_TYPE
from .buckets import focus_bucket_id
from .buckets import raw_bucket_id
from .schema import BucketSpec
from .schema import Event
from .schema import WatcherPayload

DEFAULT_GAP_MINUTES = 10
CLIENT_NAME = "aw-watcher-llm"
SOURCE_NAME = "opencode"
TOOL_NAME = "OpenCode"
OPENCODE_DIR = Path.home() / ".local" / "share" / "opencode"
OPENCODE_CANDIDATES = (
    OPENCODE_DIR / "opencode.db",
    OPENCODE_DIR / "opencode_2.db",
)


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


@dataclass(frozen=True)
class _Burst:
    session: _SessionState
    start_ms: int
    end_ms: int


def find_db() -> Path | None:
    existing = [candidate for candidate in OPENCODE_CANDIDATES if candidate.exists()]
    if not existing:
        return None
    readable = [candidate for candidate in existing if _can_read(candidate)]
    candidates = readable or existing
    return max(candidates, key=_db_activity_key)


def collect_payload(
    *,
    host: str,
    target_date: date,
    db_path: Path | None = None,
    gap_minutes: int = DEFAULT_GAP_MINUTES,
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
    display_bucket = BucketSpec(
        id=focus_bucket_id(host),
        type=DISPLAY_BUCKET_TYPE,
        client=CLIENT_NAME,
        hostname=host,
        name="LLM focus timeline",
    )

    connection = _connect_readonly(resolved)
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
    raw_events = _build_raw_events(
        sessions=sessions,
        start_ms=start_ms,
        end_ms=end_ms,
        session_max_created=session_max_created,
    )
    display_events = _build_display_events(sessions=sessions, gap_minutes=gap_minutes)
    return WatcherPayload(
        raw_bucket=raw_bucket,
        display_bucket=display_bucket,
        raw_events=raw_events,
        display_events=display_events,
    )


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
        ended_ms = _resolve_message_end_ms(
            payload=payload,
            created_ms=created_ms,
            row_time_updated=_coerce_int(row["time_updated"]),
            part_extent=part_extents.get(str(row["message_id"])),
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
                tokens=_extract_tokens(payload),
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
        title = _compose_title(session.focus_label)
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


def _build_display_events(
    *,
    sessions: list[_SessionState],
    gap_minutes: int,
) -> list[Event]:
    root_sessions = [session for session in sessions if not session.is_child]
    bursts = _build_bursts(root_sessions, gap_minutes)
    if not bursts:
        return []

    updates_by_time = _build_update_schedule(root_sessions)
    boundaries = sorted(
        {point for burst in bursts for point in (burst.start_ms, burst.end_ms)}
        | set(updates_by_time)
    )
    priority: list[str] = []
    segments: list[Event] = []
    for start_ms, end_ms in zip(boundaries, boundaries[1:]):
        if end_ms <= start_ms:
            continue
        for session_id in updates_by_time.get(start_ms, []):
            _move_session_to_front(priority, session_id)
        active = [
            burst
            for burst in bursts
            if burst.start_ms < end_ms and burst.end_ms > start_ms
        ]
        if not active:
            continue
        focus = _choose_focus(active, priority)
        data = {
            "app": TOOL_NAME,
            "title": _compose_title(focus.session.focus_label),
            "source": SOURCE_NAME,
            "project": focus.session.project,
        }
        segments.append(
            Event(
                timestamp=_iso_from_ms(start_ms),
                duration=(end_ms - start_ms) / 1000.0,
                data=data,
            )
        )
    return _merge_adjacent_events(segments)


def _build_bursts(sessions: list[_SessionState], gap_minutes: int) -> list[_Burst]:
    gap_ms = gap_minutes * 60 * 1000
    bursts: list[_Burst] = []
    for session in sessions:
        intervals = _assistant_intervals(session)
        if not intervals:
            continue
        burst_start, burst_end = intervals[0]
        for start_ms, end_ms in intervals[1:]:
            if start_ms - burst_end <= gap_ms:
                burst_end = max(burst_end, end_ms)
                continue
            bursts.append(_Burst(session=session, start_ms=burst_start, end_ms=burst_end))
            burst_start, burst_end = start_ms, end_ms
        bursts.append(_Burst(session=session, start_ms=burst_start, end_ms=burst_end))
    bursts.sort(key=lambda burst: (burst.start_ms, burst.end_ms, burst.session.raw_session_id))
    return bursts


def _build_update_schedule(sessions: list[_SessionState]) -> dict[int, list[str]]:
    updates: dict[int, dict[str, _SessionState]] = defaultdict(dict)
    for session in sessions:
        for start_ms, end_ms in _assistant_intervals(session):
            updates[start_ms][session.raw_session_id] = session
            updates[end_ms][session.raw_session_id] = session

    ordered: dict[int, list[str]] = {}
    for timestamp_ms, by_session in updates.items():
        desired = sorted(by_session.values(), key=_session_priority_key)
        ordered[timestamp_ms] = [session.raw_session_id for session in reversed(desired)]
    return ordered


def _assistant_intervals(session: _SessionState) -> list[tuple[int, int]]:
    return sorted(
        [
            (msg.time_created_ms, msg.time_ended_ms)
            for msg in session.messages
            if msg.role == "assistant" and msg.time_ended_ms > msg.time_created_ms
        ]
    )


def _move_session_to_front(priority: list[str], session_id: str) -> None:
    try:
        priority.remove(session_id)
    except ValueError:
        pass
    priority.insert(0, session_id)


def _choose_focus(active: list[_Burst], priority: list[str]) -> _Burst:
    active_by_session = {burst.session.raw_session_id: burst for burst in active}
    for session_id in priority:
        burst = active_by_session.get(session_id)
        if burst is not None:
            return burst
    return min(active, key=_burst_fallback_key)


def _session_priority_key(session: _SessionState) -> tuple[int, int, str]:
    return (
        1 if session.is_child else 0,
        -session.created_ms,
        session.raw_session_id,
    )


def _burst_fallback_key(burst: _Burst) -> tuple[int, int, str]:
    return (
        1 if burst.session.is_child else 0,
        -burst.start_ms,
        burst.session.raw_session_id,
    )


def _merge_adjacent_events(events: list[Event]) -> list[Event]:
    if not events:
        return []
    merged = [events[0]]
    for event in events[1:]:
        previous = merged[-1]
        previous_end_ms = _datetime_to_ms(previous.timestamp) + int(previous.duration * 1000)
        current_start_ms = _datetime_to_ms(event.timestamp)
        if previous.data == event.data and previous_end_ms == current_start_ms:
            merged[-1] = Event(
                timestamp=previous.timestamp,
                duration=previous.duration + event.duration,
                data=previous.data,
            )
            continue
        merged.append(event)
    return merged


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
) -> int:
    completed = _coerce_int(payload.get("time", {}).get("completed"))
    if completed is not None and completed >= created_ms:
        return completed
    if part_extent is not None and part_extent >= created_ms:
        return part_extent
    if row_time_updated is not None and row_time_updated >= created_ms:
        return row_time_updated
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


def _compose_title(focus_label: str) -> str:
    return focus_label


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


def _datetime_to_ms(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp).timestamp() * 1000)


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
