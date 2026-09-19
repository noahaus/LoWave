"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  shouldReportStep,
  stepNumberFromTitle,
  payloadFromStep,
} = require("./playwright-step-reporter");

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
