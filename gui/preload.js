"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("qaPipeline", {
  listExamples: () => ipcRenderer.invoke("pipeline:listExamples"),
  pickSteps: () => ipcRenderer.invoke("pipeline:pickSteps"),
  readSteps: (filePath) => ipcRenderer.invoke("pipeline:readSteps", filePath),
  run: (opts) => ipcRenderer.invoke("pipeline:run", opts),
  repoRoot: () => ipcRenderer.invoke("pipeline:repoRoot"),
  onEvent: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("pipeline:event", listener);
    return () => ipcRenderer.removeListener("pipeline:event", listener);
  },
});
