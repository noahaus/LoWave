"use strict";

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const {
  explainFailure,
  stageStatusMessage,
  statusFromLogLine,
} = require("./status-messages");
const {
  parseNumberedSteps,
  parseSpecSteps,
  parseReporterLine,
  stepFromReporterPayload,
  wrapSpecInTestSteps,
} = require("./step-tiles");
const { createResultsStore } = require("./results-store");

const REPO_ROOT = path.resolve(__dirname, "..");
const RESULTS_DB_PATH = path.join(REPO_ROOT, "outputs", "qa-results.sqlite");

let defaultStore;

function defaultResultsStore() {
  if (!defaultStore) defaultStore = createResultsStore(RESULTS_DB_PATH);
  return defaultStore;
}

function defaultModelForBackend(backend) {
  return backend === "ollama" ? "qwen3-coder:30b" : undefined;
}

function runtimeAuthEnvironment(authHook) {
  return authHook
    ? { QA_AUTH_HOOK: authHook, QA_USERNAME: null, QA_PASSWORD: null }
    : { QA_AUTH_HOOK: null };
}

function workflowPaths(stepsPath, baseUrl, specName, hasExplicitSpecName, authHook = "") {
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
  if (authHook) {
    identity.update("\0runtime-auth\0").update(path.resolve(authHook)).update("\0").update(fs.readFileSync(authHook));
  }
  const digest = identity.digest("hex");
  const workflowDir = path.join(REPO_ROOT, "outputs", "workflows", digest);

  return {
    workflowHash: digest,
    workflowDir,
    actionPlan: path.join(workflowDir, "action_plan.json"),
    refinedPlan: path.join(workflowDir, "refined_action_plan.json"),
    specPath: path.join(REPO_ROOT, "outputs", "tests", digest, `${stem}.spec.ts`),
  };
}

function specStatus(opts = {}) {
  const stepsPath = opts.stepsPath;
  if (!stepsPath || !fs.existsSync(stepsPath)) {
    return { specPath: "", exists: false, content: "", workflowHash: "", stepStatuses: {}, lastRun: null };
  }
  const resolvedSteps = path.isAbsolute(stepsPath)
    ? stepsPath
    : path.resolve(process.cwd(), stepsPath);
  const specName = opts.specName;
  const { specPath, workflowHash } = workflowPaths(
    resolvedSteps,
    opts.baseUrl || "http://localhost:3000",
    specName,
    Object.hasOwn(opts, "specName") && specName !== undefined,
    opts.authHook || ""
  );
  const exists = fs.existsSync(specPath);
  const store = opts.resultsStore || defaultResultsStore();
  const latest = store.latestResults(workflowHash);
  return {
    specPath,
    exists,
    content: exists ? fs.readFileSync(specPath, "utf8") : "",
    workflowHash,
    stepStatuses: latest?.stepStatuses || {},
    lastRun: latest
      ? {
          runId: latest.runId,
          passed: latest.passed,
          cancelled: latest.cancelled,
          finishedAt: latest.finishedAt,
        }
      : null,
  };
}

function newRunLogPath(workflowDir) {
  const logsDir = path.join(workflowDir, "logs");
  fs.mkdirSync(logsDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return path.join(logsDir, `${stamp}.log`);
}

function appendRunLog(logPath, line, stream = "stdout") {
  if (!logPath) return;
  fs.appendFileSync(logPath, `[${stream}] ${line}\n`);
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
  if (!child) return;
  const pid = child.pid;
  if (!pid) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(pid), "/T", "/F"]);
    return;
  }
  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    // already gone
  }
  setTimeout(() => {
    try {
      // Descendants may survive even when the direct Python child has exited.
      process.kill(-pid, "SIGKILL");
    } catch {
      // already gone
    }
  }, 800).unref();
}

const INCOMPLETE_EXIT_CODE = 3;

function cancelledError(stdout = "", stderr = "") {
  const err = new Error("Pipeline cancelled");
  err.cancelled = true;
  err.stdout = stdout;
  err.stderr = stderr;
  return err;
}

function failureMeta(err) {
  return {
    explanation: err?.explanation || "",
    logPath: err?.logPath || "",
    stage: err?.stage || "",
  };
}

function runEventFromError(err) {
  if (err?.cancelled) {
    return {
      type: "run",
      status: "cancelled",
      error: err.message,
      ...failureMeta(err),
    };
  }
  if (err?.incomplete) {
    return {
      type: "run",
      status: "incomplete",
      error: err.message,
      stderr: err.stderr || "",
      result: err.result,
      ...failureMeta(err),
    };
  }
  return {
    type: "run",
    status: "failed",
    error: err?.message || "Failed",
    stderr: err?.stderr || "",
    ...failureMeta(err),
  };
}

function watchJsonl(filePath, onLine) {
  let position = 0;
  let pending = "";
  const consume = () => {
    let fd;
    try {
      fd = fs.openSync(filePath, "r");
    } catch {
      return;
    }
    try {
      const size = fs.fstatSync(fd).size;
      if (size <= position) return;
      const buf = Buffer.alloc(size - position);
      fs.readSync(fd, buf, 0, buf.length, position);
      position = size;
      pending += buf.toString("utf8");
      const lines = pending.split(/\r?\n/);
      pending = lines.pop() || "";
      for (const line of lines) {
        if (line.trim()) onLine(line);
      }
    } finally {
      fs.closeSync(fd);
    }
  };
  consume();
  let watcher;
  try {
    watcher = fs.watch(filePath, () => consume());
  } catch {
    watcher = null;
  }
  const timer = setInterval(consume, 50);
  return () => {
    if (watcher) watcher.close();
    clearInterval(timer);
    consume();
    if (pending.trim()) onLine(pending);
  };
}

function liveSpecPathFor(specPath) {
  return specPath.replace(/\.spec\.ts$/, ".lowave-run.spec.ts");
}

function attachFailure(err, { stage, logPath, incomplete = false } = {}) {
  let logText = "";
  try {
    if (logPath) logText = fs.readFileSync(logPath, "utf8");
  } catch {
    logText = "";
  }
  err.stage = stage || err.stage || "";
  err.logPath = logPath || err.logPath || "";
  err.explanation = explainFailure({
    stage: err.stage,
    logText,
    error: err.message,
    stderr: err.stderr,
    incomplete: incomplete || err.incomplete,
  });
  return err;
}

function runCommand(bin, args, { cwd = REPO_ROOT, env = {}, onLine, signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(cancelledError());
      return;
    }

    const childEnv = { ...process.env, ...env, PYTHONUNBUFFERED: "1" };
    for (const [key, value] of Object.entries(env)) if (value == null) delete childEnv[key];
    // Electron leaves these set; Playwright Chromium will not show a window if they leak in.
    delete childEnv.ELECTRON_RUN_AS_NODE;
    delete childEnv.ELECTRON_NO_ASAR;
    delete childEnv.ELECTRON_NO_ATTACH_CONSOLE;
    const child = spawn(bin, args, {
      cwd,
      env: childEnv,
      shell: false,
      // A dedicated POSIX process group lets Stop terminate Python and its CLIs.
      detached: process.platform !== "win32",
    });

    const leftovers = { stdout: "", stderr: "" };
    const push = (chunk, stream) => {
      const text = leftovers[stream] + chunk.toString();
      const lines = text.split(/\r?\n/);
      leftovers[stream] = lines.pop() || "";
      if (onLine) {
        for (const line of lines) {
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
      if (onLine) {
        for (const stream of ["stdout", "stderr"]) {
          if (leftovers[stream]) onLine(leftovers[stream], stream);
        }
      }
      if (signal?.aborted) {
        reject(cancelledError(stdout, stderr));
        return;
      }
      if (code === 0) resolve({ code, stdout, stderr });
      else {
        const err = new Error(`${path.basename(bin)} exited with code ${code}`);
        err.code = code;
        err.exitCode = code;
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
 * @param {string} [opts.authHook] absolute path to explicitly trusted local code
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
  const authHook = opts.authHook ? path.resolve(opts.authHook) : "";
  if (authHook && (!path.isAbsolute(opts.authHook) || !fs.existsSync(authHook))) {
    throw new Error("Runtime authentication hook must be an existing absolute path");
  }
  if (authHook) {
    const rawBaseUrl = opts.baseUrl || "http://localhost:3000";
    const authority = rawBaseUrl.match(/^[a-z][a-z0-9+.-]*:\/\/([^/]*)/i)?.[1] || "";
    if (/[^\x00-\x7F]/.test(authority)) throw new Error("Runtime authentication requires an ASCII or punycode hostname");
    const parsedBaseUrl = new URL(rawBaseUrl);
    if (parsedBaseUrl.username || parsedBaseUrl.password) throw new Error("Runtime authentication base URL must not contain userinfo");
    const protocol = parsedBaseUrl.protocol;
    if (protocol !== "http:" && protocol !== "https:") {
      throw new Error("Runtime authentication requires an HTTP(S) base URL");
    }
  }
  const {
    stepsPath,
    baseUrl = "http://localhost:3000",
    backend = "ollama",
    model = defaultModelForBackend(backend),
    username = authHook ? "" : "demo@kestrel.app",
    password = authHook ? "" : "test1234",
    parse = true,
    refine = true,
    generate = true,
    runTests = false,
    headed = true,
    specName,
    onEvent = () => {},
    signal,
  } = opts;

  if (authHook && (username || password)) {
    throw new Error("Runtime authentication cannot be combined with legacy credentials");
  }

  if (!stepsPath) {
    throw new Error("Steps file path is required");
  }
  const resolvedSteps = path.isAbsolute(stepsPath)
    ? stepsPath
    : path.resolve(process.cwd(), stepsPath);
  if (!fs.existsSync(resolvedSteps)) {
    throw new Error(`Steps file not found: ${resolvedSteps}`);
  }

  const { workflowDir, actionPlan, refinedPlan, specPath, workflowHash } = workflowPaths(
    resolvedSteps,
    baseUrl,
    specName,
    Object.hasOwn(opts, "specName") && specName !== undefined,
    authHook
  );
  const resultsStore = opts.resultsStore || defaultResultsStore();

  const llmArgs = [];
  if (backend) llmArgs.push("--backend", backend);
  if (model) llmArgs.push("--model", model);

  const runningAny = parse || refine || generate || runTests;
  let logPath = "";
  let currentStage = "";
  let testSpecText = "";
  let testRun = null;

  try {
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

    if (!runningAny) {
      return { actionPlan, refinedPlan, specPath, workflowHash };
    }

    fs.mkdirSync(workflowDir, { recursive: true });
    logPath = newRunLogPath(workflowDir);
    fs.writeFileSync(logPath, `# QA pipeline run ${new Date().toISOString()}\n`);

    const emit = (stage, status, detail) => {
      currentStage = stage;
      const message = stageStatusMessage(stage, status);
      if (message) onEvent({ type: "status", stage, message });
      onEvent({ type: "stage", stage, status, detail });
    };

    const onLine = (line, stream = "stdout") => {
      appendRunLog(logPath, line, stream);
      const message = statusFromLogLine(line, currentStage);
      if (message) onEvent({ type: "status", stage: currentStage, stream, message });
    };

    const throwIfCancelled = () => {
      if (signal?.aborted) throw cancelledError();
    };
    const authEnv = runtimeAuthEnvironment(authHook);

    throwIfCancelled();
    if (parse) {
      fs.mkdirSync(path.dirname(actionPlan), { recursive: true });
      appendRunLog(logPath, "==== parse ====", "stage");
      emit("parse", "running", "steps → action_plan.json");
      const args = [
        resolvedSteps,
        actionPlan,
        "--base-url",
        baseUrl,
        ...llmArgs,
        ...(authHook ? ["--runtime-auth"] : []),
      ];
      if (username) args.push("--username", username);
      if (password) args.push("--password", password);
      await runCommand(resolveCli("qa-parse"), args, { onLine, signal, env: authEnv });
      emit("parse", "done", actionPlan);
    }

    throwIfCancelled();
    if (refine) {
      appendRunLog(logPath, "==== refine ====", "stage");
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
        ...(authHook ? ["--auth-hook", authHook] : []),
      ];
      await runCommand(resolveCli("qa-refine"), args, { onLine, signal, env: authEnv });
      emit("refine", "done", refinedPlan);
    }

    throwIfCancelled();
    if (generate) {
      fs.mkdirSync(path.dirname(specPath), { recursive: true });
      appendRunLog(logPath, "==== generate ====", "stage");
      emit("generate", "running", "plan → Playwright spec");
      const planIn = refine && fs.existsSync(refinedPlan) ? refinedPlan : actionPlan;
      try {
        await runCommand(
          resolveCli("qa-generate"),
          [planIn, specPath, "--base-url", baseUrl, ...(authHook ? ["--runtime-auth"] : [])],
          { onLine, signal, env: authEnv }
        );
        emit("generate", "done", specPath);
      } catch (err) {
        if ((err.exitCode ?? err.code) === INCOMPLETE_EXIT_CODE) {
          err.incomplete = true;
          err.result = { specPath, incomplete: true, logPath };
          emit("generate", "incomplete", specPath);
        }
        throw err;
      }
    }

    throwIfCancelled();
    if (runTests) {
      appendRunLog(logPath, "==== test ====", "stage");
      emit("test", "running", specPath);
      appendRunLog(logPath, headed ? "browser: headed" : "browser: headless", "stage");
      onEvent({
        type: "status",
        message: headed
          ? "Opening a visible browser for this test run."
          : "Running tests without a browser window.",
      });
      const originalSpec = fs.existsSync(specPath) ? fs.readFileSync(specPath, "utf8") : "";
      const wrappedSpec = wrapSpecInTestSteps(originalSpec);
      const livePath = wrappedSpec !== originalSpec ? liveSpecPathFor(specPath) : specPath;
      if (livePath !== specPath) fs.writeFileSync(livePath, wrappedSpec);
      const specText = wrappedSpec || originalSpec;
      testSpecText = specText;
      const fromFile = parseNumberedSteps(fs.readFileSync(resolvedSteps, "utf8"));
      const fromSpec = parseSpecSteps(specText).map((step) => ({ number: step.number, text: step.text }));
      testRun = resultsStore.startRun({
        workflowHash,
        stepsPath: resolvedSteps,
        specPath,
        steps: fromFile.length ? fromFile : fromSpec,
      });
      const progressPath = path.join(workflowDir, "step-progress.jsonl");
      fs.writeFileSync(progressPath, "");
      const reporterPath = path.join(__dirname, "playwright-step-reporter.js");
      const emitProgress = (payload) => {
        if (!payload) return;
        if (payload.status === "test-end") {
          onEvent({
            type: "test-step",
            status: "test-end",
            passed: Boolean(payload.passed),
            specPath,
            specText,
          });
          return;
        }
        const step = stepFromReporterPayload(payload, specText);
        if (!step) return;
        if (testRun && (payload.status === "running" || payload.status === "pass" || payload.status === "fail")) {
          resultsStore.setStepStatus(testRun.id, step, payload.status, payload.title);
        }
        onEvent({
          type: "test-step",
          step,
          status: payload.status,
          specPath,
          specText,
        });
      };
      const stopWatch = watchJsonl(progressPath, (line) => {
        emitProgress(parseReporterLine(line) || (() => {
          try {
            return JSON.parse(line);
          } catch {
            return null;
          }
        })());
      });
      try {
        await runCommand(
          "npx",
          [
            "playwright",
            "test",
            ...(headed ? ["--headed"] : []),
            "--reporter",
            "list",
            "--reporter",
            reporterPath,
            livePath,
          ],
          {
            onLine: (line, stream) => {
              const payload = parseReporterLine(line);
              if (payload) {
                emitProgress(payload);
                return;
              }
              onLine(line, stream);
            },
            env: {
              QA_HEADED: headed ? "1" : "0",
              QA_SLOWMO: headed ? "800" : "0",
              QA_BASE_URL: baseUrl,
              LOWAVE_STEP_EVENTS: progressPath,
              ...(headed ? { CI: null } : {}),
              ...authEnv,
            },
            signal,
          }
        );
        resultsStore.finishRun(testRun.id, { passed: true });
        testRun = null;
        emit("test", "done", specPath);
        onEvent({ type: "test-steps", passed: true, specPath, specText });
      } finally {
        stopWatch();
        if (livePath !== specPath) {
          try {
            fs.unlinkSync(livePath);
          } catch {
            // Best-effort cleanup of the wrapped spec used for live step reporting.
          }
        }
      }
    }

    return { actionPlan, refinedPlan, specPath, logPath, workflowHash };
  } catch (err) {
    if (testRun) {
      resultsStore.finishRun(testRun.id, { passed: false, cancelled: Boolean(err?.cancelled) });
      testRun = null;
    }
    if (currentStage === "test" && !err?.cancelled) {
      onEvent({
        type: "test-steps",
        passed: false,
        specPath,
        specText: testSpecText,
        output: `${err.stdout || ""}\n${err.stderr || ""}\n${err.message || ""}`,
      });
    }
    if (err?.cancelled) {
      err.stage = currentStage;
      err.logPath = logPath;
      err.explanation = "The run was cancelled before it finished.";
      throw err;
    }
    throw attachFailure(err, {
      stage: currentStage,
      logPath,
      incomplete: err.incomplete,
    });
  }
}

module.exports = {
  runPipeline,
  runCommand,
  runEventFromError,
  REPO_ROOT,
  resolveCli,
  defaultModelForBackend,
  runtimeAuthEnvironment,
  INCOMPLETE_EXIT_CODE,
  stopChild,
  specStatus,
  createResultsStore,
  RESULTS_DB_PATH,
};
