"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..");

function resolveCli(name) {
  const candidates = [
    path.join(REPO_ROOT, ".venv", "bin", name),
    path.join(REPO_ROOT, ".venv", "Scripts", `${name}.exe`),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return name; // fall back to PATH
}

function runCommand(bin, args, { cwd = REPO_ROOT, env = {}, onLine } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd,
      env: { ...process.env, ...env, PYTHONUNBUFFERED: "1" },
      shell: false,
    });

    const push = (chunk, stream) => {
      const text = chunk.toString();
      if (onLine) {
        for (const line of text.split(/\r?\n/)) {
          if (line.length) onLine(line, stream);
        }
      }
    };

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => {
      stdout += d;
      push(d, "stdout");
    });
    child.stderr.on("data", (d) => {
      stderr += d;
      push(d, "stderr");
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve({ code, stdout, stderr });
      else {
        const err = new Error(`${path.basename(bin)} exited with code ${code}`);
        err.code = code;
        err.stdout = stdout;
        err.stderr = stderr;
        reject(err);
      }
    });
  });
}

/**
 * @param {object} opts
 * @param {string} opts.stepsPath
 * @param {string} [opts.baseUrl]
 * @param {string} [opts.backend]
 * @param {string} [opts.model]
 * @param {string} [opts.username]
 * @param {string} [opts.password]
 * @param {boolean} [opts.parse]
 * @param {boolean} [opts.refine]
 * @param {boolean} [opts.generate]
 * @param {boolean} [opts.runTests]
 * @param {string} [opts.specName]
 * @param {(evt: object) => void} [opts.onEvent]
 */
async function runPipeline(opts) {
  const {
    stepsPath,
    baseUrl = "http://localhost:3000",
    backend = "ollama",
    model = "qwen3-coder:30b",
    username = "demo@kestrel.app",
    password = "test1234",
    parse = true,
    refine = true,
    generate = true,
    runTests = false,
    specName,
    onEvent = () => {},
  } = opts;

  if (!stepsPath) {
    throw new Error("Steps file path is required");
  }
  const resolvedSteps = path.isAbsolute(stepsPath)
    ? stepsPath
    : path.resolve(process.cwd(), stepsPath);
  if (!fs.existsSync(resolvedSteps)) {
    throw new Error(`Steps file not found: ${resolvedSteps}`);
  }

  const stem =
    specName ||
    path.basename(resolvedSteps, path.extname(resolvedSteps)).replace(/[^\w.-]+/g, "_");
  const actionPlan = path.join(REPO_ROOT, "action_plan.json");
  const refinedPlan = path.join(REPO_ROOT, "refined_action_plan.json");
  const specPath = path.join(REPO_ROOT, "tests", `${stem}.spec.ts`);

  const llmArgs = [];
  if (backend) llmArgs.push("--backend", backend);
  if (model) llmArgs.push("--model", model);

  const emit = (stage, status, detail) =>
    onEvent({ type: "stage", stage, status, detail });

  const log = (line, stream = "stdout") =>
    onEvent({ type: "log", stream, line });

  if (parse) {
    emit("parse", "running", "steps → action_plan.json");
    const args = [
      resolvedSteps,
      actionPlan,
      "--base-url",
      baseUrl,
      ...llmArgs,
    ];
    if (username) args.push("--username", username);
    if (password) args.push("--password", password);
    await runCommand(resolveCli("qa-parse"), args, { onLine: log });
    emit("parse", "done", actionPlan);
  }

  if (refine) {
    emit("refine", "running", "grounding against live DOM");
    const args = [
      "--plan",
      actionPlan,
      "--out",
      refinedPlan,
      "--url",
      baseUrl,
      ...llmArgs,
    ];
    await runCommand(resolveCli("qa-refine"), args, { onLine: log });
    emit("refine", "done", refinedPlan);
  }

  if (generate) {
    emit("generate", "running", "plan → Playwright spec");
    const planIn = refine && fs.existsSync(refinedPlan) ? refinedPlan : actionPlan;
    await runCommand(
      resolveCli("qa-generate"),
      [planIn, specPath, "--base-url", baseUrl],
      { onLine: log }
    );
    emit("generate", "done", specPath);
  }

  if (runTests) {
    emit("test", "running", specPath);
    await runCommand(
      "npx",
      ["playwright", "test", specPath],
      {
        onLine: log,
        env: { QA_SLOWMO: "0", QA_BASE_URL: baseUrl },
      }
    );
    emit("test", "done", specPath);
  }

  return { actionPlan, refinedPlan, specPath };
}

module.exports = { runPipeline, REPO_ROOT, resolveCli };
