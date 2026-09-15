// Real-browser complete vs incomplete generated specs. No model, network app, or credentials.
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const repo = process.argv[2] || path.resolve(__dirname, "..");
const python = path.join(repo, ".venv", "bin", "python");
const generatedRoot = path.join(repo, "tests", "generated");
fs.mkdirSync(generatedRoot, { recursive: true });
const tmp = fs.mkdtempSync(path.join(generatedRoot, "verify-incomplete-"));
const pageUrl = "data:text/html," + encodeURIComponent('<!doctype html><h1>Ready</h1><button>Save (draft)</button><button>Save "draft"</button>');

function writePlan(name, steps) {
  const plan = path.join(tmp, `${name}.json`);
  fs.writeFileSync(plan, JSON.stringify({
    workflow: { title: name, base_url: "http://localhost:3000" },
    steps,
    metadata: {},
  }));
  return plan;
}

function generate(plan, spec, extra = []) {
  return spawnSync(python, ["-m", "qa_pipeline.generate", plan, spec, ...extra], {
    cwd: repo,
    encoding: "utf8",
  });
}

function runSpec(spec) {
  return spawnSync("npx", ["--no-install", "playwright", "test", spec, "--reporter=line"], {
    cwd: repo,
    encoding: "utf8",
    env: { ...process.env, QA_SLOWMO: "0", QA_BASE_URL: "http://127.0.0.1:1" },
  });
}

const completePlan = writePlan("complete-ready", [
  {
    step: 1,
    action: "navigate",
    description: "Open the local page.",
    value: pageUrl,
    target: {},
  },
  {
    step: 2,
    action: "assert",
    description: "The heading is visible.",
    target: { playwright_locator: 'get_by_text("Ready")' },
    expected_outcome: {},
    refinement: { grounded: true, confidence: 1 },
  },
]);
// Exercise punctuation-bearing accessible names in a real generated browser run.
const completeData = JSON.parse(fs.readFileSync(completePlan, "utf8"));
for (const name of ['Save (draft)', 'Save "draft"']) {
  completeData.steps.push({
    step: completeData.steps.length + 1,
    action: "click",
    target: { playwright_locator: `get_by_role("button", name=${JSON.stringify(name)}, exact=True)` },
    refinement: { grounded: true, confidence: 1 },
  });
}
fs.writeFileSync(completePlan, JSON.stringify(completeData));

const incompletePlan = writePlan("incomplete-todo", [
  {
    step: 1,
    action: "navigate",
    description: "Must not run.",
    value: "http://127.0.0.1:1/should-not-run",
    target: {},
  },
  {
    step: 2,
    action: "click",
    description: "Click a missing control.",
    target: { playwright_locator: 'locator("n/a")', css_selector: "n/a" },
    refinement: { grounded: true },
  },
]);

const failedGroundingPlan = writePlan("failed-grounding", [
  {
    step: 1,
    action: "navigate",
    description: "Must not run.",
    value: "http://127.0.0.1:1/should-not-run",
    target: {},
  },
  {
    step: 2,
    action: "click",
    description: "Save the record.",
    target: {
      css_selector: "button",
      text_content: "Save",
      playwright_locator: 'get_by_role("button", name="Save")',
    },
    refinement: { grounded: false, confidence: 0.8 },
  },
]);

const completeSpec = path.join(tmp, "complete.spec.ts");
const incompleteSpec = path.join(tmp, "todo.spec.ts");
const draftSpec = path.join(tmp, "draft.spec.ts");
const failedGroundingSpec = path.join(tmp, "failed-grounding.spec.ts");

const completeGen = generate(completePlan, completeSpec);
const incompleteGen = generate(incompletePlan, incompleteSpec);
const allowGen = generate(incompletePlan, draftSpec, ["--allow-incomplete"]);
const failedGroundingGen = generate(failedGroundingPlan, failedGroundingSpec);

let failures = 0;
const report = [];

function check(name, ok, detail) {
  report.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(JSON.stringify({ name, ok, detail }));
}

function thrownIncompleteMessage(output) {
  return /Error: Generated spec is incomplete(?:$|:)/m.test(output);
}

function noNavigationFailure(output) {
  return !/ERR_CONNECTION|net::/i.test(output);
}

check("complete generate exit 0", completeGen.status === 0, completeGen.stderr);
check("incomplete generate exit 3", incompleteGen.status === 3, incompleteGen.stderr);
check("allow-incomplete generate exit 0", allowGen.status === 0, allowGen.stderr);
check("failed-grounding generate exit 3", failedGroundingGen.status === 3, failedGroundingGen.stderr);
check("incomplete spec has runtime guard before goto", (() => {
  const spec = fs.readFileSync(incompleteSpec, "utf8");
  return spec.includes("throw new Error('Generated spec is incomplete:")
    && spec.indexOf("throw new Error") < spec.indexOf("should-not-run");
})(), "");
check("allow-incomplete draft still has runtime guard", fs.readFileSync(draftSpec, "utf8").includes("throw new Error('Generated spec is incomplete:"), "");
check("failed-grounding spec has runtime guard", fs.readFileSync(failedGroundingSpec, "utf8").includes("throw new Error('Generated spec is incomplete:"), "");

const completeRun = runSpec(completeSpec);
const incompleteRun = runSpec(incompleteSpec);
const draftRun = runSpec(draftSpec);
const failedGroundingRun = runSpec(failedGroundingSpec);
const incompleteOut = incompleteRun.stdout + incompleteRun.stderr;
const draftOut = draftRun.stdout + draftRun.stderr;
const failedOut = failedGroundingRun.stdout + failedGroundingRun.stderr;

check("complete spec passes in Chrome", completeRun.status === 0, completeRun.stdout + completeRun.stderr);
check(
  "incomplete spec fails before page actions",
  incompleteRun.status !== 0 && thrownIncompleteMessage(incompleteOut) && noNavigationFailure(incompleteOut),
  incompleteOut,
);
check(
  "allow-incomplete draft still fails at runtime",
  draftRun.status !== 0 && thrownIncompleteMessage(draftOut) && noNavigationFailure(draftOut),
  draftOut,
);
check(
  "failed-grounding spec fails before page actions",
  failedGroundingRun.status !== 0 && thrownIncompleteMessage(failedOut) && noNavigationFailure(failedOut),
  failedOut,
);

fs.rmSync(tmp, { recursive: true, force: true });
process.exitCode = failures ? 1 : 0;
