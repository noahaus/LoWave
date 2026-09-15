"use strict";

/**
 * Headless smoke test for the same pipeline runner the Electron GUI uses.
 * Usage:
 *   node smoke.js [steps.txt] [--stages=parse,generate,refine,test]
 */

const path = require("path");
const { runPipeline, REPO_ROOT } = require("./pipeline-runner");

const args = process.argv.slice(2);
const stagesArg = args.find((a) => a.startsWith("--stages="));
const stepsArg = args.find((a) => !a.startsWith("-"));

const stages = (stagesArg || "--stages=parse,generate")
  .split("=")[1]
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const stepsPath =
  stepsArg ||
  path.join(REPO_ROOT, "examples", "workflows", "login_and_search_report.txt");

(async () => {
  console.log("[smoke] steps:", stepsPath);
  console.log("[smoke] stages:", stages.join(","));
  try {
    const result = await runPipeline({
      stepsPath,
      baseUrl: process.env.QA_BASE_URL || "http://localhost:3000",
      backend: process.env.LLM_BACKEND || "ollama",
      model: process.env.QA_MODEL,
      parse: stages.includes("parse"),
      refine: stages.includes("refine"),
      generate: stages.includes("generate"),
      runTests: stages.includes("test"),
      onEvent: (evt) => {
        if (evt.type === "log") console.log(`[log] ${evt.line}`);
        if (evt.type === "stage")
          console.log(`[stage] ${evt.stage}: ${evt.status} ${evt.detail || ""}`);
      },
    });
    console.log("[smoke] OK", result);
    process.exit(0);
  } catch (err) {
    console.error("[smoke] FAILED", err.message);
    if (err.stderr) console.error(String(err.stderr).slice(-2000));
    process.exit(1);
  }
})();
