"use strict";

const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const fs = require("fs");
const path = require("path");
const { runPipeline, runEventFromError, specStatus, REPO_ROOT } = require("./pipeline-runner");
const { createProjectStore } = require("./project-store");

if (!app || !ipcMain) {
  console.error(
    "Electron APIs unavailable. If ELECTRON_RUN_AS_NODE=1 is set, unset it and run via `npx electron .`"
  );
  process.exit(1);
}

const store = createProjectStore(REPO_ROOT);
const ICON_PATH = path.join(__dirname, "icon.png");

let mainWindow = null;
let running = false;
let abortController = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 860,
    minHeight: 620,
    title: "LoWave",
    backgroundColor: "#ffffff",
    icon: ICON_PATH,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "index.html"));
}

function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

async function executePipeline(opts) {
  if (running) throw new Error("A pipeline run is already in progress");
  running = true;
  abortController = new AbortController();
  try {
    send("pipeline:event", { type: "run", status: "started" });
    const result = await runPipeline({
      ...opts,
      signal: abortController.signal,
      onEvent: (evt) => send("pipeline:event", evt),
    });
    send("pipeline:event", { type: "run", status: "finished", result });
    return result;
  } catch (err) {
    send("pipeline:event", runEventFromError(err));
    if (err.cancelled) {
      return { cancelled: true };
    }
    if (err.incomplete) {
      return { incomplete: true, specPath: err.result?.specPath };
    }
    throw err;
  } finally {
    running = false;
    abortController = null;
  }
}

function cancelPipeline() {
  if (!running || !abortController || abortController.signal.aborted) {
    return { cancelled: false };
  }
  abortController.abort();
  return { cancelled: true };
}

function handleIpc(channel, listener) {
  ipcMain.removeHandler(channel);
  ipcMain.handle(channel, listener);
}

function registerIpc() {
  handleIpc("projects:list", () => store.seedDemoIfEmpty());
  handleIpc("projects:get", (_e, slug) => store.getProject(slug));
  handleIpc("projects:create", (_e, payload) => store.createProject(payload));
  handleIpc("projects:update", (_e, slug, patch) => store.updateProject(slug, patch));
  handleIpc("projects:addSteps", (_e, slug, payload) => store.addProjectSteps(slug, payload));

  handleIpc("pipeline:pickSteps", async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
      title: "Import a steps file",
      defaultPath: path.join(REPO_ROOT, "examples", "workflows"),
      filters: [{ name: "Steps", extensions: ["txt"] }],
      properties: ["openFile"],
    });
    if (canceled || !filePaths.length) return null;
    return filePaths[0];
  });
  handleIpc("pipeline:readSteps", (_e, filePath) => {
    return fs.readFileSync(filePath, "utf8");
  });
  handleIpc("pipeline:specStatus", (_e, opts) => specStatus(opts));
  handleIpc("pipeline:run", async (_e, opts) => executePipeline(opts));
  handleIpc("pipeline:cancel", () => cancelPipeline());
  handleIpc("pipeline:repoRoot", () => REPO_ROOT);
  handleIpc("pipeline:openPath", async (_e, filePath) => {
    if (typeof filePath !== "string" || !filePath.trim()) return { ok: false };
    const resolved = path.resolve(filePath);
    const rel = path.relative(REPO_ROOT, resolved);
    if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) return { ok: false };
    if (!fs.existsSync(resolved)) return { ok: false };
    const error = await shell.openPath(resolved);
    return { ok: !error, error: error || undefined };
  });

  handleIpc("settings:get", () => store.readSettings());
  handleIpc("settings:set", (_e, patch) => store.writeSettings(patch));
}

registerIpc();

app.whenReady().then(() => {
  if (process.platform === "darwin" && app.dock) {
    app.dock.setIcon(ICON_PATH);
  }
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
