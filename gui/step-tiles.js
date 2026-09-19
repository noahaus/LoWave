"use strict";

function parseNumberedSteps(text) {
  const steps = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const match = line.match(/^\s*(\d+)\.\s+(.*\S)\s*$/);
    if (match) steps.push({ number: Number(match[1]), text: match[2] });
  }
  return steps;
}

function parseSpecSteps(specText) {
  const steps = [];
  const lines = String(specText || "").split(/\r?\n/);
  lines.forEach((line, index) => {
    const match = line.match(/^\s*\/\/\s*Step\s+(\d+)\s*:\s*(.*)$/i);
    if (match) {
      steps.push({
        number: Number(match[1]),
        text: match[2].trim(),
        startLine: index + 1,
      });
    }
  });
  return steps;
}

function buildStepTiles(stepsText, specText) {
  const fromFile = parseNumberedSteps(stepsText);
  if (fromFile.length) return fromFile;
  return parseSpecSteps(specText).map((step) => ({
    number: step.number,
    text: step.text,
  }));
}

function stepNumberForSpecLine(specText, lineNumber) {
  let current = null;
  for (const step of parseSpecSteps(specText)) {
    if (step.startLine <= lineNumber) current = step.number;
    else break;
  }
  return current;
}

function playwrightErrorLine(output, specPath) {
  const text = String(output || "");
  const fileName = String(specPath || "").split(/[/\\]/).pop();
  if (fileName) {
    const escaped = fileName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = text.match(new RegExp(`${escaped}:(\\d+)(?::\\d+)?`));
    if (match) return Number(match[1]);
  }
  const generic = text.match(/(\w[\w.-]*\.spec\.ts):(\d+)/);
  return generic ? Number(generic[2]) : null;
}

function resultsForTestRun({ passed, output, specPath, specText, steps }) {
  const list = Array.isArray(steps) ? steps : [];
  if (passed) {
    return list.map((step) => ({ number: step.number, status: "pass" }));
  }
  const line = playwrightErrorLine(output, specPath);
  const failedStep = line ? stepNumberForSpecLine(specText, line) : null;
  return list.map((step) => {
    if (failedStep == null) return { number: step.number, status: "fail" };
    if (step.number < failedStep) return { number: step.number, status: "pass" };
    if (step.number === failedStep) return { number: step.number, status: "fail" };
    return { number: step.number, status: "idle" };
  });
}

function parseReporterLine(line) {
  const text = String(line || "")
    .replace(/\u001b\[[0-9;]*m/g, "")
    .trim();
  if (!text) return null;
  if (text.startsWith("LOWAVE_STEP ")) {
    try {
      return JSON.parse(text.slice("LOWAVE_STEP ".length));
    } catch {
      return null;
    }
  }
  if (text.startsWith("{") && (text.includes('"status"') || text.includes("'status'"))) {
    try {
      return JSON.parse(text);
    } catch {
      return null;
    }
  }
  return null;
}

function stepFromReporterPayload(payload, specText) {
  if (!payload) return null;
  if (payload.step) return Number(payload.step);
  if (payload.line) return stepNumberForSpecLine(specText, payload.line) || null;
  return null;
}

function statusesAfterProgress(prev, { step, status, steps, passed }) {
  const next = { ...(prev || {}) };
  const numbers = Array.isArray(steps) ? steps.map((item) => item.number) : Object.keys(next).map(Number);
  if (status === "running") {
    for (const number of numbers) {
      if (number < step && next[number] !== "fail") next[number] = "pass";
    }
    if (next[step] !== "fail") next[step] = "running";
    return next;
  }
  if (status === "pass") {
    next[step] = "pass";
    return next;
  }
  if (status === "fail") {
    next[step] = "fail";
    return next;
  }
  if (status === "test-end") {
    if (passed) {
      for (const number of numbers) next[number] = "pass";
    } else {
      for (const number of numbers) {
        if (next[number] === "running") next[number] = "fail";
      }
    }
    return next;
  }
  return next;
}

function wrapSpecInTestSteps(specText) {
  const source = String(specText || "");
  if (!source.trim() || /\btest\.step\s*\(/.test(source)) return source;
  const lines = source.split(/\r?\n/);
  const out = [];
  let open = false;
  let indent = "  ";
  for (const line of lines) {
    const match = line.match(/^(\s*)\/\/\s*Step\s+(\d+)\s*:\s*(.*)$/i);
    if (match) {
      if (open) {
        out.push(`${indent}});`);
        open = false;
      }
      indent = match[1];
      const title = `Step ${match[2]}: ${match[3].trim()}`;
      out.push(line);
      out.push(`${indent}await test.step(${JSON.stringify(title)}, async () => {`);
      open = true;
      continue;
    }
    if (open && /^}\);\s*$/.test(line)) {
      out.push(`${indent}});`);
      open = false;
    }
    out.push(line);
  }
  if (open) out.push(`${indent}});`);
  return out.join("\n");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    parseNumberedSteps,
    parseSpecSteps,
    buildStepTiles,
    stepNumberForSpecLine,
    playwrightErrorLine,
    resultsForTestRun,
    parseReporterLine,
    stepFromReporterPayload,
    statusesAfterProgress,
    wrapSpecInTestSteps,
  };
}

if (typeof window !== "undefined") {
  window.stepTiles = {
    parseNumberedSteps,
    parseSpecSteps,
    buildStepTiles,
    resultsForTestRun,
    parseReporterLine,
    stepFromReporterPayload,
    statusesAfterProgress,
    wrapSpecInTestSteps,
  };
}
