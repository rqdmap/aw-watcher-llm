from __future__ import annotations

from .buckets import DISPLAY_BUCKET_TYPE
from .buckets import RAW_BUCKET_TYPE
from .buckets import default_host
from .buckets import focus_bucket_id
from .buckets import raw_bucket_id
from .schema import BucketSpec
from .schema import Event
from .schema import WatcherPayload


def build_demo_payload(host: str | None = None, source: str = "opencode") -> WatcherPayload:
    resolved_host = host or default_host()
    tool_name = _tool_name(source)
    raw_bucket = BucketSpec(
        id=raw_bucket_id(source, resolved_host),
        type=RAW_BUCKET_TYPE,
        client="aw-watcher-llm",
        hostname=resolved_host,
        name=f"LLM raw events ({source})",
    )
    display_bucket = BucketSpec(
        id=focus_bucket_id(resolved_host),
        type=DISPLAY_BUCKET_TYPE,
        client="aw-watcher-llm",
        hostname=resolved_host,
        name="LLM focus timeline",
    )
    raw_events = [
        Event(
            timestamp="2026-04-22T10:00:00Z",
            duration=0.0,
            data={
                "kind": "session.started",
                "source": source,
                "project": "ghostwire",
                "session_id": "sess_root_1",
                "root_session_id": "sess_root_1",
                "parent_session_id": None,
                "is_child": False,
                "app": tool_name,
                "title": "snapshot fix",
                "model": "claude-sonnet-4",
                "provider": "anthropic",
            },
        ),
        Event(
            timestamp="2026-04-22T10:00:15Z",
            duration=0.0,
            data={
                "kind": "session.started",
                "source": source,
                "project": "ghostwire",
                "session_id": "sess_child_2",
                "root_session_id": "sess_root_1",
                "parent_session_id": "sess_root_1",
                "is_child": True,
                "app": tool_name,
                "title": "inspect aw schema",
                "model": "claude-sonnet-4",
                "provider": "anthropic",
            },
        ),
        Event(
            timestamp="2026-04-22T10:00:40Z",
            duration=0.0,
            data={
                "kind": "session.started",
                "source": source,
                "project": "ghostwire",
                "session_id": "sess_child_3",
                "root_session_id": "sess_root_1",
                "parent_session_id": "sess_root_1",
                "is_child": True,
                "app": tool_name,
                "title": "write tests",
                "model": "claude-sonnet-4",
                "provider": "anthropic",
            },
        ),
        Event(
            timestamp="2026-04-22T10:01:05Z",
            duration=11.8,
            data={
                "kind": "response.completed",
                "source": source,
                "project": "ghostwire",
                "session_id": "sess_child_2",
                "root_session_id": "sess_root_1",
                "parent_session_id": "sess_root_1",
                "is_child": True,
                "message_id": "msg_child_9",
                "app": tool_name,
                "title": "inspect aw schema",
                "model": "claude-sonnet-4",
                "provider": "anthropic",
                "input_tokens": 1280,
                "output_tokens": 341,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0132,
            },
        ),
        Event(
            timestamp="2026-04-22T10:01:18Z",
            duration=14.2,
            data={
                "kind": "response.completed",
                "source": source,
                "project": "ghostwire",
                "session_id": "sess_root_1",
                "root_session_id": "sess_root_1",
                "parent_session_id": None,
                "is_child": False,
                "message_id": "msg_root_4",
                "app": tool_name,
                "title": "snapshot fix",
                "model": "claude-sonnet-4",
                "provider": "anthropic",
                "input_tokens": 2144,
                "output_tokens": 512,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0217,
            },
        ),
    ]
    display_events = [
        Event(
            timestamp="2026-04-22T10:01:00Z",
            duration=60.0,
            data={
                "app": tool_name,
                "title": "snapshot fix",
                "source": source,
                "project": "ghostwire",
            },
        ),
    ]
    return WatcherPayload(
        raw_bucket=raw_bucket,
        display_bucket=display_bucket,
        raw_events=raw_events,
        display_events=display_events,
    )


def _tool_name(source: str) -> str:
    normalized = source.strip().lower()
    if normalized == "opencode":
        return "OpenCode"
    if normalized == "claudecode":
        return "Claude Code"
    if normalized == "codex":
        return "Codex"
    raise ValueError(f"unsupported source: {source}")
