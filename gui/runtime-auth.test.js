"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { spawn } = require("node:child_process");
const { validateStorageState, parseAuthOutput, runAuthHook, treeKillCommand } = require("../runtime/auth-hook-runner.cjs");
const { runCommand, runPipeline, runtimeAuthEnvironment } = require("./pipeline-runner");

test("storage state rejects cross-origin local storage", () => {
  assert.throws(() => validateStorageState({ cookies: [], origins: [{ origin: "https://evil.test", localStorage: [] }] }, "https://app.test"), /cross-origin/);
});

test("storage state rejects cookies that cannot apply to the bound origin", () => {
  assert.throws(() => validateStorageState({ cookies: [{ name: "sid", value: "secret", domain: "evil.test", path: "/" }], origins: [] }, "https://app.test"), /cookie domain/);
});

test("IPv6 cookie domains use the same hostname form as the bound origin", () => {
  assert.doesNotThrow(() => validateStorageState(
    { cookies: [{ name: "sid", value: "secret", domain: "::1", path: "/" }], origins: [] },
    "http://[::1]:3000",
  ));
});

test("Windows cleanup uses taskkill for the complete helper tree", () => {
  assert.deepEqual(treeKillCommand(4321, "win32"), {
    command: "taskkill",
    args: ["/pid", "4321", "/T", "/F"],
  });
});

test("malformed runtime-auth URL fails promptly before spawning a helper", async () => {
  const started = Date.now();
  await assert.rejects(
    runAuthHook({ hookPath: "/does/not/matter.cjs", baseURL: "https://user:LEAK_SENTINEL@app.test" }),
    /invalid bound origin/,
  );
  assert.ok(Date.now() - started < 1000);
});

test("malformed helper output fails without echoing it", () => {
  assert.throws(() => parseAuthOutput(Buffer.from("LEAK_SENTINEL"), "https://app.test"), (err) => {
    assert.match(err.message, /invalid authentication state/);
    assert.doesNotMatch(err.message, /LEAK_SENTINEL/);
    return true;
  });
});

test("runPipeline isolates authenticated cached artifacts by hook identity", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-id-"));
  const steps = path.join(dir, "steps.txt");
  const hook = path.join(dir, "hook.cjs");
  fs.writeFileSync(steps, "1. Open app"); fs.writeFileSync(hook, "exports.authenticate=async()=>{};");
  const opts = { stepsPath: steps, baseUrl: "https://app.test", parse: false, refine: false, generate: false, runTests: false };
  const plain = await runPipeline(opts);
  const first = await runPipeline({ ...opts, authHook: hook });
  fs.appendFileSync(hook, "\n// changed");
  const second = await runPipeline({ ...opts, authHook: hook });
  assert.notEqual(first.actionPlan, plain.actionPlan);
  assert.notEqual(first.actionPlan, second.actionPlan);
  assert.equal(plain.actionPlan, (await runPipeline(opts)).actionPlan);
});

test("no-auth child strips an inherited hook", async () => {
  const previous = process.env.QA_AUTH_HOOK;
  process.env.QA_AUTH_HOOK = "/ambient/hook.cjs";
  try {
    const result = await runCommand(process.execPath, ["-e", "process.stdout.write(process.env.QA_AUTH_HOOK || 'ABSENT')"], { env: { QA_AUTH_HOOK: null } });
    assert.equal(result.stdout, "ABSENT");
  } finally {
    if (previous === undefined) delete process.env.QA_AUTH_HOOK; else process.env.QA_AUTH_HOOK = previous;
  }
});

test("runtime-auth child environment strips inherited legacy credentials", () => {
  assert.deepEqual(runtimeAuthEnvironment("/trusted/hook.cjs"), {
    QA_AUTH_HOOK: "/trusted/hook.cjs",
    QA_USERNAME: null,
    QA_PASSWORD: null,
  });
});

test("Node helper kills a hook descendant after nonzero exit", { skip: process.platform === "win32", timeout: 10000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-node-tree-"));
  const hook = path.join(dir, "hook.cjs");
  const pidFile = path.join(dir, "child.pid");
  fs.writeFileSync(hook, `const {spawn}=require('node:child_process');const fs=require('node:fs');module.exports.authenticate=async()=>{const c=spawn('sh',['-c',"trap '' TERM; sleep 60"],{stdio:'ignore'});fs.writeFileSync(${JSON.stringify(pidFile)},String(c.pid));throw new Error('LEAK_SENTINEL');};`);
  await assert.rejects(runAuthHook({ hookPath: hook, baseURL: "http://127.0.0.1:9" }), /hook failed/);
  const pid = Number(fs.readFileSync(pidFile, "utf8"));
  let gone = false;
  for (let i = 0; i < 20; i++) {
    try { process.kill(pid, 0); } catch (err) { if (err.code === "ESRCH") { gone = true; break; } }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(gone, true, "hook descendant survived helper cleanup");
});

test("Node timeout settles when exited helper leaves descendant holding stdout", { skip: process.platform === "win32", timeout: 3000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-node-stdout-"));
  const hook = path.join(dir, "hook.cjs");
  fs.writeFileSync(hook, `const {spawn}=require('node:child_process');module.exports.authenticate=async()=>{const c=spawn('sh',['-c',"trap '' TERM HUP; while true; do sleep 1; done"],{stdio:['ignore',process.stdout,process.stderr]});c.unref();throw new Error('stop');};`);
  await assert.rejects(runAuthHook({ hookPath: hook, baseURL: "http://127.0.0.1:9", timeoutMs: 1000 }), /timed out|hook failed/);
});

test("Node helper kills a hook descendant after successful state transfer", { skip: process.platform === "win32", timeout: 10000 }, async (t) => {
  const server = http.createServer((_req, res) => res.end("ok"));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => server.close());
  const baseURL = `http://127.0.0.1:${server.address().port}`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-node-success-"));
  const hook = path.join(dir, "hook.cjs"); const pidFile = path.join(dir, "child.pid");
  fs.writeFileSync(hook, `const {spawn}=require('node:child_process');const fs=require('node:fs');module.exports.authenticate=async({page,baseURL})=>{const c=spawn('sh',['-c',"trap '' TERM; sleep 60"],{stdio:'ignore',detached:false});c.unref();fs.writeFileSync(${JSON.stringify(pidFile)},String(c.pid));await page.goto(baseURL);};`);
  await runAuthHook({ hookPath: hook, baseURL });
  const pid = Number(fs.readFileSync(pidFile, "utf8"));
  let gone = false;
  for (let i = 0; i < 20; i++) { try { process.kill(pid, 0); } catch (err) { if (err.code === "ESRCH") { gone = true; break; } } await new Promise(r => setTimeout(r, 50)); }
  assert.equal(gone, true, "hook descendant survived successful helper cleanup");
});

test("Node helper ignores hook logging and exits after valid state despite live handles", { timeout: 5000 }, async (t) => {
  const server = http.createServer((_req, res) => res.end("ok"));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve)); t.after(() => server.close());
  const baseURL = `http://127.0.0.1:${server.address().port}`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-node-live-handle-"));
  const hook = path.join(dir, "hook.cjs");
  fs.writeFileSync(hook, "console.log('HOOK_LOG');setInterval(()=>{},1000);module.exports.authenticate=async({page,baseURL})=>page.goto(baseURL);");
  const state = await runAuthHook({ hookPath: hook, baseURL, timeoutMs: 3000 });
  assert.deepEqual(state.cookies, []);
});

test("runtime auth rejects userinfo before calculating pipeline artifacts", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-url-"));
  const steps = path.join(dir, "steps.txt"); const hook = path.join(dir, "hook.cjs");
  fs.writeFileSync(steps, "1. Open app"); fs.writeFileSync(hook, "exports.authenticate=async()=>{};");
  await assert.rejects(runPipeline({ stepsPath: steps, baseUrl: "https://user:LEAK_SENTINEL@app.test", authHook: hook, parse: false, refine: false, generate: false }), /userinfo/);
});

test("runtime fixture authenticates a protected page without writing the secret", { timeout: 15000 }, async (t) => {
  const secret = "LEAK_SENTINEL_RUNTIME_ONLY";
  const server = http.createServer((req, res) => {
    if (req.headers.cookie === `sid=${secret}`) res.end("PROTECTED_OK");
    else { res.statusCode = 401; res.end("DENIED"); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const baseURL = `http://127.0.0.1:${server.address().port}`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qa-auth-fixture-"));
  const hook = path.join(dir, "hook.cjs");
  fs.writeFileSync(hook, `module.exports.authenticate = async ({ page, baseURL }) => { console.log(process.env.RUNTIME_TEST_SECRET); await page.context().addCookies([{name:'sid',value:process.env.RUNTIME_TEST_SECRET,url:baseURL}]); await page.goto(baseURL); await page.evaluate(async token => { const req=indexedDB.open('firebaseLocalStorageDb',1); await new Promise((ok,no)=>{req.onupgradeneeded=()=>req.result.createObjectStore('firebaseLocalStorage');req.onsuccess=ok;req.onerror=no}); const db=req.result; const tx=db.transaction('firebaseLocalStorage','readwrite');tx.objectStore('firebaseLocalStorage').put(token,'firebase:authUser:test');await new Promise((ok,no)=>{tx.oncomplete=ok;tx.onerror=no}); }, process.env.RUNTIME_TEST_SECRET); };`);
  const spec = path.join(__dirname, "..", "tests", "generated", "runtime-auth.fixture.spec.ts");
  fs.mkdirSync(path.dirname(spec), { recursive: true });
  fs.writeFileSync(spec, `import { test, expect } from '../../runtime/auth-fixture'; test('protected', async ({page}) => { await page.goto('/'); await expect(page.getByText('PROTECTED_OK')).toBeVisible(); const token=await page.evaluate(async()=>{const r=indexedDB.open('firebaseLocalStorageDb');await new Promise((ok,no)=>{r.onsuccess=ok;r.onerror=no});const q=r.result.transaction('firebaseLocalStorage').objectStore('firebaseLocalStorage').get('firebase:authUser:test');return await new Promise((ok,no)=>{q.onsuccess=()=>ok(q.result);q.onerror=no})}); expect(token).toBe(process.env.RUNTIME_TEST_SECRET); });`);
  t.after(() => fs.rmSync(spec, { force: true }));
  const result = await new Promise((resolve) => {
    const child = spawn("npx", ["playwright", "test", spec, "--reporter=line"], { cwd: path.resolve(__dirname, ".."), env: { ...process.env, QA_AUTH_HOOK: hook, QA_BASE_URL: baseURL, QA_SLOWMO: "0", RUNTIME_TEST_SECRET: secret } });
    let output = ""; child.stdout.on("data", d => output += d); child.stderr.on("data", d => output += d);
    child.on("close", code => resolve({ code, output }));
  });
  assert.equal(result.code, 0, result.output);
  assert.doesNotMatch(result.output, new RegExp(secret));
  assert.doesNotMatch(fs.readFileSync(spec, "utf8"), new RegExp(secret));

  const denied = await new Promise((resolve) => {
    const child = spawn("npx", ["playwright", "test", spec, "--reporter=line"], { cwd: path.resolve(__dirname, ".."), env: { ...process.env, QA_AUTH_HOOK: hook, QA_BASE_URL: baseURL, QA_SLOWMO: "0", RUNTIME_TEST_SECRET: "WRONG" } });
    let output = ""; child.stdout.on("data", d => output += d); child.stderr.on("data", d => output += d);
    child.on("close", code => resolve({ code, output }));
  });
  assert.notEqual(denied.code, 0);
  assert.match(denied.output, /PROTECTED_OK/);
  assert.doesNotMatch(denied.output, new RegExp(secret));

  const missing = await new Promise((resolve) => {
    const env = { ...process.env, QA_BASE_URL: baseURL, QA_SLOWMO: "0" }; delete env.QA_AUTH_HOOK;
    const child = spawn("npx", ["playwright", "test", spec, "--reporter=line"], { cwd: path.resolve(__dirname, ".."), env });
    let output = ""; child.stdout.on("data", d => output += d); child.stderr.on("data", d => output += d);
    child.on("close", code => resolve({ code, output }));
  });
  assert.notEqual(missing.code, 0);
  assert.match(missing.output, /QA_AUTH_HOOK is missing/);
});
