"use strict";

const TOKEN_KEY = "scrape-gateway.operator-token";
const LIVE_REFRESH_MS = 15000;

const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || "",
  service: null,
  session: null,
  runs: [],
  summary: null,
  selectedRunId: null,
  selectedDetail: null,
  selectedStepId: null,
  activeView: "trace",
  previews: new Map(),
  artifactObjectUrl: null,
  activeArtifactPath: null,
  activeOutputLabel: null,
  autoRefresh: true,
  refreshInterval: null,
  toastTimer: null,
  launchInterval: null,
  pendingRun: null,
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
  state.refreshInterval = window.setInterval(() => {
    if (document.visibilityState === "visible" && !state.pendingRun) {
      refreshData({ background: true });
    }
  }, LIVE_REFRESH_MS);
}

function disconnect() {
  stopLiveRefresh();
  state.token = "";
  state.session = null;
  state.runs = [];
  state.summary = null;
  state.selectedRunId = null;
  state.selectedDetail = null;
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
  nodes.authButton.textContent = state.service?.token_required ? "Disconnect" : "Local access";
  setConnection("online", state.session.evaluation?.mode === "audit" ? "Audit enabled" : "Connected");
  configureLiveRefresh();
  await refreshData();
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
  title.append(run.pending ? element("span", "compact-badge", "Running") : compactAuditBadge(run));
  body.append(title);
  body.append(element("span", "run-row-path", parts.path || run.url || "Unknown target"));

  const meta = element("span", "run-row-meta");
  meta.append(element("span", "", run.provider || (run.pending ? "gateway" : "no provider")));
  meta.append(element("span", "", run.pending ? "in progress" : formatDuration(run.elapsed_ms)));
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

async function refreshData({ selectNewest = false, background = false } = {}) {
  if (!state.session) return;
  if (!background) nodes.refreshButton.classList.add("is-refreshing");
  try {
    const [runsPayload, auditPayload] = await Promise.all([
      fetchJson("/api/runs?limit=500"),
      fetchJson("/api/evaluations?limit=500"),
    ]);
    state.runs = runsPayload.runs || [];
    state.summary = auditPayload.summary || null;
    setText(nodes.lastUpdated, `Updated ${new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())}`);
    renderRuns();
    renderSummary();

    if (state.pendingRun) return;
    const selectionExists = state.runs.some((run) => run.run_id === state.selectedRunId);
    if (selectNewest || !selectionExists) state.selectedRunId = state.runs[0]?.run_id || null;
    if (state.selectedRunId) await selectRun(state.selectedRunId, { keepView: background });
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
  nodes.workspaceEmpty.hidden = false;
  nodes.workspaceContent.hidden = true;
}

function metadataItem(label, value) {
  const item = element("span");
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
  setText(nodes.copyRunIdButton, `run ${report.run_id || "pending"}`);
  nodes.copyRunIdButton.dataset.runId = pending ? "" : report.run_id || "";
  setText(nodes.traceUrl, report.url || "Unknown target");
  nodes.copyUrlButton.dataset.url = report.url || "";
  nodes.openUrlButton.href = /^https?:\/\//i.test(report.url || "") ? report.url : "#";
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

  const duration = element("span", "step-duration", step.duration_ms === null || step.duration_ms === undefined ? "—" : formatDuration(step.duration_ms));
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

function renderTraceTimeline(trace) {
  const steps = trace?.steps || [];
  state.selectedStepId = defaultStepId(steps);
  setText(nodes.traceStepCount, `${steps.length} ${steps.length === 1 ? "step" : "steps"}`);
  nodes.traceTimeline.replaceChildren();
  if (!steps.length) {
    nodes.traceTimeline.append(element("div", "empty-note", "No lifecycle steps were recorded."));
    renderStepInspector(null);
    return;
  }
  const total = traceTotal(trace);
  const fragment = document.createDocumentFragment();
  steps.forEach((step) => fragment.append(renderTraceRow(step, total)));
  nodes.traceTimeline.append(fragment);
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
  state.activeView = viewName;
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
}

function renderInspector(detail, { pending = false, keepView = false } = {}) {
  state.selectedDetail = detail;
  state.activeArtifactPath = null;
  state.activeOutputLabel = null;
  nodes.workspaceEmpty.hidden = true;
  nodes.workspaceContent.hidden = false;
  renderTraceHeader(detail, pending);
  renderTraceTimeline(detail.trace);
  renderOutput(detail);
  renderEvaluation(detail.report || {});
  renderArtifacts(detail);
  renderRaw(detail.report || {});
  if (!keepView || pending) showView("trace");
  else showView(state.activeView);
}

async function selectRun(runId, { keepView = false } = {}) {
  if (!runId || runId === "pending") return;
  if (state.selectedRunId !== runId) state.selectedStepId = null;
  state.selectedRunId = runId;
  renderRuns();
  nodes.workspaceEmpty.hidden = false;
  nodes.workspaceContent.hidden = true;
  nodes.workspaceEmpty.querySelector("h2").textContent = "Loading trace…";
  try {
    const detail = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    renderInspector(detail, { keepView });
    nodes.workspaceEmpty.querySelector("h2").textContent = "Select a trace";
  } catch (error) {
    nodes.workspaceEmpty.querySelector("h2").textContent = "Trace unavailable";
    nodes.workspaceEmpty.querySelector("p").textContent = error.message;
    if (error.status === 401) disconnect();
  }
}

function openScrapeDialog() {
  if (!state.session) {
    showAuth("Connect before starting a scrape.");
    return;
  }
  if (!nodes.newScrapeDialog.open) nodes.newScrapeDialog.showModal();
  window.setTimeout(() => nodes.urlInput.focus(), 30);
}

function closeScrapeDialog() {
  if (nodes.newScrapeDialog.open) nodes.newScrapeDialog.close();
}

function pendingTrace(url, payload, status = "running", error = null) {
  const elapsed = state.pendingRun ? Date.now() - new Date(state.pendingRun.started_at).getTime() : 0;
  return {
    report: {
      run_id: "pending",
      started_at: state.pendingRun?.started_at,
      elapsed_ms: elapsed,
      url,
      success: status === "error" ? false : null,
      diagnosis: status === "error" ? "request_failed" : "in_progress",
      request: payload,
      final: {},
    },
    trace: {
      run_id: "pending",
      status: status === "error" ? "error" : "running",
      audit_verdict: null,
      duration_ms: elapsed,
      steps: [
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
          summary: error || "Provider, validation, evaluation, and persistence steps appear when telemetry is saved.",
          offset_ms: 0,
          duration_ms: null,
          timing: "order_only",
          attributes: {},
        },
      ],
    },
    artifacts: [],
  };
}

function renderPendingWorkspace(status = "running", error = null) {
  if (!state.pendingRun) return;
  state.selectedRunId = "pending";
  state.selectedStepId = "gateway";
  renderRuns();
  renderInspector(pendingTrace(state.pendingRun.url, state.pendingRun.payload, status, error), { pending: status !== "error" });
  nodes.activityBar.hidden = false;
  nodes.activityBar.classList.toggle("is-error", status === "error");
  setText(nodes.activityTitle, status === "error" ? "Scrape request failed" : "Scrape in progress");
  setText(nodes.activityDetail, error || "Detailed steps will appear when the run report is persisted.");
}

function startLaunchTimer() {
  window.clearInterval(state.launchInterval);
  const tick = () => {
    if (!state.pendingRun) return;
    const elapsed = Date.now() - new Date(state.pendingRun.started_at).getTime();
    const seconds = Math.floor(elapsed / 1000);
    setText(nodes.activityTimer, `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`);
  };
  tick();
  state.launchInterval = window.setInterval(tick, 1000);
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
    evaluation_goal: String(formData.get("evaluation_goal") || "").trim(),
    output_format: String(formData.get("output_format") || "markdown"),
    screenshot: formData.has("screenshot"),
    render_js: formData.has("render_js"),
    mobile: formData.has("mobile"),
    premium: formData.has("premium"),
    block_ads: formData.has("block_ads"),
    use_cache: !formData.has("fresh"),
  };

  state.pendingRun = {
    pending: true,
    run_id: "pending",
    url,
    domain: urlParts(url).domain,
    started_at: new Date().toISOString(),
    payload,
  };
  nodes.launchButton.disabled = true;
  closeScrapeDialog();
  renderPendingWorkspace();
  startLaunchTimer();

  try {
    const result = await fetchJson("/api/scrapes", { method: "POST", body: JSON.stringify(payload) });
    if (result.run_id && result.preview) state.previews.set(result.run_id, result.preview);
    const runId = result.run_id;
    state.pendingRun = null;
    nodes.activityBar.hidden = true;
    state.selectedRunId = runId || null;
    showToast(result.success ? `Trace ${runId} completed.` : `Trace ${runId} failed; evidence was saved.`, !result.success);
    await refreshData({ selectNewest: !runId });
  } catch (error) {
    renderPendingWorkspace("error", error.message);
    showToast(error.message, true);
    if (error.status === 401) {
      state.pendingRun = null;
      disconnect();
    } else {
      await refreshData({ background: true });
    }
  } finally {
    stopLaunchTimer();
    nodes.launchButton.disabled = false;
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

function bindEvents() {
  nodes.newScrapeButton.addEventListener("click", openScrapeDialog);
  nodes.emptyNewScrapeButton.addEventListener("click", openScrapeDialog);
  nodes.closeScrapeDialog.addEventListener("click", closeScrapeDialog);
  nodes.cancelScrapeButton.addEventListener("click", closeScrapeDialog);
  nodes.scrapeForm.addEventListener("submit", submitScrape);
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
    if (row && row.dataset.runId !== "pending") selectRun(row.dataset.runId);
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
  nodes.copyRawButton.addEventListener("click", () => copyText(JSON.stringify(state.selectedDetail?.report || {}, null, 2), "Raw report copied."));
  window.addEventListener("beforeunload", () => {
    revokeArtifactUrl();
    stopLiveRefresh();
    stopLaunchTimer();
  });
}

function collectNodes() {
  const ids = [
    "service-state", "service-state-label", "version-label", "live-toggle", "live-toggle-label",
    "refresh-button", "new-scrape-button", "auth-button", "run-count", "last-updated",
    "metric-success", "metric-audit-fail", "metric-review", "judge-cost", "run-search",
    "status-filter", "run-list", "trace-workspace", "workspace-empty", "empty-new-scrape-button",
    "workspace-content", "activity-bar", "activity-title", "activity-detail", "activity-timer",
    "trace-status-badge", "trace-audit-badge", "copy-run-id-button", "trace-url", "trace-metadata",
    "copy-url-button", "open-url-button", "artifact-count", "trace-step-count", "trace-timeline",
    "step-inspector", "content-toolbar", "content-viewer", "evaluation-subtitle", "evaluation-content",
    "artifact-list", "artifact-viewer", "raw-viewer", "copy-raw-button", "new-scrape-dialog",
    "scrape-form", "close-scrape-dialog", "cancel-scrape-button", "url-input", "evaluation-goal",
    "launch-button", "auth-dialog", "auth-form", "token-input", "auth-error", "toast",
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
