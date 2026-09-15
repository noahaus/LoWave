"use strict";

const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");

function cookieApplies(cookie, hostname) {
  const domain = String(cookie.domain || "").replace(/^\./, "").toLowerCase();
  const host = String(hostname).replace(/^\[|\]$/g, "").toLowerCase();
  return domain && (host === domain || host.endsWith(`.${domain}`));
}

function canonicalOrigin(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("invalid bound origin");
  return url.origin;
}

function validateStorageState(state, baseURL) {
  const bound = new URL(canonicalOrigin(baseURL));
  if (!state || !Array.isArray(state.cookies) || !Array.isArray(state.origins)) {
    throw new Error("invalid authentication state");
  }
  for (const item of state.origins) {
    if (canonicalOrigin(item.origin) !== bound.origin) throw new Error("cross-origin authentication state rejected");
  }
  for (const cookie of state.cookies) {
    if (!cookieApplies(cookie, bound.hostname)) throw new Error("cookie domain is not applicable to the bound origin");
  }
  return state;
}

function parseAuthOutput(raw, baseURL) {
  try { return validateStorageState(JSON.parse(raw.toString("utf8")), baseURL); }
  catch { throw new Error("invalid authentication state from runtime hook"); }
}

function treeKillCommand(pid, platform = process.platform) {
  if (platform !== "win32") return null;
  return { command: "taskkill", args: ["/pid", String(pid), "/T", "/F"] };
}

function killTree(child, signal = "SIGTERM") {
  if (!child?.pid) return;
  const command = treeKillCommand(child.pid);
  if (command) {
    try { spawn(command.command, command.args, { stdio: "ignore", windowsHide: true }).unref(); } catch {}
    return;
  }
  try { process.kill(-child.pid, signal); } catch {}
}

function stop(child) {
  if (!child?.pid || (process.platform === "win32" && child.exitCode != null)) return null;
  killTree(child, "SIGTERM");
  const timer = setTimeout(() => killTree(child, "SIGKILL"), 500);
  timer.unref();
  return timer;
}

function runAuthHook({ hookPath, baseURL, timeoutMs = 30000, maxBytes = 1024 * 1024, signal }) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("runtime authentication cancelled"));
    let request;
    try {
      request = { version: 1, baseURL, origin: canonicalOrigin(baseURL) };
    } catch {
      return reject(new Error("invalid bound origin"));
    }
    const child = spawn(process.execPath, [__filename, hookPath], {
      detached: process.platform !== "win32",
      stdio: ["pipe", "pipe", "ignore", "ipc"],
    });
    let chunks = [], size = 0, settled = false, failure = null;
    const parentExit = () => { if (child.pid && process.platform !== "win32") { try { process.kill(-child.pid, "SIGKILL"); } catch {} } };
    process.once("exit", parentExit);
    const fail = (message) => {
      if (settled) return;
      settled = true; failure = new Error(message); stop(child);
    };
    const timer = setTimeout(() => fail("runtime authentication timed out"), timeoutMs);
    const abort = () => fail("runtime authentication cancelled");
    signal?.addEventListener("abort", abort, { once: true });
    child.stdout.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBytes) return fail("runtime authentication exceeded output limit");
      chunks.push(chunk);
    });
    child.on("error", () => fail("runtime authentication helper failed"));
    child.on("close", (code) => {
      clearTimeout(timer); signal?.removeEventListener("abort", abort);
      process.removeListener("exit", parentExit);
      if (process.platform !== "win32" && child.pid) {
        try { process.kill(-child.pid, "SIGKILL"); } catch {}
      }
      if (failure) return reject(failure);
      if (settled) return;
      settled = true;
      if (code !== 0) return reject(new Error("runtime authentication hook failed"));
      try { resolve(parseAuthOutput(Buffer.concat(chunks), baseURL)); }
      catch (err) { reject(err); }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

async function childMain(hookPath) {
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  const request = JSON.parse(raw);
  const originalOut = process.stdout.write.bind(process.stdout);
  const originalErr = process.stderr.write.bind(process.stderr);
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  const parentPid = process.ppid;
  let browser;
  const closeForSignal = async () => { try { await browser?.close(); } finally { process.exit(1); } };
  for (const event of ["SIGTERM", "SIGINT", "SIGHUP", "disconnect"]) process.once(event, closeForSignal);
  const parentWatch = setInterval(() => {
    try { process.kill(parentPid, 0); } catch { void closeForSignal(); }
  }, 250);
  parentWatch.unref();
  try {
    const hook = await import(pathToFileURL(hookPath).href);
    const authenticate = hook.authenticate || hook.default?.authenticate;
    if (typeof authenticate !== "function") throw new Error("hook must export authenticate");
    const { chromium } = require("@playwright/test");
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    const page = await context.newPage();
    await authenticate({ page, baseURL: request.baseURL, origin: request.origin });
    if (new URL(page.url()).origin !== request.origin) throw new Error("hook ended on wrong origin");
    const state = await context.storageState({ indexedDB: true });
    await new Promise((resolve, reject) => {
      originalOut(JSON.stringify(validateStorageState(state, request.baseURL)), err => err ? reject(err) : resolve());
    });
  } finally {
    clearInterval(parentWatch);
    await browser?.close();
    process.stdout.write = originalOut;
    process.stderr.write = originalErr;
  }
  process.exit(0);
}

if (require.main === module) childMain(process.argv[2]).catch(() => process.exit(1));
module.exports = { canonicalOrigin, validateStorageState, parseAuthOutput, runAuthHook, treeKillCommand };
