# aw-watcher-llm

`aw-watcher-llm` is a local-first ActivityWatch watcher skeleton for LLM and AI coding activity.

Current scope:

- define bucket naming
- define raw and display event schemas
- generate realistic demo events for `OpenCode`
- read real `OpenCode` sessions from the local SQLite database

The current design uses:

- per-source raw buckets such as `aw-watcher-llm-opencode_<host>`
- one aggregated display bucket such as `aw-watcher-llm-focus_<host>`

Raw events are append-only facts:

- `session.started`
- `session.ended`
- `response.completed`

Display events are heartbeat-friendly state snapshots:

- `state.snapshot`

## Quick start

Install editable:

```bash
python3 -m pip install -e .
```

Show recommended bucket ids:

```bash
aw-watcher-llm bucket-ids
```

Print the full OpenCode demo payload:

```bash
aw-watcher-llm demo-json --source opencode --pretty
```

Print only the display snapshot:

```bash
aw-watcher-llm demo-json --source opencode --only display --pretty
```

Read real OpenCode events for a day:

```bash
python3 -m aw_watcher_llm opencode-json --date 2026-04-22 --pretty
```

Print only the real display layer:

```bash
python3 -m aw_watcher_llm opencode-json --date 2026-04-22 --only display --pretty
```

Push a day's OpenCode events into local ActivityWatch:

```bash
python3 -m aw_watcher_llm opencode-push \
  --date 2026-04-22 \
  --db-path ~/.local/share/opencode/opencode.db \
  --aw-url http://127.0.0.1:5600 \
  --pretty
```

By default, `opencode-push` replaces existing events only inside the same local-day window for the affected buckets.

If omitted, `--host` now defaults to the local system hostname from `socket.gethostname()`, which matches the bucket naming used by other ActivityWatch watchers.

## Files

- `aw_watcher_llm/schema.py`: event and bucket models
- `aw_watcher_llm/buckets.py`: bucket naming helpers
- `aw_watcher_llm/demo.py`: realistic v0.1 demo payloads
- `aw_watcher_llm/opencode.py`: real OpenCode SQLite adapter
- `aw_watcher_llm/activitywatch.py`: zero-dependency ActivityWatch REST transport
- `aw_watcher_llm/cli.py`: small CLI for inspection
- `docs/aw-watcher-llm-demo.md`: design demo with concrete examples
- `docs/custom-visual-design.md`: v1 custom visualization spec
- `visualization/dist`: first no-build custom visualization bundle

## Custom visualization

The repo now ships a minimal `visualization/dist` bundle that can be mounted by
ActivityWatch as a custom visualization.

For `aw-server-rust`:

```toml
[custom_static]
aw-watcher-llm = "/Users/rqdmap/Codes/aw-watcher-llm/visualization/dist"
```

For `aw-server`:

```toml
[server.custom_static]
aw-watcher-llm = "/Users/rqdmap/Codes/aw-watcher-llm/visualization/dist"
```

Then restart the server and add it in the Activity page:

1. `Edit view`
2. `Add visualization`
3. `Custom visualization`
4. enter `aw-watcher-llm`

The first version renders:

- a `Focus Ribbon` from the display bucket
- `Root Lanes` rebuilt from raw `response.completed` events
- a small inspector for the selected root session
