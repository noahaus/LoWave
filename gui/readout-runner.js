"use strict";

function itemName(item) {
  if (item && typeof item === "object" && item.name) return String(item.name);
  const stepsPath = typeof item === "string" ? item : item?.stepsPath || "";
  return String(stepsPath).split(/[/\\]/).pop() || stepsPath;
}

function normalizeItem(item) {
  if (typeof item === "string") {
    return { stepsPath: item, name: itemName(item) };
  }
  return {
    stepsPath: item?.stepsPath || "",
    name: itemName(item),
  };
}

/**
 * Run generated Playwright tests one after another and emit readout events.
 * Failures do not stop later tests; cancel stops the current test and the rest.
 */
async function runReadout(opts = {}) {
  const {
    items,
    runPipeline,
    signal,
    onEvent = () => {},
    runOpts = {},
  } = opts;

  if (!Array.isArray(items) || !items.length) {
    throw new Error("Choose at least one generated test for this readout");
  }
  if (typeof runPipeline !== "function") {
    throw new Error("runPipeline is required");
  }

  const queue = items.map(normalizeItem);
  if (queue.some((item) => !item.stepsPath)) {
    throw new Error("Each readout test needs a steps file");
  }

  const results = [];
  onEvent({
    type: "readout",
    status: "started",
    items: queue.map((item) => ({ ...item })),
  });

  for (const item of queue) {
    if (signal?.aborted) {
      const cancelled = { ...item, status: "cancelled" };
      results.push(cancelled);
      onEvent({ type: "readout-item", ...cancelled });
      continue;
    }

    onEvent({ type: "readout-item", ...item, status: "started" });
    try {
      const result = await runPipeline({
        ...runOpts,
        stepsPath: item.stepsPath,
        parse: false,
        refine: false,
        generate: false,
        runTests: true,
        signal,
        onEvent: (evt) => onEvent({ ...evt, source: "readout", stepsPath: item.stepsPath }),
      });
      const entry = {
        ...item,
        status: "passed",
        specPath: result?.specPath || "",
        logPath: result?.logPath || "",
      };
      results.push(entry);
      onEvent({ type: "readout-item", ...entry });
    } catch (err) {
      const status = err?.cancelled ? "cancelled" : "failed";
      const entry = {
        ...item,
        status,
        error: err?.message || "Failed",
        logPath: err?.logPath || err?.result?.logPath || "",
        specPath: err?.result?.specPath || "",
      };
      results.push(entry);
      onEvent({ type: "readout-item", ...entry });
    }
  }

  const cancelled = Boolean(signal?.aborted) || results.some((item) => item.status === "cancelled");
  const finished = {
    type: "readout",
    status: cancelled ? "cancelled" : "finished",
    results,
  };
  onEvent(finished);
  return { cancelled, results };
}

module.exports = { runReadout, normalizeItem };
