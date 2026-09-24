"use strict";

const fs = require("fs");

function shouldReportStep(step) {
  const category = step && step.category;
  if (category !== "pw:api" && category !== "expect" && category !== "test.step") return false;
  const parentCategory = step.parent && step.parent.category;
  if (parentCategory === "pw:api" || parentCategory === "expect" || parentCategory === "test.step") return false;
  if (category === "test.step") return Boolean(stepNumberFromTitle(step.title));
  const file = step.location && step.location.file;
  return Boolean(file && /\.spec\.ts$/.test(file));
}

function stepNumberFromTitle(title) {
  const match = String(title || "").match(/^Step\s+(\d+)\s*:/i);
  return match ? Number(match[1]) : undefined;
}

function payloadFromStep(step, status) {
  const title = (step && step.title) || "";
  const location = (step && step.location) || {};
  const stepNumber = stepNumberFromTitle(title);
  const payload = {
    status,
    title,
    category: step && step.category,
  };
  if (stepNumber) payload.step = stepNumber;
  if (location.line) payload.line = location.line;
  if (location.file) payload.file = location.file;
  return payload;
}

class LowaveStepReporter {
  constructor() {
    this.eventsPath = process.env.LOWAVE_STEP_EVENTS || "";
  }

  printsToStdio() {
    return false;
  }

  onStepBegin(_test, _result, step) {
    if (!shouldReportStep(step)) return;
    this._write(payloadFromStep(step, "running"));
  }

  onStepEnd(_test, _result, step) {
    if (!shouldReportStep(step)) return;
    this._write(payloadFromStep(step, step && step.error ? "fail" : "pass"));
  }

  onTestEnd(_test, result) {
    this._write({
      status: "test-end",
      passed: result.status === "passed",
      testStatus: result.status,
    });
  }

  _write(payload) {
    const line = `${JSON.stringify(payload)}\n`;
    if (this.eventsPath) {
      try {
        fs.appendFileSync(this.eventsPath, line);
      } catch {
        // Progress is best-effort; the pipeline still applies a final pass/fail.
      }
    }
    process.stdout.write(`LOWAVE_STEP ${line}`);
  }
}

module.exports = LowaveStepReporter;
module.exports.shouldReportStep = shouldReportStep;
module.exports.stepNumberFromTitle = stepNumberFromTitle;
module.exports.payloadFromStep = payloadFromStep;
