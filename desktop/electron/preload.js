'use strict'

const { contextBridge, ipcRenderer } = require('electron')

const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload)

contextBridge.exposeInMainWorld('highlightStudioDesktop', Object.freeze({
  isDesktop: true,
  chooseVideo: () => invoke('desktop:choose-video'),
  chooseProjectDirectory: () => invoke('desktop:choose-project-directory'),
  setProjectsDirectory: (targetPath) => invoke('desktop:set-projects-directory', { path: targetPath }),
  openPath: (targetPath) => invoke('desktop:open-path', { path: targetPath }),
  showItemInFolder: (targetPath) => invoke('desktop:show-item-in-folder', { path: targetPath }),
  openExternal: (url) => invoke('desktop:open-external', { url }),
  getInfo: () => invoke('desktop:get-info'),
  installOllama: () => invoke('desktop:install-ollama'),
  pullDefaultOllamaModel: () => invoke('desktop:pull-default-ollama-model'),
  getUpdateStatus: () => invoke('desktop:update-get-status'),
  checkForUpdates: () => invoke('desktop:update-check'),
  downloadUpdate: () => invoke('desktop:update-download'),
  installUpdate: () => invoke('desktop:update-install'),
  getUpdateChannel: () => invoke('desktop:update-get-channel'),
  setUpdateChannel: (channel) => invoke('desktop:update-set-channel', { channel }),
  createUpdateBackup: () => invoke('desktop:create-update-backup'),
  onLocalAiStatus: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('desktop:local-ai-status', listener)
    return () => ipcRenderer.removeListener('desktop:local-ai-status', listener)
  },
  onUpdateStatus: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('desktop:update-status', listener)
    return () => ipcRenderer.removeListener('desktop:update-status', listener)
  },
}))
