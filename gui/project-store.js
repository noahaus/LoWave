"use strict";

const fs = require("fs");
const path = require("path");

function createProjectStore(repoRoot) {
  const projectsDir = path.join(repoRoot, "projects");
  const examplesDir = path.join(repoRoot, "examples", "workflows");

  function slugify(name) {
    const base = String(name || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60);
    return base || "project";
  }

  function assertSlug(slug) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug || "")) {
      throw new Error("Invalid project slug");
    }
  }

  function uniqueSlug(base) {
    let slug = base;
    let i = 2;
    while (fs.existsSync(path.join(projectsDir, slug))) {
      slug = `${base}-${i}`;
      i += 1;
    }
    return slug;
  }

  function ensureRoot() {
    fs.mkdirSync(projectsDir, { recursive: true });
  }

  function projectDir(slug) {
    assertSlug(slug);
    return path.join(projectsDir, slug);
  }

  function stepsDir(slug) {
    return path.join(projectDir(slug), "steps");
  }

  function readJson(slug) {
    const file = path.join(projectDir(slug), "project.json");
    if (!fs.existsSync(file)) return null;
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    return { ...data, slug, dir: projectDir(slug) };
  }

  function writeJson(slug, data) {
    const dir = projectDir(slug);
    fs.mkdirSync(path.join(dir, "steps"), { recursive: true });
    const payload = {
      name: data.name,
      baseUrl: data.baseUrl,
      username: data.username || "",
      createdAt: data.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(path.join(dir, "project.json"), `${JSON.stringify(payload, null, 2)}\n`);
  }

  function listSteps(slug) {
    const dir = stepsDir(slug);
    if (!fs.existsSync(dir)) return [];
    return fs
      .readdirSync(dir)
      .filter((f) => f.endsWith(".txt"))
      .sort()
      .map((f) => ({
        name: f,
        path: path.join(dir, f),
      }));
  }

  function decorate(slug) {
    const project = readJson(slug);
    if (!project) return null;
    const steps = listSteps(slug);
    return { ...project, stepsCount: steps.length, steps };
  }

  function listProjects() {
    ensureRoot();
    return fs
      .readdirSync(projectsDir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => decorate(d.name))
      .filter(Boolean)
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  function getProject(slug) {
    const project = decorate(slug);
    if (!project) throw new Error(`Project not found: ${slug}`);
    return project;
  }

  function createProject({ name, baseUrl, username } = {}) {
    if (!name || !String(name).trim()) throw new Error("Project name is required");
    if (!baseUrl || !String(baseUrl).trim()) throw new Error("Web app URL is required");
    ensureRoot();
    const slug = uniqueSlug(slugify(name));
    writeJson(slug, {
      name: String(name).trim(),
      baseUrl: String(baseUrl).trim(),
      username: username ? String(username).trim() : "",
      createdAt: new Date().toISOString(),
    });
    return getProject(slug);
  }

  function updateProject(slug, patch = {}) {
    const current = readJson(slug);
    if (!current) throw new Error(`Project not found: ${slug}`);
    writeJson(slug, {
      name: patch.name != null ? String(patch.name).trim() : current.name,
      baseUrl: patch.baseUrl != null ? String(patch.baseUrl).trim() : current.baseUrl,
      username: patch.username != null ? String(patch.username).trim() : current.username,
      createdAt: current.createdAt,
    });
    return getProject(slug);
  }

  function uniqueFilename(dir, filename) {
    const ext = path.extname(filename) || ".txt";
    const stem = path.basename(filename, ext).replace(/[^\w.-]+/g, "_") || "steps";
    let candidate = `${stem}${ext.startsWith(".") ? ext : `.${ext}`}`;
    let i = 2;
    while (fs.existsSync(path.join(dir, candidate))) {
      candidate = `${stem}-${i}${ext.startsWith(".") ? ext : `.${ext}`}`;
      i += 1;
    }
    return candidate;
  }

  function addProjectSteps(slug, { sourcePath, name, content } = {}) {
    if (!readJson(slug)) throw new Error(`Project not found: ${slug}`);
    const dir = stepsDir(slug);
    fs.mkdirSync(dir, { recursive: true });

    if (sourcePath) {
      if (!fs.existsSync(sourcePath)) throw new Error(`Steps file not found: ${sourcePath}`);
      const destName = uniqueFilename(dir, path.basename(sourcePath));
      fs.copyFileSync(sourcePath, path.join(dir, destName));
      return getProject(slug);
    }

    const rawName = name && String(name).trim() ? String(name).trim() : "steps";
    const destName = uniqueFilename(
      dir,
      rawName.toLowerCase().endsWith(".txt") ? rawName : `${rawName}.txt`
    );
    fs.writeFileSync(path.join(dir, destName), content != null ? content : "");
    return getProject(slug);
  }

  function seedDemoIfEmpty() {
    ensureRoot();
    if (listProjects().length) return listProjects();
    const demo = createProject({
      name: "Kestrel Demo",
      baseUrl: "http://localhost:3000",
      username: "demo@kestrel.app",
    });
    if (fs.existsSync(examplesDir)) {
      for (const file of fs.readdirSync(examplesDir).filter((f) => f.endsWith(".txt"))) {
        addProjectSteps(demo.slug, { sourcePath: path.join(examplesDir, file) });
      }
    }
    return listProjects();
  }

  return {
    projectsDir,
    listProjects,
    getProject,
    createProject,
    updateProject,
    listSteps,
    addProjectSteps,
    seedDemoIfEmpty,
  };
}

module.exports = { createProjectStore };
