"use strict";

const TOKEN_KEY = "scrape-gateway.operator-token";

const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || "",
  service: null,
  session: null,
  runs: [],
  summary: null,
  selectedRunId: null,
  selectedDetail: null,
  previews: new Map(),
  activeTab: "overview",
  artifactObjectUrl: null,
  launchInterval: null,
  toastTimer: null,
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
  }).format(date);
}

function formatCost(value) {
  const cost = Number(value || 0);
  if (cost === 0) return "$0";
  if (cost < 0.001) return `<$${cost.toFixed(4).replace(/^0/, "")}`;
  return `$${cost.toFixed(cost < 0.01 ? 4 : 2)}`;
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
      // The artifact endpoint may return a non-JSON proxy error.
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
  state.toastTimer = window.setTimeout(() => nodes.toast.classList.remove("is-visible"), 3800);
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

function disconnect() {
  state.token = "";
  state.session = null;
  state.runs = [];
  state.summary = null;
  sessionStorage.removeItem(TOKEN_KEY);
  nodes.authButton.textContent = "Connect";
  setConnection("error", "Locked");
  renderRuns();
  renderMetrics();
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
  const auditLabel = state.session.evaluation?.mode === "audit" ? "Audit ready" : "Connected";
  setConnection("online", auditLabel);
  await refreshData();
  return true;
}

function runDisplayVerdict(run) {
  const evaluation = run.evaluation;
  if (!evaluation) return "unevaluated";
  if (evaluation.verdict === "fail") return "fail";
  if (evaluation.needs_human_review) return "review";
  if (evaluation.verdict === "pass") return "pass";
  return "unevaluated";
}

function verdictLabel(verdict) {
  return {
    pass: "Pass",
    fail: "Fail",
    review: "Review",
    unevaluated: "No audit",
  }[verdict] || "Unknown";
}

function matchesRunFilters(run) {
  const query = nodes.runSearch.value.trim().toLowerCase();
  const filter = nodes.verdictFilter.value;
  const verdict = runDisplayVerdict(run);
  const haystack = `${run.url || ""} ${run.domain || ""} ${run.run_id || ""} ${run.provider || ""}`.toLowerCase();
  const queryMatches = !query || haystack.includes(query);
  let verdictMatches = filter === "all" || filter === verdict;
  if (filter === "review") verdictMatches = run.evaluation?.needs_human_review === true;
  return queryMatches && verdictMatches;
}

function badge(verdict) {
  const node = element("span", `badge badge--${verdict}`, verdictLabel(verdict));
  return node;
}

function renderRunCard(run) {
  const verdict = runDisplayVerdict(run);
  const card = element("button", "run-card");
  card.type = "button";
  card.dataset.runId = run.run_id;
  card.dataset.verdict = verdict;
  card.setAttribute("role", "option");
  card.setAttribute("aria-selected", String(run.run_id === state.selectedRunId));
  if (run.run_id === state.selectedRunId) card.classList.add("is-selected");

  const top = element("div", "run-card-top");
  top.append(element("span", "run-card-domain", run.domain || "Unknown domain"));
  top.append(badge(verdict));

  const url = element("span", "run-card-url", run.url || "Unknown URL");

  const meta = element("div", "run-card-meta");
  meta.append(element("span", "", run.provider || "no provider"));
  meta.append(element("span", "", formatDuration(run.elapsed_ms)));
  if (run.status_code) meta.append(element("span", "", `HTTP ${run.status_code}`));

  const foot = element("div", "run-card-foot");
  foot.append(element("time", "", formatDate(run.started_at)));
  foot.append(element("span", "", run.diagnosis ? titleCase(run.diagnosis) : "No diagnosis"));

  card.append(top, url, meta, foot);
  return card;
}

function renderRuns() {
  const filtered = state.runs.filter(matchesRunFilters);
  nodes.runList.replaceChildren();
  setText(nodes.runCount, `${filtered.length} ${filtered.length === 1 ? "record" : "records"}`);
  if (!state.session) {
    nodes.runList.append(element("div", "empty-note", "Connect to load saved runs."));
    return;
  }
  if (!filtered.length) {
    nodes.runList.append(element("div", "empty-note", "No runs match these filters."));
    return;
  }
  const fragment = document.createDocumentFragment();
  filtered.forEach((run) => fragment.append(renderRunCard(run)));
  nodes.runList.append(fragment);
}

function updateRunSelection() {
  nodes.runList.querySelectorAll("[data-run-id]").forEach((card) => {
    const selected = card.dataset.runId === state.selectedRunId;
    card.classList.toggle("is-selected", selected);
    card.setAttribute("aria-selected", String(selected));
  });
}

function mostFrequent(counts = {}) {
  return Object.entries(counts).sort((left, right) => right[1] - left[1])[0] || null;
}

function renderMetrics() {
  const summary = state.summary;
  if (!summary) {
    [nodes.metricRuns, nodes.metricPassRate, nodes.metricReview, nodes.metricCost].forEach((node) => setText(node, "—"));
    setText(nodes.metricEvaluated, "Awaiting records");
    setText(nodes.metricVerdicts, "No evaluations loaded");
    setText(nodes.metricFailedCheck, "Most failed check: —");
    setText(nodes.metricTokens, "0 tokens recorded");
    renderImprovementWatch([]);
    return;
  }

  const passes = Number(summary.verdict_counts?.pass || 0);
  const failures = Number(summary.verdict_counts?.fail || 0);
  const judged = passes + failures;
  const rate = judged ? `${Math.round((passes / judged) * 100)}%` : "—";
  const reviewCount = summary.review_queue?.length || 0;
  const failedCheck = mostFrequent(summary.check_failure_counts);
  const usage = summary.usage || {};
  const cost = usage.upstream_inference_cost || usage.cost || 0;

  setText(nodes.metricRuns, formatNumber(summary.runs_scanned));
  setText(nodes.metricEvaluated, `${formatNumber(summary.evaluated_runs)} evaluated · ${formatNumber(summary.unevaluated_runs)} pending`);
  setText(nodes.metricPassRate, rate);
  setText(nodes.metricVerdicts, `${formatNumber(passes)} pass · ${formatNumber(failures)} fail`);
  setText(nodes.metricReview, formatNumber(reviewCount));
  setText(nodes.metricFailedCheck, failedCheck ? `Most failed: ${titleCase(failedCheck[0])} (${failedCheck[1]})` : "Most failed check: none");
  setText(nodes.metricCost, formatCost(cost));
  setText(nodes.metricTokens, `${formatNumber(usage.total_tokens)} tokens · ${formatNumber(usage.cached_runs)} cached`);
  renderImprovementWatch(summary.improvement_opportunities || []);
}

function renderImprovementWatch(opportunities) {
  if (!nodes.opportunityTicker) return;
  nodes.opportunityTicker.replaceChildren();
  if (!opportunities.length) {
    nodes.opportunityTicker.append(element("span", "watch-empty", "No recurring improvements yet."));
    return;
  }
  opportunities.slice(0, 4).forEach((item) => {
    const signal = element("span", "watch-signal");
    signal.append(element("strong", "", `${item.count}×`));
    signal.append(document.createTextNode(` ${item.text}`));
    nodes.opportunityTicker.append(signal);
  });
}

async function refreshData({ selectNewest = false } = {}) {
  if (!state.session) return;
  nodes.refreshButton.classList.add("is-refreshing");
  try {
    const [runsPayload, auditPayload] = await Promise.all([
      fetchJson("/api/runs?limit=500"),
      fetchJson("/api/evaluations?limit=500"),
    ]);
    state.runs = runsPayload.runs || [];
    state.summary = auditPayload.summary || null;
    renderRuns();
    renderMetrics();

    const selectionExists = state.runs.some((run) => run.run_id === state.selectedRunId);
    if (selectNewest || !selectionExists) {
      state.selectedRunId = state.runs[0]?.run_id || null;
    }
    if (state.selectedRunId) await selectRun(state.selectedRunId, { keepScroll: true });
    else showEmptyInspector();
  } catch (error) {
    if (error.status === 401) {
      disconnect();
      return;
    }
    setConnection("error", "Read error");
    showToast(error.message, true);
  } finally {
    nodes.refreshButton.classList.remove("is-refreshing");
  }
}

function showEmptyInspector() {
  state.selectedDetail = null;
  nodes.inspectorEmpty.hidden = false;
  nodes.inspectorContent.hidden = true;
}

function metaChip(text) {
  return element("span", "meta-chip", text);
}

function addRibbonCell(parent, label, value) {
  const cell = element("div", "ribbon-cell");
  cell.append(element("span", "", label));
  cell.append(element("strong", "", value || "—"));
  parent.append(cell);
}

function sectionBlock(title, hint = "") {
  const section = element("section", "section-block");
  const heading = element("header", "section-heading");
  heading.append(element("h4", "", title));
  if (hint) heading.append(element("span", "", hint));
  const body = element("div", "section-body");
  section.append(heading, body);
  return { section, body };
}

function renderEvaluation(report) {
  nodes.evaluationBody.replaceChildren();
  const evaluation = report.evaluation;

  if (!evaluation) {
    const deterministic = sectionBlock("Deterministic result", "AI audit not recorded");
    deterministic.body.append(
      element(
        "div",
        "critique-box",
        `The gateway classified this run as ${titleCase(report.diagnosis)}. ${report.recommended_next_action && report.recommended_next_action !== "none" ? `Recommended next action: ${titleCase(report.recommended_next_action)}.` : "No follow-up action was recorded."}`,
      ),
    );
    nodes.evaluationBody.append(deterministic.section);
    return;
  }

  const judgment = sectionBlock("Evaluator judgment", `${evaluation.model || "Unknown model"} · ${evaluation.cached ? "cached" : "fresh"}`);
  judgment.body.append(element("div", "critique-box", evaluation.critique || evaluation.error || "No critique was returned."));
  nodes.evaluationBody.append(judgment.section);

  const checks = sectionBlock("Quality checks", evaluation.prompt_version || "audit schema");
  const checksGrid = element("div", "checks-grid");
  const checkEntries = Object.entries(evaluation.checks || {});
  if (!checkEntries.length) {
    checksGrid.append(element("div", "empty-note", "No check-level evidence was saved."));
  }
  checkEntries.forEach(([name, check]) => {
    const card = element("article", "check-card");
    const top = element("div", "check-card-top");
    top.append(element("h5", "", titleCase(name)));
    const result = String(check?.result || "unknown").replace(/_/g, "-");
    top.append(element("span", `result-chip result-chip--${result}`, titleCase(check?.result || "unknown")));
    card.append(top, element("p", "", check?.evidence || "No evidence supplied."));
    checksGrid.append(card);
  });
  checks.body.append(checksGrid);
  nodes.evaluationBody.append(checks.section);

  const opportunities = evaluation.improvement_opportunities || [];
  if (opportunities.length) {
    const improvement = sectionBlock("Improvement opportunities", `${opportunities.length} signal${opportunities.length === 1 ? "" : "s"}`);
    const list = element("ul", "improvement-list");
    opportunities.forEach((opportunity) => list.append(element("li", "", opportunity)));
    improvement.body.append(list);
    nodes.evaluationBody.append(improvement.section);
  }

  const issues = evaluation.issues || [];
  if (issues.length) {
    const issueBlock = sectionBlock("Detected issues", `${issues.length} issue${issues.length === 1 ? "" : "s"}`);
    const list = element("ul", "issue-list");
    issues.forEach((issue) => {
      const item = element("li");
      const name = issue.code || issue.category || "Issue";
      item.append(element("strong", "", `${titleCase(name)}${issue.severity ? ` · ${titleCase(issue.severity)}` : ""}`));
      if (issue.evidence || issue.detail || issue.message) {
        item.append(document.createTextNode(` — ${issue.evidence || issue.detail || issue.message}`));
      }
      list.append(item);
    });
    issueBlock.body.append(list);
    nodes.evaluationBody.append(issueBlock.section);
  }
}

function renderAttempts(report) {
  nodes.attemptsBody.replaceChildren();
  const attempts = report.attempts || [];
  const block = sectionBlock("Provider attempts", `${attempts.length} attempted · ${(report.skipped || []).length} skipped`);
  if (!attempts.length) {
    block.body.append(element("div", "empty-note", report.final?.provider === "cache" ? "Served from cache; no provider call was needed." : "No provider attempts were recorded."));
  } else {
    const list = element("div", "attempt-list");
    attempts.forEach((attempt) => {
      const row = element("div", "attempt-row");
      row.dataset.result = attempt.result || "failed";
      row.append(element("span", "attempt-provider", attempt.provider || "unknown"));
      row.append(element("span", "attempt-result", titleCase(attempt.result || attempt.failure_reason || "failed")));
      row.append(element("span", "", formatDuration(attempt.elapsed_ms)));
      row.append(element("span", "", attempt.route || attempt.reason || attempt.block_type || (attempt.status ? `HTTP ${attempt.status}` : "—")));
      list.append(row);
    });
    block.body.append(list);
  }
  nodes.attemptsBody.append(block.section);
}

function contentSources(detail) {
  const sources = [];
  const artifacts = detail.artifacts || [];
  const preferredPaths = [
    "evaluation/final.md",
    "evaluation/input.md",
    "evaluation/final.html",
  ];
  preferredPaths.forEach((path) => {
    const artifact = artifacts.find((item) => item.path === path);
    if (artifact) sources.push({ artifact, kind: artifact.kind, label: artifact.name });
  });
  const preview = state.previews.get(detail.report.run_id);
  if (preview?.markdown && !sources.some((source) => source.kind === "markdown")) {
    sources.push({ preview: preview.markdown, kind: "markdown", label: "Live Markdown" });
  }
  if (preview?.html && !sources.some((source) => source.kind === "html")) {
    sources.push({ preview: preview.html, kind: "html", label: "Live HTML" });
  }
  return sources;
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

async function loadContentSource(source, button) {
  nodes.contentToolbar.querySelectorAll("button").forEach((item) => item.classList.toggle("is-selected", item === button));
  nodes.contentViewer.setAttribute("aria-busy", "true");
  renderCode(nodes.contentViewer, "Loading evidence…", "text");
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

function renderContentChoices(detail) {
  nodes.contentToolbar.replaceChildren();
  renderCode(nodes.contentViewer, "Select a Markdown or HTML artifact.", "text");
  const sources = contentSources(detail);
  if (!sources.length) {
    nodes.contentToolbar.append(element("span", "empty-note", "This run has no saved page body."));
    return;
  }
  sources.forEach((source) => {
    const button = element("button", "content-choice", source.label);
    button.type = "button";
    button.addEventListener("click", () => loadContentSource(source, button));
    nodes.contentToolbar.append(button);
  });
}

function revokeArtifactUrl() {
  if (state.artifactObjectUrl) {
    URL.revokeObjectURL(state.artifactObjectUrl);
    state.artifactObjectUrl = null;
  }
}

async function loadArtifact(artifact, button) {
  nodes.artifactList.querySelectorAll("button").forEach((item) => item.classList.toggle("is-selected", item === button));
  nodes.artifactViewer.replaceChildren(element("div", "artifact-placeholder", "Loading artifact…"));
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
        // JSONL and partial diagnostic files remain readable as raw text.
      }
    }
    const reader = element("pre", "artifact-code");
    renderCode(reader, text, artifact.kind);
    nodes.artifactViewer.replaceChildren(reader);
  } catch (error) {
    nodes.artifactViewer.replaceChildren(element("div", "artifact-placeholder", error.message));
    if (error.status === 401) disconnect();
  }
}

function renderArtifacts(detail) {
  nodes.artifactList.replaceChildren();
  nodes.artifactViewer.replaceChildren(element("div", "artifact-placeholder", "Choose an artifact to inspect it safely."));
  revokeArtifactUrl();
  const artifacts = detail.artifacts || [];
  setText(nodes.artifactCount, artifacts.length, "0");
  if (!artifacts.length) {
    nodes.artifactList.append(element("div", "empty-note", "No artifacts were saved for this run."));
    return;
  }
  artifacts.forEach((artifact) => {
    const button = element("button", "artifact-button");
    button.type = "button";
    button.append(element("span", "", artifact.path));
    button.append(element("small", "", formatBytes(artifact.size)));
    button.addEventListener("click", () => loadArtifact(artifact, button));
    nodes.artifactList.append(button);
  });
}

function showTab(tabName) {
  state.activeTab = tabName;
  nodes.tabButtons.forEach((button) => {
    const selected = button.dataset.tab === tabName;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  Object.entries(nodes.tabPanels).forEach(([name, panel]) => {
    panel.hidden = name !== tabName;
  });
  if (tabName === "content" && state.selectedDetail) {
    const first = nodes.contentToolbar.querySelector("button");
    if (first && !nodes.contentToolbar.querySelector(".is-selected")) first.click();
  }
}

function renderInspector(detail) {
  state.selectedDetail = detail;
  const report = detail.report;
  const final = report.final || {};
  const evaluation = report.evaluation;
  const verdict = runDisplayVerdict({ evaluation });

  nodes.inspectorEmpty.hidden = true;
  nodes.inspectorContent.hidden = false;
  setText(nodes.inspectorDomain, report.domain || "Unknown domain");
  setText(nodes.inspectorUrl, report.url || "Unknown URL");
  nodes.inspectorMeta.replaceChildren();
  [
    final.provider && `provider · ${final.provider}`,
    final.route && `route · ${final.route}`,
    `duration · ${formatDuration(report.elapsed_ms)}`,
    final.status && `HTTP ${final.status}`,
    report.run_id && `run · ${report.run_id}`,
    final.markdown_chars && `${formatNumber(final.markdown_chars)} md chars`,
  ].filter(Boolean).forEach((item) => nodes.inspectorMeta.append(metaChip(item)));

  nodes.inspectorBeacon.dataset.verdict = verdict;
  nodes.openUrlButton.href = /^https?:\/\//i.test(report.url || "") ? report.url : "#";
  nodes.copyUrlButton.dataset.url = report.url || "";

  nodes.auditRibbon.replaceChildren();
  nodes.auditRibbon.dataset.verdict = verdict;
  addRibbonCell(nodes.auditRibbon, "Audit verdict", verdictLabel(verdict));
  addRibbonCell(nodes.auditRibbon, "Page type", evaluation?.page_type ? titleCase(evaluation.page_type) : "Not classified");
  addRibbonCell(nodes.auditRibbon, "Recommended action", evaluation?.recommended_action ? titleCase(evaluation.recommended_action) : titleCase(report.recommended_next_action || "none"));

  renderEvaluation(report);
  renderAttempts(report);
  renderContentChoices(detail);
  renderArtifacts(detail);
  showTab("overview");
}

async function selectRun(runId, { keepScroll = false } = {}) {
  if (!runId) return;
  state.selectedRunId = runId;
  updateRunSelection();
  nodes.inspectorEmpty.hidden = false;
  nodes.inspectorContent.hidden = true;
  nodes.inspectorEmpty.querySelector("h3").textContent = "Opening the evidence bundle…";
  try {
    const detail = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    renderInspector(detail);
    if (!keepScroll && window.matchMedia("(max-width: 840px)").matches) {
      nodes.runInspector.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    nodes.inspectorEmpty.querySelector("h3").textContent = "This evidence bundle could not be opened.";
    nodes.inspectorEmpty.querySelector("p").textContent = error.message;
    if (error.status === 401) disconnect();
  }
}

function startLaunchTimer() {
  const startedAt = Date.now();
  window.clearInterval(state.launchInterval);
  const tick = () => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    const minutes = Math.floor(seconds / 60);
    setText(nodes.launchTimer, `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`);
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

  nodes.launchButton.disabled = true;
  nodes.launchStatus.className = "launch-status is-running";
  setText(nodes.launchStatusText, payload.render_js ? "Rendering and validating…" : "Fetching and validating…");
  startLaunchTimer();

  try {
    const result = await fetchJson("/api/scrapes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (result.run_id && result.preview) state.previews.set(result.run_id, result.preview);
    nodes.launchStatus.className = "launch-status is-complete";
    setText(nodes.launchStatusText, result.success ? `Captured via ${result.provider}` : "Capture failed — evidence saved");
    showToast(result.success ? `Run ${result.run_id} completed.` : `Run ${result.run_id} failed; open it for evidence.`, !result.success);
    if (result.run_id) state.selectedRunId = result.run_id;
    await refreshData({ selectNewest: !result.run_id });
  } catch (error) {
    nodes.launchStatus.className = "launch-status";
    setText(nodes.launchStatusText, "Capture did not start");
    showToast(error.message, true);
    if (error.status === 401) disconnect();
  } finally {
    stopLaunchTimer();
    nodes.launchButton.disabled = false;
  }
}

function bindEvents() {
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
  nodes.runSearch.addEventListener("input", renderRuns);
  nodes.verdictFilter.addEventListener("change", renderRuns);
  nodes.runList.addEventListener("click", (event) => {
    const card = event.target.closest("[data-run-id]");
    if (card) selectRun(card.dataset.runId);
  });
  nodes.copyUrlButton.addEventListener("click", async () => {
    const url = nodes.copyUrlButton.dataset.url;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      showToast("URL copied to clipboard.");
    } catch (_error) {
      showToast("Clipboard access was unavailable.", true);
    }
  });
  nodes.tabButtons.forEach((button) => button.addEventListener("click", () => showTab(button.dataset.tab)));
  window.addEventListener("beforeunload", revokeArtifactUrl);
}

function collectNodes() {
  const ids = [
    "service-state", "service-state-label", "version-label", "refresh-button", "auth-button",
    "metric-runs", "metric-evaluated", "metric-pass-rate", "metric-verdicts", "metric-review",
    "metric-failed-check", "metric-cost", "metric-tokens", "opportunity-ticker", "scrape-form",
    "url-input", "evaluation-goal", "launch-button", "launch-status", "launch-status-text",
    "launch-timer", "run-search", "verdict-filter", "run-count", "run-list", "run-inspector",
    "inspector-empty", "inspector-content", "inspector-domain", "inspector-url", "inspector-meta",
    "inspector-beacon", "open-url-button", "copy-url-button", "audit-ribbon", "artifact-count",
    "evaluation-body", "attempts-body", "content-toolbar", "content-viewer", "artifact-list",
    "artifact-viewer", "auth-dialog", "auth-form", "token-input", "auth-error", "toast",
  ];
  ids.forEach((id) => {
    const key = id.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
    nodes[key] = document.getElementById(id);
  });
  nodes.tabButtons = Array.from(document.querySelectorAll("[data-tab]"));
  nodes.tabPanels = {
    overview: document.getElementById("overview-panel"),
    content: document.getElementById("content-panel"),
    artifacts: document.getElementById("artifacts-panel"),
  };
}

async function initialize() {
  collectNodes();
  bindEvents();
  renderRuns();
  renderMetrics();
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
