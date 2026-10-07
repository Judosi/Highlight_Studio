export function getDesktopBridge() {
  if (typeof window === 'undefined') return null
  const bridge = window.highlightStudioDesktop
  return bridge?.isDesktop ? bridge : null
}

export function isDesktopRuntime() {
  return Boolean(getDesktopBridge())
}

export async function chooseNativeVideo() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.chooseVideo()
}

export async function openNativePath(targetPath) {
  const bridge = getDesktopBridge()
  if (!bridge || !targetPath) return null
  return bridge.openPath(targetPath)
}

export async function revealNativeFile(targetPath) {
  const bridge = getDesktopBridge()
  if (!bridge || !targetPath) return null
  return bridge.showItemInFolder(targetPath)
}

export async function getDesktopInfo() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.getInfo()
}


export async function chooseNativeProjectDirectory() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.chooseProjectDirectory()
}

export async function setNativeProjectsDirectory(targetPath) {
  const bridge = getDesktopBridge()
  if (!bridge || !targetPath) return null
  return bridge.setProjectsDirectory(targetPath)
}

export async function getNativeUpdateStatus() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.getUpdateStatus()
}

export async function checkNativeUpdates() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.checkForUpdates()
}

export async function downloadNativeUpdate() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.downloadUpdate()
}

export async function installNativeUpdate() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.installUpdate()
}

export function subscribeNativeUpdateStatus(callback) {
  const bridge = getDesktopBridge()
  if (!bridge || typeof bridge.onUpdateStatus !== 'function') return () => {}
  return bridge.onUpdateStatus(callback)
}


export async function installNativeOllama() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.installOllama()
}

export async function pullNativeDefaultOllamaModel() {
  const bridge = getDesktopBridge()
  if (!bridge) return null
  return bridge.pullDefaultOllamaModel()
}


export function subscribeNativeLocalAiStatus(callback) {
  const bridge = getDesktopBridge()
  if (!bridge || typeof bridge.onLocalAiStatus !== 'function') return () => {}
  return bridge.onLocalAiStatus(callback)
}

export async function openNativeExternalUrl(url) {
  const bridge = getDesktopBridge()
  if (!bridge || !url) return null
  return bridge.openExternal(url)
}

export async function getNativeUpdateChannel() {
  const bridge = getDesktopBridge()
  if (!bridge || typeof bridge.getUpdateChannel !== 'function') return null
  return bridge.getUpdateChannel()
}

export async function setNativeUpdateChannel(channel) {
  const bridge = getDesktopBridge()
  if (!bridge || typeof bridge.setUpdateChannel !== 'function') return null
  return bridge.setUpdateChannel(channel)
}

export async function createNativeUpdateBackup() {
  const bridge = getDesktopBridge()
  if (!bridge || typeof bridge.createUpdateBackup !== 'function') return null
  return bridge.createUpdateBackup()
}
