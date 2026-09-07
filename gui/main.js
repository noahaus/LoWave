"use strict";

const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const fs = require("fs");
const path = require("path");
const { runPipeline, REPO_ROOT } = require("./pipeline-runner");
const { createProjectStore } = require("./project-store");

if (!app || !ipcMain) {
  console.error(
    "Electron APIs unavailable. If ELECTRON_RUN_AS_NODE=1 is set, unset it and run via `npx electron .`"
  );
  process.exit(1);
}

const store = createProjectStore(REPO_ROOT);

let mainWindow = null;
let running = false;
let abortController = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 860,
    minHeight: 620,
    title: "QA Pipeline",
    backgroundColor: "#e8ebe6",
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
    if (err.cancelled) {
      send("pipeline:event", { type: "run", status: "cancelled", error: err.message });
      return { cancelled: true };
    }
    send("pipeline:event", {
      type: "run",
      status: "failed",
      error: err.message,
      stderr: err.stderr || "",
    });
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

function registerIpc() {
  ipcMain.handle("projects:list", () => store.seedDemoIfEmpty());
  ipcMain.handle("projects:get", (_e, slug) => store.getProject(slug));
  ipcMain.handle("projects:create", (_e, payload) => store.createProject(payload));
  ipcMain.handle("projects:update", (_e, slug, patch) => store.updateProject(slug, patch));
  ipcMain.handle("projects:addSteps", (_e, slug, payload) => store.addProjectSteps(slug, payload));

  ipcMain.handle("pipeline:pickSteps", async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
      title: "Import a steps file",
      defaultPath: path.join(REPO_ROOT, "examples", "workflows"),
      filters: [{ name: "Steps", extensions: ["txt"] }],
      properties: ["openFile"],
    });
    if (canceled || !filePaths.length) return null;
    return filePaths[0];
  });
  ipcMain.handle("pipeline:readSteps", (_e, filePath) => {
    return fs.readFileSync(filePath, "utf8");
  });
  ipcMain.handle("pipeline:run", async (_e, opts) => executePipeline(opts));
  ipcMain.handle("pipeline:cancel", () => cancelPipeline());
  ipcMain.handle("pipeline:repoRoot", () => REPO_ROOT);
}

app.whenReady().then(() => {
  registerIpc();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
