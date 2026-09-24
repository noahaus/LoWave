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
