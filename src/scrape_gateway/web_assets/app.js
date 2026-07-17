"use strict";

const TOKEN_KEY = "scrape-gateway.operator-token";
const LIVE_REFRESH_MS = 15000;
const ACTIVE_REFRESH_MS = 1000;
const VIEW_NAMES = new Set(["trace", "output", "evaluation", "visual", "artifacts", "raw"]);

function deepLinkSelection() {
  const url = new URL(window.location.href);
  const runId = url.searchParams.get("run")?.trim() || null;
  const requestedView = url.searchParams.get("tab") || "trace";
  return {
    runId,
    view: runId && VIEW_NAMES.has(requestedView) ? requestedView : "trace",
  };
}

const initialDeepLink = deepLinkSelection();

const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || "",
  service: null,
  session: null,
  settings: null,
  runs: [],
  summary: null,
  selectedRunId: initialDeepLink.runId,
  selectedDetail: null,
  selectedStepId: null,
  activeView: initialDeepLink.view,
  previews: new Map(),
  artifactObjectUrl: null,
  visualObjectUrl: null,
  activeArtifactPath: null,
  activeOutputLabel: null,
  autoRefresh: true,
  refreshInterval: null,
  toastTimer: null,
  launchInterval: null,
  pendingRun: null,
  retryPayload: null,
  launch: null,
  refreshRequestId: 0,
  selectionRequestId: 0,
  announcedRunIds: new Set(),
  timelineRunId: null,
  timelineWasLive: false,
  timelineStepIds: new Set(),
};

const nodes = {};

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setText(node, value, fallback = "—") {
  node.textContent = value === undefined || value === null || value === "" ? fallback : String(value);
}

function titleCase(value) {
  if (!value) return "Unknown";
  return String(value)
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function formatDuration(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value)) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`;
  return `${Math.floor(value / 60000)}m ${Math.round((value % 60000) / 1000)}s`;
}

function formatLiveDuration(milliseconds) {
  const value = Math.max(0, Number(milliseconds || 0));
  if (value < 60000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.floor(value / 60000)}m ${String(Math.floor((value % 60000) / 1000)).padStart(2, "0")}s`;
}

function formatOffset(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value <= 0) return "+0 ms";
  return `+${formatDuration(value)}`;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value) {
  if (!value) return "Unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatCost(value) {
  const cost = Number(value || 0);
  if (!Number.isFinite(cost) || cost === 0) return "$0";
  if (cost < 0.001) return `<$${cost.toFixed(4).replace(/^0/, "")}`;
  return `$${cost.toFixed(cost < 0.01 ? 4 : 2)}`;
}

function urlParts(value) {
  try {
    const url = new URL(value);
    return {
      domain: url.hostname,
      path: `${url.pathname}${url.search}` || "/",
    };
  } catch (_error) {
    return { domain: value || "Unknown URL", path: "" };
  }
}

function buildRunLink(runId, viewName = state.activeView) {
  const url = new URL(window.location.href);
  url.searchParams.set("run", runId);
  url.searchParams.set("tab", VIEW_NAMES.has(viewName) ? viewName : "trace");
  return url.href;
}

function replaceRunLocation(runId, viewName = state.activeView) {
  if (!runId || runId === "pending") return;
  const url = new URL(buildRunLink(runId, viewName));
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function authHeaders(json = false) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...authHeaders(Boolean(options.body)), ...(options.headers || {}) },
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(payload?.error || `Request failed with HTTP ${response.status}.`, response.status);
  }
  return payload;
}

async function fetchArtifact(artifact) {
  const response = await fetch(artifact.url, { headers: authHeaders() });
  if (!response.ok) {
    let message = `Artifact failed with HTTP ${response.status}.`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (_error) {
      // Proxy errors and image responses are not guaranteed to be JSON.
    }
    throw new ApiError(message, response.status);
  }
  return response;
}

function showToast(message, error = false) {
  window.clearTimeout(state.toastTimer);
  nodes.toast.textContent = message;
  nodes.toast.classList.toggle("is-error", error);
  nodes.toast.classList.add("is-visible");
  state.toastTimer = window.setTimeout(() => nodes.toast.classList.remove("is-visible"), 3500);
}

function announceRunOutcome(run) {
  if (!run?.run_id || state.announcedRunIds.has(run.run_id)) return;
  state.announcedRunIds.add(run.run_id);
  const target = urlParts(run.url).domain;
  showToast(
    run.success
      ? `Scrape completed: ${target}.`
      : `Scrape failed: ${target}. Evidence was saved.`,
    !run.success,
  );
}

function setConnection(kind, label) {
  nodes.serviceState.classList.remove("is-online", "is-error");
  if (kind === "online") nodes.serviceState.classList.add("is-online");
  if (kind === "error") nodes.serviceState.classList.add("is-error");
  setText(nodes.serviceStateLabel, label);
}

function showAuth(message = "") {
  setText(nodes.authError, message, "");
  nodes.tokenInput.value = state.token;
  if (!nodes.authDialog.open) nodes.authDialog.showModal();
  window.setTimeout(() => nodes.tokenInput.focus(), 30);
}

function stopLiveRefresh() {
  window.clearInterval(state.refreshInterval);
  state.refreshInterval = null;
}

function configureLiveRefresh() {
  stopLiveRefresh();
  nodes.liveToggle.setAttribute("aria-pressed", String(state.autoRefresh));
  setText(nodes.liveToggleLabel, state.autoRefresh ? "Live" : "Paused");
  if (!state.autoRefresh || !state.session) return;
  const schedule = () => {
    const delay = state.pendingRun ? ACTIVE_REFRESH_MS : LIVE_REFRESH_MS;
    state.refreshInterval = window.setTimeout(async () => {
      if (document.visibilityState === "visible") {
        await refreshData({ background: true });
      }
      if (state.autoRefresh && state.session) schedule();
    }, delay);
  };
  schedule();
}

function disconnect() {
  stopLiveRefresh();
  state.token = "";
  state.session = null;
  state.settings = null;
  state.runs = [];
  state.summary = null;
  state.selectedRunId = null;
  state.selectedDetail = null;
  state.pendingRun = null;
  state.launch = null;
  state.refreshRequestId += 1;
  state.selectionRequestId += 1;
  state.announcedRunIds.clear();
  sessionStorage.removeItem(TOKEN_KEY);
  nodes.authButton.textContent = "Connect";
  setConnection("error", "Locked");
  renderRuns();
  renderSummary();
  showEmptyWorkspace();
  showAuth();
}

async function connect(token = state.token) {
  state.token = token.trim();
  try {
    state.session = await fetchJson("/api/session");
  } catch (error) {
    state.token = "";
    sessionStorage.removeItem(TOKEN_KEY);
    if (error.status === 401) {
      setConnection("error", "Locked");
      showAuth("That token was not accepted.");
      return false;
    }
    setConnection("error", "Unavailable");
    showAuth(error.message);
    return false;
  }

  if (state.token) sessionStorage.setItem(TOKEN_KEY, state.token);
  else sessionStorage.removeItem(TOKEN_KEY);
  if (nodes.authDialog.open) nodes.authDialog.close();
  populateProviderSelect();
  nodes.authButton.textContent = state.service?.token_required ? "Disconnect" : "Local access";
  setConnection("online", state.session.evaluation?.mode === "audit" ? "Audit enabled" : "Connected");
  try {
    state.settings = await fetchJson("/api/settings");
  } catch (error) {
    state.settings = null;
    showToast(`Gateway settings are unavailable: ${error.message}`, true);
  }
  configureLiveRefresh();
  const deepLink = deepLinkSelection();
  state.activeView = deepLink.view;
  await refreshData({ preferredRunId: deepLink.runId });
  return true;
}

function auditVerdict(run) {
  const evaluation = run?.evaluation;
  if (!evaluation) return "unaudited";
  if (evaluation.verdict === "fail") return "fail";
  if (evaluation.needs_human_review) return "review";
  if (evaluation.verdict === "pass") return "pass";
  return "unaudited";
}

function runStatus(run) {
  if (run?.pending) return "running";
  if (run?.success === false) return "error";
  const verdict = auditVerdict(run);
  if (verdict === "fail") return "audit-fail";
  if (verdict === "review") return "review";
  return "ok";
}

function liveRunElapsed(run = state.pendingRun) {
  const started = new Date(run?.started_at || "").getTime();
  return Number.isFinite(started) ? Math.max(0, Date.now() - started) : 0;
}

function currentLiveStep(run = state.pendingRun) {
  const steps = Array.isArray(run?.steps) ? run.steps : [];
  const running = [...steps].reverse().find((step) => step.status === "running");
  if (running) return running;
  const recorded = steps.find((step) => step.id === run?.current_step?.id);
  return recorded || run?.current_step || steps.at(-1) || null;
}

function liveActivityText(run = state.pendingRun) {
  const step = currentLiveStep(run);
  const elapsed = liveRunElapsed(run);
  if (!step) return `Routing request · ${formatLiveDuration(elapsed)}`;
  const provider = step.attributes?.provider || run?.provider;
  const label = provider
    ? `${titleCase(provider)} attempt`
    : step.kind === "routing"
      ? "Routing"
      : step.name || "Gateway processing";
  const stateLabel = step.status === "running" ? "in progress" : titleCase(step.outcome || step.status);
  const stepElapsed = Math.max(0, elapsed - Number(step.offset_ms || 0));
  return `${label} ${stateLabel.toLowerCase()} · ${formatLiveDuration(stepElapsed)}`;
}

function statusFilterMatches(run, filter) {
  if (filter === "all") return true;
  if (filter === "unaudited") return auditVerdict(run) === "unaudited";
  if (filter === "audit-fail") return auditVerdict(run) === "fail";
  if (filter === "review") return run.evaluation?.needs_human_review === true;
  return runStatus(run) === filter;
}

function matchesRunFilters(run) {
  const query = nodes.runSearch.value.trim().toLowerCase();
  const haystack = `${run.url || ""} ${run.domain || ""} ${run.run_id || ""} ${run.provider || ""} ${run.route || ""}`.toLowerCase();
  return (!query || haystack.includes(query)) && statusFilterMatches(run, nodes.statusFilter.value);
}

function compactAuditBadge(run) {
  const verdict = auditVerdict(run);
  const labels = { pass: "Pass", fail: "Audit fail", review: "Review", unaudited: "No audit" };
  const badge = element("span", "compact-badge", labels[verdict]);
  badge.dataset.verdict = verdict;
  return badge;
}

function renderRunRow(run) {
  const row = element("button", "run-row");
  row.type = "button";
  row.dataset.runId = run.run_id;
  row.dataset.status = runStatus(run);
  row.setAttribute("role", "option");
  const selected = run.run_id === state.selectedRunId;
  row.setAttribute("aria-selected", String(selected));
  row.classList.toggle("is-selected", selected);

  row.append(element("span", "run-status"));
  const body = element("span", "run-row-body");
  const parts = urlParts(run.url);
  const title = element("span", "run-row-title");
  title.append(element("strong", "", run.domain || parts.domain));
  const badge = run.pending ? element("span", "compact-badge", "Running") : compactAuditBadge(run);
  if (run.pending) badge.dataset.status = "running";
  title.append(badge);
  body.append(title);
  body.append(element("span", "run-row-path", parts.path || run.url || "Unknown target"));

  const meta = element("span", "run-row-meta");
  meta.append(element("span", "", run.provider || (run.pending ? "routing" : "no provider")));
  const elapsed = element("span", "", run.pending ? formatLiveDuration(liveRunElapsed(run)) : formatDuration(run.elapsed_ms));
  if (run.pending) elapsed.dataset.liveElapsed = "true";
  meta.append(elapsed);
  if (run.status_code) meta.append(element("span", "", `HTTP ${run.status_code}`));
  body.append(meta);

  const foot = element("span", "run-row-foot");
  foot.append(element("time", "", formatDate(run.started_at)));
  foot.append(element("span", "", run.pending ? "Awaiting trace" : titleCase(run.diagnosis || "unknown")));
  body.append(foot);
  row.append(body);
  return row;
}

function renderRuns() {
  nodes.runList.replaceChildren();
  const filtered = state.runs.filter(matchesRunFilters);
  const pendingMatches = state.pendingRun && matchesRunFilters(state.pendingRun);
  const count = filtered.length + (pendingMatches ? 1 : 0);
  setText(nodes.runCount, `${count} ${count === 1 ? "run" : "runs"}`);

  if (!state.session) {
    nodes.runList.append(element("div", "empty-note", "Connect to load saved traces."));
    return;
  }
  if (pendingMatches) nodes.runList.append(renderRunRow(state.pendingRun));
  if (!count) {
    nodes.runList.append(element("div", "empty-note", "No traces match the current filters."));
    return;
  }
  const fragment = document.createDocumentFragment();
  filtered.forEach((run) => fragment.append(renderRunRow(run)));
  nodes.runList.append(fragment);
}

function renderSummary() {
  if (!state.session) {
    setText(nodes.metricSuccess, "—");
    setText(nodes.metricAuditFail, "—");
    setText(nodes.metricReview, "—");
    setText(nodes.judgeCost, "Judge cost —");
    return;
  }
  const successful = state.runs.filter((run) => run.success === true).length;
  const failedAudits = Number(state.summary?.verdict_counts?.fail || 0);
  const reviewCount = state.summary?.review_queue?.length || 0;
  const usage = state.summary?.usage || {};
  const cost = usage.upstream_inference_cost || usage.cost || 0;
  setText(nodes.metricSuccess, formatNumber(successful));
  setText(nodes.metricAuditFail, formatNumber(failedAudits));
  setText(nodes.metricReview, formatNumber(reviewCount));
  setText(nodes.judgeCost, `Judge cost ${formatCost(cost)}`);
}

function restoreActiveScrape(activeRuns) {
  const active = Array.isArray(activeRuns) ? activeRuns : [];
  const restored =
    active.find((run) => run.run_id === state.pendingRun?.run_id) ||
    active.find((run) => run.url === state.pendingRun?.url) ||
    active[0];
  if (restored) {
    state.pendingRun = { ...restored, pending: true };
    if (state.launch && state.launch.url === restored.url) state.launch.runId = restored.run_id;
    return true;
  }
  if (state.pendingRun?.run_id === "pending") return true;
  state.pendingRun = null;
  stopLaunchTimer();
  nodes.activityBar.hidden = true;
  nodes.workspaceContent.classList.remove("is-pending");
  return false;
}

async function refreshData({ selectNewest = false, background = false, preferredRunId = null } = {}) {
  if (!state.session) return;
  const refreshId = ++state.refreshRequestId;
  if (!background) nodes.refreshButton.classList.add("is-refreshing");
  try {
    const [runsPayload, auditPayload] = await Promise.all([
      fetchJson("/api/runs?limit=500"),
      fetchJson("/api/evaluations?limit=500"),
    ]);
    if (refreshId !== state.refreshRequestId) return;
    const previousPending = state.pendingRun;
    const pendingWasSelected =
      !state.selectedRunId ||
      state.selectedRunId === "pending" ||
      state.selectedRunId === previousPending?.run_id;
    state.runs = runsPayload.runs || [];
    state.summary = auditPayload.summary || null;
    const hasActiveScrape = restoreActiveScrape(runsPayload.active_runs);
    setText(nodes.lastUpdated, `Updated ${new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`);
    renderRuns();
    renderSummary();

    if (hasActiveScrape && pendingWasSelected) {
      renderPendingWorkspace();
      startLaunchTimer();
      return;
    }
    const completedRun = previousPending?.run_id !== "pending"
      ? state.runs.find((run) => run.run_id === previousPending?.run_id)
      : null;
    if (completedRun) {
      announceRunOutcome(completedRun);
      if (pendingWasSelected && state.launch?.watching !== false) preferredRunId = completedRun.run_id;
    }
    const selectionExists = state.runs.some((run) => run.run_id === state.selectedRunId);
    const currentDeepLink = deepLinkSelection();
    if (!preferredRunId && currentDeepLink.runId === state.selectedRunId && !selectionExists) {
      preferredRunId = currentDeepLink.runId;
    }
    if (preferredRunId) state.selectedRunId = preferredRunId;
    else if (selectNewest || !selectionExists) state.selectedRunId = state.runs[0]?.run_id || null;
    if (state.selectedRunId) {
      await selectRun(state.selectedRunId, { keepView: background || Boolean(preferredRunId) });
    }
    else showEmptyWorkspace();
  } catch (error) {
    if (error.status === 401) {
      disconnect();
      return;
    }
    setConnection("error", "Read error");
    if (!background) showToast(error.message, true);
  } finally {
    nodes.refreshButton.classList.remove("is-refreshing");
  }
}

function showEmptyWorkspace() {
  state.selectedDetail = null;
  state.selectedStepId = null;
  state.timelineRunId = null;
  state.timelineWasLive = false;
  state.timelineStepIds.clear();
  nodes.workspaceEmpty.hidden = false;
  nodes.workspaceEmpty.querySelector("h2").textContent = "Select a trace";
  nodes.workspaceEmpty.querySelector("p").textContent = "Inspect each provider attempt, validation decision, AI evaluation, output, and saved artifact.";
  nodes.workspaceLoading.hidden = true;
  nodes.workspaceContent.hidden = true;
  nodes.workspaceContent.classList.remove("is-pending");
}

function showWorkspaceLoading() {
  state.selectedDetail = null;
  nodes.workspaceEmpty.hidden = true;
  nodes.workspaceContent.hidden = true;
  nodes.workspaceLoading.hidden = false;
}

function metadataItem(label, value) {
  const item = element("span");
  item.dataset.metadataLabel = label.toLowerCase();
  item.append(element("strong", "", `${label} `));
  item.append(document.createTextNode(value || "—"));
  return item;
}

function renderTraceHeader(detail, pending = false) {
  const report = detail.report || {};
  const trace = detail.trace || {};
  const final = report.final || {};
  const verdict = auditVerdict({ evaluation: report.evaluation });
  const status = pending ? "running" : trace.status === "ok" ? "ok" : "error";
  nodes.traceStatusBadge.dataset.status = status;
  setText(nodes.traceStatusBadge, pending ? "Running" : status === "ok" ? "Success" : "Failed");
  nodes.traceAuditBadge.dataset.verdict = verdict;
  setText(nodes.traceAuditBadge, { pass: "Audit pass", fail: "Audit fail", review: "Needs review", unaudited: pending ? "Audit pending" : "No audit" }[verdict]);
  const forcedProvider = report.request?.metadata?.preferred_provider || report.request?.provider;
  nodes.forcedProviderBadge.hidden = !forcedProvider;
  setText(nodes.forcedProviderBadge, forcedProvider ? `Forced: ${titleCase(forcedProvider)}` : "", "");
  nodes.forcedProviderBadge.title = forcedProvider
    ? `This run requested ${forcedProvider} first. Global routing settings were not changed.`
    : "";
  setText(nodes.copyRunIdButton, `run ${report.run_id || "pending"}`);
  nodes.copyRunIdButton.dataset.runId = pending ? "" : report.run_id || "";
  nodes.copyLinkButton.dataset.runId = pending ? "" : report.run_id || "";
  nodes.copyLinkButton.hidden = pending;
  setText(nodes.traceUrl, report.url || "Unknown target");
  nodes.copyUrlButton.dataset.url = report.url || "";
  nodes.openUrlButton.href = /^https?:\/\//i.test(report.url || "") ? report.url : "#";
  nodes.retryButton.hidden = pending;
  nodes.retryButton.disabled = pending || !detail.retry?.url;
  nodes.traceMetadata.replaceChildren();
  const metadata = [
    ["Started", formatDate(report.started_at)],
    ["Duration", pending ? "In progress" : formatDuration(trace.duration_ms ?? report.elapsed_ms)],
    ["Provider", final.provider || (pending ? "Routing" : "—")],
    ["Route", final.route || "—"],
    ["Status", final.status ? `HTTP ${final.status}` : "—"],
  ];
  metadata.forEach(([label, value]) => nodes.traceMetadata.append(metadataItem(label, value)));
}

function stepSymbol(status) {
  return { ok: "✓", error: "!", warning: "!", skipped: "–", running: "•", info: "i" }[status] || "i";
}

function traceTotal(trace) {
  const recorded = Number(trace?.duration_ms || 0);
  const stepEnd = Math.max(0, ...(trace?.steps || []).map((step) => Number(step.offset_ms || 0) + Number(step.duration_ms || 0)));
  return Math.max(recorded, stepEnd, 1);
}

function renderTraceRow(step, total) {
  const row = element("button", "trace-row");
  row.type = "button";
  row.dataset.stepId = step.id;
  row.dataset.status = step.status || "info";
  row.classList.toggle("is-child", Boolean(step.parent_id));
  row.classList.toggle("is-selected", step.id === state.selectedStepId);
  row.setAttribute("aria-pressed", String(step.id === state.selectedStepId));

  const main = element("span", "trace-step-main");
  main.append(element("span", "step-state-icon", stepSymbol(step.status)));
  const copy = element("span", "step-copy");
  const nameLine = element("span", "step-name-line");
  nameLine.append(element("strong", "", step.name || "Unnamed step"));
  nameLine.append(element("span", "step-outcome", step.outcome || "unknown"));
  copy.append(nameLine, element("span", "step-summary", step.summary || "No step summary recorded."));
  main.append(copy);

  const isLiveStep = step.status === "running" && (step.duration_ms === null || step.duration_ms === undefined);
  const duration = element("span", "step-duration", isLiveStep ? formatLiveDuration(0) : step.duration_ms === null || step.duration_ms === undefined ? "—" : formatDuration(step.duration_ms));
  if (isLiveStep) duration.dataset.liveStepOffset = String(step.offset_ms || 0);
  const waterfall = element("span", "waterfall");
  const start = Math.max(0, Math.min(98, (Number(step.offset_ms || 0) / total) * 100));
  waterfall.style.setProperty("--start", `${start}%`);
  if (step.duration_ms === null || step.duration_ms === undefined) {
    waterfall.append(element("i", "waterfall-tick"));
  } else {
    const width = Math.max(1.2, Math.min(100 - start, (Number(step.duration_ms || 0) / total) * 100));
    waterfall.style.setProperty("--width", `${width}%`);
    waterfall.append(element("i", "waterfall-bar"));
  }
  row.append(main, duration, waterfall);
  return row;
}

function defaultStepId(steps) {
  const current = steps.find((step) => step.id === state.selectedStepId);
  if (current) return current.id;
  return (
    steps.find((step) => step.status === "error") ||
    steps.find((step) => step.status === "warning") ||
    steps.find((step) => step.kind === "provider") ||
    steps[0]
  )?.id || null;
}

function renderTraceTimeline(trace, { live = false } = {}) {
  const steps = trace?.steps || [];
  const continuingTimeline = state.timelineRunId === trace?.run_id || (live && state.timelineWasLive);
  const previousStepIds = continuingTimeline ? state.timelineStepIds : new Set();
  state.selectedStepId = defaultStepId(steps);
  setText(nodes.traceStepCount, `${steps.length} ${steps.length === 1 ? "step" : "steps"}`);
  nodes.traceTimeline.replaceChildren();
  if (!steps.length) {
    nodes.traceTimeline.append(element("div", "empty-note", "No lifecycle steps were recorded."));
    state.timelineRunId = trace?.run_id || null;
    state.timelineWasLive = live;
    state.timelineStepIds.clear();
    renderStepInspector(null);
    return;
  }
  const total = traceTotal(trace);
  const fragment = document.createDocumentFragment();
  steps.forEach((step) => {
    const row = renderTraceRow(step, total);
    if (previousStepIds.size && !previousStepIds.has(step.id)) row.classList.add("is-new");
    fragment.append(row);
  });
  nodes.traceTimeline.append(fragment);
  state.timelineRunId = trace?.run_id || null;
  state.timelineWasLive = live;
  state.timelineStepIds = new Set(steps.map((step) => step.id));
  renderStepInspector(steps.find((step) => step.id === state.selectedStepId));
}

function attributeValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function renderStepInspector(step) {
  nodes.stepInspector.replaceChildren();
  if (!step) {
    nodes.stepInspector.append(element("div", "inspector-placeholder", "Select a step to inspect its result and attributes."));
    return;
  }

  const heading = element("header", "step-heading");
  const top = element("div", "step-heading-top");
  const status = element("span", "status-badge", titleCase(step.status));
  status.dataset.status = step.status;
  top.append(status, element("span", "kind-badge", titleCase(step.kind)));
  heading.append(top, element("h3", "", step.name), element("p", "", step.summary || "No step summary recorded."));

  const facts = element("dl", "step-facts");
  const factValues = [
    ["Outcome", titleCase(step.outcome)],
    ["Timing", step.timing === "recorded" ? "Recorded" : "Order only"],
    ["Offset", formatOffset(step.offset_ms)],
    ["Duration", step.duration_ms === null || step.duration_ms === undefined ? "Not recorded" : formatDuration(step.duration_ms)],
  ];
  factValues.forEach(([label, value]) => {
    const wrapper = element("div");
    wrapper.append(element("dt", "", label), element("dd", "", value));
    facts.append(wrapper);
  });

  const attributes = element("section", "attribute-section");
  attributes.append(element("h4", "", "Attributes"));
  const entries = Object.entries(step.attributes || {});
  if (!entries.length) {
    attributes.append(element("div", "empty-note", "No attributes were recorded for this step."));
  } else {
    const list = element("div", "attribute-list");
    entries.forEach(([key, value]) => {
      const row = element("div", "attribute-row");
      row.append(element("span", "attribute-key", titleCase(key)));
      const output = element("pre", "attribute-value");
      output.textContent = attributeValue(value);
      row.append(output);
      list.append(row);
    });
    attributes.append(list);
  }
  nodes.stepInspector.append(heading, facts, attributes);
}

function codeLine(text, kind) {
  const line = element("span", "code-line");
  line.textContent = text || " ";
  if (kind === "markdown") {
    if (/^\s{0,3}#{1,6}\s/.test(text)) line.classList.add("code-heading");
    else if (/^\s*```/.test(text)) line.classList.add("code-fence");
    else if (/^\s*>/.test(text)) line.classList.add("code-quote");
    else if (/^\s*(?:[-*+] |\d+\. )/.test(text)) line.classList.add("code-list");
    else if (/\[[^\]]+\]\([^)]+\)/.test(text)) line.classList.add("code-link");
    else if (/`[^`]+`/.test(text)) line.classList.add("code-inline");
  } else if (kind === "json") {
    if (/^\s*"[^"\\]+"\s*:/.test(text)) line.classList.add("code-key");
    else if (/^\s*[}\]]/.test(text)) line.classList.add("code-punctuation");
    else line.classList.add("code-value");
  } else if (kind === "html") {
    if (/^\s*<!--/.test(text)) line.classList.add("code-comment");
    else if (/<\/?[a-z][^>]*>/i.test(text)) line.classList.add("code-tag");
  }
  return line;
}

function renderCode(target, text, kind) {
  target.replaceChildren();
  const fragment = document.createDocumentFragment();
  String(text || "").split("\n").forEach((lineText) => {
    fragment.append(codeLine(lineText, kind));
    fragment.append(document.createTextNode("\n"));
  });
  target.append(fragment);
}

function contentSources(detail) {
  const sources = [];
  const artifacts = detail.artifacts || [];
  [
    ["final.md", "Final Markdown"],
    ["final.html", "Final HTML"],
    ["evaluation/final.md", "Final Markdown"],
    ["evaluation/input.md", "Evaluator input"],
    ["evaluation/final.html", "Final HTML"],
  ].forEach(([path, label]) => {
    const artifact = artifacts.find((item) => item.path === path);
    if (artifact) sources.push({ artifact, kind: artifact.kind, label });
  });
  const preview = state.previews.get(detail.report?.run_id);
  if (preview?.markdown && !sources.some((source) => source.kind === "markdown")) {
    sources.push({ preview: preview.markdown, kind: "markdown", label: "Live Markdown" });
  }
  if (preview?.html && !sources.some((source) => source.kind === "html")) {
    sources.push({ preview: preview.html, kind: "html", label: "Live HTML" });
  }
  return sources;
}

async function loadContentSource(source, button) {
  nodes.contentToolbar.querySelectorAll("button").forEach((item) => item.classList.toggle("is-selected", item === button));
  state.activeOutputLabel = source.label;
  nodes.contentViewer.setAttribute("aria-busy", "true");
  renderCode(nodes.contentViewer, "Loading saved output…", "text");
  try {
    let text = source.preview;
    if (source.artifact) {
      const response = await fetchArtifact(source.artifact);
      text = await response.text();
    }
    renderCode(nodes.contentViewer, text, source.kind);
  } catch (error) {
    renderCode(nodes.contentViewer, error.message, "text");
    if (error.status === 401) disconnect();
  } finally {
    nodes.contentViewer.removeAttribute("aria-busy");
  }
}

function renderOutput(detail) {
  nodes.contentToolbar.replaceChildren();
  renderCode(nodes.contentViewer, "Select an output source.", "text");
  state.activeOutputLabel = null;
  const sources = contentSources(detail);
  if (!sources.length) {
    nodes.contentToolbar.append(element("span", "empty-note", "No saved page body"));
    return;
  }
  sources.forEach((source) => {
    const button = element("button", "content-choice", source.label);
    button.type = "button";
    button.addEventListener("click", () => loadContentSource(source, button));
    nodes.contentToolbar.append(button);
  });
}

function evaluationOverviewCell(label, value) {
  const cell = element("div");
  cell.append(element("span", "evaluation-label", label), element("strong", "", value || "—"));
  return cell;
}

function renderEvaluation(report) {
  nodes.evaluationContent.replaceChildren();
  const evaluation = report.evaluation;
  if (!evaluation) {
    nodes.evaluationContent.append(element("div", "empty-note", `No AI evaluation was saved. Deterministic diagnosis: ${titleCase(report.diagnosis)}.`));
    setText(nodes.evaluationSubtitle, "No AI audit recorded");
    return;
  }
  setText(nodes.evaluationSubtitle, `${evaluation.model || "Unknown model"} · ${evaluation.cached ? "cached" : "fresh"}`);
  const usage = evaluation.usage || {};
  const overview = element("div", "evaluation-overview");
  overview.append(
    evaluationOverviewCell("Verdict", titleCase(evaluation.verdict || evaluation.status)),
    evaluationOverviewCell("Page type", titleCase(evaluation.page_type || "unknown")),
    evaluationOverviewCell("Duration", formatDuration(evaluation.elapsed_ms)),
    evaluationOverviewCell("Cost", formatCost(usage.upstream_inference_cost || usage.cost || 0)),
  );
  nodes.evaluationContent.append(overview);
  if (evaluation.critique || evaluation.error) {
    nodes.evaluationContent.append(element("p", "evaluation-critique", evaluation.critique || evaluation.error));
  }

  const checks = Object.entries(evaluation.checks || {});
  if (checks.length) {
    const section = element("section", "evaluation-section");
    section.append(element("h4", "", "Quality checks"));
    const list = element("div", "check-list");
    checks.forEach(([name, check]) => {
      const row = element("div", "check-row");
      row.append(element("strong", "", titleCase(name)));
      const result = element("span", "check-result", titleCase(check?.result || "unknown"));
      result.dataset.result = check?.result || "unknown";
      row.append(result, element("p", "", check?.evidence || "No evidence was supplied."));
      list.append(row);
    });
    section.append(list);
    nodes.evaluationContent.append(section);
  }

  const signalGroups = [
    ["Improvement opportunities", evaluation.improvement_opportunities || []],
    ["Detected issues", evaluation.issues || []],
  ];
  signalGroups.forEach(([title, values]) => {
    if (!values.length) return;
    const section = element("section", "evaluation-section");
    section.append(element("h4", "", title));
    const list = element("ul", "signal-list");
    values.forEach((value) => {
      const text = typeof value === "string" ? value : value.evidence || value.detail || value.message || JSON.stringify(value);
      list.append(element("li", "", text));
    });
    section.append(list);
    nodes.evaluationContent.append(section);
  });
}

function revokeArtifactUrl() {
  if (!state.artifactObjectUrl) return;
  URL.revokeObjectURL(state.artifactObjectUrl);
  state.artifactObjectUrl = null;
}

function revokeVisualUrl() {
  if (!state.visualObjectUrl) return;
  URL.revokeObjectURL(state.visualObjectUrl);
  state.visualObjectUrl = null;
}

function screenshotArtifact(detail) {
  const images = (detail.artifacts || []).filter((artifact) => artifact.kind === "image");
  return (
    images.find((artifact) => /^screenshot\.(png|jpe?g|webp)$/i.test(artifact.path)) ||
    images.find((artifact) => /(?:^|\/)screenshot\.(png|jpe?g|webp)$/i.test(artifact.path)) ||
    null
  );
}

function visualStateCard(title, detail, status = "info") {
  const card = element("div", "visual-state-card");
  card.dataset.status = status;
  card.append(element("strong", "", title), element("p", "", detail));
  return card;
}

function renderVisual(detail, { pending = false } = {}) {
  revokeVisualUrl();
  nodes.visualViewer.replaceChildren();
  const report = detail.report || {};
  const requested = report.request?.screenshot === true;
  const artifact = screenshotArtifact(detail);
  const provider = report.final?.provider || "provider";

  if (pending) {
    setText(nodes.visualSubtitle, requested ? "Capture requested · waiting for provider" : "Screenshot not requested");
    nodes.visualViewer.append(
      visualStateCard(
        requested ? "Screenshot capture is in progress" : "No screenshot requested",
        requested
          ? "The live trace will report the byte count as soon as a provider returns visual evidence."
          : "Start a scrape with Screenshot enabled to capture rendered visual evidence.",
      ),
    );
    return;
  }

  if (!artifact) {
    const finalBytes = Number(report.final?.screenshot_bytes || 0);
    setText(nodes.visualSubtitle, requested ? "Requested · no saved image" : "Not requested");
    nodes.visualViewer.append(
      visualStateCard(
        requested ? "Screenshot requested, but none was captured" : "No screenshot was requested",
        requested
          ? `${provider} returned ${formatNumber(finalBytes)} screenshot bytes. Inspect the provider step for the exact failure and retry with a screenshot-capable route.`
          : "Enable Screenshot in the New scrape dialog when visual page state matters.",
        requested ? "error" : "info",
      ),
    );
    return;
  }

  setText(nodes.visualSubtitle, `${provider} · ${formatBytes(artifact.size)} · ${artifact.path}`);
  const loading = visualStateCard("Loading saved screenshot", "Fetching the authenticated image artifact.");
  nodes.visualViewer.append(loading);
  const runId = report.run_id;
  fetchArtifact(artifact)
    .then((response) => response.blob())
    .then((blob) => {
      if (state.selectedDetail?.report?.run_id !== runId) return;
      revokeVisualUrl();
      state.visualObjectUrl = URL.createObjectURL(blob);
      const frame = element("figure", "visual-frame");
      const image = element("img");
      image.src = state.visualObjectUrl;
      image.alt = `Rendered screenshot of ${report.url || "the scraped page"}`;
      const caption = element("figcaption", "", `${artifact.path} · ${formatBytes(artifact.size)}`);
      frame.append(image, caption);
      nodes.visualViewer.replaceChildren(frame);
    })
    .catch((error) => {
      if (state.selectedDetail?.report?.run_id !== runId) return;
      nodes.visualViewer.replaceChildren(visualStateCard("Screenshot unavailable", error.message, "error"));
      if (error.status === 401) disconnect();
    });
}

async function loadArtifact(artifact, button) {
  nodes.artifactList.querySelectorAll("button").forEach((item) => item.classList.toggle("is-selected", item === button));
  state.activeArtifactPath = artifact.path;
  nodes.artifactViewer.replaceChildren(element("div", "panel-placeholder", "Loading artifact…"));
  revokeArtifactUrl();
  try {
    const response = await fetchArtifact(artifact);
    if (artifact.kind === "image") {
      const blob = await response.blob();
      state.artifactObjectUrl = URL.createObjectURL(blob);
      const image = element("img");
      image.src = state.artifactObjectUrl;
      image.alt = `Saved scrape screenshot from ${artifact.name}`;
      nodes.artifactViewer.replaceChildren(image);
      return;
    }
    let text = await response.text();
    if (artifact.kind === "json") {
      try {
        text = JSON.stringify(JSON.parse(text), null, 2);
      } catch (_error) {
        // Keep JSONL and partial diagnostics readable as raw text.
      }
    }
    const reader = element("pre", "artifact-code");
    renderCode(reader, text, artifact.kind);
    nodes.artifactViewer.replaceChildren(reader);
  } catch (error) {
    nodes.artifactViewer.replaceChildren(element("div", "panel-placeholder", error.message));
    if (error.status === 401) disconnect();
  }
}

function renderArtifacts(detail) {
  revokeArtifactUrl();
  state.activeArtifactPath = null;
  nodes.artifactList.replaceChildren();
  nodes.artifactViewer.replaceChildren(element("div", "panel-placeholder", "Select an artifact to preview it safely."));
  const artifacts = detail.artifacts || [];
  setText(nodes.artifactCount, artifacts.length, "0");
  if (!artifacts.length) {
    nodes.artifactList.append(element("div", "empty-note", "No artifacts were saved for this run."));
    return;
  }
  artifacts.forEach((artifact) => {
    const button = element("button", "artifact-row");
    button.type = "button";
    button.dataset.artifactPath = artifact.path;
    button.append(element("span", "", artifact.path), element("small", "", formatBytes(artifact.size)));
    button.addEventListener("click", () => loadArtifact(artifact, button));
    nodes.artifactList.append(button);
  });
}

function renderRaw(report) {
  renderCode(nodes.rawViewer, JSON.stringify(report || {}, null, 2), "json");
}

function showView(viewName) {
  state.activeView = VIEW_NAMES.has(viewName) ? viewName : "trace";
  nodes.viewButtons.forEach((button) => {
    const selected = button.dataset.view === viewName;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  Object.entries(nodes.viewPanels).forEach(([name, panel]) => {
    panel.hidden = name !== viewName;
  });
  if (viewName === "output" && state.selectedDetail && !state.activeOutputLabel) {
    nodes.contentToolbar.querySelector("button")?.click();
  }
  if (viewName === "artifacts" && state.selectedDetail && !state.activeArtifactPath) {
    const screenshot = Array.from(nodes.artifactList.querySelectorAll("button")).find((button) => /screenshot\.(png|jpe?g|webp)$/i.test(button.dataset.artifactPath));
    (screenshot || nodes.artifactList.querySelector("button"))?.click();
  }
  if (
    state.selectedRunId &&
    state.selectedRunId !== "pending" &&
    !nodes.workspaceContent.classList.contains("is-pending")
  ) {
    replaceRunLocation(state.selectedRunId, state.activeView);
  }
}

function renderInspector(detail, { pending = false, keepView = false } = {}) {
  state.selectedDetail = detail;
  state.activeArtifactPath = null;
  state.activeOutputLabel = null;
  nodes.workspaceEmpty.hidden = true;
  nodes.workspaceLoading.hidden = true;
  nodes.workspaceContent.hidden = false;
  nodes.workspaceContent.classList.toggle("is-pending", pending);
  renderTraceHeader(detail, pending);
  renderTraceTimeline(detail.trace, { live: pending });
  renderOutput(detail);
  renderEvaluation(detail.report || {});
  renderVisual(detail, { pending });
  renderArtifacts(detail);
  renderRaw(detail.report || {});
  if (!keepView || pending) showView("trace");
  else showView(state.activeView);
}

async function selectRun(runId, { keepView = false, userInitiated = false } = {}) {
  if (!runId || runId === "pending") return;
  if (userInitiated && state.launch && runId !== state.launch.runId && runId !== state.pendingRun?.run_id) {
    state.launch.watching = false;
  }
  const selectionId = ++state.selectionRequestId;
  if (state.selectedRunId !== runId) state.selectedStepId = null;
  state.selectedRunId = runId;
  replaceRunLocation(runId, state.activeView);
  renderRuns();
  const needsLoadingState = !keepView || state.selectedDetail?.report?.run_id !== runId;
  if (needsLoadingState) showWorkspaceLoading();
  try {
    const detail = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    if (selectionId !== state.selectionRequestId || state.selectedRunId !== runId) return;
    renderInspector(detail, { keepView });
  } catch (error) {
    if (selectionId !== state.selectionRequestId || state.selectedRunId !== runId) return;
    if (!needsLoadingState) {
      showToast(`Trace refresh failed: ${error.message}`, true);
      if (error.status === 401) disconnect();
      return;
    }
    nodes.workspaceLoading.hidden = true;
    nodes.workspaceEmpty.hidden = false;
    nodes.workspaceContent.hidden = true;
    nodes.workspaceEmpty.querySelector("h2").textContent = error.status === 404
      ? "Run no longer available"
      : "Trace unavailable";
    nodes.workspaceEmpty.querySelector("p").textContent = error.status === 404
      ? `Run ${runId} is unknown or expired. Check the copied link or choose another trace.`
      : error.message;
    if (error.status === 401) disconnect();
  }
}

function openScrapeDialog() {
  if (!state.session) {
    showAuth("Connect before starting a scrape.");
    return;
  }
  state.retryPayload = null;
  nodes.scrapeForm.reset();
  populateProviderSelect();
  setText(nodes.newScrapeTitle, "New scrape");
  setText(nodes.scrapeDialogDescription, "Run a page through the gateway and save the full trace.");
  setText(nodes.launchButton, "Start scrape");
  if (!nodes.newScrapeDialog.open) nodes.newScrapeDialog.showModal();
  window.setTimeout(() => nodes.urlInput.focus(), 30);
}

function openRetryDialog() {
  const retry = state.selectedDetail?.retry;
  if (!retry?.url) {
    showToast("This trace does not include enough request detail to retry.", true);
    return;
  }
  state.retryPayload = { ...retry };
  nodes.scrapeForm.reset();
  populateProviderSelect();
  nodes.urlInput.value = retry.url;
  nodes.evaluationGoal.value = retry.evaluation_goal || "";
  ["screenshot", "render_js", "mobile", "premium", "block_ads"].forEach((name) => {
    nodes.scrapeForm.elements[name].checked = retry[name] === true;
  });
  nodes.scrapeForm.elements.fresh.checked = true;
  const output = nodes.scrapeForm.querySelector(`input[name="output_format"][value="${retry.output_format}"]`);
  if (output) output.checked = true;
  if (Array.from(nodes.providerSelect.options).some((option) => option.value === retry.provider)) {
    nodes.providerSelect.value = retry.provider;
  }
  setText(nodes.newScrapeTitle, "Retry scrape");
  setText(nodes.scrapeDialogDescription, "Original capture options restored. Cache bypass is required; the provider can be changed for this retry.");
  setText(nodes.launchButton, "Retry scrape");
  if (!nodes.newScrapeDialog.open) nodes.newScrapeDialog.showModal();
  window.setTimeout(() => nodes.providerSelect.focus(), 30);
}

function closeScrapeDialog() {
  if (nodes.newScrapeDialog.open) nodes.newScrapeDialog.close();
}

function pendingTrace(url, payload, status = "running", error = null) {
  const elapsed = liveRunElapsed();
  const pendingId = state.pendingRun?.run_id || "pending";
  const recordedSteps = Array.isArray(state.pendingRun?.steps) ? state.pendingRun.steps : [];
  const fallbackSteps = [
    {
      id: "request",
      parent_id: null,
      name: "Request submitted",
      kind: "request",
      status: "ok",
      outcome: "accepted",
      summary: url,
      offset_ms: 0,
      duration_ms: null,
      timing: "order_only",
      attributes: payload,
    },
    {
      id: "gateway",
      parent_id: null,
      name: status === "error" ? "Gateway request failed" : "Gateway processing",
      kind: "provider",
      status,
      outcome: status === "error" ? "failed" : "in_progress",
      summary: error || "Waiting for the first recorded gateway step.",
      offset_ms: 0,
      duration_ms: null,
      timing: "order_only",
      attributes: {},
    },
  ];
  const steps = recordedSteps.length > 1
    ? recordedSteps
    : [recordedSteps[0] || fallbackSteps[0], fallbackSteps[1]];
  return {
    report: {
      run_id: pendingId,
      started_at: state.pendingRun?.started_at,
      elapsed_ms: elapsed,
      url,
      success: status === "error" ? false : null,
      diagnosis: status === "error" ? "request_failed" : "in_progress",
      request: payload,
      final: {
        provider: state.pendingRun?.provider || currentLiveStep()?.attributes?.provider || null,
      },
    },
    trace: {
      run_id: pendingId,
      status: status === "error" ? "error" : "running",
      audit_verdict: null,
      duration_ms: elapsed,
      steps,
    },
    artifacts: [],
  };
}

function renderPendingWorkspace(status = "running", error = null) {
  if (!state.pendingRun) return;
  state.selectionRequestId += 1;
  state.selectedRunId = state.pendingRun.run_id;
  const latestStep = state.pendingRun.steps?.at(-1);
  state.selectedStepId = latestStep?.id || "gateway";
  renderRuns();
  renderInspector(pendingTrace(state.pendingRun.url, state.pendingRun.payload, status, error), { pending: status !== "error" });
  nodes.activityBar.hidden = false;
  nodes.activityBar.classList.toggle("is-error", status === "error");
  setText(nodes.activityTitle, status === "error" ? "Scrape request failed" : "Scrape in progress");
  setText(nodes.activityDetail, error || liveActivityText());
  updateLiveRunClock();
}

function updateLiveRunClock() {
  if (!state.pendingRun) return;
  const elapsed = liveRunElapsed();
  const seconds = elapsed / 1000;
  setText(nodes.activityTimer, `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`);
  if (!nodes.activityBar.classList.contains("is-error")) setText(nodes.activityDetail, liveActivityText());
  nodes.runList.querySelectorAll("[data-live-elapsed]").forEach((node) => setText(node, formatLiveDuration(elapsed)));
  const duration = nodes.traceMetadata.querySelector('[data-metadata-label="duration"]');
  if (duration) {
    const value = duration.lastChild;
    if (value) value.textContent = formatLiveDuration(elapsed);
  }
  nodes.traceTimeline.querySelectorAll("[data-live-step-offset]").forEach((node) => {
    setText(node, formatLiveDuration(elapsed - Number(node.dataset.liveStepOffset || 0)));
  });
}

function startLaunchTimer() {
  window.clearInterval(state.launchInterval);
  updateLiveRunClock();
  state.launchInterval = window.setInterval(updateLiveRunClock, 100);
}

function stopLaunchTimer() {
  window.clearInterval(state.launchInterval);
  state.launchInterval = null;
}

async function submitScrape(event) {
  event.preventDefault();
  if (!state.session) {
    showAuth("Connect before starting a scrape.");
    return;
  }
  const formData = new FormData(nodes.scrapeForm);
  const url = String(formData.get("url") || "").trim();
  if (!url) {
    nodes.urlInput.focus();
    return;
  }
  const payload = {
    url,
    country: state.retryPayload?.country || null,
    evaluation_goal: String(formData.get("evaluation_goal") || "").trim(),
    provider: String(formData.get("provider") || "").trim(),
    output_format: String(formData.get("output_format") || "markdown"),
    screenshot: formData.has("screenshot"),
    render_js: formData.has("render_js"),
    mobile: formData.has("mobile"),
    premium: formData.has("premium"),
    block_ads: formData.has("block_ads"),
    use_cache: state.retryPayload ? false : !formData.has("fresh"),
  };

  state.pendingRun = {
    pending: true,
    run_id: "pending",
    url,
    domain: urlParts(url).domain,
    started_at: new Date().toISOString(),
    payload,
  };
  state.launch = { url, runId: "pending", watching: true };
  nodes.launchButton.disabled = true;
  closeScrapeDialog();
  renderPendingWorkspace();
  startLaunchTimer();
  configureLiveRefresh();

  try {
    const result = await fetchJson("/api/scrapes", { method: "POST", body: JSON.stringify(payload) });
    if (result.run_id && result.preview) state.previews.set(result.run_id, result.preview);
    const runId = result.run_id;
    const shouldFocus = state.launch?.watching !== false;
    if (state.launch) state.launch.runId = runId;
    state.pendingRun = null;
    nodes.activityBar.hidden = true;
    nodes.workspaceContent.classList.remove("is-pending");
    if (shouldFocus) state.selectedRunId = runId || null;
    announceRunOutcome(result);
    await refreshData({
      selectNewest: shouldFocus && !runId,
      background: !shouldFocus,
      preferredRunId: shouldFocus ? runId : null,
    });
    if (shouldFocus && payload.screenshot && state.selectedDetail?.report?.run_id === runId) showView("visual");
    state.retryPayload = null;
    state.launch = null;
  } catch (error) {
    renderPendingWorkspace("error", error.message);
    showToast(error.message, true);
    if (error.status === 401) {
      state.pendingRun = null;
      disconnect();
    } else {
      await refreshData({ background: true });
      if (!state.pendingRun) state.launch = null;
    }
  } finally {
    stopLaunchTimer();
    nodes.launchButton.disabled = false;
    configureLiveRefresh();
  }
}

async function copyText(value, successMessage) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    showToast(successMessage);
  } catch (_error) {
    showToast("Clipboard access was unavailable.", true);
  }
}

function providerCapabilities(provider) {
  const values = provider.capabilities || [];
  if (!values.length) return "No declared capabilities";
  return values.map(titleCase).join(" · ");
}

function populateProviderSelect() {
  const selected = nodes.providerSelect.value;
  nodes.providerSelect.replaceChildren(new Option("Auto routing", ""));
  (state.session?.providers || []).forEach((provider) => {
    nodes.providerSelect.append(new Option(titleCase(provider), provider));
  });
  nodes.providerSelect.value = Array.from(nodes.providerSelect.options).some(
    (option) => option.value === selected,
  ) ? selected : "";
}

function renderProviderSettings() {
  nodes.providerSettingsList.replaceChildren();
  const providers = state.settings?.providers || [];
  if (!providers.length) {
    nodes.providerSettingsList.append(element("div", "empty-note", "No providers were discovered."));
    return;
  }
  providers.forEach((provider) => {
    const row = element("div", "provider-setting-row");
    row.dataset.providerName = provider.name;

    const toggle = element("label", "provider-toggle");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = provider.enabled;
    checkbox.dataset.providerEnabled = "true";
    checkbox.setAttribute("aria-label", `Enable ${provider.name}`);
    toggle.append(checkbox, element("span", "switch-track"));

    const identity = element("div", "provider-identity");
    const title = element("div", "provider-name-line");
    title.append(element("strong", "", provider.name));
    const availability = element("span", "provider-availability", provider.available ? "Available" : "Not installed");
    availability.dataset.available = String(provider.available);
    title.append(availability);
    identity.append(title, element("small", "", providerCapabilities(provider)));

    const timeout = element("label", "provider-timeout");
    const timeoutInput = document.createElement("input");
    timeoutInput.type = "number";
    timeoutInput.min = "1";
    timeoutInput.max = "600";
    timeoutInput.step = "1";
    timeoutInput.value = provider.timeout_seconds ?? "";
    timeoutInput.placeholder = String(state.settings.default_timeout_seconds);
    timeoutInput.dataset.providerTimeout = "true";
    timeoutInput.setAttribute("aria-label", `${provider.name} timeout in seconds`);
    timeout.append(timeoutInput, element("span", "", "s"));

    row.append(toggle, identity, timeout);
    nodes.providerSettingsList.append(row);
  });
}

async function openSettingsDialog() {
  if (!state.session) {
    showAuth("Connect before changing gateway settings.");
    return;
  }
  setText(nodes.settingsError, "", "");
  try {
    state.settings = await fetchJson("/api/settings");
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  nodes.defaultTimeoutInput.value = state.settings.default_timeout_seconds;
  nodes.evaluationTimeoutInput.value = state.settings.evaluation_timeout_seconds;
  renderProviderSettings();
  if (!nodes.settingsDialog.open) nodes.settingsDialog.showModal();
  window.setTimeout(() => nodes.defaultTimeoutInput.focus(), 30);
}

function closeSettingsDialog() {
  if (nodes.settingsDialog.open) nodes.settingsDialog.close();
}

async function submitSettings(event) {
  event.preventDefault();
  setText(nodes.settingsError, "", "");
  const providers = Array.from(nodes.providerSettingsList.querySelectorAll("[data-provider-name]")).map((row) => {
    const timeoutValue = row.querySelector("[data-provider-timeout]").value.trim();
    return {
      name: row.dataset.providerName,
      enabled: row.querySelector("[data-provider-enabled]").checked,
      timeout_seconds: timeoutValue ? Number(timeoutValue) : null,
    };
  });
  const payload = {
    default_timeout_seconds: Number(nodes.defaultTimeoutInput.value),
    evaluation_timeout_seconds: Number(nodes.evaluationTimeoutInput.value),
    providers,
  };
  nodes.saveSettingsButton.disabled = true;
  try {
    state.settings = await fetchJson("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    state.session = await fetchJson("/api/session");
    closeSettingsDialog();
    showToast("Gateway routing settings saved for new runs.");
  } catch (error) {
    setText(nodes.settingsError, error.message, "");
    if (error.status === 401) disconnect();
  } finally {
    nodes.saveSettingsButton.disabled = false;
  }
}

function bindEvents() {
  nodes.newScrapeButton.addEventListener("click", openScrapeDialog);
  nodes.emptyNewScrapeButton.addEventListener("click", openScrapeDialog);
  nodes.closeScrapeDialog.addEventListener("click", closeScrapeDialog);
  nodes.cancelScrapeButton.addEventListener("click", closeScrapeDialog);
  nodes.scrapeForm.addEventListener("submit", submitScrape);
  nodes.retryButton.addEventListener("click", openRetryDialog);
  nodes.settingsButton.addEventListener("click", openSettingsDialog);
  nodes.closeSettingsDialog.addEventListener("click", closeSettingsDialog);
  nodes.cancelSettingsButton.addEventListener("click", closeSettingsDialog);
  nodes.settingsForm.addEventListener("submit", submitSettings);
  nodes.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setText(nodes.authError, "", "");
    await connect(nodes.tokenInput.value);
  });
  nodes.authDialog.addEventListener("cancel", (event) => {
    if (!state.session) event.preventDefault();
  });
  nodes.authButton.addEventListener("click", () => {
    if (state.session && state.service?.token_required) disconnect();
    else if (!state.session) showAuth();
    else refreshData();
  });
  nodes.refreshButton.addEventListener("click", () => refreshData());
  nodes.liveToggle.addEventListener("click", () => {
    state.autoRefresh = !state.autoRefresh;
    configureLiveRefresh();
    showToast(state.autoRefresh ? "Live refresh enabled." : "Live refresh paused.");
  });
  nodes.runSearch.addEventListener("input", renderRuns);
  nodes.statusFilter.addEventListener("change", renderRuns);
  nodes.runList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-run-id]");
    if (!row) return;
    if (row.dataset.runId === state.pendingRun?.run_id) {
      if (state.launch) state.launch.watching = true;
      renderPendingWorkspace();
    } else selectRun(row.dataset.runId, { userInitiated: true });
  });
  nodes.traceTimeline.addEventListener("click", (event) => {
    const row = event.target.closest("[data-step-id]");
    if (!row || !state.selectedDetail) return;
    state.selectedStepId = row.dataset.stepId;
    nodes.traceTimeline.querySelectorAll("[data-step-id]").forEach((item) => {
      const selected = item.dataset.stepId === state.selectedStepId;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    renderStepInspector(state.selectedDetail.trace?.steps?.find((step) => step.id === state.selectedStepId));
  });
  nodes.viewButtons.forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  nodes.copyUrlButton.addEventListener("click", () => copyText(nodes.copyUrlButton.dataset.url, "Target URL copied."));
  nodes.copyRunIdButton.addEventListener("click", () => copyText(nodes.copyRunIdButton.dataset.runId, "Run ID copied."));
  nodes.copyLinkButton.addEventListener("click", () => {
    const runId = nodes.copyLinkButton.dataset.runId;
    copyText(runId ? buildRunLink(runId) : "", "Shareable link copied.");
  });
  nodes.copyRawButton.addEventListener("click", () => copyText(JSON.stringify(state.selectedDetail?.report || {}, null, 2), "Raw report copied."));
  window.addEventListener("beforeunload", () => {
    revokeArtifactUrl();
    revokeVisualUrl();
    stopLiveRefresh();
    stopLaunchTimer();
  });
}

function collectNodes() {
  const ids = [
    "service-state", "service-state-label", "version-label", "live-toggle", "live-toggle-label",
    "refresh-button", "settings-button", "new-scrape-button", "auth-button", "run-count", "last-updated",
    "metric-success", "metric-audit-fail", "metric-review", "judge-cost", "run-search",
    "status-filter", "run-list", "trace-workspace", "workspace-empty", "empty-new-scrape-button",
    "workspace-loading", "workspace-content", "activity-bar", "activity-title", "activity-detail", "activity-timer",
    "trace-status-badge", "trace-audit-badge", "forced-provider-badge", "copy-run-id-button", "trace-url", "trace-metadata",
    "retry-button", "copy-link-button", "copy-url-button", "open-url-button", "artifact-count", "trace-step-count", "trace-timeline",
    "step-inspector", "content-toolbar", "content-viewer", "evaluation-subtitle", "evaluation-content",
    "visual-subtitle", "visual-viewer",
    "artifact-list", "artifact-viewer", "raw-viewer", "copy-raw-button", "new-scrape-dialog",
    "scrape-form", "new-scrape-title", "scrape-dialog-description", "close-scrape-dialog", "cancel-scrape-button", "url-input", "evaluation-goal", "provider-select",
    "launch-button", "settings-dialog", "settings-form", "close-settings-dialog", "cancel-settings-button",
    "save-settings-button", "provider-settings-list", "default-timeout-input", "evaluation-timeout-input",
    "settings-error", "auth-dialog", "auth-form", "token-input", "auth-error", "toast",
  ];
  ids.forEach((id) => {
    const key = id.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
    nodes[key] = document.getElementById(id);
  });
  nodes.viewButtons = Array.from(document.querySelectorAll("[data-view]"));
  nodes.viewPanels = {
    trace: document.getElementById("trace-panel"),
    output: document.getElementById("output-panel"),
    evaluation: document.getElementById("evaluation-panel"),
    visual: document.getElementById("visual-panel"),
    artifacts: document.getElementById("artifacts-panel"),
    raw: document.getElementById("raw-panel"),
  };
}

async function initialize() {
  collectNodes();
  bindEvents();
  renderRuns();
  renderSummary();
  try {
    state.service = await fetchJson("/api/status");
    setText(nodes.versionLabel, `v${state.service.version}`);
    if (!state.service.token_required) {
      await connect("");
    } else if (state.token) {
      const connected = await connect(state.token);
      if (!connected) showAuth();
    } else {
      setConnection("error", "Locked");
      showAuth();
    }
  } catch (error) {
    setConnection("error", "Offline");
    showToast(`Scrape Gateway is unavailable: ${error.message}`, true);
  }
}

initialize();
