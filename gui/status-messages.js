"use strict";

const ANSI_RE = /\u001b\[[0-9;]*m/g;

const STAGE_LABEL = {
  parse: "Parse",
  refine: "Refine",
  generate: "Generate",
  test: "Playwright",
};

const STAGE_STATUS = {
  parse: {
    running: "Reading your steps and turning them into an action plan.",
    done: "Parse finished. The action plan is saved.",
  },
  refine: {
    running: "Opening the app and matching each step to real buttons and fields.",
    done: "Refine finished. Each step is now tied to a real control on the page.",
  },
  generate: {
    running: "Writing a Playwright test from the plan.",
    done: "Generate finished. A Playwright spec is ready.",
    incomplete: "Generate finished with gaps. Some steps could not be turned into test actions.",
  },
  test: {
    running: "Running the generated Playwright test.",
    done: "The Playwright test passed.",
  },
};

function stripAnsi(text) {
  return String(text || "").replace(ANSI_RE, "");
}

function collapse(text) {
  return stripAnsi(text).replace(/\s+/g, " ").trim();
}

function stageLabel(stage) {
  return STAGE_LABEL[stage] || "Pipeline";
}

function stageStatusMessage(stage, status) {
  return STAGE_STATUS[stage]?.[status] || "";
}

function firstSentence(text, max = 220) {
  const cleaned = collapse(text).replace(/^Error:\s*/i, "");
  if (!cleaned) return "";
  const cut = cleaned.split(/(?<=\.)\s/)[0] || cleaned;
  return cut.length > max ? `${cut.slice(0, max - 1)}…` : cut;
}

function humanizeError(text) {
  const cleaned = collapse(text);
  if (!cleaned) return "";
  if (/timeout \d+ms exceeded|timed out/i.test(cleaned)) {
    return "The page waited too long for the next element to appear.";
  }
  if (/target closed|page closed|browser has been closed/i.test(cleaned)) {
    return "The page closed or navigated away before the action finished.";
  }
  if (/net::ERR_CONNECTION_REFUSED|ECONNREFUSED|connection refused/i.test(cleaned)) {
    return "Nothing was listening at the app URL.";
  }
  if (/net::ERR_|NS_ERROR_CONNECTION|ERR_NAME_NOT_RESOLVED/i.test(cleaned)) {
    return "The browser could not open the web app URL.";
  }
  if (/strict mode violation|resolved to \d+ elements/i.test(cleaned)) {
    return "A locator matched more than one element, so it was not clear which one to use.";
  }
  if (/\bnot visible\b|element is hidden|not attached/i.test(cleaned)) {
    return "The control was on the page but not visible.";
  }
  if (/api[- ]?key|\bunauthorized\b|\b401\b|invalid.?api|authentication (failed|rejected|error)/i.test(cleaned)) {
    return "The AI service rejected the request. Check the API key or subscription login.";
  }
  return firstSentence(cleaned);
}

function statusFromLogLine(line, stage) {
  const text = stripAnsi(line).replace(/^\[[\d:]+\] \[[^\]]\]\s*/, "").trim();
  if (!text) return "";

  let match = text.match(/--- Step (\d+) \/ (\d+)/);
  if (match) {
    return `Working on step ${match[1]} of ${match[2]}.`;
  }
  match = text.match(/Step (\d+) complete/);
  if (match) return `Step ${match[1]} succeeded.`;
  match = text.match(/Step (\d+) FAILED: target not in snapshot/);
  if (match) return `Step ${match[1]} could not find its target on the page.`;
  match = text.match(/Step (\d+) blocked by interstitial/);
  if (match) {
    return `Step ${match[1]} stopped because a captcha or extra check appeared.`;
  }
  match = text.match(/Step (\d+) FAILED/);
  if (match) return `Step ${match[1]} could not be completed.`;
  if (/Bot-check|interstitial detected/i.test(text)) {
    return "The site is showing a captcha or extra check.";
  }
  if (/Interstitial cleared/i.test(text)) {
    return "The extra check cleared. Continuing.";
  }

  if (/Analysing with|Analyzing with/i.test(text)) {
    return "Asking the model to interpret the numbered steps.";
  }
  if (/steps parsed and validated/i.test(text)) {
    return "The model produced a valid action plan.";
  }
  if (/no numbered steps found/i.test(text)) {
    return "No numbered steps were found in the file.";
  }
  if (/model output did not match the schema/i.test(text)) {
    return "The model’s answer was not a valid action plan.";
  }

  match = text.match(/Step \[(\d+)\]/);
  if (match && stage === "generate") {
    if (/TODO|⚠/.test(text) || /ungrounded/i.test(text)) {
      return `Step ${match[1]} could not be mapped into a test action.`;
    }
    return `Mapped step ${match[1]} into a test action.`;
  }
  if (/Incomplete generation/i.test(text)) {
    return "The generated spec is incomplete and needs review.";
  }

  if (/^\d+ failed/i.test(text) || /^\s+\d+ failed/i.test(text)) {
    return "The Playwright test failed.";
  }
  if (/^\d+ passed/i.test(text) || /^\s+\d+ passed/i.test(text)) {
    return "The Playwright test passed.";
  }
  if (/Error: expect\(/i.test(text) || /Received:/.test(text)) {
    return "The test ran, but an expected result was not on the page.";
  }
  if (/Timeout/i.test(text) && stage === "test") {
    return "The Playwright test timed out.";
  }

  return "";
}

function uniqueReasons(reasons) {
  const seen = new Set();
  const out = [];
  for (const reason of reasons) {
    const key = reason.toLowerCase();
    if (!reason || seen.has(key)) continue;
    seen.add(key);
    out.push(reason);
  }
  return out;
}

function explainFailure({ stage, logText = "", error = "", stderr = "", incomplete = false } = {}) {
  const text = stripAnsi([logText, stderr, error].filter(Boolean).join("\n"));
  const reasons = [];

  const interstitial = text.match(/Step (\d+) blocked by interstitial:\s*(.+)/);
  if (interstitial) {
    reasons.push(
      `Step ${interstitial[1]} could not continue because the site showed a captcha or extra security check.`
    );
  }

  for (const match of text.matchAll(/Step (\d+) FAILED: target not in snapshot/g)) {
    reasons.push(
      `Step ${match[1]} could not find the button or field it needed on the page. The control may be missing, hidden, or labeled differently than expected.`
    );
  }

  for (const match of text.matchAll(/Step (\d+) FAILED after \d+ attempt\(s\):\s*(.+)/g)) {
    const detail = humanizeError(match[2]);
    reasons.push(
      detail
        ? `Step ${match[1]} could not be completed. ${detail}`
        : `Step ${match[1]} could not be completed after several tries.`
    );
  }

  if (/Scoped action plan is required/i.test(text)) {
    reasons.push(
      "This run needs an action plan first. Turn Parse on, or run Parse for this workflow before Refine or Generate."
    );
  }
  if (/Scoped generated spec is required/i.test(text)) {
    reasons.push(
      "This run needs a generated Playwright spec first. Turn Generate on, or run Generate for this workflow before the test stage."
    );
  }

  if (/no numbered steps found/i.test(text)) {
    reasons.push(
      "Parse could not start because the steps file has no numbered instructions. Each line should look like “1. Open the home page”."
    );
  }

  if (/model output did not match the schema/i.test(text)) {
    if (/truncated|max_tokens|unterminated/i.test(text)) {
      reasons.push(
        "Parse stopped because the model’s answer was cut off before it finished the action plan. Retry parse, or split a long workflow."
      );
    } else {
      reasons.push(
        "Parse stopped because the model did not return a valid action plan. Try again or use a stronger model."
      );
    }
  }

  if (/backend is not installed/i.test(text)) {
    reasons.push("The selected AI backend is not installed in this environment.");
  }

  if (/unknown backend/i.test(text)) {
    reasons.push("The selected AI backend is not recognized.");
  }

  if (/api[- ]?key|\bunauthorized\b|\b401\b|invalid.?api|authentication (failed|rejected|error)/i.test(text)) {
    reasons.push(
      "The AI service rejected the request. Check that the API key or subscription login is set up."
    );
  }

  if (/ECONNREFUSED|connection refused/i.test(text)) {
    if (/ollama/i.test(text)) {
      reasons.push(
        "Could not reach the local Ollama service. Make sure Ollama is running, or switch to another backend."
      );
    } else if (stage === "test" || stage === "refine") {
      reasons.push("Could not connect to the app. Confirm the web app URL is reachable.");
    } else {
      reasons.push("A required local service refused the connection.");
    }
  }

  if (/ERR_CONNECTION|net::ERR_|NS_ERROR_CONNECTION|can't reach|cannot reach/i.test(text)) {
    reasons.push(
      "The browser could not open the web app. Confirm the URL is correct and the app is running."
    );
  }

  if (
    incomplete ||
    /Incomplete generation|required step\(s\) were not compiled|emitted as TODO/i.test(text)
  ) {
    const todoSteps = [...text.matchAll(/Step \[(\d+)\][^\n]*\n[^\n]*(TODO|ungrounded)/gi)].map(
      (match) => match[1]
    );
    if (todoSteps.length) {
      reasons.push(
        `Generate could not turn step${todoSteps.length === 1 ? "" : "s"} ${todoSteps.join(", ")} into Playwright actions. Review the draft spec before treating this as a passing run.`
      );
    } else {
      reasons.push(
        "Generate could not turn every step into a Playwright action. Review the draft spec and any steps left as TODOs."
      );
    }
  }

  if (stage === "test" || /playwright/i.test(text)) {
    if (/Executable doesn't exist|playwright install/i.test(text)) {
      reasons.push("Playwright could not find a browser to run. Install the Playwright browsers and try again.");
    }
    if (/Timeout/i.test(text)) {
      reasons.push("The Playwright test timed out waiting for something on the page to appear or finish.");
    }
    if (/strict mode violation|resolved to \d+ elements/i.test(text)) {
      reasons.push(
        "A test locator matched more than one element, so Playwright did not know which one to use."
      );
    }
    if (/Error: expect\(|AssertionError|Received:/i.test(text)) {
      reasons.push("The test ran, but an expected result was not on the page.");
    }
  }

  const unique = uniqueReasons(reasons);
  if (unique.length) return unique.join("\n\n");

  const fallback = humanizeError(error) || humanizeError(stderr);
  if (fallback) {
    return `The ${stageLabel(stage).toLowerCase()} step could not finish. ${fallback}`;
  }
  return `The ${stageLabel(stage).toLowerCase()} step could not finish. Open the saved log file for the technical details.`;
}

function formatFailurePanel(explanation, logPath) {
  const lines = ["What went wrong", explanation.trim()].filter(Boolean);
  if (logPath) {
    lines.push("", `A detailed log was saved to ${logPath}`);
  }
  return lines.join("\n");
}

module.exports = {
  stripAnsi,
  stageLabel,
  stageStatusMessage,
  statusFromLogLine,
  explainFailure,
  formatFailurePanel,
  humanizeError,
};
