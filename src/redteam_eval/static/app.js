"use strict";

const $ = (id) => document.getElementById(id);
const catalogue = new Map();
let lastReport = null;

const targets = {
  reference_vulnerable: {
    label: "Vulnerable reference simulation",
    help: "The reference simulation returns a fixture marker in each response.",
  },
  reference_secure: {
    label: "Hardened reference simulation",
    help: "The reference simulation returns responses without fixture markers.",
  },
  ollama: {
    label: "Local Ollama model",
    help: "Runs benign control exercises against an installed model at 127.0.0.1:11434.",
  },
};
const outcomes = {
  pending: "Pending",
  indicator_observed: "Observed",
  no_indicator_observed: "No indicator",
  error: "Error",
};

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((item) => item.msg).join(" ")
      : `The API returned HTTP ${response.status}.`;
    throw new Error(message);
  }
  if (data === null) throw new Error("The API returned an unreadable response.");
  return data;
}

function setHealth(online) {
  $("health").dataset.state = online ? "online" : "offline";
  $("health-label").textContent = online ? "API online" : "API unavailable";
}

function setNotice(state, title, message) {
  $("notice").dataset.state = state;
  $("notice-title").textContent = title;
  $("notice-text").textContent = message;
}

function updateTarget() {
  const selection = $("target").value;
  const isModel = selection === "ollama";
  $("target-help").textContent = targets[selection].help;
  $("model-field").hidden = !isModel;
  $("model").disabled = !isModel;
  $("mode-tag").dataset.mode = isModel ? "local_model" : "simulation";
  $("mode-tag").textContent = isModel ? "Local inference" : "Reference simulation";
}

async function run(event) {
  event.preventDefault();
  if ($("run").disabled) return;

  const target = $("target").value;
  const isModel = target === "ollama";
  const request = isModel ? { target, model: $("model").value } : { target };
  $("run").disabled = true;
  $("run").dataset.running = "";
  $("run-label").textContent = "Running evaluation";
  $("target").disabled = true;
  $("model").disabled = true;
  const runningMessage = isModel ? "Local inference can take several minutes." : targets[target].label + ".";
  setNotice("running", "Running scenarios", runningMessage);

  try {
    const report = await fetchJson("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: AbortSignal.timeout(isModel ? 500_000 : 12_000),
    });
    if (!report.results?.summary || !Array.isArray(report.results.findings)
        || !Array.isArray(report.results.categories) || !report.execution) {
      throw new Error("The API returned an incomplete report.");
    }
    setHealth(true);
    lastReport = report;
    renderReport(report);
    const { evaluated, total, errors } = report.results.summary;
    const coverage = `${evaluated} of ${total} cases completed.`;
    if (errors > 0) {
      setNotice("warning", "Run finished with errors", coverage + " Review the error rows for details.");
    } else {
      const scope = isModel ? "Local model report." : "Simulated results.";
      setNotice("success", "Evaluation complete", `${coverage} ${scope}`);
    }
  } catch (error) {
    const unreachable = error instanceof TypeError;
    if (unreachable) setHealth(false);
    let message = error.message;
    if (unreachable) message = "Check that the app is running and try again.";
    if (error.name === "TimeoutError") message = "The request took too long. Try the evaluation again.";
    const previous = lastReport ? " The previous report is still displayed." : "";
    setNotice("error", "Evaluation failed", message + previous);
  } finally {
    $("run").disabled = false;
    delete $("run").dataset.running;
    $("run-label").textContent = "Run evaluation";
    $("target").disabled = false;
    $("model").disabled = !isModel;
  }
}

function formatDuration(ms) {
  if (ms === 0) return "< 0.001 ms";
  if (ms < 1) return `${ms.toFixed(3)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function renderReport(report) {
  const { execution, results } = report;
  const { summary } = results;
  const rate = summary.indicator_rate_percent;
  $("total").textContent = summary.total;
  $("total-note").textContent = `${summary.evaluated} of ${summary.total} evaluated`;
  $("observed").textContent = summary.indicator_observed;
  $("not-observed").textContent = summary.no_indicator_observed;
  $("errors").textContent = summary.errors;

  $("rate-value").textContent = rate === null ? "N/A" : `${rate}%`;
  $("rate-ring").style.setProperty("--share", `${rate ?? 0}%`);
  $("rate-context").textContent = summary.evaluated
    ? `${summary.indicator_observed} of ${summary.evaluated} completed cases`
    : "No completed cases";
  $("run-target").textContent = execution.mode === "local_model"
    ? `Ollama · ${execution.model}`
    : targets[report.target].label;
  const started = new Date(execution.started_at);
  const time = node("time", null, started.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }));
  time.dateTime = execution.started_at;
  time.title = execution.started_at;
  $("run-time").replaceChildren(time);
  $("run-duration").textContent = formatDuration(execution.duration_ms);

  $("findings-caption").textContent = execution.mode === "local_model"
    ? `Local model report · ${execution.model}`
    : targets[report.target].label;
  $("csv").disabled = false;
  $("json").disabled = false;
  $("status-filter").disabled = false;

  renderCategories(results.categories);
  renderCategoryFilter(results.categories);
  renderFindings();
}

function catalogueCategories() {
  const counts = new Map();
  for (const scenario of catalogue.values()) {
    counts.set(scenario.category, (counts.get(scenario.category) ?? 0) + 1);
  }
  return Array.from(counts, ([category, total]) => ({ category, total }))
    .sort((first, second) => first.category.localeCompare(second.category));
}

function renderCategories(categories) {
  const list = $("categories");
  list.replaceChildren();
  if (!categories.length) {
    list.append(node("li", "empty-state", "The category breakdown will appear after a run."));
    return;
  }
  for (const category of categories) {
    const { total, indicator_observed: observed, no_indicator_observed: clear, errors } = category;
    const pending = observed === undefined;
    const row = node("li", "category-row");
    const bar = node("span", "category-bar");
    bar.setAttribute("aria-hidden", "true");
    if (!pending) {
      for (const [key, count] of [["observed", observed], ["clear", clear], ["error", errors]]) {
        const segment = node("span", "segment");
        segment.dataset.key = key;
        segment.style.setProperty("--share", `${(count / total) * 100}%`);
        bar.append(segment);
      }
    }
    const count = node("span", "category-count");
    const visibleCount = node("span", null, `${pending ? "–" : observed} / ${total}`);
    visibleCount.setAttribute("aria-hidden", "true");
    const readableCount = pending ? `${total} pending`
      : `${observed} observed, ${clear} with no indicator and ${errors} errors out of ${total} cases`;
    count.append(visibleCount, node("span", "visually-hidden", readableCount));
    row.append(node("span", null, category.category), bar, count);
    list.append(row);
  }
}

function renderCategoryFilter(categories) {
  const filter = $("category-filter");
  const selected = filter.value;
  filter.replaceChildren(new Option("All categories", ""));
  for (const { category } of categories) filter.add(new Option(category, category));
  if (categories.some(({ category }) => category === selected)) filter.value = selected;
}

function renderFindings() {
  const findings = lastReport?.results.findings ?? Array.from(catalogue.values());
  const category = $("category-filter").value;
  const status = $("status-filter").value;
  const filtered = findings.filter((finding) =>
    (!category || finding.category === category) && (!status || finding.status === status));
  const rows = $("rows");
  rows.replaceChildren();
  $("result-count").textContent = findings.length ? `${filtered.length} of ${findings.length} scenarios` : "";

  if (!filtered.length) {
    const message = findings.length
      ? "No scenarios match these filters. Try a different category or outcome."
      : "Scenario information is unavailable. Run a reference evaluation to load findings.";
    const cell = node("td", "empty-state", message);
    cell.colSpan = 5;
    const row = node("tr");
    row.append(cell);
    rows.append(row);
    return;
  }

  for (const finding of filtered) rows.append(...findingRows(finding));
}

function findingRows(finding) {
  const id = finding.scenario_id ?? finding.id;
  const status = finding.status ?? "pending";
  const elapsed = status === "pending" ? "—" : formatDuration(finding.duration_ms);
  const title = node("span");
  title.append(node("span", "scenario-id", id), " ", finding.title);
  const button = node("button", "finding-toggle");
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", `detail-${id}`);
  const chevron = node("span", "chevron");
  chevron.setAttribute("aria-hidden", "true");
  button.append(chevron, title);

  const scenarioCell = node("td", "scenario-cell");
  scenarioCell.append(button);
  const badge = node("span", "badge", outcomes[status]);
  badge.dataset.status = status;
  const statusCell = node("td");
  statusCell.append(badge);
  const row = node("tr", "finding-row");
  row.append(
    scenarioCell, node("td", "table-extra", finding.category),
    node("td", "table-extra mapping", finding.owasp_reference), statusCell,
    node("td", "table-extra elapsed", elapsed),
  );

  const detail = node("div", "detail");
  const scenario = catalogue.get(id) ?? finding;
  if (scenario.description) detail.append(node("p", null, scenario.description));
  if (scenario.expected_behaviour) {
    detail.append(
      node("h3", "response-heading", "Expected local-model behaviour"),
      node("p", null, scenario.expected_behaviour),
    );
  }
  const metadata = node("dl", "detail-meta");
  const fields = [["Category", finding.category], ["OWASP theme", finding.owasp_reference]];
  if (status !== "pending") fields.push(["Elapsed", elapsed]);
  for (const [label, value] of fields) {
    const group = node("div");
    group.append(node("dt", null, label), node("dd", null, value));
    metadata.append(group);
  }
  detail.append(metadata);
  if (status !== "pending") {
    const marker = node("p");
    marker.append("Response indicator ", node("code", null, finding.indicator));
    detail.append(marker);
    detail.append(node("h3", "response-heading", status === "error" ? "Target error" : "Response preview"));
    detail.append(node("pre", "response-preview", finding.response_preview));
  }

  const detailCell = node("td");
  detailCell.colSpan = 5;
  detailCell.append(detail);
  const detailRow = node("tr", "detail-row");
  detailRow.id = `detail-${id}`;
  detailRow.hidden = true;
  detailRow.append(detailCell);

  button.addEventListener("click", () => {
    const expanded = detailRow.hidden;
    button.setAttribute("aria-expanded", String(expanded));
    detailRow.hidden = !expanded;
    row.toggleAttribute("data-expanded", expanded);
  });
  return [row, detailRow];
}

function csvCell(value) {
  let cell = String(value ?? "");
  if (/^[\t\r\n]|^\s*[=+@＝＋＠－-]/u.test(cell)) cell = "'" + cell;
  return '"' + cell.replaceAll('"', '""') + '"';
}

function toCsv(report) {
  const fields = ["scenario_id", "title", "category", "owasp_reference", "status",
    "indicator", "response_preview", "duration_ms"];
  const header = ["target", "mode", "model", "started_at", ...fields];
  const { mode, model, started_at: startedAt } = report.execution;
  const rows = report.results.findings.map((finding) => [
    report.target, mode, model, startedAt, ...fields.map((field) => finding[field]),
  ]);
  // A UTF-8 BOM lets Excel identify the encoding on Windows.
  return "\uFEFF" + [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

function download(format) {
  if (!lastReport) return;
  const isCsv = format === "csv";
  const contents = isCsv ? toCsv(lastReport) : JSON.stringify(lastReport, null, 2) + "\n";
  const type = isCsv ? "text/csv" : "application/json";
  const stamp = lastReport.execution.started_at.replace(/[:.]/g, "-");
  const url = URL.createObjectURL(new Blob([contents], { type: type + ";charset=utf-8" }));
  const link = node("a");
  link.href = url;
  link.download = `evaluation-${lastReport.target}-${stamp}.${format}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function updateNavigation() {
  const section = location.hash || "#overview";
  for (const link of document.querySelectorAll(".nav a")) {
    if (link.hash === section) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}

async function initialise() {
  const [health, listing] = await Promise.allSettled([
    fetchJson("/api/health", { signal: AbortSignal.timeout(5000) }),
    fetchJson("/api/scenarios", { signal: AbortSignal.timeout(5000) }),
  ]);
  setHealth(health.status === "fulfilled" && health.value.status === "ok");
  if (listing.status === "fulfilled") {
    for (const scenario of listing.value.scenarios) catalogue.set(scenario.id, scenario);
    $("total").textContent = catalogue.size;
    $("findings-caption").textContent = "Run an evaluation or expand a scenario to view its description.";
  } else {
    setNotice("warning", "Scenario catalogue unavailable", "Run a reference evaluation or reload the page.");
  }
  const categories = catalogueCategories();
  renderCategories(categories);
  renderCategoryFilter(categories);
  renderFindings();
  $("run").disabled = false;
}

$("target").addEventListener("change", updateTarget);
$("evaluation-form").addEventListener("submit", run);
$("category-filter").addEventListener("change", renderFindings);
$("status-filter").addEventListener("change", renderFindings);
$("csv").addEventListener("click", () => download("csv"));
$("json").addEventListener("click", () => download("json"));
window.addEventListener("hashchange", updateNavigation);

updateTarget();
updateNavigation();
initialise();
