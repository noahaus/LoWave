"use strict";

function applyPipelineEvent(evt, ui) {
  if (evt.type === "status" && evt.message) ui.appendLog(evt.message);
  if (evt.type === "stage") {
    ui.stageState[evt.stage] =
      evt.status === "running" ? "running" : evt.status === "done" ? "done" : evt.status;
    ui.renderPills();
    const statusClass =
      evt.status === "done" ? "ok" : evt.status === "incomplete" ? "warn" : "running";
    ui.setStatus(`${evt.stage}: ${evt.status}`, statusClass);
  }
  if (evt.type !== "run") return;
  if (evt.status === "started") {
    ui.resetStages();
    ui.renderPills();
    ui.clearLog();
    ui.appendLog("Starting the pipeline.");
    ui.setStatus("Running…", "running");
    ui.setRunUi(true);
  }
  if (evt.status === "finished") {
    ui.appendLog("All selected stages finished successfully.");
    appendLogFile(ui, evt.result?.logPath);
    ui.setStatus("Done", "ok", { filePath: evt.result?.specPath || "" });
    ui.setRunUi(false);
  }
  if (evt.status === "cancelled") {
    ui.markRunningStages("cancelled");
    ui.renderPills();
    ui.setStatus("Cancelled", "warn");
    ui.appendLog(evt.explanation || "The run was cancelled before it finished.");
    appendLogFile(ui, evt.logPath);
    ui.setRunUi(false);
  }
  if (evt.status === "failed") {
    ui.setStatus("Failed", "err", { filePath: evt.logPath || "" });
    ui.appendLog("");
    ui.appendLog(failureCopy(evt));
    appendLogFile(ui, evt.logPath);
    ui.setRunUi(false);
  }
  if (evt.status === "incomplete") {
    ui.markRunningStages("incomplete");
    ui.renderPills();
    ui.setStatus("Incomplete — needs review", "warn", { filePath: evt.logPath || "" });
    ui.appendLog("");
    ui.appendLog(failureCopy(evt));
    appendLogFile(ui, evt.logPath);
    ui.setRunUi(false);
  }
}

function failureCopy(evt) {
  const explanation = (evt.explanation || "").trim();
  const lines = ["What went wrong"];
  if (explanation) lines.push(explanation);
  else lines.push("This step could not finish. Open the saved log file for the technical details.");
  return lines.join("\n");
}

function appendLogFile(ui, logPath) {
  if (!logPath || !ui?.appendLog) return;
  ui.appendLog("Run log · ", { filePath: logPath });
}

const inBrowser = typeof document !== "undefined";
const $ = (id) => document.getElementById(id);
const stepTileLib =
  (typeof window !== "undefined" && window.stepTiles) ||
  (typeof require === "function" ? require("./step-tiles") : {});

const viewHome = inBrowser ? $("view-home") : null;
const viewNew = inBrowser ? $("view-new") : null;
const viewProject = inBrowser ? $("view-project") : null;
const viewAbout = inBrowser ? $("view-about") : null;
const viewSettings = inBrowser ? $("view-settings") : null;
const homeBtn = inBrowser ? $("homeBtn") : null;
const aboutBtn = inBrowser ? $("aboutBtn") : null;
const settingsBtn = inBrowser ? $("settingsBtn") : null;
const projectCards = inBrowser ? $("projectCards") : null;
const stepsList = inBrowser ? $("stepsList") : null;
const stepsPreview = inBrowser ? $("stepsPreview") : null;
const logEl = inBrowser ? $("log") : null;
const paneStepsBtn = inBrowser ? $("paneStepsBtn") : null;
const paneLogsBtn = inBrowser ? $("paneLogsBtn") : null;
const statusEl = inBrowser ? $("status") : null;
const homeStatus = inBrowser ? $("homeStatus") : null;
const newStatus = inBrowser ? $("newStatus") : null;
const settingsStatus = inBrowser ? $("settingsStatus") : null;
const addStepsStatus = inBrowser ? $("addStepsStatus") : null;
const stagePills = inBrowser ? $("stagePills") : null;
const parseBtn = inBrowser ? $("parseBtn") : null;
const testBtn = inBrowser ? $("testBtn") : null;
const cancelBtn = inBrowser ? $("cancelBtn") : null;
const noTestsHint = inBrowser ? $("noTestsHint") : null;
const stepTilesEl = inBrowser ? $("stepTiles") : null;

const idleStageState = () => ({ parse: "idle", refine: "idle", generate: "idle", test: "idle" });
const stageState = idleStageState();
const fileUi = {};

let currentProject = null;
let selectedStepsPath = "";
let runningStepsPath = "";
let lastView = "home";
let suggestedModel = "qwen3-coder:30b";
let specExists = false;
let isRunning = false;
let currentStepsText = "";
let currentSpecText = "";
let currentSpecPath = "";
let stepStatuses = {};
let expandedTiles = {};
let filePane = "steps";
let statusText = "Idle";
let statusFilePath = "";
let logEntries = [];

function defaultFileUi() {
  const log = "Status updates will appear here when you run Parse or Test.";
  return {
    log,
    logEntries: [{ text: log, filePath: "" }],
    stageState: idleStageState(),
    status: "Idle",
    statusClass: "status",
    statusFilePath: "",
    stepStatuses: {},
    expandedTiles: {},
    filePane: "steps",
  };
}

function stepsFileName(filePath) {
  return String(filePath || "").split(/[/\\]/).pop();
}

function syncBackendModel() {
  const backend = $("backend").value;
  const model = $("model");
  const nextSuggestion = backend === "ollama" ? "qwen3-coder:30b" : "";
  if (!model.value.trim() || model.value === suggestedModel) {
    model.value = nextSuggestion;
  }
  model.placeholder = nextSuggestion || "provider default";
  suggestedModel = nextSuggestion;
}

function createFileLink(filePath) {
  const link = document.createElement("a");
  link.href = "#";
  link.className = "status-file-link";
  link.textContent = stepsFileName(filePath) || filePath;
  link.title = filePath;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    if (window.qaPipeline?.openPath) window.qaPipeline.openPath(filePath);
  });
  return link;
}

function logEntriesFromText(text) {
  return text ? [{ text: String(text), filePath: "" }] : [];
}

function logEntriesText(entries) {
  return (entries || [])
    .map((entry) => `${entry.text || ""}${entry.filePath ? stepsFileName(entry.filePath) : ""}`)
    .join("\n");
}

function renderLogPane(entries = logEntries) {
  if (!logEl) return;
  logEl.replaceChildren();
  entries.forEach((entry, index) => {
    if (index) logEl.appendChild(document.createTextNode("\n"));
    if (entry.text) logEl.appendChild(document.createTextNode(entry.text));
    if (entry.filePath) logEl.appendChild(createFileLink(entry.filePath));
  });
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(text, cls = "", opts = {}) {
  statusText = text;
  statusFilePath = opts.filePath || "";
  statusEl.className = `status ${cls}`.trim();
  const filePath = statusFilePath;
  const fileLabel = stepsFileName(filePath);
  if (filePath && fileLabel) {
    statusEl.replaceChildren();
    statusEl.append(document.createTextNode(text ? `${text} · ` : ""));
    statusEl.append(createFileLink(filePath));
    return;
  }
  statusEl.textContent = text;
}

function setHomeStatus(text, cls = "") {
  homeStatus.textContent = text;
  homeStatus.className = `status ${cls}`.trim();
}

function setNewStatus(text, cls = "") {
  newStatus.textContent = text;
  newStatus.className = `status ${cls}`.trim();
}

function setSettingsStatus(text, cls = "") {
  settingsStatus.textContent = text;
  settingsStatus.className = `status ${cls}`.trim();
}

function setAddStepsStatus(text, cls = "") {
  addStepsStatus.textContent = text;
  addStepsStatus.className = `status ${cls}`.trim();
}

let saveStatusTimer = null;
function setSaveStatus(text, cls = "") {
  const el = $("saveProjStatus");
  const btn = $("saveProjBtn");
  if (saveStatusTimer) clearTimeout(saveStatusTimer);
  if (btn) {
    if (cls === "ok") {
      btn.classList.add("saved");
      btn.textContent = "Saved";
      saveStatusTimer = setTimeout(() => {
        btn.classList.remove("saved");
        btn.textContent = "Save";
      }, 2000);
    } else {
      btn.classList.remove("saved");
      btn.textContent = "Save";
    }
  }
  if (el) {
    el.textContent = cls === "ok" ? "" : text;
    el.className = `status ${cls === "ok" ? "" : cls}`.trim();
  }
}

function appendLog(line, opts = {}) {
  logEntries.push({ text: line == null ? "" : String(line), filePath: opts.filePath || "" });
  renderLogPane();
}

function setRunUi(running) {
  isRunning = running;
  parseBtn.disabled = running;
  testBtn.disabled = running || !specExists;
  cancelBtn.disabled = !running;
}

function markRunningStages(status) {
  for (const [name, state] of Object.entries(stageState)) {
    if (state === "running") stageState[name] = status;
  }
}

function combinedParseStatus() {
  let status = "idle";
  for (const name of ["parse", "refine", "generate"]) {
    const next = stageState[name];
    if (next && next !== "idle") status = next;
  }
  return status;
}

function renderPills() {
  stagePills.innerHTML = "";
  const parseStatus = combinedParseStatus();
  if (parseStatus !== "idle") {
    const span = document.createElement("span");
    span.className = `pill ${parseStatus}`;
    span.textContent = `parse: ${parseStatus}`;
    stagePills.appendChild(span);
  }
  if (stageState.test && stageState.test !== "idle") {
    const span = document.createElement("span");
    span.className = `pill ${stageState.test}`;
    span.textContent = `test: ${stageState.test}`;
    stagePills.appendChild(span);
  }
}

function currentViewName() {
  if (viewSettings && !viewSettings.hidden) return "settings";
  if (viewAbout && !viewAbout.hidden) return "about";
  if (viewNew && !viewNew.hidden) return "new";
  if (viewProject && !viewProject.hidden) return "project";
  return "home";
}

function updateHeaderNav(view) {
  if (homeBtn) homeBtn.classList.toggle("active", view === "home");
  if (aboutBtn) aboutBtn.classList.toggle("active", view === "about");
  if (settingsBtn) settingsBtn.classList.toggle("active", view === "settings");
}

function hideViews() {
  viewHome.hidden = true;
  viewNew.hidden = true;
  viewProject.hidden = true;
  if (viewAbout) viewAbout.hidden = true;
  viewSettings.hidden = true;
}

function showHome() {
  currentProject = null;
  selectedStepsPath = "";
  hideViews();
  viewHome.hidden = false;
  updateHeaderNav("home");
}

function showNew() {
  currentProject = null;
  selectedStepsPath = "";
  hideViews();
  viewNew.hidden = false;
  updateHeaderNav("new");
  $("newName").value = "";
  $("newBaseUrl").value = "http://localhost:3000";
  $("newUsername").value = "";
  setNewStatus("");
}

function showProject() {
  hideViews();
  viewProject.hidden = false;
  updateHeaderNav("project");
}

function showAbout() {
  if (viewAbout && !viewAbout.hidden) return;
  lastView = currentViewName();
  hideViews();
  if (viewAbout) viewAbout.hidden = false;
  updateHeaderNav("about");
}

function showSettings() {
  if (!viewSettings.hidden) return;
  lastView = currentViewName();
  hideViews();
  viewSettings.hidden = false;
  updateHeaderNav("settings");
  setSettingsStatus("");
}

function restoreLastView() {
  hideViews();
  if (lastView === "new") {
    viewNew.hidden = false;
    updateHeaderNav("new");
  } else if (lastView === "project" && currentProject) {
    viewProject.hidden = false;
    updateHeaderNav("project");
  } else if (lastView === "about" && viewAbout) {
    viewAbout.hidden = false;
    updateHeaderNav("about");
  } else if (lastView === "settings") {
    viewSettings.hidden = false;
    updateHeaderNav("settings");
  } else {
    viewHome.hidden = false;
    updateHeaderNav("home");
  }
}

function leaveSettings() {
  restoreLastView();
}

function setFilePane(pane) {
  filePane = pane === "logs" ? "logs" : "steps";
  const showSteps = filePane === "steps";
  if (stepsPreview) stepsPreview.hidden = !showSteps;
  if (logEl) logEl.hidden = showSteps;
  if (paneStepsBtn) {
    paneStepsBtn.classList.toggle("active", showSteps);
    paneStepsBtn.setAttribute("aria-selected", String(showSteps));
  }
  if (paneLogsBtn) {
    paneLogsBtn.classList.toggle("active", !showSteps);
    paneLogsBtn.setAttribute("aria-selected", String(!showSteps));
  }
}

function showStepsDetail(visible) {
  $("stepsEmptyState").hidden = visible;
  $("stepsDetail").hidden = !visible;
  $("testsEmptyState").hidden = visible;
  $("testsDetail").hidden = !visible;
  if (!visible) {
    specExists = false;
    currentStepsText = "";
    currentSpecText = "";
    currentSpecPath = "";
    stepStatuses = {};
    expandedTiles = {};
    if (noTestsHint) noTestsHint.hidden = true;
    if (stepTilesEl) {
      stepTilesEl.hidden = true;
      stepTilesEl.innerHTML = "";
    }
    setFilePane("steps");
  }
}

function setAddStepsFormOpen(open) {
  $("addStepsForm").hidden = !open;
  $("addStepsBtn").textContent = open ? "Cancel" : "+ Add steps file";
  if (open) {
    $("newStepsName").focus();
  } else {
    $("newStepsName").value = "";
    setAddStepsStatus("");
  }
}

function snapshotSelectedFile() {
  if (!selectedStepsPath || !statusEl) return;
  fileUi[selectedStepsPath] = {
    log: logEntriesText(logEntries),
    logEntries: logEntries.map((entry) => ({ text: entry.text || "", filePath: entry.filePath || "" })),
    stageState: { ...stageState },
    status: statusText,
    statusClass: statusEl.className,
    statusFilePath,
    stepStatuses: { ...stepStatuses },
    expandedTiles: { ...expandedTiles },
    filePane,
  };
}

function restoreSelectedFile() {
  const saved = fileUi[selectedStepsPath] || defaultFileUi();
  Object.keys(stageState).forEach((key) => {
    stageState[key] = saved.stageState[key] || "idle";
  });
  renderPills();
  logEntries = Array.isArray(saved.logEntries)
    ? saved.logEntries.map((entry) => ({ text: entry.text || "", filePath: entry.filePath || "" }))
    : logEntriesFromText(saved.log);
  renderLogPane();
  setStatus(saved.status, String(saved.statusClass || "").replace(/^status\s*/, ""), {
    filePath: saved.statusFilePath || "",
  });
  stepStatuses = { ...(saved.stepStatuses || {}) };
  expandedTiles = { ...(saved.expandedTiles || {}) };
  setFilePane(saved.filePane || "steps");
}

function uiForRun() {
  const path = runningStepsPath || selectedStepsPath;
  if (!path || path === selectedStepsPath) {
    return {
      stageState,
      renderPills,
      setStatus,
      setRunUi,
      markRunningStages,
      appendLog,
      resetStages() {
        Object.keys(stageState).forEach((k) => (stageState[k] = "idle"));
      },
      clearLog() {
        logEntries = [];
        renderLogPane();
      },
    };
  }
  if (!fileUi[path]) fileUi[path] = defaultFileUi();
  const saved = fileUi[path];
  return {
    stageState: saved.stageState,
    renderPills() {},
    setStatus(text, cls = "", opts = {}) {
      saved.status = text;
      saved.statusClass = `status ${cls}`.trim();
      saved.statusFilePath = opts.filePath || "";
    },
    setRunUi,
    markRunningStages(status) {
      for (const [name, state] of Object.entries(saved.stageState)) {
        if (state === "running") saved.stageState[name] = status;
      }
    },
    appendLog(line, opts = {}) {
      if (!saved.logEntries) saved.logEntries = logEntriesFromText(saved.log);
      saved.logEntries.push({ text: line == null ? "" : String(line), filePath: opts.filePath || "" });
      saved.log = logEntriesText(saved.logEntries);
    },
    resetStages() {
      Object.keys(saved.stageState).forEach((k) => (saved.stageState[k] = "idle"));
    },
    clearLog() {
      saved.log = "";
      saved.logEntries = [];
    },
  };
}

function projectHost(url) {
  try {
    return new URL(url).host || url;
  } catch {
    return url || "";
  }
}

function projectInitial(name) {
  const trimmed = String(name || "").trim();
  return trimmed ? trimmed[0].toUpperCase() : "P";
}

function renderNewProjectCard() {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "project-card new-project-card";
  btn.setAttribute("aria-label", "Create a new project");
  btn.innerHTML = `
    <span class="new-project-inner">
      <span class="new-project-plus" aria-hidden="true">+</span>
      <span class="new-project-label">New project</span>
    </span>
  `;
  btn.addEventListener("click", showNew);
  return btn;
}

function renderCards(projects) {
  projectCards.innerHTML = "";
  projectCards.appendChild(renderNewProjectCard());
  for (const project of projects) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "project-card";
    btn.setAttribute("aria-label", `Open ${project.name}`);
    btn.innerHTML = `
      <span class="background"></span>
      <span class="logo">
        <span class="logo-svg"></span>
        <span class="logo-name"></span>
      </span>
      <span class="box box1">
        <span class="icon" title="">
          <svg class="svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M14 3h7v7h-2V6.41l-9.29 9.3-1.42-1.42 9.3-9.29H14V3zM5 5h6v2H7v10h10v-4h2v6H5V5z"></path>
          </svg>
        </span>
      </span>
      <span class="box box2">
        <span class="icon">
          <svg class="svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm8 1.5V8h4.5L14 3.5z"></path>
          </svg>
        </span>
      </span>
      <span class="box box3">
        <span class="icon">
          <svg class="svg" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M4.646 2.146a.5.5 0 0 0 0 .708L7.793 6L4.646 9.146a.5.5 0 1 0 .708.708l3.5-3.5a.5.5 0 0 0 0-.708l-3.5-3.5a.5.5 0 0 0-.708 0z"></path>
          </svg>
        </span>
      </span>
      <span class="box box4"></span>
    `;
    btn.querySelector(".logo-svg").textContent = projectInitial(project.name);
    btn.querySelector(".logo-name").textContent = project.name;
    btn.querySelector(".box1 .icon").setAttribute("title", projectHost(project.baseUrl));
    btn.querySelector(".box2 .icon").setAttribute(
      "title",
      `${project.stepsCount} file${project.stepsCount === 1 ? "" : "s"}`
    );
    btn.addEventListener("click", () => openProjectFromCard(btn, project.slug));
    projectCards.appendChild(btn);
  }
}

function renderSteps(project) {
  stepsList.innerHTML = "";
  if (!project.steps.length) {
    const empty = document.createElement("p");
    empty.className = "steps-empty";
    empty.textContent = "No steps files yet.";
    stepsList.appendChild(empty);
    return;
  }
  for (const step of project.steps) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "steps-item" + (step.path === selectedStepsPath ? " active" : "");
    btn.textContent = step.name;
    btn.addEventListener("click", () => loadSteps(step.path));
    stepsList.appendChild(btn);
  }
}

function fillProjectForm(project) {
  $("projName").value = project.name;
  $("projBaseUrl").value = project.baseUrl;
  $("projUsername").value = project.username || "";
  $("projPassword").value = project.password || "";
}

function headedFromUi() {
  if (!inBrowser) return true;
  const selected = document.querySelector('input[name="browserMode"]:checked');
  if (selected) return selected.value === "headed";
  const headed = $("browserHeaded");
  if (headed) return Boolean(headed.checked);
  return true;
}

function currentSpecOpts() {
  return {
    stepsPath: selectedStepsPath,
    baseUrl: $("projBaseUrl").value.trim() || "http://localhost:3000",
  };
}

function renderStepTiles() {
  if (!stepTilesEl) return;
  const tiles = stepTileLib.buildStepTiles
    ? stepTileLib.buildStepTiles(currentStepsText, currentSpecText)
    : [];
  stepTilesEl.innerHTML = "";
  if (!tiles.length) {
    stepTilesEl.hidden = true;
    return;
  }
  for (const tile of tiles) {
    const status = stepStatuses[tile.number] || "idle";
    const expanded = Boolean(expandedTiles[tile.number]);
    const snippet = stepTileLib.specSnippetForStep
      ? stepTileLib.specSnippetForStep(currentSpecText, tile.number)
      : "";
    const row = document.createElement("div");
    row.className = `step-tile${expanded ? " expanded" : ""}`;
    row.dataset.step = String(tile.number);
    row.setAttribute("role", "button");
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-expanded", String(expanded));
    row.setAttribute("aria-label", `Step ${tile.number} ${status}`);
    const script = snippet
      ? escapeHtml(snippet)
      : "No generated Playwright script for this step yet.";
    row.innerHTML = `
      <span class="step-tile-status ${status}" aria-hidden="true"></span>
      <div class="step-tile-body">
        <div class="step-tile-summary">
          <span class="step-tile-num">${tile.number}.</span>
          ${escapeHtml(tile.text)}
        </div>
        <div class="step-tile-script-wrap">
          <pre class="step-tile-script">${script}</pre>
        </div>
      </div>
    `;
    const toggle = (event) => {
      const target = event.target && event.target.nodeType === 3
        ? event.target.parentElement
        : event.target;
      if (target && target.closest && target.closest(".step-tile-script")) return;
      const next = !Boolean(expandedTiles[tile.number]);
      expandedTiles[tile.number] = next;
      row.classList.toggle("expanded", next);
      row.setAttribute("aria-expanded", String(next));
      snapshotSelectedFile();
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      toggle(event);
    });
    stepTilesEl.appendChild(row);
  }
  stepTilesEl.hidden = false;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function applyTestStepResults(evt) {
  const specText = evt.specText || currentSpecText;
  const steps = stepTileLib.buildStepTiles
    ? stepTileLib.buildStepTiles(currentStepsText, specText)
    : [];
  if (!evt.passed) {
    renderStepTiles();
    snapshotSelectedFile();
    return;
  }
  const hasLive = Object.values(stepStatuses).some((status) => status === "pass" || status === "fail");
  if (hasLive && stepTileLib.statusesAfterProgress) {
    stepStatuses = stepTileLib.statusesAfterProgress(stepStatuses, {
      status: "test-end",
      passed: true,
      steps,
    });
    stepStatuses = Object.fromEntries(steps.map((step) => [step.number, "pass"]));
    renderStepTiles();
    snapshotSelectedFile();
    return;
  }
  const results = stepTileLib.resultsForTestRun
    ? stepTileLib.resultsForTestRun({
        passed: true,
        output: evt.output || "",
        specPath: evt.specPath || currentSpecPath,
        specText,
        steps,
      })
    : [];
  stepStatuses = Object.fromEntries(results.map((result) => [result.number, result.status]));
  renderStepTiles();
  snapshotSelectedFile();
}

function applyTestStepProgress(evt) {
  const tiles = stepTileLib.buildStepTiles
    ? stepTileLib.buildStepTiles(currentStepsText, evt.specText || currentSpecText)
    : [];
  if (!stepTileLib.statusesAfterProgress) return;
  stepStatuses = stepTileLib.statusesAfterProgress(stepStatuses, {
    step: Number(evt.step) || 0,
    status: evt.status,
    passed: Boolean(evt.passed),
    steps: tiles,
  });
  renderStepTiles();
  snapshotSelectedFile();
}

async function refreshSpecStatus() {
  if (!selectedStepsPath || !window.qaPipeline?.specStatus) {
    specExists = false;
    currentSpecText = "";
    currentSpecPath = "";
    if (noTestsHint) noTestsHint.hidden = false;
    if (stepTilesEl) {
      stepTilesEl.hidden = true;
      stepTilesEl.innerHTML = "";
    }
    if (testBtn && !isRunning) testBtn.disabled = true;
    return;
  }
  try {
    const status = await window.qaPipeline.specStatus(currentSpecOpts());
    specExists = Boolean(status.exists);
    currentSpecPath = status.specPath || "";
    currentSpecText = status.content || "";
    if (!isRunning || runningStepsPath !== selectedStepsPath) {
      stepStatuses = { ...(status.stepStatuses || {}) };
    }
    if ($("testsFileTitle")) $("testsFileTitle").textContent = "Generated tests";
  } catch {
    specExists = false;
    currentSpecText = "";
    currentSpecPath = "";
  }
  if (noTestsHint) noTestsHint.hidden = specExists;
  if (testBtn && !isRunning) testBtn.disabled = !specExists;
  if (specExists) renderStepTiles();
  else if (stepTilesEl) {
    stepTilesEl.hidden = true;
    stepTilesEl.innerHTML = "";
  }
}

async function loadSteps(filePath) {
  if (!filePath) {
    snapshotSelectedFile();
    selectedStepsPath = "";
    showStepsDetail(false);
    if (currentProject) renderSteps(currentProject);
    return;
  }
  snapshotSelectedFile();
  selectedStepsPath = filePath;
  const text = await window.qaPipeline.readSteps(filePath);
  currentStepsText = text || "";
  stepsPreview.textContent = currentStepsText || "(empty file)";
  $("stepsFileTitle").textContent = stepsFileName(filePath);
  restoreSelectedFile();
  showStepsDetail(true);
  if (currentProject) renderSteps(currentProject);
  await refreshSpecStatus();
}

async function refreshHome() {
  try {
    const projects = await window.qaPipeline.listProjects();
    setHomeStatus("");
    renderCards(projects);
    return projects;
  } catch (err) {
    renderCards([]);
    setHomeStatus(err.message || String(err), "err");
    return [];
  }
}

async function openProject(slug) {
  currentProject = await window.qaPipeline.getProject(slug);
  fillProjectForm(currentProject);
  setSaveStatus("");
  selectedStepsPath = "";
  setAddStepsFormOpen(false);
  showStepsDetail(false);
  renderSteps(currentProject);
  showProject();
}

let openingProject = false;
function openProjectFromCard(card, slug) {
  if (openingProject) return;
  openingProject = true;
  card.classList.add("pressed");
  const delay = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 220;
  window.setTimeout(() => {
    openProject(slug).finally(() => {
      openingProject = false;
    });
  }, delay);
}

async function loadSettings() {
  try {
    const settings = await window.qaPipeline.getSettings();
    $("backend").value = settings.backend || "ollama";
    $("model").value = settings.model || "";
    suggestedModel = $("backend").value === "ollama" ? "qwen3-coder:30b" : "";
    if (!$("model").value.trim()) syncBackendModel();
    $("model").placeholder = suggestedModel || "provider default";
  } catch {
    syncBackendModel();
  }
}

async function persistSettings() {
  try {
    await window.qaPipeline.saveSettings({
      backend: $("backend").value,
      model: $("model").value.trim(),
    });
    setSettingsStatus("Saved for all projects", "ok");
  } catch (err) {
    setSettingsStatus(err.message || String(err), "err");
  }
}

async function init() {
  await refreshHome();
  await loadSettings();
  showHome();
  $("backend").addEventListener("change", async () => {
    syncBackendModel();
    await persistSettings();
  });
  $("model").addEventListener("change", persistSettings);
  $("model").addEventListener("blur", persistSettings);

  settingsBtn.addEventListener("click", showSettings);
  aboutBtn.addEventListener("click", showAbout);
  homeBtn.addEventListener("click", async () => {
    showHome();
    await refreshHome();
  });
  $("settingsBackBtn").addEventListener("click", leaveSettings);
  $("aboutBackBtn").addEventListener("click", restoreLastView);

  $("createBtn").addEventListener("click", async () => {
    try {
      const project = await window.qaPipeline.createProject({
        name: $("newName").value.trim(),
        baseUrl: $("newBaseUrl").value.trim(),
        username: $("newUsername").value.trim(),
      });
      $("newName").value = "";
      setNewStatus(`Created ${project.name}`, "ok");
      await refreshHome();
      await openProject(project.slug);
    } catch (err) {
      setNewStatus(err.message || String(err), "err");
    }
  });

  $("saveProjBtn").addEventListener("click", async () => {
    if (!currentProject) return;
    try {
      currentProject = await window.qaPipeline.updateProject(currentProject.slug, {
        name: $("projName").value.trim(),
        baseUrl: $("projBaseUrl").value.trim(),
        username: $("projUsername").value.trim(),
        password: $("projPassword").value,
      });
      fillProjectForm(currentProject);
      setSaveStatus("Saved", "ok");
      await refreshSpecStatus();
    } catch (err) {
      setSaveStatus(err.message || String(err), "err");
    }
  });

  $("addStepsBtn").addEventListener("click", () => {
    setAddStepsFormOpen($("addStepsForm").hidden);
  });

  $("createStepsBtn").addEventListener("click", async () => {
    if (!currentProject) return;
    const name = $("newStepsName").value.trim();
    if (!name) {
      setAddStepsStatus("Name the new steps file first", "err");
      return;
    }
    try {
      const before = new Set((currentProject.steps || []).map((s) => s.path));
      currentProject = await window.qaPipeline.addProjectSteps(currentProject.slug, { name });
      const created = currentProject.steps.find((s) => !before.has(s.path));
      setAddStepsFormOpen(false);
      renderSteps(currentProject);
      if (created) await loadSteps(created.path);
    } catch (err) {
      setAddStepsStatus(err.message || String(err), "err");
    }
  });

  $("newStepsName").addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") {
      evt.preventDefault();
      $("createStepsBtn").click();
    }
  });

  $("importBtn").addEventListener("click", async () => {
    if (!currentProject) return;
    const picked = await window.qaPipeline.pickSteps();
    if (!picked) return;
    try {
      const before = new Set((currentProject.steps || []).map((s) => s.path));
      currentProject = await window.qaPipeline.addProjectSteps(currentProject.slug, {
        sourcePath: picked,
      });
      const imported = currentProject.steps.find((s) => !before.has(s.path));
      setAddStepsFormOpen(false);
      renderSteps(currentProject);
      if (imported) await loadSteps(imported.path);
    } catch (err) {
      setAddStepsStatus(err.message || String(err), "err");
    }
  });

  paneStepsBtn.addEventListener("click", () => setFilePane("steps"));
  paneLogsBtn.addEventListener("click", () => setFilePane("logs"));

  window.qaPipeline.onEvent((evt) => {
    applyPipelineEvent(evt, uiForRun());
    if (evt.type === "run" && evt.status === "started") {
      if (!runningStepsPath || runningStepsPath === selectedStepsPath) setFilePane("logs");
    }
    if (evt.type === "stage" && evt.stage === "test" && evt.status === "running") {
      stepStatuses = {};
      renderStepTiles();
    }
    if (evt.type === "test-step") {
      applyTestStepProgress(evt);
    }
    if (evt.type === "test-steps") {
      applyTestStepResults(evt);
    }
    if (evt.type === "run" && (evt.status === "finished" || evt.status === "cancelled" || evt.status === "failed" || evt.status === "incomplete")) {
      runningStepsPath = "";
      refreshSpecStatus();
    }
  });

  async function runAction(kind) {
    if (!currentProject) {
      setStatus("Open a project first", "err");
      return;
    }
    if (!selectedStepsPath) {
      setStatus("Choose a steps file first", "err");
      return;
    }
    if (kind === "test" && !specExists) {
      setStatus("No tests yet. Click Parse first.", "warn");
      return;
    }
    runningStepsPath = selectedStepsPath;
    const parse = kind === "parse";
    try {
      await window.qaPipeline.run({
        stepsPath: selectedStepsPath,
        baseUrl: $("projBaseUrl").value.trim(),
        backend: $("backend").value,
        model: $("model").value.trim(),
        username: $("projUsername").value.trim(),
        password: $("projPassword").value,
        parse,
        refine: parse,
        generate: parse,
        runTests: kind === "test",
        headed: headedFromUi(),
      });
    } catch (err) {
      setStatus(err.message || String(err), "err");
      setRunUi(false);
      runningStepsPath = "";
      await refreshSpecStatus();
    }
  }

  parseBtn.addEventListener("click", () => runAction("parse"));
  testBtn.addEventListener("click", () => runAction("test"));
  $("projBaseUrl").addEventListener("change", refreshSpecStatus);

  cancelBtn.addEventListener("click", async () => {
    cancelBtn.disabled = true;
    try {
      await window.qaPipeline.cancel();
      setStatus("Cancelling…", "running");
    } catch (err) {
      setStatus(err.message || String(err), "err");
      setRunUi(false);
    }
  });
}

if (inBrowser) {
  init().catch((err) => {
    renderCards([]);
    setHomeStatus(err.message || String(err), "err");
    if (logEl) appendLog(String(err));
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { applyPipelineEvent, failureCopy, appendLogFile, projectHost };
}
