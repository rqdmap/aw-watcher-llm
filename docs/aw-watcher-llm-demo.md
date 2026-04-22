# AW Watcher LLM Demo

This document is a concrete `aw-watcher-llm` v0.1 demo.

The design goal is:

- keep raw facts in ActivityWatch
- keep source adapters independent
- keep the default ActivityWatch timeline readable
- leave burst/concurrency/root-vs-child analysis to a higher layer

## Bucket layout

Raw buckets are split by source:

- `aw-watcher-llm-opencode_<host>`
- `aw-watcher-llm-claudecode_<host>`
- `aw-watcher-llm-codex_<host>`

Recommended raw bucket type:

```text
com.rqdmap.llm.raw.v1
```

Display is one aggregated bucket per host:

- `aw-watcher-llm-focus_<host>`

Recommended display bucket type:

```text
com.rqdmap.llm.display.v1
```

## Event kinds

Raw buckets:

- `session.started`
- `session.ended`
- `response.completed`

Display bucket:

- `state.snapshot`

`response.completed` is the main raw unit. It is the safest shared abstraction across OpenCode, Claude Code, and Codex because token usage and response duration naturally hang off it.

## Demo scenario

Host: `work-mac`

Source: `opencode`

Project: `ghostwire`

Timeline:

1. `10:00:00Z` root session starts: `snapshot fix`
2. `10:00:15Z` child session starts: `inspect aw schema`
3. `10:00:40Z` child session starts: `write tests`
4. `10:01:05Z` child session finishes one response
5. `10:01:18Z` root session finishes one response

At `10:01`, the real system has 3 active sessions in parallel, but the display bucket only tracks the root session and ignores child/subagent sessions.

## Raw event examples

Bucket:

```text
aw-watcher-llm-opencode_work-mac
```

### `session.started`

```json
{
  "timestamp": "2026-04-22T10:00:00Z",
  "duration": 0,
  "data": {
    "kind": "session.started",
    "source": "opencode",
    "project": "ghostwire",
    "session_id": "sess_root_1",
    "root_session_id": "sess_root_1",
    "parent_session_id": null,
    "is_child": false,
    "app": "OpenCode",
    "title": "snapshot fix",
    "model": "claude-sonnet-4",
    "provider": "anthropic"
  }
}
```

### `session.started` child

```json
{
  "timestamp": "2026-04-22T10:00:15Z",
  "duration": 0,
  "data": {
    "kind": "session.started",
    "source": "opencode",
    "project": "ghostwire",
    "session_id": "sess_child_2",
    "root_session_id": "sess_root_1",
    "parent_session_id": "sess_root_1",
    "is_child": true,
    "app": "OpenCode",
    "title": "inspect aw schema",
    "model": "claude-sonnet-4",
    "provider": "anthropic"
  }
}
```

### `response.completed` child

```json
{
  "timestamp": "2026-04-22T10:01:05Z",
  "duration": 11.8,
  "data": {
    "kind": "response.completed",
    "source": "opencode",
    "project": "ghostwire",
    "session_id": "sess_child_2",
    "root_session_id": "sess_root_1",
    "parent_session_id": "sess_root_1",
    "is_child": true,
    "message_id": "msg_child_9",
    "app": "OpenCode",
    "title": "inspect aw schema",
    "model": "claude-sonnet-4",
    "provider": "anthropic",
    "input_tokens": 1280,
    "output_tokens": 341,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cost_usd": 0.0132
  }
}
```

### `response.completed` root

```json
{
  "timestamp": "2026-04-22T10:01:18Z",
  "duration": 14.2,
  "data": {
    "kind": "response.completed",
    "source": "opencode",
    "project": "ghostwire",
    "session_id": "sess_root_1",
    "root_session_id": "sess_root_1",
    "parent_session_id": null,
    "is_child": false,
    "message_id": "msg_root_4",
    "app": "OpenCode",
    "title": "snapshot fix",
    "model": "claude-sonnet-4",
    "provider": "anthropic",
    "input_tokens": 2144,
    "output_tokens": 512,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cost_usd": 0.0217
  }
}
```

## Display event examples

Bucket:

```text
aw-watcher-llm-focus_work-mac
```

The display bucket is written with heartbeats. It does not try to preserve every raw event. It emits a compact state snapshot for the current focus.

### `state.snapshot`

```json
{
  "timestamp": "2026-04-22T10:01:00Z",
  "duration": 60,
  "data": {
    "app": "OpenCode",
    "title": "snapshot fix",
    "source": "opencode",
    "project": "ghostwire"
  }
}
```

## What the two layers are for

Raw buckets answer:

- what sessions existed
- which ones were root or child
- when responses completed
- how many tokens each response used

Display bucket answers:

- what the current LLM work state looked like
- what title should show up in the default ActivityWatch timeline
- only for root sessions, not child/subagent sessions

## Why this split is useful

- raw data can overlap in time without losing truth
- display stays readable inside ActivityWatch's default UI
- future burst/concurrency charts can be built from raw events
- source adapters can evolve without breaking the display layer

## Suggested next step

Implement only the OpenCode adapter first:

1. write `session.started`
2. write `response.completed`
3. write `session.ended`
4. derive `state.snapshot` heartbeats from the currently active sessions

Once that works, reuse the same raw schema for Claude Code and Codex.
