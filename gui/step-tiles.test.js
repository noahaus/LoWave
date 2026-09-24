"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  parseNumberedSteps,
  parseSpecSteps,
  buildStepTiles,
  playwrightErrorLine,
  resultsForTestRun,
  wrapSpecInTestSteps,
  parseReporterLine,
  stepFromReporterPayload,
  statusesAfterProgress,
  specSnippetForStep,
} = require("./step-tiles");

test("parseNumberedSteps reads 1. lines from a steps file", () => {
  const steps = parseNumberedSteps("# comment\n\n1. Open login\n2. Click Sign in\nnot a step\n10. Assert dashboard");
  assert.deepEqual(steps, [
    { number: 1, text: "Open login" },
    { number: 2, text: "Click Sign in" },
    { number: 10, text: "Assert dashboard" },
  ]);
});

test("buildStepTiles prefers the numbered steps file over spec comments", () => {
  const tiles = buildStepTiles(
    "1. Open login\n2. Click Sign in",
    "  // Step 1: Navigate\n  await page.goto('/');\n  // Step 2: Submit"
  );
  assert.equal(tiles[0].text, "Open login");
  assert.equal(tiles[1].number, 2);
});

test("resultsForTestRun marks the failing spec step red and earlier ones green", () => {
  const spec = [
    "test('flow', async ({ page }) => {",
    "  // Step 1: Open login",
    "  await page.goto('/');",
    "  // Step 2: Click Sign in",
    "  await page.getByRole('button').click();",
    "  // Step 3: Assert dashboard",
    "  await expect(page.getByText('Dashboard')).toBeVisible();",
    "});",
  ].join("\n");
  const steps = parseNumberedSteps("1. Open login\n2. Click Sign in\n3. Assert dashboard");
  const results = resultsForTestRun({
    passed: false,
    output: "Error: Timeout\n    at outputs/tests/abc/flow.spec.ts:5:3",
    specPath: "outputs/tests/abc/flow.spec.ts",
    specText: spec,
    steps,
  });
  assert.deepEqual(results.map((r) => r.status), ["pass", "fail", "idle"]);
  assert.equal(playwrightErrorLine("at /tmp/flow.spec.ts:5:3", "/tmp/flow.spec.ts"), 5);
});

test("resultsForTestRun turns every tile green when the spec passes", () => {
  const steps = parseNumberedSteps("1. Open login\n2. Click Sign in");
  const results = resultsForTestRun({ passed: true, steps });
  assert.deepEqual(results.map((r) => r.status), ["pass", "pass"]);
});

test("specSnippetForStep returns the Playwright block for one numbered step", () => {
  const spec = [
    "test('flow', async ({ page }) => {",
    "  // Step 1: Open login",
    "  await page.goto('/');",
    "  // Step 2: Click Sign in",
    "  await page.getByRole('button').click();",
    "  await expect(page).toHaveURL('/app');",
    "  // Step 3: Assert dashboard",
    "  await expect(page.getByText('Dashboard')).toBeVisible();",
    "});",
  ].join("\n");
  assert.match(specSnippetForStep(spec, 1), /await page\.goto\('\/'\);/);
  assert.doesNotMatch(specSnippetForStep(spec, 1), /Click Sign in/);
  const two = specSnippetForStep(spec, 2);
  assert.match(two, /getByRole\('button'\)/);
  assert.match(two, /toHaveURL/);
  assert.doesNotMatch(two, /Assert dashboard/);
  const last = specSnippetForStep(spec, 3);
  assert.match(last, /Dashboard/);
  assert.doesNotMatch(last, /^\s*\}\);\s*$/m);
  assert.equal(specSnippetForStep(spec, 9), "");
});

test("parseSpecSteps reads generated Step comments", () => {
  const parsed = parseSpecSteps("  // Step 1: Go home\n  await page.goto('/');");
  assert.equal(parsed[0].number, 1);
  assert.equal(parsed[0].startLine, 1);
});

test("wrapSpecInTestSteps wraps numbered spec comments in test.step", () => {
  const spec = [
    "import { test, expect } from '@playwright/test';",
    "",
    "test('flow', async ({ page }) => {",
    "  // Step 1: Open login",
    "  await page.goto('/');",
    "  // Step 2: Click Sign in",
    "  await page.getByRole('button').click();",
    "});",
    "",
  ].join("\n");
  const wrapped = wrapSpecInTestSteps(spec);
  assert.match(wrapped, /await test\.step\("Step 1: Open login", async \(\) => \{/);
  assert.match(wrapped, /await test\.step\("Step 2: Click Sign in", async \(\) => \{/);
  assert.equal(wrapSpecInTestSteps(wrapped), wrapped);
});

test("parseReporterLine reads LOWAVE_STEP payloads", () => {
  const payload = parseReporterLine('LOWAVE_STEP {"status":"running","step":2}');
  assert.equal(payload.status, "running");
  assert.equal(payload.step, 2);
  assert.equal(stepFromReporterPayload({ line: 2 }, "  // Step 1: A\n  a();\n  // Step 2: B\n  b();"), 1);
});

test("statusesAfterProgress turns earlier tiles green as the next step starts", () => {
  const steps = parseNumberedSteps("1. Open login\n2. Click Sign in\n3. Assert dashboard");
  let statuses = statusesAfterProgress({}, { step: 1, status: "running", steps });
  assert.deepEqual(statuses, { 1: "running" });
  statuses = statusesAfterProgress(statuses, { step: 2, status: "running", steps });
  assert.equal(statuses[1], "pass");
  assert.equal(statuses[2], "running");
  assert.equal(statuses[3], undefined);
  statuses = statusesAfterProgress(statuses, { step: 2, status: "fail", steps });
  assert.equal(statuses[2], "fail");
  statuses = statusesAfterProgress(statuses, { status: "test-end", passed: false, steps });
  assert.equal(statuses[1], "pass");
  assert.equal(statuses[2], "fail");
  assert.equal(statuses[3], undefined);

  const frozen = statusesAfterProgress(
    { 1: "pass", 2: "running", 3: "idle" },
    { status: "test-end", passed: false, steps }
  );
  assert.deepEqual(frozen, { 1: "pass", 2: "running", 3: "idle" });
});
