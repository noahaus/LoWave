"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { defaultModelForBackend, runCommand, stopChild, specStatus } = require("./pipeline-runner");
const { projectHost } = require("./renderer");


test("specStatus reports a missing Playwright file until one exists", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lowave-spec-status-"));
  let specPath = "";
  try {
    const stepsPath = path.join(dir, "flow.txt");
    fs.writeFileSync(stepsPath, "1. Open login");
    const missing = specStatus({ stepsPath, baseUrl: "http://localhost:3000" });
    specPath = missing.specPath;
    assert.equal(missing.exists, false);
    assert.equal(missing.content, "");
    assert.match(missing.specPath, /\.spec\.ts$/);
    fs.mkdirSync(path.dirname(missing.specPath), { recursive: true });
    fs.writeFileSync(missing.specPath, "test('ok', async () => {});");
    const present = specStatus({ stepsPath, baseUrl: "http://localhost:3000" });
    assert.equal(present.exists, true);
    assert.equal(present.specPath, missing.specPath);
    assert.match(present.content, /test\('ok'/);
  } finally {
    if (specPath) fs.rmSync(path.dirname(specPath), { recursive: true, force: true });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("only Ollama receives a hardcoded local model default", () => {
  assert.equal(defaultModelForBackend("ollama"), "qwen3-coder:30b");
  assert.equal(defaultModelForBackend("claude-cli"), undefined);
  assert.equal(defaultModelForBackend("codex-cli"), undefined);
  assert.equal(defaultModelForBackend("anthropic"), undefined);
});


test("GUI exposes subscription backends without a prefilled password", () => {
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

  assert.match(html, /option value="claude-cli"/);
  assert.match(html, /option value="codex-cli"/);
  assert.doesNotMatch(html, /id="projPassword"[^>]*value=/);
  assert.doesNotMatch(html, /id="password"/);
});

test("backend and model live on the settings page, not the project form", () => {
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  const [beforeSettings, settings] = html.split('id="view-settings"');
  const project = beforeSettings.split('id="view-project"')[1] || "";

  assert.match(settings, /id="backend"/);
  assert.match(settings, /id="model"/);
  assert.doesNotMatch(project, /id="backend"/);
  assert.doesNotMatch(project, /id="model"/);
  assert.match(project, /id="addStepsBtn"/);
  assert.match(project, /id="stepsList"/);
  assert.match(project, /id="parseBtn"/);
  assert.match(project, /id="testBtn"/);
  assert.match(project, /id="noTestsHint"/);
  assert.match(project, /id="stepTiles"/);
  assert.match(project, /id="paneStepsBtn"/);
  assert.match(project, /id="paneLogsBtn"/);
  assert.match(project, /class="project-col"/);
  assert.match(project, /id="projPassword"/);
  assert.match(project, /id="saveProjStatus"/);
  assert.match(project, /class="console-pane"/);
  assert.doesNotMatch(project, /id="specName"/);
  assert.doesNotMatch(project, /id="password"/);
  assert.doesNotMatch(project, /id="backBtn"/);
  assert.doesNotMatch(project, /Back to projects/);
  assert.doesNotMatch(project, /id="doRefine"/);
  assert.doesNotMatch(project, /id="doGenerate"/);
  assert.doesNotMatch(project, /Run pipeline/);
  assert.doesNotMatch(project, /Run status/);
});

test("header navigation includes Home, About, and Settings", () => {
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  assert.match(html, /id="homeBtn"/);
  assert.match(html, /id="aboutBtn"/);
  assert.match(html, /id="settingsBtn"/);
  assert.match(html, /id="view-about"/);
  assert.match(html, /About LoWave/);
  assert.doesNotMatch(html, /id="newBackBtn"/);
});

test("project tiles use the hover card and show the URL host", () => {
  const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  assert.match(css, /\.project-card \.background/);
  assert.match(css, /\.project-card \.box1/);
  assert.match(css, /\.project-card:hover/);
  assert.equal(projectHost("http://localhost:3000/reports"), "localhost:3000");
  assert.equal(projectHost("not a url"), "not a url");
});

test("cancelling a command also stops its CLI descendants", { skip: process.platform === "win32", timeout: 5000 }, async () => {
  const controller = new AbortController();
  let descendant;
  const script = `
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    process.stdout.write(String(child.pid) + '\\n');
    setInterval(() => {}, 1000);
  `;
  try {
    await assert.rejects(runCommand(process.execPath, ["-e", script], {
      signal: controller.signal,
      onLine(line, stream) {
        if (stream !== "stdout") return;
        if (!/^\d+$/.test(line.trim())) return;
        descendant = Number(line.trim());
        controller.abort();
      },
    }), (err) => err.cancelled === true);
    for (let attempt = 0; attempt < 30; attempt++) {
      try { process.kill(descendant, 0); }
      catch (err) { if (err.code === "ESRCH") return; throw err; }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.fail("CLI descendant survived cancellation");
  } finally {
    if (descendant) {
      try { process.kill(descendant, "SIGKILL"); } catch {}
    }
  }
});

test("cancelling after the leader exits still stops descendants holding stdout", { skip: process.platform === "win32", timeout: 5000 }, async () => {
  const controller = new AbortController();
  let descendant;
  const script = `
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: ['ignore', 'inherit', 'ignore'] });
    process.stdout.write(String(child.pid) + '\\n');
  `;
  try {
    await assert.rejects(runCommand(process.execPath, ["-e", script], {
      signal: controller.signal,
      onLine(line, stream) {
        if (stream !== "stdout" || !/^\d+$/.test(line.trim())) return;
        descendant = Number(line.trim());
        setTimeout(() => controller.abort(), 100);
      },
    }), (err) => err.cancelled === true);
    for (let attempt = 0; attempt < 30; attempt++) {
      try { process.kill(descendant, 0); }
      catch (err) { if (err.code === "ESRCH") return; throw err; }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.fail("CLI descendant survived cancellation after its leader exited");
  } finally {
    if (descendant) {
      try { process.kill(descendant, "SIGKILL"); } catch {}
    }
  }
});

test("stopChild signals the process group even after the leader exits", { skip: process.platform === "win32" }, () => {
  const originalKill = process.kill;
  const calls = [];
  process.kill = (pid, signal) => calls.push([pid, signal]);
  try {
    stopChild({ killed: false, exitCode: 0, pid: 987654321 });
    assert.deepEqual(calls[0], [-987654321, "SIGTERM"]);
  } finally {
    process.kill = originalKill;
  }
});
