const GAP_MINUTES = 10;
const SOURCE_LABELS = {
  opencode: "OpenCode",
  claudecode: "Claude Code",
  codex: "Codex",
};
const SOURCE_COLORS = {
  opencode: "var(--opencode)",
  claudecode: "var(--claudecode)",
  codex: "var(--codex)",
};

const state = {
  selectedRootId: null,
};

const hostInput = document.getElementById("host-input");
const dateInput = document.getElementById("date-input");
const sourceSelect = document.getElementById("source-select");
const loadButton = document.getElementById("load-button");
const summaryNode = document.getElementById("summary");
const focusRibbonNode = document.getElementById("focus-ribbon");
const focusCaptionNode = document.getElementById("focus-caption");
const lanesRootNode = document.getElementById("lanes-root");
const lanesCaptionNode = document.getElementById("lanes-caption");
const inspectorNode = document.getElementById("inspector");
const statusLogNode = document.getElementById("status-log");

boot();

async function boot() {
  const params = new URLSearchParams(window.location.search);
  hostInput.value = params.get("host") || "";
  dateInput.value = params.get("date") || new Date().toISOString().slice(0, 10);
  sourceSelect.value = params.get("source") || sourceSelect.value;
  loadButton.addEventListener("click", () => loadVisualization());
  if (!hostInput.value) {
    hostInput.value = (await fetchServerHostname()) || "localhost";
  }
  loadVisualization();
}

async function loadVisualization() {
  const host = hostInput.value.trim() || "work-mac";
  const date = dateInput.value || new Date().toISOString().slice(0, 10);
  const source = sourceSelect.value;
  const rawBucket = `aw-watcher-llm-${source}_${host}`;
  const displayBucket = `aw-watcher-llm-focus_${host}`;
  const [start, end] = localDayRange(date);

  logStatus(`loading host=${host} date=${date} source=${source}`);
  syncQueryString({ host, date, source });

  try {
    const [rawResult, displayResult] = await Promise.all([
      fetchBucketEvents(rawBucket, start, end),
      fetchBucketEvents(displayBucket, start, end),
    ]);
    const model = buildModel({
      rawEvents: rawResult.events,
      displayEvents: displayResult.events,
      source,
      start,
      end,
      missingRaw: rawResult.missing,
      missingDisplay: displayResult.missing,
    });
    render(model);
    logStatus(
      `loaded raw=${rawResult.events.length} display=${displayResult.events.length} roots=${model.rootGroups.length}`,
    );
    if (rawResult.missing) {
      logStatus(`warning: raw bucket missing: ${rawBucket}`);
    }
    if (displayResult.missing) {
      logStatus(`warning: display bucket missing: ${displayBucket}`);
    }
  } catch (error) {
    console.error(error);
    renderError(error instanceof Error ? error.message : String(error));
    logStatus(`error: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function syncQueryString(values) {
  const params = new URLSearchParams(window.location.search);
  Object.entries(values).forEach(([key, value]) => params.set(key, value));
  const next = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, "", next);
}

async function fetchBucketEvents(bucketId, start, end) {
  const url = `/api/0/buckets/${encodeURIComponent(bucketId)}/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  const response = await fetch(url);
  if (response.status === 404) {
    return { events: [], missing: true };
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`failed to load ${bucketId}: ${response.status} ${detail}`);
  }
  return {
    events: await response.json(),
    missing: false,
  };
}

async function fetchServerHostname() {
  try {
    const response = await fetch("/api/0/info");
    if (!response.ok) {
      return null;
    }
    const info = await response.json();
    return typeof info.hostname === "string" && info.hostname ? info.hostname : null;
  } catch {
    return null;
  }
}

function buildModel({ rawEvents, displayEvents, source, start, end, missingRaw, missingDisplay }) {
  const filteredRaw = rawEvents.filter((event) => event.data?.source === source);
  const filteredDisplay = displayEvents.filter((event) => event.data?.source === source);
  const sessions = new Map();
  const responses = [];

  for (const event of filteredRaw) {
    const data = event.data || {};
    const kind = data.kind;
    if (kind === "session.started") {
      sessions.set(data.session_id, {
        sessionId: data.session_id,
        rootSessionId: data.root_session_id || data.session_id,
        parentSessionId: data.parent_session_id || null,
        isChild: Boolean(data.is_child),
        source: data.source,
        project: data.project || "unknown",
        title: data.title || "untitled",
        model: data.model || "unknown",
        provider: data.provider || "unknown",
        agent: data.agent || "unknown",
        startedAt: event.timestamp,
        endedAt: null,
      });
      continue;
    }
    if (kind === "session.ended") {
      const session = sessions.get(data.session_id);
      if (session) {
        session.endedAt = event.timestamp;
      }
      continue;
    }
    if (kind === "response.completed") {
      responses.push({
        sessionId: data.session_id,
        rootSessionId: data.root_session_id || data.session_id,
        source: data.source,
        project: data.project || "unknown",
        title: data.title || "untitled",
        isChild: Boolean(data.is_child),
        startAt: new Date(event.timestamp).getTime() - Math.round((event.duration || 0) * 1000),
        endAt: new Date(event.timestamp).getTime(),
        inputTokens: Number(data.input_tokens || 0),
        outputTokens: Number(data.output_tokens || 0),
        cacheReadTokens: Number(data.cache_read_tokens || 0),
        cacheWriteTokens: Number(data.cache_write_tokens || 0),
        reasoningTokens: Number(data.reasoning_tokens || 0),
      });
    }
  }

  const rootGroupsById = new Map();
  for (const response of responses) {
    const rootId = response.rootSessionId;
    if (!rootGroupsById.has(rootId)) {
      const rootSession = sessions.get(rootId) || {
        sessionId: rootId,
        rootSessionId: rootId,
        parentSessionId: null,
        isChild: false,
        source,
        project: response.project,
        title: response.title,
        model: "unknown",
        provider: "unknown",
        agent: "unknown",
        startedAt: new Date(response.startAt).toISOString(),
        endedAt: null,
      };
      rootGroupsById.set(rootId, {
        rootSessionId: rootId,
        rootSession,
        childSessions: [],
        responses: [],
        totalTokens: 0,
        totalResponses: 0,
        activeStart: response.startAt,
        activeEnd: response.endAt,
      });
    }
    const group = rootGroupsById.get(rootId);
    group.responses.push(response);
    group.totalResponses += 1;
    group.totalTokens +=
      response.inputTokens +
      response.outputTokens +
      response.cacheReadTokens +
      response.cacheWriteTokens +
      response.reasoningTokens;
    group.activeStart = Math.min(group.activeStart, response.startAt);
    group.activeEnd = Math.max(group.activeEnd, response.endAt);
  }

  for (const session of sessions.values()) {
    if (!session.isChild) {
      continue;
    }
    const group = rootGroupsById.get(session.rootSessionId);
    if (group) {
      group.childSessions.push(session);
    }
  }

  const rootGroups = Array.from(rootGroupsById.values())
    .map((group) => ({
      ...group,
      bursts: buildBursts(group.responses),
    }))
    .sort((a, b) => b.activeEnd - a.activeEnd);

  const displaySegments = filteredDisplay.map((event) => ({
    title: event.data?.title || "untitled",
    project: event.data?.project || "unknown",
    source: event.data?.source || source,
    startAt: new Date(event.timestamp).getTime(),
    endAt: new Date(event.timestamp).getTime() + Math.round((event.duration || 0) * 1000),
  }));

  if (!state.selectedRootId && rootGroups[0]) {
    state.selectedRootId = rootGroups[0].rootSessionId;
  }
  if (state.selectedRootId && !rootGroups.some((group) => group.rootSessionId === state.selectedRootId)) {
    state.selectedRootId = rootGroups[0]?.rootSessionId || null;
  }

  return {
    source,
    startAt: new Date(start).getTime(),
    endAt: new Date(end).getTime(),
    missingRaw,
    missingDisplay,
    rawEvents: filteredRaw,
    displaySegments,
    rootGroups,
    selectedRoot: rootGroups.find((group) => group.rootSessionId === state.selectedRootId) || null,
  };
}

function buildBursts(responses) {
  const sorted = [...responses].sort((a, b) => a.startAt - b.startAt || a.endAt - b.endAt);
  const gapMs = GAP_MINUTES * 60 * 1000;
  const bursts = [];
  for (const response of sorted) {
    const last = bursts.at(-1);
    if (!last || response.startAt - last.endAt > gapMs) {
      bursts.push({
        startAt: response.startAt,
        endAt: response.endAt,
        responses: [response],
      });
      continue;
    }
    last.endAt = Math.max(last.endAt, response.endAt);
    last.responses.push(response);
  }
  return bursts;
}

function render(model) {
  renderSummary(model);
  renderFocusRibbon(model);
  renderRootLanes(model);
  renderInspector(model.selectedRoot);
}

function renderSummary(model) {
  const childCount = model.rootGroups.reduce((sum, group) => sum + group.childSessions.length, 0);
  const responseCount = model.rootGroups.reduce((sum, group) => sum + group.totalResponses, 0);
  const totalTokens = model.rootGroups.reduce((sum, group) => sum + group.totalTokens, 0);
  const cards = [
    ["Root Sessions", String(model.rootGroups.length)],
    ["Child Sessions", String(childCount)],
    ["Responses", String(responseCount)],
    ["Tokens", formatCompactNumber(totalTokens)],
  ];
  summaryNode.innerHTML = cards
    .map(
      ([label, value]) =>
        `<article class="summary-card"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></article>`,
    )
    .join("");
}

function renderFocusRibbon(model) {
  if (model.missingDisplay) {
    focusCaptionNode.textContent = "Display bucket missing";
    focusRibbonNode.innerHTML = '<div class="empty-state">Display bucket does not exist yet. Run opencode-push --only display or --only all.</div>';
    return;
  }
  focusCaptionNode.textContent = model.displaySegments.length
    ? `${model.displaySegments.length} root-only focus segments from display bucket`
    : "No display events for this range";
  if (!model.displaySegments.length) {
    focusRibbonNode.innerHTML = '<div class="empty-state">No display segments.</div>';
    return;
  }
  const totalSpan = Math.max(1, model.endAt - model.startAt);
  focusRibbonNode.innerHTML = model.displaySegments
    .map((segment) => {
      const width = Math.max(1.5, ((segment.endAt - segment.startAt) / totalSpan) * 100);
      const title = `${segment.title} • ${segment.project} • ${formatRange(segment.startAt, segment.endAt)}`;
      const sourceLabel = SOURCE_LABELS[segment.source] || segment.source;
      return `<div class="focus-segment" style="width:${width}%;background:${colorForSource(segment.source)}" title="${escapeHtml(title)}">${escapeHtml(segment.title)} <small>${escapeHtml(sourceLabel)}</small></div>`;
    })
    .join("");
}

function renderRootLanes(model) {
  if (model.missingRaw) {
    lanesCaptionNode.textContent = "Raw bucket missing";
    lanesRootNode.innerHTML = '<div class="empty-state">Raw bucket does not exist yet. Run opencode-push --only raw or --only all.</div>';
    return;
  }
  lanesCaptionNode.textContent = model.rootGroups.length
    ? `${model.rootGroups.length} root lanes sorted by most recently active`
    : "No root sessions found";
  if (!model.rootGroups.length) {
    lanesRootNode.innerHTML = '<div class="empty-state">No root sessions found.</div>';
    return;
  }
  const totalSpan = Math.max(1, model.endAt - model.startAt);
  lanesRootNode.innerHTML = "";

  for (const group of model.rootGroups) {
    const row = document.createElement("article");
    row.className = "lane-row";

    const label = document.createElement("div");
    label.className = "lane-label";
    const button = document.createElement("button");
    if (group.rootSessionId === state.selectedRootId) {
      button.classList.add("active");
    }
    button.innerHTML = `
      <div class="lane-title">${escapeHtml(group.rootSession.title)}</div>
      <div class="lane-meta">${escapeHtml(group.rootSession.project)} · ${group.childSessions.length} child · ${group.totalResponses} responses</div>
    `;
    button.addEventListener("click", () => {
      state.selectedRootId = group.rootSessionId;
      renderInspector(group);
      renderRootLanes(model);
    });
    label.appendChild(button);

    const track = document.createElement("div");
    track.className = "lane-track";
    track.title = `${group.rootSession.title} • ${group.rootSession.project}`;
    for (const burst of group.bursts) {
      const burstNode = document.createElement("div");
      burstNode.className = "burst";
      burstNode.style.left = `${((burst.startAt - model.startAt) / totalSpan) * 100}%`;
      burstNode.style.width = `${Math.max(0.6, ((burst.endAt - burst.startAt) / totalSpan) * 100)}%`;
      burstNode.style.background = colorForSource(model.source);
      burstNode.style.opacity = String(Math.min(0.96, 0.45 + burst.responses.length * 0.08));
      burstNode.title = `${group.rootSession.title} • ${formatRange(burst.startAt, burst.endAt)} • ${burst.responses.length} responses`;
      track.appendChild(burstNode);
    }
    for (const response of group.responses) {
      const tick = document.createElement("div");
      tick.className = "response-tick";
      tick.style.left = `${((response.endAt - model.startAt) / totalSpan) * 100}%`;
      tick.style.background = response.outputTokens > response.inputTokens ? "var(--token)" : "rgba(30, 28, 26, 0.72)";
      tick.title = `${response.title} • ${formatClock(response.endAt)} • ${formatCompactNumber(totalTokensOf(response))} tok`;
      track.appendChild(tick);
    }

    row.append(label, track);
    lanesRootNode.appendChild(row);
  }
}

function renderInspector(group) {
  if (!group) {
    inspectorNode.className = "inspector empty";
    inspectorNode.textContent = "Select a lane to inspect it.";
    return;
  }
  inspectorNode.className = "inspector";
  inspectorNode.innerHTML = `
    <div class="inspector-grid">
      <article class="inspector-card">
        <span class="label">Title</span>
        <span class="value">${escapeHtml(group.rootSession.title)}</span>
      </article>
      <article class="inspector-card">
        <span class="label">Project</span>
        <span class="value">${escapeHtml(group.rootSession.project)}</span>
      </article>
      <article class="inspector-card">
        <span class="label">Active Span</span>
        <span class="value">${escapeHtml(formatRange(group.activeStart, group.activeEnd))}</span>
      </article>
      <article class="inspector-card">
        <span class="label">Child Sessions</span>
        <span class="value">${group.childSessions.length}</span>
      </article>
      <article class="inspector-card">
        <span class="label">Responses</span>
        <span class="value">${group.totalResponses}</span>
      </article>
      <article class="inspector-card">
        <span class="label">Tokens</span>
        <span class="value">${escapeHtml(formatCompactNumber(group.totalTokens))}</span>
      </article>
    </div>
  `;
}

function renderError(message) {
  summaryNode.innerHTML = "";
  focusCaptionNode.textContent = "";
  lanesCaptionNode.textContent = "";
  focusRibbonNode.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  lanesRootNode.innerHTML = "";
  inspectorNode.className = "inspector empty";
  inspectorNode.textContent = "See status log for details.";
}

function localDayRange(dateString) {
  const start = new Date(`${dateString}T00:00:00`);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  return [start.toISOString(), end.toISOString()];
}

function formatClock(timestampMs) {
  return new Date(timestampMs).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatRange(startMs, endMs) {
  return `${formatClock(startMs)} - ${formatClock(endMs)}`;
}

function colorForSource(source) {
  return SOURCE_COLORS[source] || "var(--focus)";
}

function totalTokensOf(response) {
  return (
    response.inputTokens +
    response.outputTokens +
    response.cacheReadTokens +
    response.cacheWriteTokens +
    response.reasoningTokens
  );
}

function formatCompactNumber(value) {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function logStatus(line) {
  const prefix = new Date().toLocaleTimeString();
  statusLogNode.textContent = `[${prefix}] ${line}\n${statusLogNode.textContent}`.trim();
}
