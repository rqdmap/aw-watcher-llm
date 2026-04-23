# Custom Visual Design

Note: this design doc predates the raw-only refactor. The current code no
longer persists a display bucket; the visualization rebuilds focus and lane
views from raw `response.completed` events at runtime.

This document defines the first custom visualization for `aw-watcher-llm`.

The goal is not to replace ActivityWatch's default timeline. The goal is to add
one watcher-provided view that makes LLM session structure readable.

## Why this exists

The default ActivityWatch UI is fine for:

- checking that buckets exist
- browsing raw events
- verifying simple `app/title` timelines

It is not good at:

- showing overlapping LLM sessions
- showing root vs child structure
- showing response cadence
- showing token-heavy periods

So the custom visual should do two things:

1. keep the default ActivityWatch timeline simple
2. provide one richer LLM-native view beside it

## V1 goals

V1 should answer these questions quickly:

- which root sessions existed in the selected time range?
- when did each root session become active again?
- how many child sessions were attached to each root?
- where were the response-heavy bursts?
- what is the currently selected lane, and what do we know about it?

V1 should **not** try to solve everything:

- no cost charts
- no full prompt/completion text
- no per-token histograms
- no cross-day summary dashboard
- no editable filters beyond a few simple toggles

## Inputs

The visual reads two buckets for the selected host and time range:

1. raw bucket

Example:

```text
aw-watcher-llm-opencode_<host>
```

It contains:

- `session.started`
- `session.ended`
- `response.completed`

2. display bucket

Example:

```text
aw-watcher-llm-focus_<host>
```

It contains a simplified root-only projection:

- `app`
- `title`
- `source`
- `project`

## Data model inside the visual

The frontend should build these derived structures:

### 1. Session map

One entry per `session_id` from raw events.

Fields:

- `session_id`
- `root_session_id`
- `parent_session_id`
- `is_child`
- `source`
- `project`
- `title`
- `model`
- `provider`
- `agent`
- `started_at`
- `ended_at`

### 2. Response intervals

Derived from `response.completed`.

Fields:

- `session_id`
- `start_at`
  `timestamp - duration`
- `end_at`
  `timestamp`
- `input_tokens`
- `output_tokens`
- `reasoning_tokens`
- `cache_read_tokens`
- `cache_write_tokens`

### 3. Root groups

Group sessions by `root_session_id`.

Fields:

- `root_session_id`
- `root_session`
- `child_sessions[]`
- `responses[]`
- `total_responses`
- `total_tokens`
- `active_span`

### 4. Root bursts

Do not reuse the display bucket as the only source of truth.

Build root bursts from raw `response.completed` events using the same gap rule as
the adapter. This keeps the visual self-consistent even if the display bucket is
minimal.

## Layout

V1 layout should have 4 horizontal regions.

```text
+---------------------------------------------------------------+
| Header: source chip, project filter, root-only toggle         |
+---------------------------------------------------------------+
| Focus Ribbon: simple root-only title timeline                 |
+---------------------------------------------------------------+
| Root Lanes: one row per root session, bursts + response dots  |
+---------------------------------------------------------------+
| Inspector: selected lane/session details                      |
+---------------------------------------------------------------+
```

### Header

Purpose:

- establish context
- expose 2-3 useful toggles

Controls:

- source filter
  `All / OpenCode / Claude Code / Codex`
- project filter
  dropdown or free text
- child visibility
  `Off` by default in V1

Summary chips:

- root sessions count
- child sessions count
- total responses
- total tokens

### Focus Ribbon

This is the visual version of the current display bucket.

Behavior:

- one compact horizontal strip
- colored by `source`
- labeled with `title`
- hover shows:
  - `source`
  - `project`
  - start/end
  - duration

Purpose:

- quickly answer "what was my main root thread over time?"
- stay intentionally simple

### Root Lanes

This is the main chart.

One row per root session.

Each row shows:

- a lane label on the left
  - `title`
  - optional project badge
- a burst bar for each root burst
- small response ticks/dots inside the burst
- subtle token intensity based on total tokens in each response

Recommended encoding:

- burst bar:
  medium-weight rounded rectangle
- response tick:
  thin vertical marker
- token-heavy response:
  darker fill or taller marker

Default sort:

- most recently active root first

Alternative sort toggle:

- longest total active span

### Inspector

A right or bottom panel, depending on available width.

When nothing is selected:

- show a short hint

When a root lane is selected:

- `title`
- `project`
- `source`
- root session duration
- child session count
- total responses
- total tokens
- top models

When a response tick is selected:

- response end time
- duration
- token breakdown
- model/provider

## Interaction model

V1 interactions should stay simple:

- hover lane
  highlight the whole root group
- hover response tick
  show response-level tooltip
- click lane
  pin inspector to that root session
- click empty area
  clear selection
- zoom follows the ActivityWatch-selected time range

Do not add complex brushing in V1.

## Child sessions

Child sessions should not drive the main focus ribbon.

For V1:

- child sessions are counted
- child sessions contribute to root group metrics
- child sessions are hidden by default in the main lane chart

Optional V1.1 toggle:

- `Show child lanes`

If enabled:

- children appear indented under their root
- lighter color
- lower visual priority than roots

## Color system

Keep colors semantic and restrained.

Suggested palette:

- `OpenCode`: blue
- `Claude Code`: amber
- `Codex`: green

State tones:

- root burst: solid medium fill
- child burst: lighter fill
- response tick: dark accent
- selected lane: bold outline

Avoid rainbow-by-session. Sessions should be readable, not noisy.

## Data access strategy

The visual should query raw buckets directly for the current ActivityWatch time
range.

Recommended flow:

1. read selected time range from the Activity view context
2. fetch raw events from the current host/source buckets
3. fetch display events from the focus bucket
4. derive session map, root groups, and bursts in the browser

Reason:

- the raw bucket is still the source of truth
- the display bucket is just a simplified projection

## ActivityWatch integration

The watcher should ship a `visualization/` directory with static assets.

Recommended target layout:

```text
visualization/
  src/
    index.ts
    llm_visual.ts
    data.ts
    layout.ts
    colors.ts
  dist/
    index.html
    bundle.js
    styles.css
```

Expected hosting pattern:

- expose `visualization/dist` through ActivityWatch `custom_static`
- add it in Activity view via:
  `Edit view -> Add visualization -> Custom visualization`

Recommended custom visualization id:

```text
aw-watcher-llm
```

## Rendering strategy

Prefer SVG for V1.

Reason:

- crisp lane rendering
- easy hit-testing
- easy hover states
- easier labels than canvas

Canvas is only worth it if performance becomes a problem at very large ranges.

## Performance boundary

V1 target:

- one day
- one host
- a few thousand raw events

That should be fine with derived structures built in the browser.

If it later gets slow:

- pre-group by root session
- decimate response ticks at low zoom
- hide child lanes by default

## V1 implementation order

1. Render the focus ribbon from display bucket
2. Render root lanes from raw bucket
3. Add response ticks
4. Add inspector
5. Add source/project filters

This order keeps the first usable version small.

## Success criteria

The visual is successful if, within a few seconds, a user can answer:

- what root sessions did I drive today?
- when did each one become active?
- which root session produced the densest responses?
- which roots spawned many child sessions?

That is enough for V1.
