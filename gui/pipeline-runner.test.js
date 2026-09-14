"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const { defaultModelForBackend, runCommand } = require("./pipeline-runner");


test("only Ollama receives a hardcoded local model default", () => {
  assert.equal(defaultModelForBackend("ollama"), "qwen3-coder:30b");
  assert.equal(defaultModelForBackend("claude-cli"), undefined);
  assert.equal(defaultModelForBackend("codex-cli"), undefined);
  assert.equal(defaultModelForBackend("anthropic"), undefined);
});


test("GUI exposes subscription backends without a prefilled password", () => {
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

  assert.match(html, /option value="claude-cli"/);
  assert.match(html, /option value="codex-cli"/);
  assert.doesNotMatch(html, /id="password"[^>]*value=/);
});

test("cancelling a command also stops its CLI descendants", { skip: process.platform === "win32", timeout: 5000 }, async () => {
  const controller = new AbortController();
  let descendant;
  const script = `
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    process.stdout.write(String(child.pid) + '\\n');
    setInterval(() => {}, 1000);
  `;
  try {
    await assert.rejects(runCommand(process.execPath, ["-e", script], {
      signal: controller.signal,
      onLine(line, stream) {
        if (stream !== "stdout") return;
        if (!/^\d+$/.test(line.trim())) return;
        descendant = Number(line.trim());
        controller.abort();
      },
    }), (err) => err.cancelled === true);
    for (let attempt = 0; attempt < 30; attempt++) {
      try { process.kill(descendant, 0); }
      catch (err) { if (err.code === "ESRCH") return; throw err; }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.fail("CLI descendant survived cancellation");
  } finally {
    if (descendant) {
      try { process.kill(descendant, "SIGKILL"); } catch {}
    }
  }
});
