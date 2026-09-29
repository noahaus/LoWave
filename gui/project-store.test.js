"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createProjectStore } = require("./project-store");

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "lowave-settings-"));
}

test("settings default to ollama and persist across store instances", () => {
  const root = tempRoot();
  try {
    const first = createProjectStore(root);
    assert.deepEqual(first.readSettings(), { backend: "ollama", model: "qwen3-coder:30b" });

    first.writeSettings({ backend: "anthropic", model: "claude-sonnet-4-0" });
    const second = createProjectStore(root);
    assert.deepEqual(second.readSettings(), {
      backend: "anthropic",
      model: "claude-sonnet-4-0",
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("project save persists password with the other project fields", () => {
  const root = tempRoot();
  try {
    const store = createProjectStore(root);
    const created = store.createProject({
      name: "Alpha",
      baseUrl: "http://localhost:3000",
      username: "demo@kestrel.app",
    });
    assert.deepEqual(created.readouts, []);
    const updated = store.updateProject(created.slug, { password: "test1234" });
    assert.equal(updated.password, "test1234");
    const saved = JSON.parse(fs.readFileSync(path.join(root, "projects", created.slug, "project.json"), "utf8"));
    assert.equal(saved.password, "test1234");
    const reloaded = createProjectStore(root).getProject(created.slug);
    assert.equal(reloaded.password, "test1234");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readouts persist as a named set of steps files for a project", () => {
  const root = tempRoot();
  try {
    const store = createProjectStore(root);
    const project = store.createProject({ name: "Alpha", baseUrl: "http://localhost:3000" });
    store.addProjectSteps(project.slug, { name: "login", content: "1. Sign in\n" });
    store.addProjectSteps(project.slug, { name: "search", content: "1. Search\n" });

    const created = store.createReadout(project.slug, {
      name: "Smoke suite",
      stepNames: ["login.txt", "search.txt"],
    });
    assert.equal(created.readouts.length, 1);
    assert.equal(created.readouts[0].name, "Smoke suite");
    assert.deepEqual(created.readouts[0].stepNames, ["login.txt", "search.txt"]);

    const readoutFile = path.join(root, "projects", project.slug, "readouts.json");
    assert.equal(fs.existsSync(readoutFile), true);
    const projectJson = JSON.parse(
      fs.readFileSync(path.join(root, "projects", project.slug, "project.json"), "utf8")
    );
    assert.equal(projectJson.readouts, undefined);

    const updated = store.updateReadout(project.slug, created.readouts[0].id, {
      name: "Nightly",
      stepNames: ["search.txt"],
      lastRun: {
        finishedAt: "2026-09-28T00:00:00.000Z",
        cancelled: false,
        results: [{ name: "search.txt", status: "passed" }],
      },
    });
    assert.equal(updated.readouts[0].name, "Nightly");
    assert.deepEqual(updated.readouts[0].stepNames, ["search.txt"]);
    assert.equal(updated.readouts[0].lastRun.results[0].status, "passed");

    const reloaded = createProjectStore(root).getProject(project.slug);
    assert.equal(reloaded.readouts[0].name, "Nightly");

    const emptied = store.deleteReadout(project.slug, updated.readouts[0].id);
    assert.deepEqual(emptied.readouts, []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readout save rejects a missing name or unknown steps file", () => {
  const root = tempRoot();
  try {
    const store = createProjectStore(root);
    const project = store.createProject({ name: "Alpha", baseUrl: "http://localhost:3000" });
    store.addProjectSteps(project.slug, { name: "login", content: "1. Sign in\n" });
    assert.throws(() => store.createReadout(project.slug, { name: "  " }), /Readout name is required/);
    assert.throws(
      () => store.createReadout(project.slug, { name: "Smoke", stepNames: ["missing.txt"] }),
      /Steps file not found/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("settings are stored outside any project directory", () => {
  const root = tempRoot();
  try {
    const store = createProjectStore(root);
    store.createProject({ name: "Alpha", baseUrl: "http://localhost:3000" });
    store.writeSettings({ backend: "openai", model: "gpt-4.1" });

    const settingsPath = path.join(root, "outputs", "gui-settings.json");
    assert.equal(fs.existsSync(settingsPath), true);
    const projectDirs = fs.readdirSync(path.join(root, "projects"), { withFileTypes: true })
      .filter((d) => d.isDirectory());
    for (const dir of projectDirs) {
      assert.equal(fs.existsSync(path.join(root, "projects", dir.name, "gui-settings.json")), false);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
