from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any

from .buckets import RAW_BUCKET_TYPE
from .buckets import raw_bucket_id
from .schema import BucketSpec
from .schema import Event
from .schema import WatcherPayload

CLIENT_NAME = "aw-watcher-llm"
SOURCE_NAME = "codex"
TOOL_NAME = "Codex"
CODEX_DIR = Path.home() / ".codex"
CODEX_SESSIONS_DIR = CODEX_DIR / "sessions"


def _zero_usage() -> dict[str, int]:
    return {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
    }


@dataclass
class _TurnState:
    turn_id: str
    started_ms: int
    start_usage: dict[str, int] = field(default_factory=_zero_usage)
    end_usage: dict[str, int] = field(default_factory=_zero_usage)
    prompt: str | None = None
    model: str | None = None
    completed_ms: int | None = None
    duration_ms: int | None = None
    aborted: bool = False


@dataclass
class _SessionState:
    session_id: str
    started_ms: int
    cwd: str | None
    project: str | None
    title: str
    provider: str | None
    agent: str | None
    model: str | None
    last_event_ms: int
    turns: list[_TurnState]


def find_sessions_dir() -> Path | None:
    if not CODEX_SESSIONS_DIR.exists():
        return None
    return CODEX_SESSIONS_DIR


def collect_payload(
    *,
    host: str,
    target_date: date,
    sessions_dir: Path | None = None,
) -> WatcherPayload:
    resolved = sessions_dir or find_sessions_dir()
    if resolved is None:
        raise FileNotFoundError("no Codex sessions directory found")

    raw_bucket = BucketSpec(
        id=raw_bucket_id(SOURCE_NAME, host),
        type=RAW_BUCKET_TYPE,
        client=CLIENT_NAME,
        hostname=host,
        name="LLM raw events (codex)",
    )

    start_ms, end_ms = _day_bounds_ms(target_date)
    sessions = _load_sessions(resolved)
    raw_events = _build_raw_events(sessions=sessions, start_ms=start_ms, end_ms=end_ms)
    return WatcherPayload(
        raw_bucket=raw_bucket,
        raw_events=raw_events,
    )


def _load_sessions(sessions_dir: Path) -> list[_SessionState]:
    sessions: list[_SessionState] = []
    for path in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        session = _load_session(path)
        if session is not None:
            sessions.append(session)
    return sorted(sessions, key=lambda item: (item.started_ms, item.session_id))


def _load_session(path: Path) -> _SessionState | None:
    session_id = _session_id_from_filename(path)
    started_ms = None
    cwd = None
    provider = None
    agent = None
    session_title = None
    last_event_ms = None
    turns: dict[str, _TurnState] = {}
    turn_order: list[str] = []
    current_totals = _zero_usage()
    active_turn_id: str | None = None

    for raw_line in path.open():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_ms = _parse_iso_ms(item.get("timestamp"))
        if event_ms is not None:
            last_event_ms = max(last_event_ms or event_ms, event_ms)

        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue

        item_type = item.get("type")
        if item_type == "session_meta":
            session_id = _nonempty_str(payload.get("id")) or session_id
            started_ms = _parse_iso_ms(payload.get("timestamp")) or event_ms or started_ms
            cwd = _nonempty_str(payload.get("cwd")) or cwd
            provider = _nonempty_str(payload.get("model_provider")) or provider
            agent = _nonempty_str(payload.get("originator")) or agent
            continue

        if item_type == "turn_context":
            turn_id = _nonempty_str(payload.get("turn_id"))
            if turn_id and turn_id in turns:
                turns[turn_id].model = _nonempty_str(payload.get("model")) or turns[turn_id].model
            if cwd is None:
                cwd = _nonempty_str(payload.get("cwd"))
            continue

        if item_type != "event_msg":
            continue

        event_type = _nonempty_str(payload.get("type"))
        if event_type == "task_started":
            turn_id = _nonempty_str(payload.get("turn_id"))
            if turn_id is None:
                continue
            if active_turn_id is not None and active_turn_id in turns:
                turns[active_turn_id].aborted = True
            started_at = _coerce_int(payload.get("started_at"))
            turn = _TurnState(
                turn_id=turn_id,
                started_ms=(started_at * 1000) if started_at is not None else (event_ms or 0),
                start_usage=current_totals.copy(),
            )
            turns[turn_id] = turn
            turn_order.append(turn_id)
            active_turn_id = turn_id
            continue

        if event_type == "user_message" and active_turn_id is not None:
            prompt = _nonempty_str(payload.get("message"))
            if prompt and active_turn_id in turns and turns[active_turn_id].prompt is None:
                turns[active_turn_id].prompt = prompt
                if session_title is None:
                    session_title = prompt
            continue

        if event_type == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                totals = _usage_from_total(info.get("total_token_usage"))
                if totals is not None:
                    current_totals = totals
            continue

        if event_type == "task_complete":
            turn_id = _nonempty_str(payload.get("turn_id"))
            if turn_id is None or turn_id not in turns:
                active_turn_id = None
                continue
            turn = turns[turn_id]
            completed_at = _coerce_int(payload.get("completed_at"))
            turn.completed_ms = (completed_at * 1000) if completed_at is not None else event_ms
            turn.duration_ms = _coerce_int(payload.get("duration_ms"))
            turn.end_usage = current_totals.copy()
            active_turn_id = None
            if turn.completed_ms is not None:
                last_event_ms = max(last_event_ms or turn.completed_ms, turn.completed_ms)
            continue

        if event_type == "turn_aborted" and active_turn_id is not None and active_turn_id in turns:
            turns[active_turn_id].aborted = True
            active_turn_id = None

    if session_id is None:
        return None
    if started_ms is None:
        started_ms = last_event_ms or 0
    if last_event_ms is None:
        last_event_ms = started_ms

    ordered_turns = [turns[turn_id] for turn_id in turn_order]
    title = _short_label(session_title or f"New session - {_iso_from_ms(started_ms)}")
    project = _project_name(cwd)
    dominant_model = _dominant_value(
        [turn.model for turn in ordered_turns if turn.model],
        [_usage_total(_usage_delta(turn.end_usage, turn.start_usage)) for turn in ordered_turns if turn.model],
    )
    return _SessionState(
        session_id=session_id,
        started_ms=started_ms,
        cwd=cwd,
        project=project,
        title=title,
        provider=provider,
        agent=agent,
        model=dominant_model,
        last_event_ms=last_event_ms,
        turns=ordered_turns,
    )


def _build_raw_events(
    *,
    sessions: list[_SessionState],
    start_ms: int,
    end_ms: int,
) -> list[Event]:
    events: list[Event] = []
    for session in sessions:
        if start_ms <= session.started_ms < end_ms:
            events.append(
                Event(
                    timestamp=_iso_from_ms(session.started_ms),
                    duration=0.0,
                    data={
                        "kind": "session.started",
                        "source": SOURCE_NAME,
                        "project": session.project,
                        "session_id": session.session_id,
                        "root_session_id": session.session_id,
                        "parent_session_id": None,
                        "is_child": False,
                        "app": TOOL_NAME,
                        "title": session.title,
                        "model": session.model,
                        "provider": session.provider,
                        "agent": session.agent,
                    },
                )
            )

        for turn in session.turns:
            if turn.aborted or turn.completed_ms is None:
                continue
            if not (start_ms <= turn.completed_ms < end_ms):
                continue
            usage = _usage_delta(turn.end_usage, turn.start_usage)
            duration_ms = turn.duration_ms
            if duration_ms is None:
                duration_ms = max(0, turn.completed_ms - turn.started_ms)
            events.append(
                Event(
                    timestamp=_iso_from_ms(turn.completed_ms),
                    duration=duration_ms / 1000.0,
                    data={
                        "kind": "response.completed",
                        "source": SOURCE_NAME,
                        "project": session.project,
                        "session_id": session.session_id,
                        "root_session_id": session.session_id,
                        "parent_session_id": None,
                        "is_child": False,
                        "message_id": turn.turn_id,
                        "app": TOOL_NAME,
                        "title": _short_label(turn.prompt or session.title),
                        "model": turn.model or session.model,
                        "provider": session.provider,
                        "agent": session.agent,
                        "input_tokens": usage["input"],
                        "output_tokens": usage["output"],
                        "reasoning_tokens": usage["reasoning"],
                        "cache_read_tokens": usage["cache_read"],
                        "cache_write_tokens": usage["cache_write"],
                        "cost": None,
                    },
                )
            )

        if start_ms <= session.last_event_ms < end_ms:
            events.append(
                Event(
                    timestamp=_iso_from_ms(session.last_event_ms),
                    duration=0.0,
                    data={
                        "kind": "session.ended",
                        "source": SOURCE_NAME,
                        "project": session.project,
                        "session_id": session.session_id,
                        "root_session_id": session.session_id,
                        "parent_session_id": None,
                        "is_child": False,
                        "app": TOOL_NAME,
                        "title": session.title,
                        "model": session.model,
                        "provider": session.provider,
                        "agent": session.agent,
                    },
                )
            )

    events.sort(key=lambda event: (event.timestamp, event.duration, event.data.get("kind", "")))
    return events


def _usage_from_total(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        "input": _coerce_int(value.get("input_tokens")) or 0,
        "output": _coerce_int(value.get("output_tokens")) or 0,
        "reasoning": _coerce_int(value.get("reasoning_output_tokens")) or 0,
        "cache_read": _coerce_int(value.get("cached_input_tokens")) or 0,
        "cache_write": 0,
    }


def _usage_delta(current: dict[str, int], baseline: dict[str, int]) -> dict[str, int]:
    return {
        key: max(0, current.get(key, 0) - baseline.get(key, 0))
        for key in ("input", "output", "reasoning", "cache_read", "cache_write")
    }


def _usage_total(usage: dict[str, int]) -> int:
    return sum(usage.values())


def _session_id_from_filename(path: Path) -> str | None:
    prefix = "rollout-"
    suffix = ".jsonl"
    name = path.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    stem = name[len(prefix) : -len(suffix)]
    if len(stem) <= 37:
        return stem
    return stem[37:]


def _project_name(directory: str | None) -> str | None:
    if not directory:
        return None
    return Path(directory).name or None


def _short_label(title: str) -> str:
    compact = " ".join(title.strip().split())
    if len(compact) > 96:
        return compact[:93].rstrip() + "..."
    return compact or "untitled session"


def _dominant_value(values: list[str], weights: list[int]) -> str | None:
    if not values:
        return None
    scored: Counter[str] = Counter()
    for value, weight in zip(values, weights):
        scored[value] += max(weight, 1)
    if not scored:
        return None
    return scored.most_common(1)[0][0]


def _day_bounds_ms(target_date: date) -> tuple[int, int]:
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(target_date, time.min, tzinfo=local_tz)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _iso_from_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


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
