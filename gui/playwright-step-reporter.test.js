"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const LowaveStepReporter = require("./playwright-step-reporter");
const {
  shouldReportStep,
  stepNumberFromTitle,
  payloadFromStep,
} = LowaveStepReporter;

test("shouldReportStep ignores nested Playwright API calls", () => {
  const parent = { category: "test.step", title: "Step 1: Open login" };
  assert.equal(shouldReportStep(parent), true);
  assert.equal(shouldReportStep({ category: "pw:api", title: "Go to /", parent }), false);
  assert.equal(
    shouldReportStep({
      category: "pw:api",
      title: "Go to /",
      location: { file: "/tmp/flow.spec.ts", line: 4 },
    }),
    true
  );
  assert.equal(shouldReportStep({ category: "pw:api", title: "Launch browser" }), false);
});

test("payloadFromStep reads the numbered step from the title", () => {
  assert.equal(stepNumberFromTitle("Step 4: Click Sign in"), 4);
  const payload = payloadFromStep(
    { category: "test.step", title: "Step 4: Click Sign in", location: { line: 18, file: "/tmp/flow.spec.ts" } },
    "running"
  );
  assert.equal(payload.step, 4);
  assert.equal(payload.status, "running");
  assert.equal(payload.line, 18);
});

test("onStepEnd reports pass when the step has no error", () => {
  const lines = [];
  const reporter = new LowaveStepReporter();
  reporter._write = (payload) => lines.push(payload);
  const step = { category: "test.step", title: "Step 1: Open login" };
  reporter.onStepEnd(null, null, step);
  assert.equal(lines[0].status, "pass");
  assert.equal(lines[0].step, 1);
  reporter.onStepEnd(null, null, { ...step, error: new Error("boom") });
  assert.equal(lines[1].status, "fail");
});
