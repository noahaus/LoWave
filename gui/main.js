"use strict";

const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const fs = require("fs");
const path = require("path");
const { runPipeline, REPO_ROOT } = require("./pipeline-runner");

if (!app || !ipcMain) {
  console.error(
    "Electron APIs unavailable. If ELECTRON_RUN_AS_NODE=1 is set, unset it and run via `npx electron .`"
  );
  process.exit(1);
}

let mainWindow = null;
let running = false;

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
  try {
    send("pipeline:event", { type: "run", status: "started" });
    const result = await runPipeline({
      ...opts,
      onEvent: (evt) => send("pipeline:event", evt),
    });
    send("pipeline:event", { type: "run", status: "finished", result });
    return result;
  } catch (err) {
    send("pipeline:event", {
      type: "run",
      status: "failed",
      error: err.message,
      stderr: err.stderr || "",
    });
    throw err;
  } finally {
    running = false;
  }
}

function listWorkflowExamples() {
  const dir = path.join(REPO_ROOT, "examples", "workflows");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".txt"))
    .map((f) => ({
      name: f,
      path: path.join(dir, f),
    }));
}

function registerIpc() {
  ipcMain.handle("pipeline:listExamples", () => listWorkflowExamples());
  ipcMain.handle("pipeline:pickSteps", async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
      title: "Choose a steps file",
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
