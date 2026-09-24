"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { runEventFromError, runPipeline, REPO_ROOT } = require("./pipeline-runner");
const { applyPipelineEvent } = require("./renderer");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "qa-incomplete-"));
  const dir = path.join(root, "project", "steps");
  fs.mkdirSync(dir, { recursive: true });
  const stepsPath = path.join(dir, "flow.txt");
  fs.writeFileSync(stepsPath, "1. Open checkout");
  return {
    root,
    stepsPath,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
  };
}

function removeIfEmpty(dir) {
  try {
    fs.rmdirSync(dir);
  } catch {
    // shared parent
  }
}

async function scopedPaths(stepsPath, specName) {
  return runPipeline({
    stepsPath,
    specName,
    parse: false,
    refine: false,
    generate: false,
    runTests: false,
  });
}

function writePlan(planPath, steps, title = "scoped plan") {
  fs.mkdirSync(path.dirname(planPath), { recursive: true });
  fs.writeFileSync(planPath, JSON.stringify({
    workflow: { title, base_url: "https://app.example" },
    steps,
    metadata: {},
  }));
}

test("incomplete generate is a distinct run result and never starts Playwright", async (t) => {
  const files = fixture();
  t.after(files.cleanup);
  const scoped = await scopedPaths(files.stepsPath, "incomplete-flow");
  t.after(() => {
    fs.rmSync(path.dirname(scoped.actionPlan), { recursive: true, force: true });
    fs.rmSync(path.dirname(scoped.specPath), { recursive: true, force: true });
    removeIfEmpty(path.join(REPO_ROOT, "outputs", "workflows"));
    removeIfEmpty(path.join(REPO_ROOT, "outputs", "tests"));
  });
  writePlan(scoped.actionPlan, [{
    step: 1,
    action: "click",
    description: "Click a missing control.",
    target: { playwright_locator: 'locator("n/a")', css_selector: "n/a" },
    refinement: { grounded: true },
  }]);

  const events = [];
  const err = await runPipeline({
    stepsPath: files.stepsPath,
    specName: "incomplete-flow",
    parse: false,
    refine: false,
    generate: true,
    runTests: true,
    onEvent: (event) => events.push(event),
  }).then(
    () => null,
    (error) => error,
  );

  assert.ok(err, "incomplete generation must not resolve as success");
  assert.equal(err.incomplete, true);
  assert.equal(err.cancelled, undefined);
  assert.equal(err.exitCode ?? err.code, 3);
  assert.ok(err.logPath);
  assert.equal(fs.existsSync(err.logPath), true);
  assert.match(err.explanation, /generate|playwright action|todo/i);
  assert.equal(fs.existsSync(scoped.specPath), true);
  assert.match(fs.readFileSync(scoped.specPath, "utf8"), /throw new Error/);
  assert.equal(events.some((event) => event.type === "log"), false);
  assert.ok(events.some((event) => event.type === "status"));
  assert.equal(events.some((event) => event.stage === "test"), false);
  assert.ok(events.some((event) => event.stage === "generate" && event.status === "incomplete"));
  const mapped = runEventFromError(err);
  assert.equal(mapped.type, "run");
  assert.equal(mapped.status, "incomplete");
  assert.equal(mapped.explanation, err.explanation);
  assert.equal(mapped.logPath, err.logPath);
});

test("complete generate still starts the test stage when requested", async (t) => {
  const files = fixture();
  t.after(files.cleanup);
  const scoped = await scopedPaths(files.stepsPath, "complete-flow");
  t.after(() => {
    fs.rmSync(path.dirname(scoped.actionPlan), { recursive: true, force: true });
    fs.rmSync(path.dirname(scoped.specPath), { recursive: true, force: true });
    removeIfEmpty(path.join(REPO_ROOT, "outputs", "workflows"));
    removeIfEmpty(path.join(REPO_ROOT, "outputs", "tests"));
  });
  writePlan(scoped.actionPlan, [{
    step: 1,
    action: "terminate",
    description: "End the workflow here.",
    target: {},
  }], "complete terminate");

  const events = [];
  const result = await runPipeline({
    stepsPath: files.stepsPath,
    specName: "complete-flow",
    parse: false,
    refine: false,
    generate: true,
    runTests: true,
    headed: false,
    onEvent: (event) => events.push(event),
  });

  assert.equal(result.specPath, scoped.specPath);
  assert.doesNotMatch(fs.readFileSync(result.specPath, "utf8"), /throw new Error/);
  assert.ok(events.some((event) => event.stage === "generate" && event.status === "done"));
  assert.ok(events.some((event) => event.stage === "test" && event.status === "running"));
  assert.ok(events.some((event) => event.stage === "test" && event.status === "done"));
});

test("cancelled and provider failures stay distinct from incomplete", () => {
  const cancelled = new Error("Pipeline cancelled");
  cancelled.cancelled = true;
  assert.equal(runEventFromError(cancelled).status, "cancelled");

  const failed = new Error("qa-refine exited with code 1");
  failed.code = 1;
  failed.stderr = "provider down";
  assert.deepEqual(runEventFromError(failed), {
    type: "run",
    status: "failed",
    error: failed.message,
    stderr: "provider down",
    explanation: "",
    logPath: "",
    stage: "",
  });
});

test("renderer incomplete event unlocks controls and asks for review", () => {
  const ui = {
    stageState: { parse: "done", refine: "done", generate: "running", test: "idle" },
    log: "",
    status: "",
    statusClass: "",
    running: true,
    renderPills() {},
    setStatus(text, cls = "") {
      this.status = text;
      this.statusClass = cls;
    },
    setRunUi(isRunning) {
      this.running = isRunning;
    },
    markRunningStages(status) {
      for (const name of Object.keys(this.stageState)) {
        if (this.stageState[name] === "running") this.stageState[name] = status;
      }
    },
    appendLog(line) {
      this.log += line;
    },
    resetStages() {
      for (const name of Object.keys(this.stageState)) this.stageState[name] = "idle";
    },
    clearLog() {
      this.log = "";
    },
  };

  applyPipelineEvent({ type: "stage", stage: "generate", status: "incomplete", detail: "/tmp/draft.spec.ts" }, ui);
  applyPipelineEvent({
    type: "run",
    status: "incomplete",
    error: "qa-generate exited with code 3",
    explanation: "Generate could not turn every step into a Playwright action.",
    logPath: "/tmp/run.log",
    result: { specPath: "/tmp/draft.spec.ts", incomplete: true },
  }, ui);

  assert.equal(ui.running, false);
  assert.match(ui.status, /incomplete|needs review/i);
  assert.equal(ui.statusClass, "warn");
  assert.equal(ui.stageState.generate, "incomplete");
  assert.equal(ui.stageState.test, "idle");
  assert.match(ui.log, /what went wrong/i);
  assert.match(ui.log, /playwright action/i);
  assert.match(ui.log, /run log/i);
  assert.doesNotMatch(ui.log, /exited with code 3/);
  assert.doesNotMatch(ui.log, /\/tmp\/run\.log/);
});

test("renderer shows parsed English on failure and ignores raw log lines", () => {
  const ui = {
    stageState: { parse: "idle", refine: "running", generate: "idle", test: "idle" },
    log: "",
    status: "",
    statusClass: "",
    statusOpts: null,
    running: true,
    renderPills() {},
    setStatus(text, cls = "", opts = {}) {
      this.status = text;
      this.statusClass = cls;
      this.statusOpts = opts;
    },
    setRunUi(isRunning) {
      this.running = isRunning;
    },
    markRunningStages(status) {
      for (const name of Object.keys(this.stageState)) {
        if (this.stageState[name] === "running") this.stageState[name] = status;
      }
    },
    appendLog(line) {
      this.log += (this.log ? "\n" : "") + line;
    },
    resetStages() {
      for (const name of Object.keys(this.stageState)) this.stageState[name] = "idle";
    },
    clearLog() {
      this.log = "";
    },
  };

  applyPipelineEvent({ type: "log", line: "locator.click: Timeout 30000ms exceeded" }, ui);
  applyPipelineEvent({ type: "status", message: "Working on step 2 of 8." }, ui);
  applyPipelineEvent({
    type: "run",
    status: "failed",
    error: "qa-refine exited with code 1",
    stderr: "locator.click: Timeout 30000ms exceeded\n    at execute_step",
    explanation: "Step 2 could not be completed. The page waited too long for the next element to appear.",
    logPath: "/tmp/refine.log",
  }, ui);

  assert.equal(ui.running, false);
  assert.equal(ui.statusClass, "err");
  assert.equal(ui.statusOpts.filePath, "/tmp/refine.log");
  assert.match(ui.log, /working on step 2 of 8/i);
  assert.match(ui.log, /waited too long/i);
  assert.match(ui.log, /run log/i);
  assert.doesNotMatch(ui.log, /locator\.click/);
  assert.doesNotMatch(ui.log, /execute_step/);
  assert.doesNotMatch(ui.log, /\/tmp\/refine\.log/);
});

test("renderer done status uses a short filename instead of the full spec path", () => {
  const ui = {
    stageState: { parse: "done", refine: "done", generate: "done", test: "done" },
    log: "",
    status: "",
    statusClass: "",
    statusOpts: null,
    running: true,
    renderPills() {},
    setStatus(text, cls = "", opts = {}) {
      this.status = text;
      this.statusClass = cls;
      this.statusOpts = opts;
    },
    setRunUi(isRunning) {
      this.running = isRunning;
    },
    markRunningStages() {},
    appendLog(line, opts = {}) {
      this.log += line;
      if (opts.filePath) this.logFilePath = opts.filePath;
    },
    resetStages() {},
    clearLog() {},
  };

  const specPath =
    "/Users/noah.legall/DMX_Agent_Sandbox/detailed_webapp_qa_testing/outputs/tests/abc/login_and_search_report.spec.ts";
  const logPath =
    "/Users/noah.legall/DMX_Agent_Sandbox/detailed_webapp_qa_testing/outputs/workflows/abc/logs/2026-09-24T17-22-42-229Z.log";
  applyPipelineEvent({ type: "run", status: "finished", result: { specPath, logPath } }, ui);

  assert.equal(ui.running, false);
  assert.equal(ui.status, "Done");
  assert.equal(ui.statusClass, "ok");
  assert.equal(ui.statusOpts.filePath, specPath);
  assert.doesNotMatch(ui.status, /Users|outputs\/tests/);
  assert.match(ui.log, /run log/i);
  assert.equal(ui.logFilePath, logPath);
  assert.doesNotMatch(ui.log, /outputs\/workflows/);
});
