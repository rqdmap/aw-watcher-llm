# Product Summary - 2026-04-23

This document summarizes four things:

1. what `aw-watcher-llm` can do today
2. what existing community tools already cover
3. what product gap still appears to exist
4. what we tried today, what failed, and what we learned

## Short conclusion

There are already many adjacent tools for:

- usage and quota tracking
- session browsing and search
- agent observability

But there still does not appear to be a mature, focused product that is centered on all three of these questions at once:

- "What AI sessions am I running right now?"
- "What is the relationship between those sessions?"
- "How did those sessions consume my attention today?"

That gap is especially visible for a tool that is:

- local-first
- personal, not team-first
- multi-tool across Claude Code, Codex, OpenCode, and similar tools
- root/child/subagent aware
- timeline-native
- more session-centric than token-centric

## What this repo can do today

Current scope in `aw-watcher-llm`:

- read real OpenCode session data from the local SQLite database
- normalize it into raw events
- push those raw events into ActivityWatch
- backfill a rolling window, such as the past 30 days
- run as a polling watcher for today's data
- render a standalone visualization that derives focus and lanes from raw events

Current data model:

- one raw bucket per client per host
  - example: `aw-watcher-llm-opencode_<host>`
- event kinds:
  - `session.started`
  - `session.ended`
  - `response.completed`
- event granularity is response-like
- `session_id` is preserved as a grouping key
- overlapping intervals are preserved in raw events

Current commands:

- `opencode-json`
- `opencode-push`
- `opencode-backfill`
- `opencode-watch`
- `visualize-serve`

Current visualization:

- full-width standalone page, not constrained by the ActivityWatch grid
- derives `Focus Ribbon` from raw root-session responses
- derives `Root Lanes` from raw responses
- includes a simple inspector for the selected root session

## What this product is trying to become

The intended product is not primarily:

- a token tracker
- a quota tracker
- a general observability suite
- a generic ActivityWatch plugin

The intended product is closer to:

**a local-first personal AI session monitor**

The core questions are:

- what sessions are currently active
- how those sessions relate to each other
- how attention moves across root sessions over time
- how subagents fragment or support that work
- what the actual rhythm of work was today

Important emphasis:

- session is the center of gravity
- timeline is first-class
- root/child structure matters
- token and cost are secondary metrics, not the main story

## Community landscape

The current ecosystem already has several adjacent categories.

### 1. Usage, quota, and cost tools

These are strong, but mostly answer "how much" rather than "what session structure existed".

- OpenUsage
  - local usage dashboard across many coding agents
  - strong for spend, quota, rate limits, and provider monitoring
  - weaker for session relationships and attention timeline
  - https://github.com/janekbaraniewski/openusage
- CodexBar
  - menu bar usage and limit tracking
  - provider-centric, not session-centric
  - https://github.com/steipete/CodexBar
- OpenCode Bar
  - strong provider and quota monitoring for OpenCode ecosystem
  - not a root/child session timeline tool
  - https://github.com/opgginc/opencode-bar
- claude-usage
  - local Claude Code usage dashboard
  - strong for tokens, costs, and session history
  - not a general cross-tool session relationship monitor
  - https://github.com/phuryn/claude-usage

### 2. Session browsers and search tools

These are closer to the idea of a session manager, but usually focus on browsing, search, and resume.

- Agent Sessions
  - unified local session browser for Codex CLI, Claude Code, and Gemini CLI
  - very close in spirit to a local session manager
  - still more historical browser/resume tool than attention timeline monitor
  - https://github.com/jazzyalex/agent-sessions
- AI Sessions MCP
  - local session search and read access for multiple coding agents
  - strong retrieval layer
  - not a monitoring or attention tool
  - https://github.com/yoavf/ai-sessions-mcp
- Polpo
  - local session browser with multi-source discovery
  - more remote access/browser oriented than timeline-oriented
  - https://github.com/pugliatechs/polpo
- coding-agent-session-search
  - indexes and searches local coding agent history across many providers
  - strong archive/search use case
  - not focused on live session attention structure
  - https://github.com/Dicklesworthstone/coding_agent_session_search

### 3. Agent orchestration or monitoring dashboards

These are the closest category to the desired direction, but usually too narrow or too platform-specific.

- Claude-Code-Agent-Monitor
  - monitors Claude Code sessions, agent activity, tool usage, and subagent orchestration
  - strong signal that the need is real
  - still primarily Claude Code specific
  - not clearly positioned as a cross-tool personal session monitor
  - https://github.com/hoangsonww/Claude-Code-Agent-Monitor
- Entropic
  - desktop companion for Claude Code, Codex, and Gemini
  - tracks TODOs, history, and repository activity
  - adjacent, but not centered on attention timeline and session relationships
  - https://github.com/Dimension-AI-Technologies/Entropic

### 4. Heavier observability platforms

These are powerful, but they feel heavier than the target use case.

- Agent Replay
  - local-first desktop observability and memory platform
  - traces, memory, tool calls, analytics
  - impressive, but feels closer to a full platform than a focused personal session monitor
  - https://github.com/agentreplay/agentreplay
- LangGraph Studio
  - great for graph debugging and local agent app inspection
  - not a personal monitor for local coding sessions across tools
  - https://github.com/langchain-ai/langgraph-studio

### 5. Vendor-specific built-in session views

- GitHub Copilot now has agent session tracking in its own product
  - useful signal that session visibility matters
  - but it is vendor-specific and cloud-product specific
  - not a local multi-tool personal monitor
  - https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/track-copilot-sessions

## Current market read

The ecosystem already covers:

- usage tracking
- quota tracking
- cost breakdowns
- session search
- session browsing
- provider dashboards
- heavy agent observability

The ecosystem still appears weak on this exact combination:

- local-first personal monitoring
- cross-tool support
- session-centric primary UI
- visible root/child/subagent relationships
- attention timeline as a first-class feature

So the space is not empty, but the specific wedge still looks real.

## What we tried today

We explored an ActivityWatch-first implementation path.

### Attempt 1: raw bucket plus display bucket

Initial design:

- raw bucket for real session/response facts
- display bucket for a simplified focus timeline

What worked:

- simple default timeline compatibility
- easier initial demo

What failed:

- two buckets showing up in ActivityWatch felt awkward
- the product semantics became split across storage and projection
- display bucket started to feel like a workaround, not a clean model

Conclusion:

- removed persisted display bucket
- moved to raw-only storage
- derive display views at runtime instead

### Attempt 2: make ActivityWatch custom visualization the main product UI

What worked:

- proved that the data could be mounted inside ActivityWatch
- proved that custom visualizations can read raw buckets

What failed:

- the Activity page grid constrained width badly
- the visualization felt like a tile, not a real session monitor
- the end result did not feel like a proper chart or product surface

Conclusion:

- ActivityWatch custom visualization is useful as an experiment
- it is not a good primary product shell for this use case

### Attempt 3: use a standalone HTML page directly

What worked:

- conceptually the right direction
- a full-width page is much better than the grid tile

What failed:

- opening local `file://` HTML directly hit browser cross-origin limitations
- local page could not reliably fetch `http://127.0.0.1:5600/api/...`

Conclusion:

- a standalone page is right
- but it needs a small local viewer server, not just a naked local HTML file

### Attempt 4: use ActivityWatch as the product center

What worked:

- good local event store
- useful for basic bucket storage and debug
- good plugin/watcher ecosystem fit

What failed:

- ActivityWatch is not optimized for overlapping AI session structure
- default timeline does not naturally show concurrency the way this product needs
- custom visualization is still experimental and layout-constrained
- the product kept bending toward ActivityWatch concepts instead of session concepts

Conclusion:

- ActivityWatch is useful as an integration and storage/debug layer
- it is not a strong final product shell for a session-first monitor

## Specific lessons from today's debugging

### 1. Hostname mismatches matter

We saw data pushed under one hostname while the visual queried another.

Effect:

- bucket appeared "missing"
- visual showed empty state even though data existed

Lesson:

- host resolution must be automatic wherever possible
- the UI should never make hostname ambiguity easy

### 2. ActivityWatch restarts can make the current state confusing

We saw the active `5600` server come back with older bucket coverage than expected.

Effect:

- viewer returned empty arrays for dates that definitely existed in source data
- this looked like a visualization bug but was actually a storage/server state mismatch

Lesson:

- always verify the actual server bucket metadata
- always distinguish:
  - source data exists
  - bucket exists
  - bucket contains the selected date range

### 3. ActivityWatch default timeline is not a concurrency view

Even if raw events overlap, the default ActivityWatch timeline is not designed to show overlapping session lanes.

Lesson:

- store overlaps in raw data
- do not expect ActivityWatch default timeline to visualize those overlaps well

### 4. Raw-only was the right simplification

The product became clearer when we moved to:

- raw-only buckets
- runtime-derived focus and lane views

Lesson:

- storage should contain facts
- display should be derived

## What seems like the right architecture now

The current best direction appears to be:

### Product center

- local session store
- standalone full-width dashboard
- session-centric UI

### Integrations

- optional export to ActivityWatch
- optional export to richer observability later

### Core entities

- `session`
- `response`
- derived `burst`

### Primary UI questions

- what sessions are active right now
- what root sessions existed today
- how many child/subagent sessions were attached
- where attention shifted over time
- which sessions consumed most responses/tokens/attention

## Proposed interaction shape

The product should probably feel like a session manager, not a metrics console.

### Main view

- top summary strip
- focus ribbon across the day
- root session lanes as the main body
- click a lane to inspect details

### Session list

- browse recent sessions
- filter by source, project, root-only
- sort by recency, duration, token load

### Session detail

- root/child tree
- burst timeline
- response cadence
- model and token breakdown
- notes, tags, and archive actions later

## Recommendation

The best near-term path looks like this:

1. keep `aw-watcher-llm` usable as a watcher/integration layer
2. stop treating ActivityWatch as the final product shell
3. move toward a proper standalone local app or local dashboard
4. keep the product centered on session structure, not usage accounting

## Bottom line

There are already many adjacent tools.

There still does not appear to be a mature tool that cleanly owns this exact product position:

**a local-first, personal AI session monitor that answers what sessions are active, how they relate, and how they consumed your attention over time**

That is the wedge.
