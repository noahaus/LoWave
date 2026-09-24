"use strict";

const fs = require("fs");
const path = require("path");

function openDatabase(dbPath) {
  let DatabaseSync;
  try {
    ({ DatabaseSync } = require("node:sqlite"));
  } catch (err) {
    throw new Error(
      "SQLite step history requires Node's node:sqlite module (Node 22+ / Electron 37+). " +
        (err && err.message ? err.message : err)
    );
  }
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new DatabaseSync(dbPath);
  db.exec("PRAGMA foreign_keys = ON");
  db.exec("PRAGMA busy_timeout = 5000");
  db.exec(`
    CREATE TABLE IF NOT EXISTS test_runs (
      id INTEGER PRIMARY KEY,
      workflow_hash TEXT NOT NULL,
      steps_path TEXT NOT NULL,
      spec_path TEXT,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      passed INTEGER,
      cancelled INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS test_runs_hash_id ON test_runs (workflow_hash, id);
    CREATE TABLE IF NOT EXISTS step_results (
      run_id INTEGER NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
      step_number INTEGER NOT NULL,
      description TEXT,
      status TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (run_id, step_number)
    );
  `);
  return db;
}

function nowIso() {
  return new Date().toISOString();
}

function descriptionFromTitle(title) {
  const text = String(title || "");
  const match = text.match(/^Step\s+\d+\s*:\s*(.*)$/i);
  return (match ? match[1] : text).trim();
}

function createResultsStore(dbPath) {
  if (!dbPath) throw new Error("SQLite results path is required");
  const db = openDatabase(dbPath);

  const insertRun = db.prepare(
    `INSERT INTO test_runs (workflow_hash, steps_path, spec_path, started_at, passed, cancelled)
     VALUES (?, ?, ?, ?, NULL, 0)`
  );
  const abandonOpenRuns = db.prepare(
    `UPDATE test_runs
     SET finished_at = ?, cancelled = 1
     WHERE workflow_hash = ? AND finished_at IS NULL`
  );
  const idleAbandonedSteps = db.prepare(
    `UPDATE step_results
     SET status = 'idle', updated_at = ?
     WHERE status = 'running'
       AND run_id IN (SELECT id FROM test_runs WHERE workflow_hash = ? AND finished_at IS NULL)`
  );
  const insertStep = db.prepare(
    `INSERT INTO step_results (run_id, step_number, description, status, updated_at)
     VALUES (?, ?, ?, ?, ?)`
  );
  const upsertStep = db.prepare(
    `INSERT INTO step_results (run_id, step_number, description, status, updated_at)
     VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(run_id, step_number) DO UPDATE SET
       status = excluded.status,
       description = CASE
         WHEN excluded.description IS NOT NULL AND excluded.description != '' THEN excluded.description
         ELSE step_results.description
       END,
       updated_at = excluded.updated_at`
  );
  const finishRunStmt = db.prepare(
    `UPDATE test_runs
     SET finished_at = ?, passed = ?, cancelled = ?
     WHERE id = ?`
  );
  const coerceRunning = db.prepare(
    `UPDATE step_results SET status = ?, updated_at = ? WHERE run_id = ? AND status = 'running'`
  );
  const coerceIdlePass = db.prepare(
    `UPDATE step_results SET status = 'pass', updated_at = ? WHERE run_id = ? AND status IN ('running', 'idle')`
  );
  const latestRunStmt = db.prepare(
    `SELECT id, passed, cancelled, finished_at
     FROM test_runs
     WHERE workflow_hash = ?
     ORDER BY id DESC
     LIMIT 1`
  );
  const stepsForRun = db.prepare(
    `SELECT step_number, status, description
     FROM step_results
     WHERE run_id = ?
     ORDER BY step_number`
  );
  const listRunsStmt = db.prepare(
    `SELECT id, workflow_hash, steps_path, spec_path, started_at, finished_at, passed, cancelled
     FROM test_runs
     WHERE workflow_hash = ?
     ORDER BY id DESC
     LIMIT ?`
  );

  function startRun({ workflowHash, stepsPath, specPath = "", steps = [] } = {}) {
    if (!workflowHash) throw new Error("workflowHash is required");
    const started = nowIso();
    idleAbandonedSteps.run(started, workflowHash);
    abandonOpenRuns.run(started, workflowHash);
    const info = insertRun.run(workflowHash, stepsPath || "", specPath || "", started);
    const id = Number(info.lastInsertRowid);
    for (const step of steps) {
      const number = Number(step.number);
      if (!Number.isFinite(number)) continue;
      insertStep.run(id, number, step.text || step.description || "", "idle", started);
    }
    return { id, workflowHash, startedAt: started };
  }

  function setStepStatus(runId, stepNumber, status, description) {
    if (!runId) return;
    if (!["running", "pass", "fail", "idle"].includes(status)) return;
    const number = Number(stepNumber);
    if (!Number.isFinite(number)) return;
    upsertStep.run(runId, number, descriptionFromTitle(description) || "", status, nowIso());
  }

  function finishRun(runId, { passed = false, cancelled = false } = {}) {
    if (!runId) return;
    const finished = nowIso();
    if (cancelled) coerceRunning.run("idle", finished, runId);
    else if (passed) coerceIdlePass.run(finished, runId);
    finishRunStmt.run(finished, cancelled ? null : passed ? 1 : 0, cancelled ? 1 : 0, runId);
  }

  function latestResults(workflowHash) {
    if (!workflowHash) return null;
    const run = latestRunStmt.get(workflowHash);
    if (!run) return null;
    const stepStatuses = {};
    for (const row of stepsForRun.all(run.id)) {
      stepStatuses[row.step_number] = row.status;
    }
    return {
      runId: Number(run.id),
      passed: run.passed == null ? null : Boolean(run.passed),
      cancelled: Boolean(run.cancelled),
      finishedAt: run.finished_at || "",
      stepStatuses,
    };
  }

  function listRuns(workflowHash, { limit = 20 } = {}) {
    return listRunsStmt.all(workflowHash, limit).map((row) => ({
      id: Number(row.id),
      workflowHash: row.workflow_hash,
      stepsPath: row.steps_path,
      specPath: row.spec_path,
      startedAt: row.started_at,
      finishedAt: row.finished_at || "",
      passed: row.passed == null ? null : Boolean(row.passed),
      cancelled: Boolean(row.cancelled),
    }));
  }

  function close() {
    db.close();
  }

  return {
    dbPath,
    startRun,
    setStepStatus,
    finishRun,
    latestResults,
    listRuns,
    close,
  };
}

module.exports = {
  createResultsStore,
  descriptionFromTitle,
};
