# aw-watcher-llm

`aw-watcher-llm` is a local-first ActivityWatch watcher skeleton for LLM and AI coding activity.

Current scope:

- define bucket naming
- define raw event schemas
- generate realistic demo events for `OpenCode`
- read real `OpenCode` sessions from the local SQLite database
- run as a polling watcher or a historical backfill tool

The current design uses:

- one raw bucket per client such as `aw-watcher-llm-opencode_<host>`
- optional one-bucket-per-session workspace layout such as `aw-watcher-llm-session-opencode_<host>_<session-id>`

Raw events are append-only facts:

- `session.started`
- `session.ended`
- `response.completed`

The session-bucket workspace is a separate display-oriented projection:

- one ActivityWatch bucket per session
- by default only root sessions are included
- child/subagent sessions are opt-in via `--include-child-sessions`
- multiple message-level events per bucket
- `assistant` messages are emitted as span events with per-message `model`, `provider`, `agent`, token, and cost fields
- `user` and other roles are emitted as short marker spans so they remain visible in the default ActivityWatch timeline
- a synthetic `session.active` fallback is only used if a session cannot produce any message-level events
- by default it uses a synthetic hostname like `llm-workspace-<real-host>` so it shows up as a separate device/workspace in ActivityWatch

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

Read real OpenCode events for a day:

```bash
python3 -m aw_watcher_llm opencode-json --date 2026-04-22 --pretty
```

By default, OpenCode auto-discovery reads the primary local database at `~/.local/share/opencode/opencode.db`.
If you need a different database, pass it explicitly with `--db-path`.

Build the per-session workspace buckets for a day:

```bash
python3 -m aw_watcher_llm opencode-session-buckets-json --date 2026-04-22 --pretty

# include child/subagent sessions as well
python3 -m aw_watcher_llm opencode-session-buckets-json --date 2026-04-22 --include-child-sessions --pretty
```

Push a day's OpenCode events into local ActivityWatch:

```bash
python3 -m aw_watcher_llm opencode-push \
  --date 2026-04-22 \
  --db-path ~/.local/share/opencode/opencode.db \
  --aw-url http://127.0.0.1:5600 \
  --pretty
```

Push the per-session workspace buckets into local ActivityWatch:

```bash
python3 -m aw_watcher_llm opencode-session-buckets-push \
  --date 2026-04-22 \
  --db-path ~/.local/share/opencode/opencode.db \
  --aw-url http://127.0.0.1:5600 \
  --pretty

# include child/subagent sessions in the pushed workspace
python3 -m aw_watcher_llm opencode-session-buckets-push \
  --date 2026-04-22 \
  --include-child-sessions \
  --aw-url http://127.0.0.1:5600 \
  --pretty
```

Backfill the past 30 days:

```bash
python3 -m aw_watcher_llm opencode-backfill \
  --days 30 \
  --aw-url http://127.0.0.1:5600 \
  --pretty
```

Backfill the per-session workspace buckets:

```bash
python3 -m aw_watcher_llm opencode-session-buckets-backfill \
  --days 30 \
  --aw-url http://127.0.0.1:5600 \
  --pretty
```

Run a polling watcher for today's data:

```bash
python3 -m aw_watcher_llm opencode-watch \
  --aw-url http://127.0.0.1:5600 \
  --interval-seconds 15
```

Run the session-bucket workspace watcher for today's data:

```bash
python3 -m aw_watcher_llm opencode-session-buckets-watch \
  --aw-url http://127.0.0.1:5600 \
  --interval-seconds 15
```

Run the standalone visualization viewer without ActivityWatch grid constraints:

```bash
python3 -m aw_watcher_llm visualize-serve \
  --aw-url http://127.0.0.1:5600 \
  --port 8787
```

Then open `http://127.0.0.1:8787/`.

`opencode-push`, `opencode-backfill`, and `opencode-watch` replace existing events only inside the same local-day window by default.

If omitted, `--host` defaults to the ActivityWatch server hostname for push/backfill/watch and to the local system hostname for offline inspection commands.

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

- a `Focus Ribbon` rebuilt from raw `response.completed` events
- `Root Lanes` rebuilt from raw `response.completed` events
- a small inspector for the selected root session

If you want the same visual without the Activity grid constraints, use the
standalone page shipped in the same bundle or run the local viewer server:

- `visualization/dist/standalone.html`
- `python3 -m aw_watcher_llm visualize-serve --aw-url http://127.0.0.1:5600`

The viewer server is the most reliable option because it serves the page and
proxies `/api/...` requests to ActivityWatch, so the browser does not hit
`file://` cross-origin limits.
