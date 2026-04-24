from __future__ import annotations

import json
import re
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
SOURCE_NAME = "qoder"
TOOL_NAME = "Qoder"
QODER_DIR = Path.home() / ".qoder"
QODER_PROJECTS_DIR = QODER_DIR / "projects"
QODER_LOG_PATH = QODER_DIR / "logs" / "qodercli.log"
_LOG_INPUT_ESTIMATE_RE = re.compile(
    r"^(?P<timestamp>\S+)\s+\S+\s+\S+\s+current token usage rate: "
    r"(?P<rate>[0-9.]+)%, max input tokens: (?P<max_input_tokens>\d+), sessionId: "
    r"(?P<session_id>[^\s,]+)"
)


def _zero_usage() -> dict[str, int]:
    return {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
    }


@dataclass
class _SessionMeta:
    session_id: str
    title: str | None
    parent_session_id: str | None
    working_dir: str | None
    created_ms: int | None
    updated_ms: int | None


@dataclass
class _RawRecord:
    node_id: str
    parent_id: str | None
    role: str | None
    timestamp_ms: int
    cwd: str | None
    agent: str | None
    prompt_text: str | None = None
    assistant_text: str | None = None
    has_tool_use: bool = False
    is_tool_result: bool = False
    tool_result_end_ms: int | None = None
    usage: dict[str, int] = field(default_factory=_zero_usage)


@dataclass
class _NodeState:
    node_id: str
    role: str | None
    parent_id: str | None
    started_ms: int
    last_ms: int
    prompt_text: str | None = None
    assistant_text: str | None = None
    agent: str | None = None
    has_tool_use: bool = False
    is_tool_result: bool = False
    tool_result_end_ms: int | None = None
    usage: dict[str, int] = field(default_factory=_zero_usage)
    estimated_input_tokens: int | None = None
    usage_estimated: bool = False
    usage_estimate_source: str | None = None
    usage_estimate_rate: float | None = None
    usage_estimate_max_input_tokens: int | None = None


@dataclass
class _SessionState:
    session_id: str
    root_session_id: str
    parent_session_id: str | None
    is_child: bool
    started_ms: int
    last_event_ms: int
    cwd: str | None
    project: str | None
    title: str
    agent: str | None
    turns: list[_NodeState]


@dataclass(frozen=True)
class _LogInputEstimate:
    timestamp_ms: int
    root_session_id: str
    usage_rate: float
    max_input_tokens: int

    @property
    def input_tokens(self) -> int:
        return max(0, int(round((self.usage_rate / 100.0) * self.max_input_tokens)))


def find_projects_dir() -> Path | None:
    if not QODER_PROJECTS_DIR.exists():
        return None
    return QODER_PROJECTS_DIR


def collect_payload(
    *,
    host: str,
    target_date: date,
    projects_dir: Path | None = None,
    log_path: Path | None = None,
) -> WatcherPayload:
    resolved = projects_dir or find_projects_dir()
    if resolved is None:
        raise FileNotFoundError("no Qoder projects directory found")

    raw_bucket = BucketSpec(
        id=raw_bucket_id(SOURCE_NAME, host),
        type=RAW_BUCKET_TYPE,
        client=CLIENT_NAME,
        hostname=host,
        name="LLM raw events (qoder)",
    )

    metadata = _load_session_metadata(resolved)
    start_ms, end_ms = _day_bounds_ms(target_date)
    sessions = _load_sessions(resolved, metadata)
    estimates = _load_log_input_estimates(
        root_session_ids={session.root_session_id for session in sessions},
        log_path=log_path,
    )
    _apply_log_input_estimates(sessions, estimates)
    raw_events = _build_raw_events(sessions=sessions, start_ms=start_ms, end_ms=end_ms)
    return WatcherPayload(
        raw_bucket=raw_bucket,
        raw_events=raw_events,
    )


def _load_session_metadata(projects_dir: Path) -> dict[str, _SessionMeta]:
    metadata: dict[str, _SessionMeta] = {}
    for path in sorted(projects_dir.rglob("*-session.json")):
        if path.parent.parent != projects_dir:
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        session_id = _nonempty_str(payload.get("id"))
        if session_id is None:
            continue
        metadata[session_id] = _SessionMeta(
            session_id=session_id,
            title=_nonempty_str(payload.get("title")),
            parent_session_id=_nonempty_str(payload.get("parent_session_id")),
            working_dir=_nonempty_str(payload.get("working_dir")),
            created_ms=_coerce_int(payload.get("created_at")),
            updated_ms=_coerce_int(payload.get("updated_at")),
        )
    return metadata


def _load_sessions(
    projects_dir: Path,
    metadata: dict[str, _SessionMeta],
) -> list[_SessionState]:
    sessions: list[_SessionState] = []
    for path in sorted(projects_dir.rglob("*.jsonl")):
        if path.parent.name == "transcript":
            continue
        is_child = path.parent.name == "subagents" and path.name.startswith("agent-")
        is_root = path.parent.parent == projects_dir
        if not is_child and not is_root:
            continue
        session = _load_session(path, metadata, is_child=is_child)
        if session is not None:
            sessions.append(session)
    return sorted(sessions, key=lambda item: (item.started_ms, item.session_id))


def _load_session(
    path: Path,
    metadata: dict[str, _SessionMeta],
    *,
    is_child: bool,
) -> _SessionState | None:
    root_session_id = path.parent.parent.name if is_child else path.stem
    meta = metadata.get(root_session_id)
    records: list[_RawRecord] = []
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    first_cwd: str | None = None
    first_agent: str | None = None

    try:
        handle = path.open()
    except OSError:
        return None

    with handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = _parse_record(item)
            if record is None:
                continue
            records.append(record)
            first_timestamp_ms = min(first_timestamp_ms or record.timestamp_ms, record.timestamp_ms)
            last_timestamp_ms = max(last_timestamp_ms or record.timestamp_ms, record.timestamp_ms)
            first_cwd = first_cwd or record.cwd
            first_agent = first_agent or record.agent

    if not records:
        return None

    nodes = _group_records(records)
    turns = sorted(
        (
            node
            for node in nodes.values()
            if node.role == "assistant"
            and (node.assistant_text or node.has_tool_use or _usage_total(node.usage) > 0)
        ),
        key=lambda item: (item.started_ms, item.node_id),
    )
    if not turns:
        return None

    child_result_end_ms = _child_result_end_ms(nodes)
    prompt_cache: dict[str, str | None] = {}
    for turn in turns:
        completed_ms = max(turn.last_ms, child_result_end_ms.get(turn.node_id, 0))
        turn.last_ms = completed_ms
        turn.prompt_text = _resolve_prompt(turn.node_id, nodes, prompt_cache)

    first_prompt = _first_user_prompt(nodes)
    metadata_title = meta.title if meta and not is_child else None
    metadata_created_ms = meta.created_ms if meta and not is_child else None
    metadata_updated_ms = meta.updated_ms if meta and not is_child else None
    started_ms = metadata_created_ms if metadata_created_ms is not None else first_timestamp_ms
    if started_ms is None:
        return None
    last_event_ms = max(
        metadata_updated_ms or 0,
        last_timestamp_ms or started_ms,
        max((turn.last_ms for turn in turns), default=started_ms),
    )
    cwd = first_cwd or (meta.working_dir if meta else None)
    project = _project_name(cwd)
    session_agent = first_agent or (path.stem if is_child else None)
    parent_session_id = root_session_id if is_child else (meta.parent_session_id if meta else None)
    session_id = f"{root_session_id}:{path.stem}" if is_child else root_session_id
    title = _session_title(
        metadata_title=metadata_title,
        first_prompt=first_prompt,
        is_child=is_child,
        agent=session_agent,
    )
    return _SessionState(
        session_id=session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
        is_child=is_child,
        started_ms=started_ms,
        last_event_ms=last_event_ms,
        cwd=cwd,
        project=project,
        title=title,
        agent=session_agent,
        turns=turns,
    )


def _parse_record(item: dict[str, Any]) -> _RawRecord | None:
    if item.get("isMeta"):
        return None
    message = item.get("message")
    if not isinstance(message, dict):
        return None
    node_id = _nonempty_str(message.get("id")) or _nonempty_str(item.get("uuid"))
    timestamp_ms = _parse_iso_ms(item.get("timestamp"))
    if node_id is None or timestamp_ms is None:
        return None
    role = _nonempty_str(message.get("role")) or _nonempty_str(item.get("type"))
    content = message.get("content")
    prompt_text = None
    assistant_text = None
    has_tool_use = False
    is_tool_result = False
    if isinstance(content, list):
        for entry in content:
            if not isinstance(entry, dict):
                continue
            entry_type = _nonempty_str(entry.get("type"))
            if entry_type == "text":
                text = _nonempty_str(entry.get("text"))
                if role == "user" and prompt_text is None and text:
                    prompt_text = text
                if role == "assistant" and assistant_text is None and text:
                    assistant_text = text
            elif entry_type == "tool_use":
                has_tool_use = True
            elif entry_type == "tool_result":
                is_tool_result = True
    tool_result = item.get("toolUseResult")
    tool_result_end_ms = None
    if isinstance(tool_result, dict):
        is_tool_result = True
        tool_result_end_ms = _coerce_int(tool_result.get("end_time")) or _coerce_int(tool_result.get("timestamp"))
    usage = _usage_from_message(message.get("usage"))
    return _RawRecord(
        node_id=node_id,
        parent_id=_nonempty_str(item.get("parentUuid")),
        role=role,
        timestamp_ms=timestamp_ms,
        cwd=_nonempty_str(item.get("cwd")),
        agent=_nonempty_str(item.get("agentId")),
        prompt_text=prompt_text,
        assistant_text=assistant_text,
        has_tool_use=has_tool_use,
        is_tool_result=is_tool_result,
        tool_result_end_ms=tool_result_end_ms,
        usage=usage,
    )


def _group_records(records: list[_RawRecord]) -> dict[str, _NodeState]:
    nodes: dict[str, _NodeState] = {}
    for record in records:
        node = nodes.get(record.node_id)
        if node is None:
            node = _NodeState(
                node_id=record.node_id,
                role=record.role,
                parent_id=record.parent_id,
                started_ms=record.timestamp_ms,
                last_ms=record.timestamp_ms,
            )
            nodes[record.node_id] = node
        node.started_ms = min(node.started_ms, record.timestamp_ms)
        node.last_ms = max(node.last_ms, record.timestamp_ms)
        node.role = node.role or record.role
        node.parent_id = node.parent_id or record.parent_id
        node.prompt_text = node.prompt_text or record.prompt_text
        node.assistant_text = node.assistant_text or record.assistant_text
        node.agent = node.agent or record.agent
        node.has_tool_use = node.has_tool_use or record.has_tool_use
        node.is_tool_result = node.is_tool_result or record.is_tool_result
        if record.tool_result_end_ms is not None:
            node.tool_result_end_ms = max(node.tool_result_end_ms or 0, record.tool_result_end_ms)
        node.usage = {
            key: max(node.usage.get(key, 0), record.usage.get(key, 0))
            for key in ("input", "output", "reasoning", "cache_read", "cache_write")
        }
    return nodes


def _child_result_end_ms(nodes: dict[str, _NodeState]) -> dict[str, int]:
    result: dict[str, int] = {}
    for node in nodes.values():
        if not node.is_tool_result or node.parent_id is None:
            continue
        end_ms = node.tool_result_end_ms or node.last_ms
        result[node.parent_id] = max(result.get(node.parent_id, 0), end_ms)
    return result


def _resolve_prompt(
    node_id: str,
    nodes: dict[str, _NodeState],
    cache: dict[str, str | None],
) -> str | None:
    if node_id in cache:
        return cache[node_id]
    seen: set[str] = set()
    current_id = node_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        current = nodes.get(current_id)
        if current is None:
            break
        if current.role == "user" and current.prompt_text:
            cache[node_id] = current.prompt_text
            return current.prompt_text
        current_id = current.parent_id or ""
    cache[node_id] = None
    return None


def _first_user_prompt(nodes: dict[str, _NodeState]) -> str | None:
    prompts = [
        node.prompt_text
        for node in sorted(nodes.values(), key=lambda item: (item.started_ms, item.node_id))
        if node.role == "user" and node.prompt_text
    ]
    return prompts[0] if prompts else None


def _session_title(
    *,
    metadata_title: str | None,
    first_prompt: str | None,
    is_child: bool,
    agent: str | None,
) -> str:
    preferred = metadata_title
    if preferred in (None, "", "New Session") and first_prompt:
        preferred = first_prompt
    if preferred:
        return _short_label(preferred)
    if is_child:
        return _short_label(f"Subagent {agent or 'session'}")
    return "untitled session"


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
                        "root_session_id": session.root_session_id,
                        "parent_session_id": session.parent_session_id,
                        "is_child": session.is_child,
                        "app": TOOL_NAME,
                        "title": session.title,
                        "model": None,
                        "provider": None,
                        "agent": session.agent,
                    },
                )
            )

        for turn in session.turns:
            if not (start_ms <= turn.last_ms < end_ms):
                continue
            duration_ms = max(0, turn.last_ms - turn.started_ms)
            input_tokens = turn.usage["input"]
            if input_tokens == 0 and turn.estimated_input_tokens is not None:
                input_tokens = turn.estimated_input_tokens
            data = {
                "kind": "response.completed",
                "source": SOURCE_NAME,
                "project": session.project,
                "session_id": session.session_id,
                "root_session_id": session.root_session_id,
                "parent_session_id": session.parent_session_id,
                "is_child": session.is_child,
                "message_id": turn.node_id,
                "app": TOOL_NAME,
                "title": _short_label(turn.prompt_text or session.title),
                "model": None,
                "provider": None,
                "agent": turn.agent or session.agent,
                "input_tokens": input_tokens,
                "output_tokens": turn.usage["output"],
                "reasoning_tokens": turn.usage["reasoning"],
                "cache_read_tokens": turn.usage["cache_read"],
                "cache_write_tokens": turn.usage["cache_write"],
                "cost": None,
            }
            if turn.usage_estimated:
                data.update(
                    {
                        "usage_estimated": True,
                        "usage_estimate_source": turn.usage_estimate_source,
                        "usage_estimated_fields": ["input_tokens"],
                        "usage_estimate_input_rate": turn.usage_estimate_rate,
                        "usage_estimate_max_input_tokens": turn.usage_estimate_max_input_tokens,
                    }
                )
            events.append(
                Event(
                    timestamp=_iso_from_ms(turn.last_ms),
                    duration=duration_ms / 1000.0,
                    data=data,
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
                        "root_session_id": session.root_session_id,
                        "parent_session_id": session.parent_session_id,
                        "is_child": session.is_child,
                        "app": TOOL_NAME,
                        "title": session.title,
                        "model": None,
                        "provider": None,
                        "agent": session.agent,
                    },
                )
            )

    events.sort(key=lambda event: (event.timestamp, event.duration, event.data.get("kind", "")))
    return events


def _usage_from_message(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return _zero_usage()
    return {
        "input": _coerce_int(value.get("input_tokens")) or 0,
        "output": _coerce_int(value.get("output_tokens")) or 0,
        "reasoning": _coerce_int(value.get("reasoning_output_tokens")) or 0,
        "cache_read": _coerce_int(value.get("cache_read_input_tokens")) or 0,
        "cache_write": _coerce_int(value.get("cache_creation_input_tokens")) or 0,
    }


def _usage_total(usage: dict[str, int]) -> int:
    return sum(usage.values())


def _load_log_input_estimates(
    *,
    root_session_ids: set[str],
    log_path: Path | None,
) -> dict[str, list[_LogInputEstimate]]:
    if not root_session_ids:
        return {}

    resolved = log_path or QODER_LOG_PATH
    if not resolved.exists():
        return {}

    estimates: dict[str, list[_LogInputEstimate]] = {}
    try:
        handle = resolved.open()
    except OSError:
        return {}

    with handle:
        for raw_line in handle:
            match = _LOG_INPUT_ESTIMATE_RE.match(raw_line.strip())
            if match is None:
                continue
            root_session_id = match.group("session_id")
            if root_session_id not in root_session_ids:
                continue
            timestamp_ms = _parse_log_timestamp_ms(match.group("timestamp"))
            if timestamp_ms is None:
                continue
            try:
                usage_rate = float(match.group("rate"))
            except ValueError:
                continue
            max_input_tokens = _coerce_int(match.group("max_input_tokens")) or 0
            if usage_rate <= 0 or max_input_tokens <= 0:
                continue
            estimates.setdefault(root_session_id, []).append(
                _LogInputEstimate(
                    timestamp_ms=timestamp_ms,
                    root_session_id=root_session_id,
                    usage_rate=usage_rate,
                    max_input_tokens=max_input_tokens,
                )
            )

    for root_session_id in estimates:
        estimates[root_session_id].sort(key=lambda item: item.timestamp_ms)
    return estimates


def _apply_log_input_estimates(
    sessions: list[_SessionState],
    estimates: dict[str, list[_LogInputEstimate]],
) -> None:
    turns_by_root: dict[str, list[_NodeState]] = {}
    for session in sessions:
        turns_by_root.setdefault(session.root_session_id, []).extend(session.turns)

    for root_session_id, turns in turns_by_root.items():
        session_estimates = estimates.get(root_session_id)
        if not session_estimates:
            continue
        ordered_turns = sorted(turns, key=lambda item: (item.last_ms, item.node_id))
        for turn_index, estimate_index in _align_turns_to_input_estimates(ordered_turns, session_estimates):
            turn = ordered_turns[turn_index]
            if turn.usage["input"] > 0:
                continue
            estimate = session_estimates[estimate_index]
            if estimate.input_tokens <= 0:
                continue
            turn.estimated_input_tokens = estimate.input_tokens
            turn.usage_estimated = True
            turn.usage_estimate_source = "qodercli.log.current_token_usage_rate"
            turn.usage_estimate_rate = estimate.usage_rate
            turn.usage_estimate_max_input_tokens = estimate.max_input_tokens


def _align_turns_to_input_estimates(
    turns: list[_NodeState],
    estimates: list[_LogInputEstimate],
) -> list[tuple[int, int]]:
    if not turns or not estimates:
        return []

    match_window_ms = 30_000
    skip_cost = match_window_ms + 1
    inf = 10**18
    turn_count = len(turns)
    estimate_count = len(estimates)
    costs = [[inf] * (estimate_count + 1) for _ in range(turn_count + 1)]
    moves: list[list[tuple[str, int, int] | None]] = [
        [None] * (estimate_count + 1) for _ in range(turn_count + 1)
    ]
    costs[0][0] = 0

    for turn_index in range(turn_count + 1):
        for estimate_index in range(estimate_count + 1):
            current_cost = costs[turn_index][estimate_index]
            if current_cost >= inf:
                continue

            if turn_index < turn_count:
                candidate = current_cost + skip_cost
                if candidate < costs[turn_index + 1][estimate_index]:
                    costs[turn_index + 1][estimate_index] = candidate
                    moves[turn_index + 1][estimate_index] = ("skip_turn", turn_index, estimate_index)

            if estimate_index < estimate_count:
                candidate = current_cost + skip_cost
                if candidate < costs[turn_index][estimate_index + 1]:
                    costs[turn_index][estimate_index + 1] = candidate
                    moves[turn_index][estimate_index + 1] = ("skip_estimate", turn_index, estimate_index)

            if turn_index < turn_count and estimate_index < estimate_count:
                diff_ms = abs(turns[turn_index].last_ms - estimates[estimate_index].timestamp_ms)
                if diff_ms > match_window_ms:
                    continue
                candidate = current_cost + diff_ms
                if candidate < costs[turn_index + 1][estimate_index + 1]:
                    costs[turn_index + 1][estimate_index + 1] = candidate
                    moves[turn_index + 1][estimate_index + 1] = ("match", turn_index, estimate_index)

    assignments: list[tuple[int, int]] = []
    turn_index = turn_count
    estimate_index = estimate_count
    while turn_index > 0 or estimate_index > 0:
        move = moves[turn_index][estimate_index]
        if move is None:
            break
        action, prev_turn_index, prev_estimate_index = move
        if action == "match":
            assignments.append((prev_turn_index, prev_estimate_index))
        turn_index = prev_turn_index
        estimate_index = prev_estimate_index
    assignments.reverse()
    return assignments


def _project_name(directory: str | None) -> str | None:
    if not directory:
        return None
    return Path(directory).name or None


def _short_label(title: str) -> str:
    compact = " ".join(title.strip().split())
    if len(compact) > 96:
        return compact[:93].rstrip() + "..."
    return compact or "untitled session"


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


def _parse_log_timestamp_ms(value: str) -> int | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = normalized[:-5] + normalized[-5:-2] + ":" + normalized[-2:]
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


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
