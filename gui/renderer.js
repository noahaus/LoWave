"use strict";

const $ = (id) => document.getElementById(id);

const stepsPath = $("stepsPath");
const examples = $("examples");
const stepsPreview = $("stepsPreview");
const logEl = $("log");
const statusEl = $("status");
const stagePills = $("stagePills");
const runBtn = $("runBtn");

const stageState = { parse: "idle", refine: "idle", generate: "idle", test: "idle" };

function setStatus(text, cls = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${cls}`.trim();
}

function appendLog(line) {
  logEl.textContent += (logEl.textContent ? "\n" : "") + line;
  logEl.scrollTop = logEl.scrollHeight;
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

async function loadSteps(filePath) {
  if (!filePath) return;
  stepsPath.value = filePath;
  const text = await window.qaPipeline.readSteps(filePath);
  stepsPreview.textContent = text;
  if (!$("specName").value) {
    const base = filePath.split(/[/\\]/).pop().replace(/\.txt$/i, "");
    $("specName").placeholder = base;
  }
}

async function init() {
  const list = await window.qaPipeline.listExamples();
  examples.innerHTML = '<option value="">Select an example…</option>';
  for (const item of list) {
    const opt = document.createElement("option");
    opt.value = item.path;
    opt.textContent = item.name;
    examples.appendChild(opt);
  }

  if (list.length) {
    examples.value = list[0].path;
    await loadSteps(list[0].path);
  }

  examples.addEventListener("change", () => loadSteps(examples.value));
  $("browseBtn").addEventListener("click", async () => {
    const picked = await window.qaPipeline.pickSteps();
    if (picked) {
      examples.value = "";
      await loadSteps(picked);
    }
  });

  window.qaPipeline.onEvent((evt) => {
    if (evt.type === "log") appendLog(evt.line);
    if (evt.type === "stage") {
      stageState[evt.stage] = evt.status === "running" ? "running" : evt.status === "done" ? "done" : evt.status;
      renderPills();
      setStatus(`${evt.stage}: ${evt.status}`, evt.status === "done" ? "ok" : "running");
    }
    if (evt.type === "run") {
      if (evt.status === "started") {
        Object.keys(stageState).forEach((k) => (stageState[k] = "idle"));
        renderPills();
        logEl.textContent = "";
        setStatus("Running…", "running");
        runBtn.disabled = true;
      }
      if (evt.status === "finished") {
        setStatus(`Done → ${evt.result?.specPath || "ok"}`, "ok");
        runBtn.disabled = false;
      }
      if (evt.status === "failed") {
        setStatus(evt.error || "Failed", "err");
        if (evt.stderr) appendLog(evt.stderr.slice(-3000));
        runBtn.disabled = false;
      }
    }
  });

  runBtn.addEventListener("click", async () => {
    if (!stepsPath.value) {
      setStatus("Choose a steps file first", "err");
      return;
    }
    try {
      await window.qaPipeline.run({
        stepsPath: stepsPath.value,
        baseUrl: $("baseUrl").value.trim(),
        backend: $("backend").value,
        model: $("model").value.trim(),
        username: $("username").value.trim(),
        password: $("password").value,
        parse: $("doParse").checked,
        refine: $("doRefine").checked,
        generate: $("doGenerate").checked,
        runTests: $("doTest").checked,
        specName: $("specName").value.trim() || undefined,
      });
    } catch (err) {
      setStatus(err.message || String(err), "err");
      runBtn.disabled = false;
    }
  });
}

init().catch((err) => {
  setStatus(err.message || String(err), "err");
  appendLog(String(err));
});
