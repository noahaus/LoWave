"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  explainFailure,
  formatFailurePanel,
  statusFromLogLine,
  stageStatusMessage,
  stripAnsi,
} = require("./status-messages");

test("stage statuses are plain English", () => {
  assert.match(stageStatusMessage("parse", "running"), /action plan/i);
  assert.match(stageStatusMessage("refine", "done"), /real control/i);
  assert.match(stageStatusMessage("generate", "incomplete"), /gaps/i);
  assert.equal(stageStatusMessage("unknown", "running"), "");
});

test("refine log lines become per-step statuses", () => {
  assert.equal(
    statusFromLogLine("[12:01:02] [>] --- Step 3 / 12  [click] ---", "refine"),
    "Working on step 3 of 12."
  );
  assert.equal(
    statusFromLogLine("[12:01:09] [v]   Step 3 complete  (conf=0.91)", "refine"),
    "Step 3 succeeded."
  );
  assert.match(
    statusFromLogLine("[12:01:11] [x]   Step 4 FAILED: target not in snapshot", "refine"),
    /could not find its target/i
  );
});

test("ANSI-colored parse output is translated", () => {
  const line = "\u001b[1m━━━ Analysing with ollama ━━━\u001b[0m";
  assert.equal(stripAnsi(line), "━━━ Analysing with ollama ━━━");
  assert.match(statusFromLogLine(line, "parse"), /interpret the numbered steps/i);
});

test("explainFailure describes a missing on-page control", () => {
  const explanation = explainFailure({
    stage: "refine",
    logText: "[12:01:11] [x]   Step 4 FAILED: target not in snapshot\n",
    error: "qa-refine exited with code 1",
  });
  assert.match(explanation, /step 4/i);
  assert.match(explanation, /button or field/i);
  assert.doesNotMatch(explanation, /qa-refine exited/);
});

test("explainFailure describes a timed-out refine step", () => {
  const explanation = explainFailure({
    stage: "refine",
    logText:
      "[12:04:00] [x]   Step 2 FAILED after 3 attempt(s): locator.click: Timeout 30000ms exceeded.\n",
  });
  assert.match(explanation, /step 2/i);
  assert.match(explanation, /waited too long/i);
});

test("explainFailure describes parse schema and missing numbered steps", () => {
  assert.match(
    explainFailure({
      stage: "parse",
      logText: "ERROR: no numbered steps found in flow.txt",
    }),
    /numbered instructions/i
  );
  assert.match(
    explainFailure({
      stage: "parse",
      logText:
        "model output did not match the schema:\nHint: output looks truncated (the steps array never closed). This is usually a max_tokens cutoff",
    }),
    /cut off/i
  );
});

test("explainFailure describes incomplete generate without dumping the CLI error", () => {
  const explanation = explainFailure({
    stage: "generate",
    incomplete: true,
    logText: "\u001b[33m⚠ Incomplete generation — the spec fails before page actions\u001b[0m",
    error: "qa-generate exited with code 3",
  });
  assert.match(explanation, /playwright action/i);
  assert.doesNotMatch(explanation, /exited with code 3/);
});

test("explainFailure describes a missing action plan in plain English", () => {
  assert.match(
    explainFailure({
      error: "Scoped action plan is required when parse is skipped: /tmp/action_plan.json. Run parse for this workflow first.",
    }),
    /run parse/i
  );
});

test("explainFailure describes a missing Playwright browser without treating hashes as HTTP 401", () => {
  const explanation = explainFailure({
    stage: "test",
    logText:
      "Error: browserType.launch: Executable doesn't exist at /tmp/c40193819e44872ffb/chrome\nPlease run npx playwright install",
  });
  assert.match(explanation, /could not find a browser/i);
  assert.doesNotMatch(explanation, /api key|subscription login/i);
});

test("formatFailurePanel keeps a short log filename as a footnote", () => {
  const panel = formatFailurePanel("Step 2 could not be completed.", "/tmp/run.log");
  assert.match(panel, /what went wrong/i);
  assert.match(panel, /step 2/i);
  assert.match(panel, /run log · run\.log/i);
  assert.doesNotMatch(panel, /\/tmp\/run\.log/);
});
