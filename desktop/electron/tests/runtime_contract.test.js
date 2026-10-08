'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const electronRoot = path.resolve(__dirname, '..')
const repositoryRoot = path.resolve(electronRoot, '..', '..')
const mainSource = fs.readFileSync(path.join(electronRoot, 'main.js'), 'utf8')
const preloadSource = fs.readFileSync(path.join(electronRoot, 'preload.js'), 'utf8')
const startHereSource = fs.readFileSync(path.join(repositoryRoot, 'START_HERE.bat'), 'utf8')
const windowsLauncherSource = fs.readFileSync(path.join(repositoryRoot, 'scripts', 'windows', 'run_windows.bat'), 'utf8')

function sourceBlock(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  const end = source.indexOf(endMarker, start + startMarker.length)
  assert.notEqual(start, -1, `missing source marker: ${startMarker}`)
  assert.notEqual(end, -1, `missing source marker: ${endMarker}`)
  return source.slice(start, end)
}

test('BrowserWindow keeps the hardened renderer boundary', () => {
  const createWindow = sourceBlock(mainSource, 'function createWindow()', 'function requestGracefulBackendShutdown')
  assert.match(createWindow, /preload:\s*path\.join\(__dirname, 'preload\.js'\)/)
  assert.match(createWindow, /contextIsolation:\s*true/)
  assert.match(createWindow, /nodeIntegration:\s*false/)
  assert.match(createWindow, /sandbox:\s*true/)
  assert.match(createWindow, /webSecurity:\s*true/)
  assert.match(createWindow, /allowRunningInsecureContent:\s*false/)
})

test('preload exposes only the reviewed desktop API and IPC channels', () => {
  const exposedMethods = [...preloadSource.matchAll(/^\s{2}([A-Za-z][A-Za-z0-9]*):/gm)].map(match => match[1])
  assert.deepEqual(exposedMethods, [
    'isDesktop',
    'chooseVideo',
    'chooseProjectDirectory',
    'setProjectsDirectory',
    'openPath',
    'showItemInFolder',
    'openExternal',
    'getInfo',
    'installOllama',
    'pullDefaultOllamaModel',
    'getUpdateStatus',
    'checkForUpdates',
    'downloadUpdate',
    'installUpdate',
    'getUpdateChannel',
    'setUpdateChannel',
    'createUpdateBackup',
    'onLocalAiStatus',
    'onUpdateStatus',
  ])

  const invokedChannels = [...preloadSource.matchAll(/invoke\('([^']+)'/g)].map(match => match[1])
  assert.deepEqual(invokedChannels, [
    'desktop:choose-video',
    'desktop:choose-project-directory',
    'desktop:set-projects-directory',
    'desktop:open-path',
    'desktop:show-item-in-folder',
    'desktop:open-external',
    'desktop:get-info',
    'desktop:install-ollama',
    'desktop:pull-default-ollama-model',
    'desktop:update-get-status',
    'desktop:update-check',
    'desktop:update-download',
    'desktop:update-install',
    'desktop:update-get-channel',
    'desktop:update-set-channel',
    'desktop:create-update-backup',
  ])
  assert.doesNotMatch(preloadSource, /require\(['"](?:node:)?(?:fs|child_process|net|http|https)['"]\)/)
})

test('every renderer-callable IPC handler validates its sender', () => {
  const registerIpc = sourceBlock(mainSource, 'function registerDesktopIpc()', 'function createWindow()')
  const handlers = [...registerIpc.matchAll(/ipcMain\.handle\('([^']+)',\s*async\s*\(event[^)]*\)\s*=>\s*\{([\s\S]*?)(?=\n\s*ipcMain\.handle\(|\n\})/g)]
  assert.equal(handlers.length, 16)
  for (const [, channel, body] of handlers) {
    assert.match(body, /requireTrustedSender\(event\)/, `${channel} must reject an untrusted sender`)
  }
  assert.match(mainSource, /senderFrame\?\.url\s*\|\|\s*event\?\.sender\?\.getURL/)
  assert.match(mainSource, /return sameOrigin\(senderUrl, appUrl\)/)
})

test('navigation and new-window policies preserve the local application origin', () => {
  const createWindow = sourceBlock(mainSource, 'function createWindow()', 'function requestGracefulBackendShutdown')
  assert.match(createWindow, /setWindowOpenHandler\(\(\{ url \}\) => \{[\s\S]*if \(sameOrigin\(url, appUrl\)\) return \{ action: 'allow' \}/)
  assert.match(createWindow, /if \(isSafeExternalUrl\(url\)\)[\s\S]*shell\.openExternal\(url\)/)
  assert.match(createWindow, /return \{ action: 'deny' \}/)
  assert.match(createWindow, /webContents\.on\('will-navigate',[\s\S]*if \(!sameOrigin\(url, appUrl\)\) event\.preventDefault\(\)/)
  assert.match(mainSource, /setPermissionRequestHandler\([\s\S]*callback\(false\)/)
  assert.match(mainSource, /setPermissionCheckHandler\(\(\) => false\)/)
})

test('backend lifecycle and single-instance behavior remain guarded', () => {
  assert.match(mainSource, /const lock = app\.requestSingleInstanceLock\(\)/)
  assert.match(mainSource, /app\.on\('second-instance',[\s\S]*mainWindow\.restore\(\)[\s\S]*mainWindow\.focus\(\)/)
  assert.match(mainSource, /await ensureBackend\(\)[\s\S]*registerDesktopIpc\(\)[\s\S]*createWindow\(\)/)
  assert.match(mainSource, /app\.on\('before-quit',[\s\S]*event\.preventDefault\(\)[\s\S]*shutdownApplication\(\)/)
  assert.match(mainSource, /async function stopBackendGracefully\([\s\S]*requestGracefulBackendShutdown\(\)[\s\S]*waitForProcessExit\([\s\S]*forceStopBackend\(/)
  assert.match(mainSource, /process\.platform === 'win32'[\s\S]*spawnSync\('taskkill'/)
})

test('START_HERE and existing-project browser launch flow remain compatible', () => {
  assert.match(startHereSource, /call "%CD%\\scripts\\windows\\run_windows\.bat"/i)
  assert.match(startHereSource, /frontend\\dist\\index\.html/i)
  assert.match(windowsLauncherSource, /set "HIGHLIGHT_STUDIO_PORTABLE=1"/)
  assert.match(windowsLauncherSource, /if not defined HIGHLIGHT_STUDIO_OPEN_PATH set "HIGHLIGHT_STUDIO_OPEN_PATH=\/"/)
  assert.ok(windowsLauncherSource.includes(String.raw`tools\diagnostics\wait_and_open.py "%HS_URL%" "%HIGHLIGHT_STUDIO_OPEN_PATH%"`))
  assert.match(windowsLauncherSource, /-m uvicorn backend\.src\.highlight_studio\.api\.app:app --host 127\.0\.0\.1/)
})
