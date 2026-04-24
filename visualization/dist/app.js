const GAP_MINUTES = 10;
const BUCKET_MINUTES = 15;
const BUCKET_MS = BUCKET_MINUTES * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const BUCKET_COUNT = DAY_MS / BUCKET_MS;
const AVG_DAY_DEFAULT = 7;
const AVG_DAY_MIN = 2;
const AVG_DAY_MAX = 90;

const SOURCE_LABELS = {
  opencode: "OpenCode",
  claudecode: "Claude Code",
  codex: "Codex",
  qoder: "Qoder",
};

const SOURCE_COLORS = {
  opencode: "var(--opencode)",
  claudecode: "var(--claudecode)",
  codex: "var(--codex)",
  qoder: "var(--qoder)",
};

const state = {
  selectedLaneId: null,
};

const defaultApiBase = "";
const awUrlInput = document.getElementById("aw-url-input");
const hostInput = document.getElementById("host-input");
const dateInput = document.getElementById("date-input");
const viewModeSelect = document.getElementById("view-mode-select");
const daysControl = document.getElementById("days-control");
const daysInput = document.getElementById("days-input");
const sourceSelect = document.getElementById("source-select");
const loadButton = document.getElementById("load-button");
const summaryNode = document.getElementById("summary");
const focusTitleNode = document.getElementById("focus-title");
const focusRibbonNode = document.getElementById("focus-ribbon");
const focusCaptionNode = document.getElementById("focus-caption");
const lanesTitleNode = document.getElementById("lanes-title");
const lanesRootNode = document.getElementById("lanes-root");
const lanesCaptionNode = document.getElementById("lanes-caption");
const inspectorNode = document.getElementById("inspector");
const statusLogNode = document.getElementById("status-log");

boot();

async function boot() {
  const params = new URLSearchParams(window.location.search);
  if (awUrlInput) {
    awUrlInput.value = normalizeApiBase(params.get("aw_url") || defaultApiBase);
  }
  hostInput.value = params.get("host") || "";
  dateInput.value = params.get("date") || new Date().toISOString().slice(0, 10);
  sourceSelect.value = params.get("source") || sourceSelect.value;
  if (viewModeSelect) {
    viewModeSelect.value = params.get("view") || "avg24h";
    viewModeSelect.addEventListener("change", updateControlsForViewMode);
  }
  if (daysInput) {
    daysInput.value = String(clampDayCount(params.get("days") || daysInput.value));
  }
  updateControlsForViewMode();
  loadButton.addEventListener("click", () => loadVisualization());
  if (!hostInput.value) {
    hostInput.value = (await fetchServerHostname()) || "localhost";
  }
  loadVisualization();
}

function updateControlsForViewMode() {
  if (!daysInput || !daysControl) {
    return;
  }
  const averageMode = getViewMode() === "avg24h";
  daysInput.disabled = !averageMode;
  daysControl.classList.toggle("is-disabled", !averageMode);
}

async function loadVisualization() {
  const apiBase = getApiBase();
  const host = hostInput.value.trim() || "work-mac";
  const date = dateInput.value || new Date().toISOString().slice(0, 10);
  const source = sourceSelect.value;
  const viewMode = getViewMode();
  const dayCount = viewMode === "avg24h" ? getSelectedDayCount() : 1;
  const rawBucket = `aw-watcher-llm-${source}_${host}`;
  const [start, end] = viewMode === "avg24h" ? localWindowRange(date, dayCount) : localDayRange(date);

  logStatus(
    `loading aw=${apiBase || "(same-origin)"} host=${host} date=${date} source=${source} view=${viewMode}${viewMode === "avg24h" ? ` days=${dayCount}` : ""}`,
  );
  syncQueryString({
    aw_url: apiBase || null,
    host,
    date,
    source,
    view: viewMode === "day" ? null : viewMode,
    days: viewMode === "avg24h" ? String(dayCount) : null,
  });

  try {
    const rawResult = await fetchBucketEvents(apiBase, rawBucket, start, end);
    const model = buildModel({
      rawEvents: rawResult.events,
      source,
      start,
      end,
      missingRaw: rawResult.missing,
      viewMode,
      dayCount,
    });
    render(model);
    logStatus(`loaded raw=${rawResult.events.length} lanes=${model.lanes.length} focus=${model.focusSegments.length}`);
    if (rawResult.missing) {
      logStatus(`warning: raw bucket missing: ${rawBucket}`);
    }
  } catch (error) {
    console.error(error);
    renderError(error instanceof Error ? error.message : String(error));
    logStatus(`error: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function syncQueryString(values) {
  const params = new URLSearchParams(window.location.search);
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") {
      params.delete(key);
      return;
    }
    params.set(key, value);
  });
  const query = params.toString();
  const next = query ? `${window.location.pathname}?${query}` : window.location.pathname;
  window.history.replaceState({}, "", next);
}

async function fetchBucketEvents(apiBase, bucketId, start, end) {
  const url = `${apiBase}/api/0/buckets/${encodeURIComponent(bucketId)}/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
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
    const response = await fetch(`${getApiBase()}/api/0/info`);
    if (!response.ok) {
      return null;
    }
    const info = await response.json();
    return typeof info.hostname === "string" && info.hostname ? info.hostname : null;
  } catch {
    return null;
  }
}

function buildModel({ rawEvents, source, start, end, missingRaw, viewMode, dayCount }) {
  const filteredRaw = rawEvents.filter((event) => event.data?.source === source);
  const { sessions, responses } = parseRawEvents(filteredRaw, source);
  if (viewMode === "avg24h") {
    return buildAverageDayModel({
      responses,
      source,
      start,
      end,
      missingRaw,
      dayCount,
    });
  }
  return buildSingleDayModel({
    sessions,
    responses,
    source,
    start,
    end,
    missingRaw,
  });
}

function parseRawEvents(rawEvents, source) {
  const sessions = new Map();
  const responses = [];

  for (const event of rawEvents) {
    const data = event.data || {};
    const kind = data.kind;
    if (kind === "session.started") {
      sessions.set(data.session_id, {
        sessionId: data.session_id,
        rootSessionId: data.root_session_id || data.session_id,
        parentSessionId: data.parent_session_id || null,
        isChild: Boolean(data.is_child),
        source: data.source || source,
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
    if (kind !== "response.completed") {
      continue;
    }
    const inputTokens = Number(data.input_tokens || 0);
    const outputTokens = Number(data.output_tokens || 0);
    const cacheReadTokens = Number(data.cache_read_tokens || 0);
    const cacheWriteTokens = Number(data.cache_write_tokens || 0);
    const reasoningTokens = Number(data.reasoning_tokens || 0);
    responses.push({
      sessionId: data.session_id,
      rootSessionId: data.root_session_id || data.session_id,
      source: data.source || source,
      project: data.project || "unknown",
      title: data.title || "untitled",
      isChild: Boolean(data.is_child),
      startAt: new Date(event.timestamp).getTime() - Math.round((event.duration || 0) * 1000),
      endAt: new Date(event.timestamp).getTime(),
      inputTokens,
      outputTokens,
      cacheReadTokens,
      cacheWriteTokens,
      reasoningTokens,
      totalTokens:
        inputTokens +
        outputTokens +
        cacheReadTokens +
        cacheWriteTokens +
        reasoningTokens,
    });
  }

  return { sessions, responses };
}

function buildSingleDayModel({ sessions, responses, source, start, end, missingRaw }) {
  const lanesById = new Map();
  for (const response of responses) {
    const rootId = response.rootSessionId;
    if (!lanesById.has(rootId)) {
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
      lanesById.set(rootId, {
        laneId: rootId,
        rootSessionId: rootId,
        rootSession,
        title: rootSession.title,
        project: rootSession.project,
        childSessions: [],
        responses: [],
        totalTokens: 0,
        totalResponses: 0,
        activeStart: response.startAt,
        activeEnd: response.endAt,
      });
    }
    const lane = lanesById.get(rootId);
    lane.responses.push(response);
    lane.totalResponses += 1;
    lane.totalTokens += response.totalTokens;
    lane.activeStart = Math.min(lane.activeStart, response.startAt);
    lane.activeEnd = Math.max(lane.activeEnd, response.endAt);
  }

  for (const session of sessions.values()) {
    if (!session.isChild) {
      continue;
    }
    const lane = lanesById.get(session.rootSessionId);
    if (lane) {
      lane.childSessions.push(session);
    }
  }

  const lanes = Array.from(lanesById.values())
    .map((lane) => ({
      ...lane,
      bursts: buildBursts(lane.responses),
      rootResponses: lane.responses.filter((response) => response.sessionId === lane.rootSessionId),
    }))
    .sort((left, right) => right.activeEnd - left.activeEnd);

  const selectedLane = syncSelectedLane(lanes);
  const childCount = lanes.reduce((sum, lane) => sum + lane.childSessions.length, 0);
  const responseCount = lanes.reduce((sum, lane) => sum + lane.totalResponses, 0);
  const totalTokens = lanes.reduce((sum, lane) => sum + lane.totalTokens, 0);

  return {
    viewMode: "day",
    source,
    startAt: new Date(start).getTime(),
    endAt: new Date(end).getTime(),
    missingRaw,
    dayCount: 1,
    lanes,
    focusSegments: buildDayFocusSegments(lanes, source),
    selectedLane,
    summaryCards: [
      ["Root Sessions", String(lanes.length)],
      ["Child Sessions", String(childCount)],
      ["Responses", String(responseCount)],
      ["Tokens", formatCompactNumber(totalTokens)],
    ],
  };
}

function buildAverageDayModel({ responses, source, start, end, missingRaw, dayCount }) {
  const windowStartAt = new Date(start).getTime();
  const windowEndAt = new Date(end).getTime();
  const lanesById = new Map();
  let totalResponses = 0;
  let totalTokens = 0;

  // Fold a multi-day window back into a standard 24h day using fixed 15m buckets.
  for (const response of responses) {
    const laneId = averageLaneId(response.project, response.title);
    if (!lanesById.has(laneId)) {
      lanesById.set(laneId, createAverageLane(response, laneId, source));
    }
    const lane = lanesById.get(laneId);
    const bucketIndex = bucketIndexForOffset(localDayOffsetMs(response.endAt));

    lane.totalResponses += 1;
    lane.totalTokens += response.totalTokens;
    lane.rootSessionIds.add(response.rootSessionId);
    lane.responseCountByBucket[bucketIndex] += 1;
    lane.tokenSumByBucket[bucketIndex] += response.totalTokens;

    addActiveIntervalContribution(
      lane.dailyActiveMsByDay,
      Math.max(response.startAt, windowStartAt),
      Math.min(response.endAt, windowEndAt),
    );

    totalResponses += 1;
    totalTokens += response.totalTokens;
  }

  const lanes = Array.from(lanesById.values())
    .map((lane) => finalizeAverageLane(lane, dayCount))
    .sort(
      (left, right) =>
        right.averageActiveMinutesPerDay - left.averageActiveMinutesPerDay ||
        right.avgResponsesPerDay - left.avgResponsesPerDay ||
        right.avgTokensPerDay - left.avgTokensPerDay ||
        left.title.localeCompare(right.title),
    );

  const selectedLane = syncSelectedLane(lanes);

  return {
    viewMode: "avg24h",
    source,
    startAt: 0,
    endAt: DAY_MS,
    windowStartAt,
    windowEndAt,
    missingRaw,
    dayCount,
    lanes,
    focusSegments: buildAverageFocusSegments(lanes, source),
    selectedLane,
    summaryCards: [
      ["Days", String(dayCount)],
      ["Recurring Lanes", String(lanes.length)],
      ["Avg Responses / Day", formatAverage(totalResponses / Math.max(1, dayCount))],
      ["Avg Tokens / Day", formatCompactNumber(totalTokens / Math.max(1, dayCount))],
    ],
  };
}

function createAverageLane(response, laneId, source) {
  return {
    laneId,
    title: response.title || "untitled",
    project: response.project || "unknown",
    source,
    totalResponses: 0,
    totalTokens: 0,
    rootSessionIds: new Set(),
    responseCountByBucket: new Float64Array(BUCKET_COUNT),
    tokenSumByBucket: new Float64Array(BUCKET_COUNT),
    dailyActiveMsByDay: new Map(),
  };
}

function finalizeAverageLane(lane, dayCount) {
  const activeMsByBucket = new Float64Array(BUCKET_COUNT);
  const activeDaysByBucket = new Float64Array(BUCKET_COUNT);
  for (const dailyBuckets of lane.dailyActiveMsByDay.values()) {
    for (let index = 0; index < BUCKET_COUNT; index += 1) {
      const activeMs = Math.min(BUCKET_MS, dailyBuckets[index]);
      if (activeMs <= 0) {
        continue;
      }
      activeMsByBucket[index] += activeMs;
      activeDaysByBucket[index] += 1;
    }
  }

  const avgActiveRatioByBucket = Array.from(
    activeMsByBucket,
    (value) => value / (BUCKET_MS * Math.max(1, dayCount)),
  );
  const avgActiveDayShareByBucket = Array.from(
    activeDaysByBucket,
    (value) => value / Math.max(1, dayCount),
  );
  const avgResponsesByBucket = Array.from(
    lane.responseCountByBucket,
    (value) => value / Math.max(1, dayCount),
  );
  const avgTokensByBucket = Array.from(
    lane.tokenSumByBucket,
    (value) => value / Math.max(1, dayCount),
  );
  const averageActiveMinutesPerDay = sumValues(activeMsByBucket) / (60 * 1000 * Math.max(1, dayCount));
  const peakBucketIndex = findPeakBucket(avgActiveRatioByBucket, avgResponsesByBucket, avgTokensByBucket);

  return {
    laneId: lane.laneId,
    title: lane.title,
    project: lane.project,
    source: lane.source,
    totalResponses: lane.totalResponses,
    totalTokens: lane.totalTokens,
    avgResponsesPerDay: lane.totalResponses / Math.max(1, dayCount),
    avgTokensPerDay: lane.totalTokens / Math.max(1, dayCount),
    averageActiveMinutesPerDay,
    activeDayCount: lane.dailyActiveMsByDay.size,
    recurringRootCount: lane.rootSessionIds.size,
    avgActiveRatioByBucket,
    avgActiveDayShareByBucket,
    avgResponsesByBucket,
    avgTokensByBucket,
    peakBucketIndex,
    bursts: buildAverageBursts(avgActiveRatioByBucket, avgResponsesByBucket),
  };
}

function buildBursts(responses) {
  const sorted = [...responses].sort((left, right) => left.startAt - right.startAt || left.endAt - right.endAt);
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

function buildAverageBursts(avgActiveRatioByBucket, avgResponsesByBucket) {
  const bursts = [];
  for (let index = 0; index < BUCKET_COUNT; index += 1) {
    const ratio = avgActiveRatioByBucket[index];
    if (ratio <= 0) {
      continue;
    }
    const last = bursts.at(-1);
    if (!last || last.endBucket !== index) {
      bursts.push({
        startBucket: index,
        endBucket: index + 1,
        startAt: index * BUCKET_MS,
        endAt: (index + 1) * BUCKET_MS,
        peakRatio: ratio,
        totalRatio: ratio,
        totalResponses: avgResponsesByBucket[index],
        bucketCount: 1,
      });
      continue;
    }
    last.endBucket = index + 1;
    last.endAt = (index + 1) * BUCKET_MS;
    last.peakRatio = Math.max(last.peakRatio, ratio);
    last.totalRatio += ratio;
    last.totalResponses += avgResponsesByBucket[index];
    last.bucketCount += 1;
  }
  return bursts.map((burst) => ({
    ...burst,
    avgRatio: burst.totalRatio / Math.max(1, burst.bucketCount),
    avgResponses: burst.totalResponses / Math.max(1, burst.bucketCount),
  }));
}

function render(model) {
  focusTitleNode.textContent = model.viewMode === "avg24h" ? "Avg 24h Focus" : "Focus Ribbon";
  lanesTitleNode.textContent = model.viewMode === "avg24h" ? "Avg 24h Lanes" : "Root Lanes";
  renderSummary(model);
  renderFocusRibbon(model);
  renderLanes(model);
  renderInspector(model.selectedLane, model);
}

function renderSummary(model) {
  summaryNode.innerHTML = model.summaryCards
    .map(
      ([label, value]) =>
        `<article class="summary-card"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(value)}</span></article>`,
    )
    .join("");
}

function renderFocusRibbon(model) {
  if (model.missingRaw) {
    focusCaptionNode.textContent = "Raw bucket missing";
    focusRibbonNode.innerHTML =
      '<div class="empty-state">Raw bucket does not exist yet. Run opencode-push, opencode-backfill, or opencode-watch.</div>';
    return;
  }

  if (model.viewMode === "avg24h") {
    focusCaptionNode.textContent = model.focusSegments.length
      ? `${model.focusSegments.length} folded focus segments averaged from ${model.dayCount} days in ${BUCKET_MINUTES}m buckets`
      : "No averaged focus segments for this range";
  } else {
    focusCaptionNode.textContent = model.focusSegments.length
      ? `${model.focusSegments.length} focus segments rebuilt from raw responses`
      : "No focus segments for this range";
  }

  if (!model.focusSegments.length) {
    focusRibbonNode.innerHTML = '<div class="empty-state">No focus segments.</div>';
    return;
  }

  const totalSpan = Math.max(1, model.endAt - model.startAt);
  focusRibbonNode.innerHTML = model.focusSegments
    .map((segment) => {
      const rawWidth = ((segment.endAt - segment.startAt) / totalSpan) * 100;
      const width = model.viewMode === "avg24h" ? rawWidth : Math.max(1.5, rawWidth);
      const title = `${segment.title} • ${segment.project} • ${formatRangeForModel(model, segment.startAt, segment.endAt)}`;
      const sourceLabel = SOURCE_LABELS[segment.source] || segment.source;
      const inlineStyle = [
        `width:${width}%`,
        `background:${colorForSource(segment.source)}`,
        `margin-right:${model.viewMode === "avg24h" ? 0 : 4}px`,
        model.viewMode === "avg24h" ? `opacity:${0.3 + Math.min(0.7, segment.strength * 0.7)}` : "",
      ]
        .filter(Boolean)
        .join(";");
      const content =
        model.viewMode === "avg24h" && width < 8
          ? ""
          : `${escapeHtml(segment.title)}${model.viewMode === "avg24h" && width < 14 ? "" : ` <small>${escapeHtml(sourceLabel)}</small>`}`;
      return `<div class="focus-segment" style="${inlineStyle}" title="${escapeHtml(title)}">${content}</div>`;
    })
    .join("");
}

function renderLanes(model) {
  if (model.missingRaw) {
    lanesCaptionNode.textContent = "Raw bucket missing";
    lanesRootNode.innerHTML =
      '<div class="empty-state">Raw bucket does not exist yet. Run opencode-push, opencode-backfill, or opencode-watch.</div>';
    return;
  }

  lanesCaptionNode.textContent =
    model.viewMode === "avg24h"
      ? model.lanes.length
        ? `${model.lanes.length} recurring lanes folded into a standard 24h day`
        : "No recurring lanes found"
      : model.lanes.length
        ? `${model.lanes.length} root lanes sorted by most recently active`
        : "No root sessions found";

  if (!model.lanes.length) {
    lanesRootNode.innerHTML =
      model.viewMode === "avg24h"
        ? '<div class="empty-state">No recurring lanes found.</div>'
        : '<div class="empty-state">No root sessions found.</div>';
    return;
  }

  const totalSpan = Math.max(1, model.endAt - model.startAt);
  lanesRootNode.innerHTML = "";

  for (const lane of model.lanes) {
    const row = document.createElement("article");
    row.className = "lane-row";

    const label = document.createElement("div");
    label.className = "lane-label";
    const button = document.createElement("button");
    if (lane.laneId === state.selectedLaneId) {
      button.classList.add("active");
    }
    button.innerHTML =
      model.viewMode === "avg24h"
        ? `
      <div class="lane-title">${escapeHtml(lane.title)}</div>
      <div class="lane-meta">${escapeHtml(lane.project)} · ${escapeHtml(formatAverage(lane.avgResponsesPerDay))} resp/day · ${escapeHtml(formatAverage(lane.averageActiveMinutesPerDay))} active min/day</div>
    `
        : `
      <div class="lane-title">${escapeHtml(lane.rootSession.title)}</div>
      <div class="lane-meta">${escapeHtml(lane.rootSession.project)} · ${lane.childSessions.length} child · ${lane.totalResponses} responses</div>
    `;
    button.addEventListener("click", () => {
      state.selectedLaneId = lane.laneId;
      renderInspector(lane, model);
      renderLanes(model);
    });
    label.appendChild(button);

    const track = document.createElement("div");
    track.className = "lane-track";
    track.title =
      model.viewMode === "avg24h"
        ? `${lane.title} • ${lane.project} • peak ${formatBucketRangeFromIndex(lane.peakBucketIndex)}`
        : `${lane.rootSession.title} • ${lane.rootSession.project}`;

    if (model.viewMode === "avg24h") {
      renderAverageBursts(track, lane, model, totalSpan);
      renderAverageResponseTicks(track, lane, model, totalSpan);
    } else {
      renderDayBursts(track, lane, model, totalSpan);
      renderDayResponseTicks(track, lane, model, totalSpan);
    }

    row.append(label, track);
    lanesRootNode.appendChild(row);
  }
}

function renderDayBursts(track, lane, model, totalSpan) {
  for (const burst of lane.bursts) {
    const burstNode = document.createElement("div");
    burstNode.className = "burst";
    burstNode.style.left = `${((burst.startAt - model.startAt) / totalSpan) * 100}%`;
    burstNode.style.width = `${Math.max(0.6, ((burst.endAt - burst.startAt) / totalSpan) * 100)}%`;
    burstNode.style.background = colorForSource(model.source);
    burstNode.style.opacity = String(Math.min(0.96, 0.45 + burst.responses.length * 0.08));
    burstNode.title = `${lane.rootSession.title} • ${formatRangeForModel(model, burst.startAt, burst.endAt)} • ${burst.responses.length} responses`;
    track.appendChild(burstNode);
  }
}

function renderDayResponseTicks(track, lane, model, totalSpan) {
  for (const response of lane.responses) {
    const tick = document.createElement("div");
    tick.className = "response-tick";
    tick.style.left = `${((response.endAt - model.startAt) / totalSpan) * 100}%`;
    tick.style.background = response.outputTokens > response.inputTokens ? "var(--token)" : "rgba(30, 28, 26, 0.72)";
    tick.title = `${response.title} • ${formatPointForModel(model, response.endAt)} • ${formatCompactNumber(response.totalTokens)} tok`;
    track.appendChild(tick);
  }
}

function renderAverageBursts(track, lane, model, totalSpan) {
  for (const burst of lane.bursts) {
    const burstNode = document.createElement("div");
    burstNode.className = "burst";
    burstNode.style.left = `${((burst.startAt - model.startAt) / totalSpan) * 100}%`;
    burstNode.style.width = `${Math.max(0.6, ((burst.endAt - burst.startAt) / totalSpan) * 100)}%`;
    burstNode.style.background = colorForSource(model.source);
    burstNode.style.opacity = String(0.18 + Math.min(0.78, burst.peakRatio * 0.78));
    burstNode.title = `${lane.title} • ${formatRangeForModel(model, burst.startAt, burst.endAt)} • active ${formatPercent(burst.avgRatio)} • avg ${formatAverage(burst.avgResponses)} responses/day`;
    track.appendChild(burstNode);
  }
}

function renderAverageResponseTicks(track, lane, model, totalSpan) {
  const maxAvgResponses = Math.max(...lane.avgResponsesByBucket, 0);
  for (let index = 0; index < BUCKET_COUNT; index += 1) {
    const avgResponses = lane.avgResponsesByBucket[index];
    if (avgResponses <= 0) {
      continue;
    }
    const tick = document.createElement("div");
    tick.className = "response-tick";
    tick.style.left = `${((((index + 0.5) * BUCKET_MS) - model.startAt) / totalSpan) * 100}%`;
    tick.style.background = colorForSource(model.source);
    tick.style.opacity = String(0.2 + (avgResponses / Math.max(1, maxAvgResponses)) * 0.8);
    tick.title = `${lane.title} • ${formatBucketRangeFromIndex(index)} • avg ${formatAverage(avgResponses)} responses/day • ${formatCompactNumber(lane.avgTokensByBucket[index])} tok/day`;
    track.appendChild(tick);
  }
}

function renderInspector(lane, model) {
  if (!lane) {
    inspectorNode.className = "inspector empty";
    inspectorNode.textContent = "Select a lane to inspect it.";
    return;
  }
  inspectorNode.className = "inspector";
  if (model.viewMode === "avg24h") {
    inspectorNode.innerHTML = renderInspectorCards([
      ["Title", lane.title],
      ["Project", lane.project],
      ["Active Days", `${lane.activeDayCount} / ${model.dayCount}`],
      ["Recurring Roots", String(lane.recurringRootCount)],
      ["Avg Active / Day", `${formatAverage(lane.averageActiveMinutesPerDay)} min`],
      ["Avg Responses / Day", formatAverage(lane.avgResponsesPerDay)],
      ["Avg Tokens / Day", formatCompactNumber(lane.avgTokensPerDay)],
      ["Peak Window", formatBucketRangeFromIndex(lane.peakBucketIndex)],
    ]);
    return;
  }

  inspectorNode.innerHTML = renderInspectorCards([
    ["Title", lane.rootSession.title],
    ["Project", lane.rootSession.project],
    ["Active Span", formatRangeForModel(model, lane.activeStart, lane.activeEnd)],
    ["Child Sessions", String(lane.childSessions.length)],
    ["Responses", String(lane.totalResponses)],
    ["Tokens", formatCompactNumber(lane.totalTokens)],
  ]);
}

function renderInspectorCards(cards) {
  return `
    <div class="inspector-grid">
      ${cards
        .map(
          ([label, value]) => `
        <article class="inspector-card">
          <span class="label">${escapeHtml(label)}</span>
          <span class="value">${escapeHtml(value)}</span>
        </article>`,
        )
        .join("")}
    </div>
  `;
}

function renderError(message) {
  summaryNode.innerHTML = "";
  focusTitleNode.textContent = "Focus Ribbon";
  lanesTitleNode.textContent = "Root Lanes";
  focusCaptionNode.textContent = "";
  lanesCaptionNode.textContent = "";
  focusRibbonNode.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  lanesRootNode.innerHTML = "";
  inspectorNode.className = "inspector empty";
  inspectorNode.textContent = "See status log for details.";
}

function buildDayFocusSegments(lanes, source) {
  const rootBursts = [];
  const updatesByTime = new Map();
  for (const lane of lanes) {
    const bursts = buildBursts(lane.rootResponses);
    for (const burst of bursts) {
      rootBursts.push({
        laneId: lane.laneId,
        title: lane.title,
        project: lane.project,
        source,
        startAt: burst.startAt,
        endAt: burst.endAt,
        strength: 1,
      });
    }
    for (const response of lane.rootResponses) {
      addUpdate(updatesByTime, response.startAt, lane.laneId);
      addUpdate(updatesByTime, response.endAt, lane.laneId);
    }
  }
  return buildFocusSegmentsFromBursts(rootBursts, updatesByTime);
}

function buildAverageFocusSegments(lanes, source) {
  const segments = [];
  for (let index = 0; index < BUCKET_COUNT; index += 1) {
    let winner = null;
    let winnerRatio = 0;
    let winnerResponses = 0;
    let winnerTokens = 0;
    for (const lane of lanes) {
      const ratio = lane.avgActiveRatioByBucket[index];
      if (ratio <= 0) {
        continue;
      }
      const responses = lane.avgResponsesByBucket[index];
      const tokens = lane.avgTokensByBucket[index];
      if (
        !winner ||
        ratio > winnerRatio ||
        (ratio === winnerRatio && responses > winnerResponses) ||
        (ratio === winnerRatio && responses === winnerResponses && tokens > winnerTokens) ||
        (ratio === winnerRatio &&
          responses === winnerResponses &&
          tokens === winnerTokens &&
          lane.laneId.localeCompare(winner.laneId) < 0)
      ) {
        winner = lane;
        winnerRatio = ratio;
        winnerResponses = responses;
        winnerTokens = tokens;
      }
    }
    if (!winner) {
      continue;
    }
    const last = segments.at(-1);
    if (last && last.laneId === winner.laneId && last.endBucket === index) {
      last.endBucket = index + 1;
      last.endAt = (index + 1) * BUCKET_MS;
      last.totalStrength += winnerRatio;
      last.bucketCount += 1;
      continue;
    }
    segments.push({
      laneId: winner.laneId,
      title: winner.title,
      project: winner.project,
      source,
      startBucket: index,
      endBucket: index + 1,
      startAt: index * BUCKET_MS,
      endAt: (index + 1) * BUCKET_MS,
      totalStrength: winnerRatio,
      bucketCount: 1,
      strength: winnerRatio,
    });
  }
  return segments.map((segment) => ({
    ...segment,
    strength: segment.totalStrength / Math.max(1, segment.bucketCount),
  }));
}

function buildFocusSegmentsFromBursts(bursts, updatesByTime) {
  if (!bursts.length) {
    return [];
  }
  const sortedBursts = [...bursts].sort(
    (left, right) => left.startAt - right.startAt || left.endAt - right.endAt || left.laneId.localeCompare(right.laneId),
  );
  const boundaries = Array.from(
    new Set(sortedBursts.flatMap((burst) => [burst.startAt, burst.endAt]).concat(Array.from(updatesByTime.keys()))),
  ).sort((left, right) => left - right);

  const priority = [];
  const segments = [];
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const startAt = boundaries[index];
    const endAt = boundaries[index + 1];
    if (endAt <= startAt) {
      continue;
    }
    const updates = updatesByTime.get(startAt) || [];
    for (const laneId of updates) {
      moveToFront(priority, laneId);
    }
    const active = sortedBursts.filter((burst) => burst.startAt < endAt && burst.endAt > startAt);
    if (!active.length) {
      continue;
    }
    const focus = chooseFocus(active, priority);
    const previous = segments.at(-1);
    if (previous && previous.laneId === focus.laneId && previous.endAt === startAt) {
      previous.endAt = endAt;
      continue;
    }
    segments.push({
      laneId: focus.laneId,
      title: focus.title,
      project: focus.project,
      source: focus.source,
      startAt,
      endAt,
      strength: focus.strength || 1,
    });
  }
  return segments;
}

function addUpdate(updatesByTime, timestamp, laneId) {
  const existing = updatesByTime.get(timestamp);
  if (existing) {
    existing.push(laneId);
    return;
  }
  updatesByTime.set(timestamp, [laneId]);
}

function moveToFront(priority, laneId) {
  const index = priority.indexOf(laneId);
  if (index >= 0) {
    priority.splice(index, 1);
  }
  priority.unshift(laneId);
}

function chooseFocus(active, priority) {
  for (const laneId of priority) {
    const match = active.find((burst) => burst.laneId === laneId);
    if (match) {
      return match;
    }
  }
  return [...active].sort(
    (left, right) => right.startAt - left.startAt || left.laneId.localeCompare(right.laneId),
  )[0];
}

function syncSelectedLane(lanes) {
  if (!state.selectedLaneId && lanes[0]) {
    state.selectedLaneId = lanes[0].laneId;
  }
  if (state.selectedLaneId && !lanes.some((lane) => lane.laneId === state.selectedLaneId)) {
    state.selectedLaneId = lanes[0]?.laneId || null;
  }
  return lanes.find((lane) => lane.laneId === state.selectedLaneId) || null;
}

function averageLaneId(project, title) {
  return `${project}\u0000${title}`;
}

function addActiveIntervalContribution(dailyActiveMsByDay, startMs, endMs) {
  if (!(endMs > startMs)) {
    return;
  }
  let cursor = startMs;
  while (cursor < endMs) {
    const dayStartMs = localDayStartMs(cursor);
    const nextDayStartMs = dayStartMs + DAY_MS;
    const segmentEndMs = Math.min(endMs, nextDayStartMs);
    const dayKey = localDateKeyFromMs(cursor);
    let dailyBuckets = dailyActiveMsByDay.get(dayKey);
    if (!dailyBuckets) {
      dailyBuckets = new Float64Array(BUCKET_COUNT);
      dailyActiveMsByDay.set(dayKey, dailyBuckets);
    }
    let offsetStartMs = cursor - dayStartMs;
    const offsetEndMs = segmentEndMs - dayStartMs;
    while (offsetStartMs < offsetEndMs) {
      const bucketIndex = bucketIndexForOffset(offsetStartMs);
      const bucketEndMs = Math.min(offsetEndMs, (bucketIndex + 1) * BUCKET_MS);
      dailyBuckets[bucketIndex] = Math.min(BUCKET_MS, dailyBuckets[bucketIndex] + (bucketEndMs - offsetStartMs));
      offsetStartMs = bucketEndMs;
    }
    cursor = segmentEndMs;
  }
}

function findPeakBucket(avgActiveRatioByBucket, avgResponsesByBucket, avgTokensByBucket) {
  let bestIndex = 0;
  for (let index = 1; index < BUCKET_COUNT; index += 1) {
    if (
      avgActiveRatioByBucket[index] > avgActiveRatioByBucket[bestIndex] ||
      (avgActiveRatioByBucket[index] === avgActiveRatioByBucket[bestIndex] &&
        avgResponsesByBucket[index] > avgResponsesByBucket[bestIndex]) ||
      (avgActiveRatioByBucket[index] === avgActiveRatioByBucket[bestIndex] &&
        avgResponsesByBucket[index] === avgResponsesByBucket[bestIndex] &&
        avgTokensByBucket[index] > avgTokensByBucket[bestIndex])
    ) {
      bestIndex = index;
    }
  }
  return bestIndex;
}

function localDayRange(dateString) {
  const start = new Date(`${dateString}T00:00:00`);
  const end = new Date(start.getTime() + DAY_MS);
  return [start.toISOString(), end.toISOString()];
}

function localWindowRange(dateString, dayCount) {
  const resolvedCount = Math.max(AVG_DAY_MIN, dayCount);
  const endDateStart = new Date(`${dateString}T00:00:00`);
  const start = new Date(endDateStart.getTime() - (resolvedCount - 1) * DAY_MS);
  const end = new Date(endDateStart.getTime() + DAY_MS);
  return [start.toISOString(), end.toISOString()];
}

function getViewMode() {
  return viewModeSelect?.value === "avg24h" ? "avg24h" : "day";
}

function getSelectedDayCount() {
  return clampDayCount(daysInput?.value);
}

function clampDayCount(value) {
  const parsed = Number.parseInt(String(value || AVG_DAY_DEFAULT), 10);
  if (!Number.isFinite(parsed)) {
    return AVG_DAY_DEFAULT;
  }
  return Math.max(AVG_DAY_MIN, Math.min(AVG_DAY_MAX, parsed));
}

function getApiBase() {
  const value = awUrlInput?.value?.trim() || defaultApiBase;
  return normalizeApiBase(value);
}

function normalizeApiBase(value) {
  if (!value) {
    return "";
  }
  return value.replace(/\/+$/, "");
}

function localDayStartMs(timestampMs) {
  const date = new Date(timestampMs);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function localDayOffsetMs(timestampMs) {
  const date = new Date(timestampMs);
  return (
    date.getHours() * 60 * 60 * 1000 +
    date.getMinutes() * 60 * 1000 +
    date.getSeconds() * 1000 +
    date.getMilliseconds()
  );
}

function localDateKeyFromMs(timestampMs) {
  const date = new Date(timestampMs);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function bucketIndexForOffset(offsetMs) {
  return Math.max(0, Math.min(BUCKET_COUNT - 1, Math.floor(offsetMs / BUCKET_MS)));
}

function formatPointForModel(model, timestampMs) {
  return model.viewMode === "avg24h" ? formatOffsetClock(timestampMs) : formatClock(timestampMs);
}

function formatRangeForModel(model, startMs, endMs) {
  return model.viewMode === "avg24h" ? formatFoldedRange(startMs, endMs) : formatRange(startMs, endMs);
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

function formatOffsetClock(offsetMs) {
  if (offsetMs >= DAY_MS) {
    return "24:00";
  }
  const totalMinutes = Math.max(0, Math.floor(offsetMs / (60 * 1000)));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function formatFoldedRange(startMs, endMs) {
  return `${formatOffsetClock(startMs)} - ${formatOffsetClock(endMs)}`;
}

function formatBucketRangeFromIndex(index) {
  return formatFoldedRange(index * BUCKET_MS, (index + 1) * BUCKET_MS);
}

function colorForSource(source) {
  return SOURCE_COLORS[source] || "var(--focus)";
}

function formatAverage(value) {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: value >= 100 ? 0 : 1,
  }).format(value);
}

function formatCompactNumber(value) {
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: value >= 100 ? 0 : 1,
  }).format(value);
}

function formatPercent(value) {
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: value > 0 && value < 0.1 ? 1 : 0,
  }).format(value);
}

function sumValues(values) {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total;
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
