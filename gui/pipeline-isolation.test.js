"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { REPO_ROOT, runPipeline } = require("./pipeline-runner");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "qa-runner-isolation-"));
  const writeSteps = (project, contents, name = "checkout.txt") => {
    const dir = path.join(root, project, "steps");
    fs.mkdirSync(dir, { recursive: true });
    const stepsPath = path.join(dir, name);
    fs.writeFileSync(stepsPath, contents);
    return stepsPath;
  };
  return {
    root,
    writeSteps,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
  };
}

function removeIfEmpty(dir) {
  try {
    fs.rmdirSync(dir);
  } catch {
    // Another workflow may still own files in this shared parent directory.
  }
}

async function pathsFor(stepsPath, options = {}) {
  return runPipeline({
    stepsPath,
    parse: false,
    refine: false,
    generate: false,
    runTests: false,
    ...options,
  });
}

test("workflow artifacts isolate equal filenames from different projects", async (t) => {
  // Break caught: deriving output paths from only the basename would collide.
  const files = fixture();
  t.after(files.cleanup);
  const first = await pathsFor(files.writeSteps("first", "1. Open checkout"));
  const second = await pathsFor(files.writeSteps("second", "1. Open checkout"));

  assert.notEqual(first.actionPlan, second.actionPlan);
  assert.notEqual(first.refinedPlan, second.refinedPlan);
  assert.notEqual(first.specPath, second.specPath);
});

test("workflow artifacts change when steps contents or target URL changes", async (t) => {
  // Break caught: omitting contents or base URL from the identity can reuse stale artifacts.
  const files = fixture();
  t.after(files.cleanup);
  const stepsPath = files.writeSteps("project", "1. Open checkout");
  const original = await pathsFor(stepsPath, { baseUrl: "https://one.example" });
  fs.writeFileSync(stepsPath, "1. Open the cart");
  const changedSteps = await pathsFor(stepsPath, { baseUrl: "https://one.example" });
  const changedTarget = await pathsFor(stepsPath, { baseUrl: "https://two.example" });

  assert.notEqual(original.actionPlan, changedSteps.actionPlan);
  assert.notEqual(changedSteps.actionPlan, changedTarget.actionPlan);
});

test("unchanged workflow reuses its scoped output paths", async (t) => {
  // Break caught: non-deterministic paths would prevent skip-stage reuse.
  const files = fixture();
  t.after(files.cleanup);
  const stepsPath = files.writeSteps("project", "1. Open checkout");
  const first = await pathsFor(stepsPath, { baseUrl: "https://app.example", specName: "checkout-flow" });
  const second = await pathsFor(stepsPath, { baseUrl: "https://app.example", specName: "checkout-flow" });

  assert.deepEqual(second, first);
});

test("an undefined spec name uses the safe generated default", async (t) => {
  // Break caught: GUI payloads include specName: undefined when its optional field is blank.
  const files = fixture();
  t.after(files.cleanup);
  const stepsPath = files.writeSteps("project", "1. Open checkout");
  const implicit = await pathsFor(stepsPath);
  const explicitUndefined = await pathsFor(stepsPath, { specName: undefined });

  assert.deepEqual(explicitUndefined, implicit);
});

test("rejects unsafe explicit spec names", async (t) => {
  // Break caught: accepting explicit traversal or separators can write outside testDir.
  const files = fixture();
  t.after(files.cleanup);
  const stepsPath = files.writeSteps("project", "1. Open checkout");
  for (const specName of ["../escape", "nested/spec", "nested\\spec", "", "   ", "line\nbreak", "checkout flow", "checkout[1]"]) {
    await assert.rejects(pathsFor(stepsPath, { specName }), /spec name/i);
  }
});

test("generated specs stay below the configured Playwright generated-test directory", async (t) => {
  // Break caught: generated specs outside outputs/tests are skipped by the configured test run.
  const files = fixture();
  t.after(files.cleanup);
  const result = await pathsFor(files.writeSteps("project", "1. Open checkout"), {
    specName: "checkout-flow",
  });

  assert.equal(path.relative(path.join(REPO_ROOT, "outputs", "tests"), result.specPath).startsWith(".."), false);
});

test("skipped stages require artifacts from the same workflow scope", async (t) => {
  // Break caught: a skipped stage can accidentally consume another workflow's shared artifact.
  const files = fixture();
  t.after(files.cleanup);
  const stepsPath = files.writeSteps("project", "1. Open checkout");

  await assert.rejects(
    runPipeline({ stepsPath, parse: false, refine: true, generate: false, runTests: false }),
    /action plan.*parse/i
  );
  await assert.rejects(
    runPipeline({ stepsPath, parse: false, refine: false, generate: true, runTests: false }),
    /action plan.*parse/i
  );
  await assert.rejects(
    runPipeline({ stepsPath, parse: false, refine: false, generate: false, runTests: true }),
    /generated spec.*generate/i
  );
});

test("calculating an all-disabled pipeline does not create workflow artifacts", async (t) => {
  // Break caught: merely resolving a workflow creates misleading empty prerequisites.
  const files = fixture();
  t.after(files.cleanup);
  const result = await pathsFor(files.writeSteps("project", "1. Open checkout"));

  assert.equal(fs.existsSync(result.actionPlan), false);
  assert.equal(fs.existsSync(result.refinedPlan), false);
  assert.equal(fs.existsSync(result.specPath), false);
});

test("generate-only reuses the scoped raw action plan", async (t) => {
  // Break caught: generate can use an unrelated refined plan or fail to create nested spec paths.
  const files = fixture();
  t.after(files.cleanup);
  const stepsPath = files.writeSteps("project", "1. Open checkout");
  const scoped = await pathsFor(stepsPath, { specName: "checkout-flow" });
  t.after(() => {
    fs.rmSync(path.dirname(scoped.actionPlan), { recursive: true, force: true });
    fs.rmSync(path.dirname(scoped.specPath), { recursive: true, force: true });
    removeIfEmpty(path.join(REPO_ROOT, "outputs", "workflows"));
    removeIfEmpty(path.join(REPO_ROOT, "outputs", "tests"));
  });
  fs.mkdirSync(path.dirname(scoped.actionPlan), { recursive: true });
  fs.writeFileSync(scoped.actionPlan, JSON.stringify({
    workflow: { title: "Raw checkout plan", base_url: "https://app.example" },
    steps: [{ step: 1, action: "navigate", description: "Open checkout", target: {}, expected_outcome: {} }],
    metadata: {},
  }));
  fs.writeFileSync(scoped.refinedPlan, JSON.stringify({
    workflow: { title: "Wrong refined plan" },
    steps: [],
    metadata: { refined: true },
  }));
  const events = [];

  const result = await runPipeline({
    stepsPath,
    specName: "checkout-flow",
    parse: false,
    refine: false,
    generate: true,
    runTests: false,
    onEvent: (event) => events.push(event),
  });

  assert.equal(result.specPath, scoped.specPath);
  assert.equal(fs.existsSync(result.specPath), true);
  assert.ok(result.logPath);
  assert.equal(fs.existsSync(result.logPath), true);
  assert.match(fs.readFileSync(result.logPath, "utf8"), /\[stdout\]|\[stage\]/);
  assert.match(fs.readFileSync(result.specPath, "utf8"), /Raw checkout plan/);
  assert.doesNotMatch(fs.readFileSync(result.specPath, "utf8"), /Wrong refined plan/);
  assert.equal(events.some((event) => event.type === "log"), false);
  assert.ok(events.some((event) => event.type === "status"));
  assert.deepEqual(events.at(-1), { type: "stage", stage: "generate", status: "done", detail: scoped.specPath });

  const listed = spawnSync("npx", ["--no-install", "playwright", "test", result.specPath, "--list"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  assert.equal(listed.status, 0, listed.stderr);
  assert.match(listed.stdout, /Raw checkout plan/);
});
