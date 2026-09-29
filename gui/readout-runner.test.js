"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { runReadout, normalizeItem } = require("./readout-runner");

test("normalizeItem keeps a display name for a steps path", () => {
  assert.deepEqual(normalizeItem("/tmp/steps/login.txt"), {
    stepsPath: "/tmp/steps/login.txt",
    name: "login.txt",
  });
  assert.deepEqual(normalizeItem({ stepsPath: "/tmp/search.txt", name: "Search" }), {
    stepsPath: "/tmp/search.txt",
    name: "Search",
  });
});

test("runReadout runs generated tests in order and continues after a failure", async () => {
  const calls = [];
  const events = [];
  const result = await runReadout({
    items: [
      { name: "login.txt", stepsPath: "/tmp/login.txt" },
      { name: "search.txt", stepsPath: "/tmp/search.txt" },
    ],
    runOpts: { headed: false, baseUrl: "http://localhost:3000" },
    onEvent: (evt) => events.push(evt),
    async runPipeline(opts) {
      calls.push(opts.stepsPath);
      assert.equal(opts.parse, false);
      assert.equal(opts.generate, false);
      assert.equal(opts.runTests, true);
      assert.equal(opts.headed, false);
      if (opts.stepsPath.endsWith("login.txt")) {
        throw new Error("login failed");
      }
      opts.onEvent({ type: "stage", stage: "test", status: "done" });
      return { specPath: "/tmp/search.spec.ts", logPath: "/tmp/search.log" };
    },
  });

  assert.deepEqual(calls, ["/tmp/login.txt", "/tmp/search.txt"]);
  assert.equal(result.cancelled, false);
  assert.equal(result.results[0].status, "failed");
  assert.equal(result.results[1].status, "passed");
  assert.equal(events[0].type, "readout");
  assert.equal(events[0].status, "started");
  assert.equal(events.at(-1).type, "readout");
  assert.equal(events.at(-1).status, "finished");
  const child = events.find((evt) => evt.type === "stage");
  assert.equal(child.source, "readout");
  assert.equal(child.stepsPath, "/tmp/search.txt");
});

test("runReadout marks remaining tests cancelled after abort", async () => {
  const signal = { aborted: false };
  const result = await runReadout({
    items: ["/tmp/one.txt", "/tmp/two.txt"],
    signal,
    async runPipeline() {
      signal.aborted = true;
      const err = new Error("Pipeline cancelled");
      err.cancelled = true;
      throw err;
    },
  });

  assert.equal(result.cancelled, true);
  assert.equal(result.results[0].status, "cancelled");
  assert.equal(result.results[1].status, "cancelled");
});

test("runReadout requires a non-empty test list", async () => {
  await assert.rejects(() => runReadout({ items: [], runPipeline: async () => {} }), /at least one/);
});
