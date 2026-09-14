"use strict";

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..");

function workflowPaths(stepsPath, baseUrl, specName, hasExplicitSpecName) {
  const canonicalSteps = fs.realpathSync(stepsPath);
  let stem;
  if (hasExplicitSpecName) {
    if (
      typeof specName !== "string" ||
      !specName.trim() ||
      specName === "." ||
      specName === ".." ||
      !/^[A-Za-z0-9_.-]+$/.test(specName)
    ) {
      throw new Error("Explicit spec name must be a safe filename");
    }
    stem = specName;
  } else {
    stem = path
      .basename(canonicalSteps, path.extname(canonicalSteps))
      .replace(/[^\w.-]+/g, "_") || "generated";
  }

  const identity = crypto
    .createHash("sha256")
    .update(canonicalSteps)
    .update("\0")
    .update(fs.readFileSync(canonicalSteps))
    .update("\0")
    .update(baseUrl)
    .update("\0")
    .update(stem)
    .digest("hex");
  const workflowDir = path.join(REPO_ROOT, ".qa-pipeline", "workflows", identity);

  return {
    actionPlan: path.join(workflowDir, "action_plan.json"),
    refinedPlan: path.join(workflowDir, "refined_action_plan.json"),
    specPath: path.join(REPO_ROOT, "tests", "generated", identity, `${stem}.spec.ts`),
  };
}

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

function stopChild(child) {
  if (!child || child.killed || child.exitCode != null) return;
  const pid = child.pid;
  if (!pid) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(pid), "/T", "/F"]);
    return;
  }
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    // already gone
  }
  setTimeout(() => {
    try {
      if (child.exitCode == null) process.kill(pid, "SIGKILL");
    } catch {
      // already gone
    }
  }, 800);
}

function cancelledError(stdout = "", stderr = "") {
  const err = new Error("Pipeline cancelled");
  err.cancelled = true;
  err.stdout = stdout;
  err.stderr = stderr;
  return err;
}

function runCommand(bin, args, { cwd = REPO_ROOT, env = {}, onLine, signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(cancelledError());
      return;
    }

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
    const onAbort = () => stopChild(child);
    if (signal) signal.addEventListener("abort", onAbort, { once: true });

    child.stdout.on("data", (d) => {
      stdout += d;
      push(d, "stdout");
    });
    child.stderr.on("data", (d) => {
      stderr += d;
      push(d, "stderr");
    });
    child.on("error", (err) => {
      if (signal) signal.removeEventListener("abort", onAbort);
      reject(err);
    });
    child.on("close", (code) => {
      if (signal) signal.removeEventListener("abort", onAbort);
      if (signal?.aborted) {
        reject(cancelledError(stdout, stderr));
        return;
      }
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
 * @param {boolean} [opts.headed]
 * @param {string} [opts.specName]
 * @param {(evt: object) => void} [opts.onEvent]
 * @param {AbortSignal} [opts.signal]
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
    headed = true,
    specName,
    onEvent = () => {},
    signal,
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

  const { actionPlan, refinedPlan, specPath } = workflowPaths(
    resolvedSteps,
    baseUrl,
    specName,
    Object.hasOwn(opts, "specName") && specName !== undefined
  );

  if (!parse && refine && !fs.existsSync(actionPlan)) {
    throw new Error(
      `Scoped action plan is required when parse is skipped: ${actionPlan}. Run parse for this workflow first.`
    );
  }
  if (!parse && !refine && generate && !fs.existsSync(actionPlan)) {
    throw new Error(
      `Scoped action plan is required when parse and refine are skipped: ${actionPlan}. Run parse for this workflow first.`
    );
  }
  if (!generate && runTests && !fs.existsSync(specPath)) {
    throw new Error(
      `Scoped generated spec is required when generate is skipped: ${specPath}. Run generate for this workflow first.`
    );
  }

  const llmArgs = [];
  if (backend) llmArgs.push("--backend", backend);
  if (model) llmArgs.push("--model", model);

  const emit = (stage, status, detail) =>
    onEvent({ type: "stage", stage, status, detail });

  const log = (line, stream = "stdout") =>
    onEvent({ type: "log", stream, line });

  const throwIfCancelled = () => {
    if (signal?.aborted) throw cancelledError();
  };

  throwIfCancelled();
  if (parse) {
    fs.mkdirSync(path.dirname(actionPlan), { recursive: true });
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
    await runCommand(resolveCli("qa-parse"), args, { onLine: log, signal });
    emit("parse", "done", actionPlan);
  }

  throwIfCancelled();
  if (refine) {
    emit("refine", "running", "grounding against live DOM");
    const args = [
      "--plan",
      actionPlan,
      "--out",
      refinedPlan,
      "--url",
      baseUrl,
      ...(headed ? ["--headed"] : []),
      ...llmArgs,
    ];
    await runCommand(resolveCli("qa-refine"), args, { onLine: log, signal });
    emit("refine", "done", refinedPlan);
  }

  throwIfCancelled();
  if (generate) {
    fs.mkdirSync(path.dirname(specPath), { recursive: true });
    emit("generate", "running", "plan → Playwright spec");
    const planIn = refine && fs.existsSync(refinedPlan) ? refinedPlan : actionPlan;
    await runCommand(
      resolveCli("qa-generate"),
      [planIn, specPath, "--base-url", baseUrl],
      { onLine: log, signal }
    );
    emit("generate", "done", specPath);
  }

  throwIfCancelled();
  if (runTests) {
    emit("test", "running", specPath);
    await runCommand(
      "npx",
      ["playwright", "test", specPath],
      {
        onLine: log,
        env: { QA_SLOWMO: "0", QA_BASE_URL: baseUrl },
        signal,
      }
    );
    emit("test", "done", specPath);
  }

  return { actionPlan, refinedPlan, specPath };
}

module.exports = { runPipeline, REPO_ROOT, resolveCli };
