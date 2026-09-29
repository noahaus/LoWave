"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("qaPipeline", {
  listProjects: () => ipcRenderer.invoke("projects:list"),
  getProject: (slug) => ipcRenderer.invoke("projects:get", slug),
  createProject: (payload) => ipcRenderer.invoke("projects:create", payload),
  updateProject: (slug, patch) => ipcRenderer.invoke("projects:update", slug, patch),
  addProjectSteps: (slug, payload) => ipcRenderer.invoke("projects:addSteps", slug, payload),
  createReadout: (slug, payload) => ipcRenderer.invoke("projects:createReadout", slug, payload),
  updateReadout: (slug, id, patch) => ipcRenderer.invoke("projects:updateReadout", slug, id, patch),
  deleteReadout: (slug, id) => ipcRenderer.invoke("projects:deleteReadout", slug, id),
  pickSteps: () => ipcRenderer.invoke("pipeline:pickSteps"),
  readSteps: (filePath) => ipcRenderer.invoke("pipeline:readSteps", filePath),
  specStatus: (opts) => ipcRenderer.invoke("pipeline:specStatus", opts),
  run: (opts) => ipcRenderer.invoke("pipeline:run", opts),
  runReadout: (opts) => ipcRenderer.invoke("pipeline:runReadout", opts),
  cancel: () => ipcRenderer.invoke("pipeline:cancel"),
  repoRoot: () => ipcRenderer.invoke("pipeline:repoRoot"),
  openPath: (filePath) => ipcRenderer.invoke("pipeline:openPath", filePath),
  getSettings: () => ipcRenderer.invoke("settings:get"),
  saveSettings: (patch) => ipcRenderer.invoke("settings:set", patch),
  onEvent: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("pipeline:event", listener);
    return () => ipcRenderer.removeListener("pipeline:event", listener);
  },
});
