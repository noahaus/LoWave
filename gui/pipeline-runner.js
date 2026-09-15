"use strict";

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..");

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
  const workflowDir = path.join(REPO_ROOT, ".qa-pipeline", "workflows", digest);

  return {
    actionPlan: path.join(workflowDir, "action_plan.json"),
    refinedPlan: path.join(workflowDir, "refined_action_plan.json"),
    specPath: path.join(REPO_ROOT, "tests", "generated", digest, `${stem}.spec.ts`),
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

function runEventFromError(err) {
  if (err?.cancelled) {
    return { type: "run", status: "cancelled", error: err.message };
  }
  if (err?.incomplete) {
    return {
      type: "run",
      status: "incomplete",
      error: err.message,
      stderr: err.stderr || "",
      result: err.result,
    };
  }
  return {
    type: "run",
    status: "failed",
    error: err?.message || "Failed",
    stderr: err?.stderr || "",
  };
}

function runCommand(bin, args, { cwd = REPO_ROOT, env = {}, onLine, signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(cancelledError());
      return;
    }

    const childEnv = { ...process.env, ...env, PYTHONUNBUFFERED: "1" };
    for (const [key, value] of Object.entries(env)) if (value == null) delete childEnv[key];
    const child = spawn(bin, args, {
      cwd,
      env: childEnv,
      shell: false,
      // A dedicated POSIX process group lets Stop terminate Python and its CLIs.
      detached: process.platform !== "win32",
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

  const { actionPlan, refinedPlan, specPath } = workflowPaths(
    resolvedSteps,
    baseUrl,
    specName,
    Object.hasOwn(opts, "specName") && specName !== undefined,
    authHook
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
  const authEnv = runtimeAuthEnvironment(authHook);

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
      ...(authHook ? ["--runtime-auth"] : []),
    ];
    if (username) args.push("--username", username);
    if (password) args.push("--password", password);
    await runCommand(resolveCli("qa-parse"), args, { onLine: log, signal, env: authEnv });
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
      ...(authHook ? ["--auth-hook", authHook] : []),
    ];
    await runCommand(resolveCli("qa-refine"), args, { onLine: log, signal, env: authEnv });
    emit("refine", "done", refinedPlan);
  }

  throwIfCancelled();
  if (generate) {
    fs.mkdirSync(path.dirname(specPath), { recursive: true });
    emit("generate", "running", "plan → Playwright spec");
    const planIn = refine && fs.existsSync(refinedPlan) ? refinedPlan : actionPlan;
    try {
      await runCommand(
        resolveCli("qa-generate"),
        [planIn, specPath, "--base-url", baseUrl, ...(authHook ? ["--runtime-auth"] : [])],
        { onLine: log, signal, env: authEnv }
      );
      emit("generate", "done", specPath);
    } catch (err) {
      if ((err.exitCode ?? err.code) === INCOMPLETE_EXIT_CODE) {
        err.incomplete = true;
        err.result = { specPath, incomplete: true };
        emit("generate", "incomplete", specPath);
      }
      throw err;
    }
  }

  throwIfCancelled();
  if (runTests) {
    emit("test", "running", specPath);
    await runCommand(
      "npx",
      ["playwright", "test", specPath],
      {
        onLine: log,
        env: { QA_SLOWMO: "0", QA_BASE_URL: baseUrl, ...authEnv },
        signal,
      }
    );
    emit("test", "done", specPath);
  }

  return { actionPlan, refinedPlan, specPath };
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
};
