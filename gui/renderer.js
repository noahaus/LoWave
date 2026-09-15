"use strict";

function applyPipelineEvent(evt, ui) {
  if (evt.type === "log") ui.appendLog(evt.line);
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
    ui.setStatus("Running…", "running");
    ui.setRunUi(true);
  }
  if (evt.status === "finished") {
    ui.setStatus(`Done → ${evt.result?.specPath || "ok"}`, "ok");
    ui.setRunUi(false);
  }
  if (evt.status === "cancelled") {
    ui.markRunningStages("cancelled");
    ui.renderPills();
    ui.setStatus("Cancelled", "warn");
    ui.appendLog("Pipeline cancelled.");
    ui.setRunUi(false);
  }
  if (evt.status === "failed") {
    ui.setStatus(evt.error || "Failed", "err");
    if (evt.stderr) ui.appendLog(evt.stderr.slice(-3000));
    ui.setRunUi(false);
  }
  if (evt.status === "incomplete") {
    ui.markRunningStages("incomplete");
    ui.renderPills();
    ui.setStatus("Incomplete — needs review", "warn");
    if (evt.error) ui.appendLog(evt.error);
    if (evt.result?.specPath) ui.appendLog(`Draft spec: ${evt.result.specPath}`);
    ui.setRunUi(false);
  }
}

const inBrowser = typeof document !== "undefined";
const $ = (id) => document.getElementById(id);

const viewHome = inBrowser ? $("view-home") : null;
const viewProject = inBrowser ? $("view-project") : null;
const projectCards = inBrowser ? $("projectCards") : null;
const stepsList = inBrowser ? $("stepsList") : null;
const stepsPreview = inBrowser ? $("stepsPreview") : null;
const logEl = inBrowser ? $("log") : null;
const statusEl = inBrowser ? $("status") : null;
const homeStatus = inBrowser ? $("homeStatus") : null;
const stagePills = inBrowser ? $("stagePills") : null;
const runBtn = inBrowser ? $("runBtn") : null;
const cancelBtn = inBrowser ? $("cancelBtn") : null;

const stageState = { parse: "idle", refine: "idle", generate: "idle", test: "idle" };

let currentProject = null;
let selectedStepsPath = "";
let suggestedModel = "qwen3-coder:30b";

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

function setStatus(text, cls = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${cls}`.trim();
}

function setHomeStatus(text, cls = "") {
  homeStatus.textContent = text;
  homeStatus.className = `status ${cls}`.trim();
}

function appendLog(line) {
  logEl.textContent += (logEl.textContent ? "\n" : "") + line;
  logEl.scrollTop = logEl.scrollHeight;
}

function setRunUi(isRunning) {
  runBtn.disabled = isRunning;
  cancelBtn.disabled = !isRunning;
}

function markRunningStages(status) {
  for (const [name, state] of Object.entries(stageState)) {
    if (state === "running") stageState[name] = status;
  }
}

function renderPills() {
  stagePills.innerHTML = "";
  for (const [name, state] of Object.entries(stageState)) {
    if (state === "idle") continue;
    const span = document.createElement("span");
    span.className = `pill ${state}`;
    span.textContent = `${name}: ${state}`;
    stagePills.appendChild(span);
  }
}

function showHome() {
  currentProject = null;
  selectedStepsPath = "";
  viewHome.hidden = false;
  viewProject.hidden = true;
}

function showProject() {
  viewHome.hidden = true;
  viewProject.hidden = false;
}

function renderCards(projects) {
  projectCards.innerHTML = "";
  if (!projects.length) {
    const empty = document.createElement("p");
    empty.className = "empty-cards";
    empty.textContent = "No projects yet. Create one to specify a web app.";
    projectCards.appendChild(empty);
    return;
  }
  for (const project of projects) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "project-card";
    btn.innerHTML = `
      <strong></strong>
      <span class="url"></span>
      <span class="meta"></span>
    `;
    btn.querySelector("strong").textContent = project.name;
    btn.querySelector(".url").textContent = project.baseUrl;
    btn.querySelector(".meta").textContent =
      `${project.stepsCount} steps file${project.stepsCount === 1 ? "" : "s"}`;
    btn.addEventListener("click", () => openProject(project.slug));
    projectCards.appendChild(btn);
  }
}

function renderSteps(project) {
  stepsList.innerHTML = "";
  if (!project.steps.length) {
    const empty = document.createElement("p");
    empty.className = "steps-empty";
    empty.textContent = "No steps files yet. Import a .txt or create a blank one.";
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
}

async function loadSteps(filePath) {
  if (!filePath) return;
  selectedStepsPath = filePath;
  const text = await window.qaPipeline.readSteps(filePath);
  stepsPreview.textContent = text || "(empty file)";
  if (!$("specName").value) {
    const base = filePath.split(/[/\\]/).pop().replace(/\.txt$/i, "");
    $("specName").placeholder = base;
  }
  if (currentProject) renderSteps(currentProject);
}

async function refreshHome() {
  const projects = await window.qaPipeline.listProjects();
  renderCards(projects);
  return projects;
}

async function openProject(slug) {
  currentProject = await window.qaPipeline.getProject(slug);
  fillProjectForm(currentProject);
  selectedStepsPath = "";
  stepsPreview.textContent = "Select a steps file to preview.";
  $("specName").value = "";
  renderSteps(currentProject);
  showProject();
  if (currentProject.steps.length) {
    await loadSteps(currentProject.steps[0].path);
  }
}

async function init() {
  await refreshHome();
  showHome();
  $("backend").addEventListener("change", syncBackendModel);
  syncBackendModel();

  $("createBtn").addEventListener("click", async () => {
    try {
      const project = await window.qaPipeline.createProject({
        name: $("newName").value.trim(),
        baseUrl: $("newBaseUrl").value.trim(),
        username: $("newUsername").value.trim(),
      });
      $("newName").value = "";
      setHomeStatus(`Created ${project.name}`, "ok");
      await refreshHome();
      await openProject(project.slug);
    } catch (err) {
      setHomeStatus(err.message || String(err), "err");
    }
  });

  $("backBtn").addEventListener("click", async () => {
    showHome();
    await refreshHome();
  });

  $("saveProjBtn").addEventListener("click", async () => {
    if (!currentProject) return;
    try {
      currentProject = await window.qaPipeline.updateProject(currentProject.slug, {
        name: $("projName").value.trim(),
        baseUrl: $("projBaseUrl").value.trim(),
        username: $("projUsername").value.trim(),
      });
      fillProjectForm(currentProject);
      setStatus("Project saved", "ok");
    } catch (err) {
      setStatus(err.message || String(err), "err");
    }
  });

  $("createStepsBtn").addEventListener("click", async () => {
    if (!currentProject) return;
    const name = $("newStepsName").value.trim();
    if (!name) {
      setStatus("Name the new steps file first", "err");
      return;
    }
    try {
      const before = new Set((currentProject.steps || []).map((s) => s.path));
      currentProject = await window.qaPipeline.addProjectSteps(currentProject.slug, { name });
      $("newStepsName").value = "";
      const created = currentProject.steps.find((s) => !before.has(s.path));
      renderSteps(currentProject);
      if (created) await loadSteps(created.path);
      setStatus("Steps file added", "ok");
    } catch (err) {
      setStatus(err.message || String(err), "err");
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
      renderSteps(currentProject);
      if (imported) await loadSteps(imported.path);
      setStatus("Steps file imported", "ok");
    } catch (err) {
      setStatus(err.message || String(err), "err");
    }
  });

  window.qaPipeline.onEvent((evt) => {
    applyPipelineEvent(evt, {
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
        logEl.textContent = "";
      },
    });
  });

  runBtn.addEventListener("click", async () => {
    if (!currentProject) {
      setStatus("Open a project first", "err");
      return;
    }
    if (!selectedStepsPath) {
      setStatus("Choose a steps file first", "err");
      return;
    }
    try {
      await window.qaPipeline.run({
        stepsPath: selectedStepsPath,
        baseUrl: $("projBaseUrl").value.trim(),
        backend: $("backend").value,
        model: $("model").value.trim(),
        username: $("projUsername").value.trim(),
        password: $("password").value,
        parse: $("doParse").checked,
        refine: $("doRefine").checked,
        generate: $("doGenerate").checked,
        runTests: $("doTest").checked,
        headed: $("headed").checked,
        specName: $("specName").value.trim() || undefined,
      });
    } catch (err) {
      setStatus(err.message || String(err), "err");
      setRunUi(false);
    }
  });

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
    setHomeStatus(err.message || String(err), "err");
    appendLog(String(err));
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { applyPipelineEvent };
}
