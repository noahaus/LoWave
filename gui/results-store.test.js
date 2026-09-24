"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createResultsStore, descriptionFromTitle } = require("./results-store");

function tempStore() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-results-"));
  const store = createResultsStore(path.join(dir, "qa-results.sqlite"));
  return {
    store,
    cleanup: () => {
      try {
        store.close();
      } catch {
        // already closed
      }
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

test("descriptionFromTitle strips the Step N prefix", () => {
  assert.equal(descriptionFromTitle("Step 4: Click Sign in"), "Click Sign in");
  assert.equal(descriptionFromTitle("plain"), "plain");
});

test("a finished run is the latest status and earlier runs stay in history", () => {
  const { store, cleanup } = tempStore();
  try {
    const first = store.startRun({
      workflowHash: "abc",
      stepsPath: "/tmp/flow.txt",
      specPath: "/tmp/flow.spec.ts",
      steps: [
        { number: 1, text: "Open login" },
        { number: 2, text: "Click Sign in" },
      ],
    });
    store.setStepStatus(first.id, 1, "pass");
    store.setStepStatus(first.id, 2, "fail", "Step 2: Click Sign in");
    store.finishRun(first.id, { passed: false });

    const second = store.startRun({
      workflowHash: "abc",
      stepsPath: "/tmp/flow.txt",
      specPath: "/tmp/flow.spec.ts",
      steps: [
        { number: 1, text: "Open login" },
        { number: 2, text: "Click Sign in" },
      ],
    });
    store.setStepStatus(second.id, 1, "pass");
    store.setStepStatus(second.id, 2, "pass");
    store.finishRun(second.id, { passed: true });

    const latest = store.latestResults("abc");
    assert.equal(latest.runId, second.id);
    assert.equal(latest.passed, true);
    assert.deepEqual(latest.stepStatuses, { 1: "pass", 2: "pass" });

    const history = store.listRuns("abc");
    assert.equal(history.length, 2);
    assert.equal(history[0].id, second.id);
    assert.equal(history[0].passed, true);
    assert.equal(history[1].id, first.id);
    assert.equal(history[1].passed, false);
  } finally {
    cleanup();
  }
});

test("an abandoned in-progress run is closed when a new run starts", () => {
  const { store, cleanup } = tempStore();
  try {
    const first = store.startRun({
      workflowHash: "def",
      stepsPath: "/tmp/a.txt",
      steps: [{ number: 1, text: "Open" }],
    });
    store.setStepStatus(first.id, 1, "running");
    const second = store.startRun({
      workflowHash: "def",
      stepsPath: "/tmp/a.txt",
      steps: [{ number: 1, text: "Open" }],
    });
    const history = store.listRuns("def");
    assert.equal(history.length, 2);
    assert.equal(history[1].id, first.id);
    assert.equal(history[1].cancelled, true);
    assert.ok(history[1].finishedAt);
    assert.equal(history[0].id, second.id);
    assert.equal(store.latestResults("def").runId, second.id);
  } finally {
    cleanup();
  }
});

test("cancel leaves completed steps and idles the running step", () => {
  const { store, cleanup } = tempStore();
  try {
    const run = store.startRun({
      workflowHash: "ghi",
      stepsPath: "/tmp/b.txt",
      steps: [
        { number: 1, text: "One" },
        { number: 2, text: "Two" },
      ],
    });
    store.setStepStatus(run.id, 1, "pass");
    store.setStepStatus(run.id, 2, "running");
    store.finishRun(run.id, { cancelled: true });
    const latest = store.latestResults("ghi");
    assert.equal(latest.cancelled, true);
    assert.equal(latest.passed, null);
    assert.equal(latest.stepStatuses[1], "pass");
    assert.equal(latest.stepStatuses[2], "idle");
  } finally {
    cleanup();
  }
});

test("a failed run keeps step colors as they were", () => {
  const { store, cleanup } = tempStore();
  try {
    const run = store.startRun({
      workflowHash: "jkl",
      stepsPath: "/tmp/c.txt",
      steps: [
        { number: 1, text: "One" },
        { number: 2, text: "Two" },
        { number: 3, text: "Three" },
      ],
    });
    store.setStepStatus(run.id, 1, "pass");
    store.setStepStatus(run.id, 2, "running");
    store.finishRun(run.id, { passed: false });
    const latest = store.latestResults("jkl");
    assert.equal(latest.passed, false);
    assert.equal(latest.stepStatuses[1], "pass");
    assert.equal(latest.stepStatuses[2], "running");
    assert.equal(latest.stepStatuses[3], "idle");
  } finally {
    cleanup();
  }
});
