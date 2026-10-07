'use strict'

const { app, BrowserWindow, dialog, shell, ipcMain, session } = require('electron')
const { autoUpdater } = require('electron-updater')
const { spawn, spawnSync } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')
const { sameOrigin, isLoopbackHttpUrl, isSafeExternalUrl, isSafeUpdateUrl, isCompatibleHealth } = require('./security')
const { normalizedExistingPath, existingDirectory, existingFile, existingVideoFile } = require('./ipc_policy')

const HOST = '127.0.0.1'
const PREFERRED_PORT = Number(process.env.HIGHLIGHT_STUDIO_PORT || 8000)
const EXPLICIT_APP_URL = String(process.env.HIGHLIGHT_STUDIO_URL || '').trim()

function canonicalReleaseIdentity() {
  const candidates = [
    process.env.HIGHLIGHT_STUDIO_APP_ROOT ? path.join(process.env.HIGHLIGHT_STUDIO_APP_ROOT, 'release_identity.json') : '',
    app.isPackaged ? path.join(process.resourcesPath, 'app', 'release_identity.json') : '',
    path.resolve(__dirname, '..', '..', 'release_identity.json'),
  ].filter(Boolean)
  for (const filePath of candidates) {
    try {
      const payload = JSON.parse(fs.readFileSync(filePath, 'utf8'))
      if (payload?.version && payload?.app_version && payload?.design_id) return payload
    } catch (_) {}
  }
  throw new Error('release_identity.json not found or invalid')
}

const RELEASE_IDENTITY = canonicalReleaseIdentity()
const EXPECTED_APP_VERSION_PREFIX = process.env.HIGHLIGHT_STUDIO_VERSION_PREFIX || String(RELEASE_IDENTITY.app_version)
const EXPECTED_DESIGN_ID = process.env.HIGHLIGHT_STUDIO_DESIGN_ID || String(RELEASE_IDENTITY.design_id)
let appPort = PREFERRED_PORT
let appUrl = EXPLICIT_APP_URL || `http://${HOST}:${appPort}`
let backendProcess = null
let ownsBackend = false
let quitting = false
let mainWindow = null
let updateState = { supported: false, status: 'disabled', message: 'Обновления недоступны в режиме разработки.' }
let closePromptActive = false
let desktopShutdownToken = ''
let shutdownPromise = null
let allowQuit = false

function appendDesktopLog(message) {
  try {
    const logDir = app.getPath('logs')
    fs.mkdirSync(logDir, { recursive: true })
    fs.appendFileSync(path.join(logDir, 'desktop.log'), `${new Date().toISOString()} ${message}\n`)
  } catch (_) {}
}

function runFixedCommand(command, args, timeoutMs = 20 * 60 * 1000, onProgress = null) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    let output = ''
    const collect = (chunk) => {
      output = `${output}${String(chunk)}`.slice(-12000)
      if (onProgress) onProgress(String(chunk).trim().slice(-500))
    }
    child.stdout.on('data', collect)
    child.stderr.on('data', collect)
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      try { child.kill() } catch (_) {}
      resolve({ ok: false, code: null, message: 'Команда превысила допустимое время.', output })
    }, timeoutMs)
    child.on('error', (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ ok: false, code: null, message: String(error.message || error), output })
    })
    child.on('exit', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ ok: code === 0, code, message: code === 0 ? 'Готово' : `Команда завершилась с кодом ${code}`, output })
    })
  })
}

function healthCheck(timeoutMs = 1200) {
  return new Promise((resolve) => {
    const request = http.get(`${appUrl}/api/health`, { timeout: timeoutMs }, (response) => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => {
        if (body.length < 65536) body += chunk
      })
      response.on('end', () => {
        let payload = null
        try { payload = JSON.parse(body) } catch (_) {}
        const reachable = response.statusCode === 200
        resolve({
          reachable,
          compatible: reachable && isCompatibleHealth(payload, EXPECTED_APP_VERSION_PREFIX, EXPECTED_DESIGN_ID),
          version: String(payload?.app_version || ''),
          activeJobs: Number(payload?.active_jobs || 0),
        })
      })
    })
    request.on('timeout', () => {
      request.destroy()
      resolve({ reachable: false, compatible: false, version: '', activeJobs: 0 })
    })
    request.on('error', () => resolve({ reachable: false, compatible: false, version: '', activeJobs: 0 }))
  })
}

function canListen(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => resolve(false))
    server.listen({ host: HOST, port, exclusive: true }, () => {
      server.close(() => resolve(true))
    })
  })
}

async function chooseEndpoint() {
  if (EXPLICIT_APP_URL) {
    if (!isLoopbackHttpUrl(EXPLICIT_APP_URL)) throw new Error(`Небезопасный адрес локального приложения: ${EXPLICIT_APP_URL}`)
    appUrl = EXPLICIT_APP_URL.replace(/\/$/, '')
    const parsed = new URL(appUrl)
    appPort = Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80))
    return
  }

  appPort = Number.isInteger(PREFERRED_PORT) && PREFERRED_PORT >= 1024 && PREFERRED_PORT <= 65535 ? PREFERRED_PORT : 8000
  appUrl = `http://${HOST}:${appPort}`
  const existing = await healthCheck()
  if (existing.compatible || !existing.reachable) return

  // Do not force the user to close an older Highlight Studio instance or an
  // unrelated service. Find a private free port; the UI uses same-origin /api.
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const candidate = 18000 + Math.floor(Math.random() * 20000)
    if (await canListen(candidate)) {
      appPort = candidate
      appUrl = `http://${HOST}:${appPort}`
      appendDesktopLog(`Preferred port occupied by ${existing.version || 'another service'}; selected ${appPort}`)
      return
    }
  }
  throw new Error('Не удалось найти свободный локальный порт для backend.')
}

function projectRoot() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'app')
  return path.resolve(__dirname, '..', '..')
}

function desktopConfigPath() {
  return path.join(app.getPath('userData'), 'desktop-config.json')
}

function readDesktopConfig() {
  try {
    const payload = JSON.parse(fs.readFileSync(desktopConfigPath(), 'utf8'))
    return payload && typeof payload === 'object' ? payload : {}
  } catch (_) {
    return {}
  }
}

function readPaidBetaConfig() {
  try {
    const payload = JSON.parse(fs.readFileSync(path.join(__dirname, 'paid-beta-channel.json'), 'utf8'))
    return payload && typeof payload === 'object' ? payload : {}
  } catch (_) {
    return {}
  }
}

function readReleaseConfig() {
  try {
    const payload = JSON.parse(fs.readFileSync(path.join(__dirname, 'release-channel.json'), 'utf8'))
    return payload && typeof payload === 'object' ? payload : {}
  } catch (_) {
    return {}
  }
}

function selectedUpdateChannel() {
  const releaseConfig = readReleaseConfig()
  const desktopConfig = readDesktopConfig()
  const requested = String(desktopConfig.updateChannel || releaseConfig.channel || 'stable').toLowerCase()
  return requested === 'beta' && releaseConfig.allowChannelSwitch !== false ? 'beta' : 'stable'
}

function appendDesktopCrash(error, source = 'desktop') {
  try {
    const { dataDir } = runtimePaths()
    const target = path.join(dataDir, 'crash_reports.jsonl')
    const raw = String(error?.stack || error?.message || error || 'Unknown desktop error')
      .replace(/[A-Za-z]:\\[^\r\n]+/g, '<LOCAL_PATH>')
      .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '<EMAIL_REDACTED>')
      .slice(0, 1200)
    const payload = {
      crash_id: crypto.randomBytes(6).toString('hex'),
      source,
      error_type: String(error?.name || 'Error').slice(0, 160),
      message: raw,
      context: { platform: process.platform, arch: process.arch, app_version: app.getVersion() },
      app_version: app.getVersion(),
      created_at: Date.now() / 1000,
      uploaded: false,
    }
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.appendFileSync(target, `${JSON.stringify(payload)}\n`, 'utf8')
  } catch (_) {}
}

function createPreUpdateBackup() {
  try {
    const { dataDir } = runtimePaths()
    const stamp = new Date().toISOString().replace(/[:.]/g, '-')
    const target = path.join(dataDir, 'update-backups', `${app.getVersion()}-${stamp}`)
    fs.mkdirSync(target, { recursive: true })
    const files = [
      'desktop-config.json', 'onboarding.json', 'privacy_preferences.json', 'license.json',
      'trial.json', 'mass_release_evidence.json', 'quality_benchmark_report.json',
    ]
    for (const name of files) {
      const source = name === 'desktop-config.json' ? desktopConfigPath() : path.join(dataDir, name)
      if (fs.existsSync(source) && fs.statSync(source).isFile()) fs.copyFileSync(source, path.join(target, name))
    }
    fs.writeFileSync(path.join(target, 'backup.json'), JSON.stringify({ appVersion: app.getVersion(), createdAt: new Date().toISOString() }, null, 2), 'utf8')
    return { ok: true, path: target }
  } catch (error) {
    appendDesktopLog(`Pre-update backup failed: ${error.stack || error}`)
    return { ok: false, message: String(error.message || error) }
  }
}

function writeDesktopConfig(patch) {
  const target = desktopConfigPath()
  fs.mkdirSync(path.dirname(target), { recursive: true })
  const next = { ...readDesktopConfig(), ...patch, updatedAt: new Date().toISOString() }
  const temp = `${target}.tmp`
  fs.writeFileSync(temp, JSON.stringify(next, null, 2), 'utf8')
  fs.renameSync(temp, target)
  return next
}

function runtimePaths() {
  const defaultDataDir = process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, 'HighlightStudio')
    : app.getPath('userData')
  const config = readDesktopConfig()
  const dataDir = process.env.HIGHLIGHT_STUDIO_DATA_DIR || defaultDataDir
  const configuredProjectsDir = typeof config.projectsDir === 'string' ? config.projectsDir.trim() : ''
  const projectsDir = process.env.HIGHLIGHT_STUDIO_PROJECTS_DIR || configuredProjectsDir || path.join(app.getPath('videos'), 'Highlight Studio')
  fs.mkdirSync(dataDir, { recursive: true })
  fs.mkdirSync(projectsDir, { recursive: true })
  return { dataDir, projectsDir }
}

function findPython(root) {
  const candidates = process.platform === 'win32'
    ? [path.join(root, '.venv', 'Scripts', 'python.exe'), 'py', 'python']
    : [path.join(root, '.venv', 'bin', 'python'), 'python3', 'python']
  for (const candidate of candidates) {
    if (candidate.includes(path.sep) && fs.existsSync(candidate)) return { command: candidate, prefix: [] }
    if (!candidate.includes(path.sep)) {
      const args = candidate === 'py' ? ['-3', '--version'] : ['--version']
      const probe = spawnSync(candidate, args, { windowsHide: true, encoding: 'utf8' })
      if (probe.status === 0) return { command: candidate, prefix: candidate === 'py' ? ['-3'] : [] }
    }
  }
  return null
}

function engineCommand(root) {
  const explicit = process.env.HIGHLIGHT_STUDIO_ENGINE
  const packagedEngine = process.platform === 'win32'
    ? path.join(process.resourcesPath, 'engine', 'HighlightStudioEngine.exe')
    : path.join(process.resourcesPath, 'engine', 'HighlightStudioEngine')
  if (explicit && fs.existsSync(explicit)) return { command: explicit, args: [] }
  if (app.isPackaged && fs.existsSync(packagedEngine)) return { command: packagedEngine, args: [] }

  const python = findPython(root)
  if (!python) return null
  return {
    command: python.command,
    args: [...python.prefix, path.join(root, 'backend', 'desktop_entry.py')],
  }
}

async function ensureBackend() {
  await chooseEndpoint()
  if (!isLoopbackHttpUrl(appUrl)) throw new Error(`Небезопасный адрес локального приложения: ${appUrl}`)

  const existing = await healthCheck()
  if (existing.compatible) {
    appendDesktopLog(`Using compatible backend ${existing.version} at ${appUrl}`)
    return
  }
  if (existing.reachable) throw new Error(`Порт ${appPort} занят несовместимым сервисом (${existing.version || 'неизвестная версия'}).`)

  const root = projectRoot()
  const engine = engineCommand(root)
  if (!engine) throw new Error('Локальный движок не найден. Для разработки установи Python, для релиза собери HighlightStudioEngine.exe.')

  const { dataDir, projectsDir } = runtimePaths()
  const cacheDir = path.join(dataDir, 'cache')
  const huggingFaceDir = path.join(cacheDir, 'huggingface')
  fs.mkdirSync(huggingFaceDir, { recursive: true })
  desktopShutdownToken = crypto.randomBytes(32).toString('base64url')
  const paidBeta = readPaidBetaConfig()
  const releaseConfig = readReleaseConfig()
  const updateChannel = selectedUpdateChannel()
  const configuredUpdateUrl = updateChannel === 'beta' ? releaseConfig.betaUpdateUrl : releaseConfig.stableUpdateUrl
  const paidBetaEnv = {
    HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64: paidBeta.licensePublicKeyB64 || '',
    HIGHLIGHT_STUDIO_LICENSE_SERVER_URL: paidBeta.licenseServerUrl || '',
    HIGHLIGHT_STUDIO_CHECKOUT_URL: paidBeta.checkoutUrl || '',
    HIGHLIGHT_STUDIO_ACCOUNT_URL: paidBeta.accountUrl || '',
    HIGHLIGHT_STUDIO_SUPPORT_URL: paidBeta.supportUrl || '',
    HIGHLIGHT_STUDIO_PRIVACY_URL: paidBeta.privacyUrl || '',
    HIGHLIGHT_STUDIO_TERMS_URL: paidBeta.termsUrl || '',
    HIGHLIGHT_STUDIO_TELEMETRY_URL: paidBeta.telemetryUrl || '',
    HIGHLIGHT_STUDIO_FEEDBACK_URL: paidBeta.feedbackUrl || '',
    HIGHLIGHT_STUDIO_CRASH_REPORT_URL: paidBeta.crashReportUrl || '',
    HIGHLIGHT_STUDIO_UPDATE_URL: process.env.HIGHLIGHT_STUDIO_UPDATE_URL || configuredUpdateUrl || '',
    HIGHLIGHT_STUDIO_TRIAL_DAYS: String(paidBeta.trialDays || 14),
  }
  const childEnv = {
    ...process.env,
    ...Object.fromEntries(Object.entries(paidBetaEnv).filter(([, value]) => value)),
    HIGHLIGHT_STUDIO_APP_ROOT: root,
    HIGHLIGHT_STUDIO_DATA_DIR: dataDir,
    HIGHLIGHT_STUDIO_PROJECTS_DIR: projectsDir,
    HIGHLIGHT_STUDIO_DESKTOP: '1',
    HIGHLIGHT_STUDIO_DESKTOP_LOG_DIR: app.getPath('logs'),
    HIGHLIGHT_STUDIO_DESKTOP_SHUTDOWN_TOKEN: desktopShutdownToken,
    HIGHLIGHT_STUDIO_PORT: String(appPort),
    HF_HOME: process.env.HF_HOME || huggingFaceDir,
    HUGGINGFACE_HUB_CACHE: process.env.HUGGINGFACE_HUB_CACHE || path.join(huggingFaceDir, 'hub'),
    PYTHONUNBUFFERED: '1',
  }
  appendDesktopLog(`Starting backend: ${engine.command} ${engine.args.join(' ')} on ${appUrl}`)
  backendProcess = spawn(engine.command, engine.args, {
    cwd: root,
    env: childEnv,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  ownsBackend = true
  backendProcess.stdout.on('data', (data) => appendDesktopLog(`[backend] ${String(data).trimEnd()}`))
  backendProcess.stderr.on('data', (data) => appendDesktopLog(`[backend:error] ${String(data).trimEnd()}`))
  backendProcess.on('exit', (code, signal) => {
    appendDesktopLog(`Backend exited code=${code} signal=${signal}`)
    backendProcess = null
    if (!quitting && code !== 0) {
      dialog.showErrorBox('Highlight Studio', 'Локальный движок неожиданно остановился. Диагностика сохранена в desktop.log и backend.log.')
    }
  })

  const deadline = Date.now() + 90000
  while (Date.now() < deadline) {
    const probe = await healthCheck(1800)
    if (probe.compatible) return
    if (probe.reachable) throw new Error(`На порту ${appPort} появился несовместимый backend: ${probe.version || 'неизвестная версия'}.`)
    if (!backendProcess) break
    await new Promise((resolve) => setTimeout(resolve, 450))
  }
  throw new Error(`Локальный движок не запустился: ${appUrl}. Проверь desktop.log.`)
}

function trustedSender(event) {
  const senderUrl = event?.senderFrame?.url || event?.sender?.getURL?.() || ''
  return sameOrigin(senderUrl, appUrl)
}

function requireTrustedSender(event) {
  if (!trustedSender(event)) throw new Error('Blocked untrusted desktop IPC sender')
}

function broadcastLocalAiState(payload = {}) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('desktop:local-ai-status', { ...payload, updatedAt: Date.now() })
}

function broadcastUpdateState(patch = {}) {
  updateState = { ...updateState, ...patch, updatedAt: Date.now() }
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('desktop:update-status', updateState)
}

function configureUpdater() {
  const releaseConfig = readReleaseConfig()
  const channel = selectedUpdateChannel()
  const configuredUrl = channel === 'beta' ? releaseConfig.betaUpdateUrl : releaseConfig.stableUpdateUrl
  const updateUrl = String(process.env.HIGHLIGHT_STUDIO_UPDATE_URL || configuredUrl || '').trim()
  if (!app.isPackaged || !updateUrl) {
    broadcastUpdateState({ supported: false, channel, status: 'disabled', message: app.isPackaged ? `Сервер обновлений (${channel}) не настроен.` : 'Обновления доступны только в установленной версии.' })
    return
  }
  if (!isSafeUpdateUrl(updateUrl)) {
    broadcastUpdateState({ supported: false, status: 'error', message: 'Адрес обновлений должен использовать HTTPS.' })
    appendDesktopLog(`Blocked unsafe update URL: ${updateUrl}`)
    return
  }
  try {
    autoUpdater.autoDownload = false
    autoUpdater.autoInstallOnAppQuit = true
    autoUpdater.allowPrerelease = channel === 'beta'
    autoUpdater.channel = channel
    autoUpdater.setFeedURL({ provider: 'generic', url: updateUrl.replace(/\/$/, '') })
    autoUpdater.on('checking-for-update', () => broadcastUpdateState({ supported: true, status: 'checking', message: 'Проверяем обновления…' }))
    autoUpdater.on('update-available', (info) => broadcastUpdateState({ supported: true, status: 'available', version: info.version, message: `Доступна версия ${info.version}.` }))
    autoUpdater.on('update-not-available', (info) => broadcastUpdateState({ supported: true, status: 'current', version: info.version, message: 'Установлена актуальная версия.' }))
    autoUpdater.on('download-progress', (progress) => broadcastUpdateState({ supported: true, status: 'downloading', percent: Math.round(progress.percent || 0), message: `Загрузка обновления: ${Math.round(progress.percent || 0)}%` }))
    autoUpdater.on('update-downloaded', (info) => broadcastUpdateState({ supported: true, status: 'downloaded', version: info.version, percent: 100, message: 'Обновление готово к установке.' }))
    autoUpdater.on('error', (error) => broadcastUpdateState({ supported: true, status: 'error', message: `Обновление не удалось: ${String(error.message || error).slice(0, 300)}` }))
    broadcastUpdateState({ supported: true, channel, status: 'idle', message: `Автоматические обновления включены (${channel}).` })
    setTimeout(() => autoUpdater.checkForUpdates().catch((error) => appendDesktopLog(`Update check failed: ${error.message || error}`)), 15000)
    setInterval(() => autoUpdater.checkForUpdates().catch((error) => appendDesktopLog(`Scheduled update check failed: ${error.message || error}`)), 6 * 60 * 60 * 1000).unref()
  } catch (error) {
    broadcastUpdateState({ supported: false, status: 'error', message: `Updater configuration failed: ${String(error.message || error).slice(0, 300)}` })
  }
}

function registerDesktopIpc() {
  ipcMain.handle('desktop:choose-video', async (event) => {
    requireTrustedSender(event)
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Выбери исходное видео',
      properties: ['openFile'],
      filters: [
        { name: 'Видео', extensions: ['mp4', 'mkv', 'mov', 'webm', 'avi', 'm4v', 'ts'] },
        { name: 'Все файлы', extensions: ['*'] },
      ],
    })
    if (result.canceled || !result.filePaths[0]) return { ok: false, canceled: true }
    const selected = existingVideoFile(result.filePaths[0])
    if (!selected) return { ok: false, message: 'Выбранный файл не является поддерживаемым видео.' }
    return { ok: true, path: selected, name: path.basename(selected) }
  })

  ipcMain.handle('desktop:choose-project-directory', async (event) => {
    requireTrustedSender(event)
    const result = await dialog.showOpenDialog(mainWindow, { title: 'Папка проектов Highlight Studio', properties: ['openDirectory', 'createDirectory'] })
    if (result.canceled || !result.filePaths[0]) return { ok: false, canceled: true }
    const selected = existingDirectory(result.filePaths[0])
    return selected ? { ok: true, path: selected } : { ok: false, message: 'Папка недоступна.' }
  })

  ipcMain.handle('desktop:open-path', async (event, payload) => {
    requireTrustedSender(event)
    const target = normalizedExistingPath(payload?.path)
    if (!target) return { ok: false, message: 'Файл или папка не найдены.' }
    const errorMessage = await shell.openPath(target)
    return errorMessage ? { ok: false, message: errorMessage } : { ok: true, path: target }
  })

  ipcMain.handle('desktop:show-item-in-folder', async (event, payload) => {
    requireTrustedSender(event)
    const target = existingFile(payload?.path)
    if (!target) return { ok: false, message: 'Файл не найден.' }
    shell.showItemInFolder(target)
    return { ok: true, path: target }
  })

  ipcMain.handle('desktop:open-external', async (event, payload) => {
    requireTrustedSender(event)
    const target = String(payload?.url || '').trim()
    if (!isSafeExternalUrl(target)) return { ok: false, message: 'Разрешены только безопасные HTTPS или mailto-ссылки.' }
    await shell.openExternal(target)
    return { ok: true }
  })

  ipcMain.handle('desktop:set-projects-directory', async (event, payload) => {
    requireTrustedSender(event)
    const selected = existingDirectory(payload?.path)
    if (!selected) return { ok: false, message: 'Папка недоступна.' }
    const currentProjectsDir = runtimePaths().projectsDir
    writeDesktopConfig({ projectsDir: selected })
    return { ok: true, path: selected, restartRequired: path.resolve(selected) !== path.resolve(currentProjectsDir) }
  })

  ipcMain.handle('desktop:install-ollama', async (event) => {
    requireTrustedSender(event)
    if (process.platform !== 'win32') return { ok: false, message: 'Автоматическая установка Ollama доступна только в Windows.' }
    appendDesktopLog('Starting explicit Ollama installation through winget')
    broadcastLocalAiState({ status: 'installing', message: 'Windows устанавливает Ollama…' })
    const result = await runFixedCommand('winget', [
      'install', '--id', 'Ollama.Ollama', '--exact', '--silent',
      '--accept-package-agreements', '--accept-source-agreements',
    ], 20 * 60 * 1000, line => broadcastLocalAiState({ status: 'installing', message: line || 'Установка Ollama…' }))
    broadcastLocalAiState({ status: result.ok ? 'installed' : 'error', message: result.ok ? 'Ollama установлена.' : result.message })
    return result
  })

  ipcMain.handle('desktop:pull-default-ollama-model', async (event) => {
    requireTrustedSender(event)
    appendDesktopLog('Starting explicit Ollama model download: qwen3:8b')
    broadcastLocalAiState({ status: 'downloading-model', message: 'Скачиваем qwen3:8b…' })
    const result = await runFixedCommand('ollama', ['pull', 'qwen3:8b'], 90 * 60 * 1000, line => broadcastLocalAiState({ status: 'downloading-model', message: line || 'Скачивание модели…' }))
    broadcastLocalAiState({ status: result.ok ? 'model-ready' : 'error', message: result.ok ? 'Модель qwen3:8b готова.' : result.message })
    return result
  })

  ipcMain.handle('desktop:update-get-status', async (event) => {
    requireTrustedSender(event)
    return { ok: true, ...updateState }
  })

  ipcMain.handle('desktop:update-check', async (event) => {
    requireTrustedSender(event)
    if (!updateState.supported) return { ok: false, ...updateState }
    await autoUpdater.checkForUpdates()
    return { ok: true, ...updateState }
  })

  ipcMain.handle('desktop:update-download', async (event) => {
    requireTrustedSender(event)
    if (updateState.status !== 'available') return { ok: false, message: 'Нет доступного обновления.', ...updateState }
    await autoUpdater.downloadUpdate()
    return { ok: true, ...updateState }
  })

  ipcMain.handle('desktop:update-install', async (event) => {
    requireTrustedSender(event)
    if (updateState.status !== 'downloaded') return { ok: false, message: 'Обновление ещё не загружено.', ...updateState }
    const backup = createPreUpdateBackup()
    if (!backup.ok) return { ok: false, message: 'Не удалось создать резервную копию перед обновлением.' }
    setImmediate(() => shutdownApplication('update'))
    return { ok: true, backupPath: backup.path, ...updateState }
  })

  ipcMain.handle('desktop:update-get-channel', async (event) => {
    requireTrustedSender(event)
    const releaseConfig = readReleaseConfig()
    return { ok: true, channel: selectedUpdateChannel(), allowSwitch: releaseConfig.allowChannelSwitch !== false }
  })

  ipcMain.handle('desktop:update-set-channel', async (event, payload) => {
    requireTrustedSender(event)
    const releaseConfig = readReleaseConfig()
    if (releaseConfig.allowChannelSwitch === false) return { ok: false, message: 'Переключение канала отключено издателем.' }
    const channel = String(payload?.channel || '').toLowerCase()
    if (!['stable', 'beta'].includes(channel)) return { ok: false, message: 'Неизвестный канал обновлений.' }
    writeDesktopConfig({ updateChannel: channel })
    return { ok: true, channel, restartRequired: true, message: 'Канал сохранён. Перезапусти приложение.' }
  })

  ipcMain.handle('desktop:create-update-backup', async (event) => {
    requireTrustedSender(event)
    return createPreUpdateBackup()
  })

  ipcMain.handle('desktop:get-info', async (event) => {
    requireTrustedSender(event)
    const { dataDir, projectsDir } = runtimePaths()
    return {
      ok: true,
      desktop: true,
      packaged: app.isPackaged,
      appVersion: app.getVersion(),
      engineVersionPrefix: EXPECTED_APP_VERSION_PREFIX,
      appUrl,
      dataDir,
      projectsDir,
      logsDir: app.getPath('logs'),
      platform: process.platform,
      arch: process.arch,
    }
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 950,
    minWidth: 1050,
    minHeight: 700,
    show: false,
    backgroundColor: '#0b0d12',
    title: 'Highlight Studio',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      devTools: !app.isPackaged,
      spellcheck: false,
    },
  })
  mainWindow.removeMenu()
  mainWindow.once('ready-to-show', () => mainWindow.show())
  mainWindow.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    if (closePromptActive) return
    closePromptActive = true
    ;(async () => {
      try {
        const probe = await healthCheck(900)
        if (!probe.activeJobs) {
          await shutdownApplication()
          return
        }
        const answer = await dialog.showMessageBox(mainWindow, {
          type: 'warning',
          buttons: ['Продолжить работу', 'Закрыть и остановить задачи'],
          defaultId: 0,
          cancelId: 0,
          title: 'Идёт обработка видео',
          message: 'Закрытие приложения остановит текущую задачу.',
          detail: 'Прогресс и контрольные точки сохранятся. При следующем запуске проект можно будет продолжить.',
        })
        if (answer.response === 1) {
          await shutdownApplication()
        }
      } finally {
        closePromptActive = false
      }
    })().catch((error) => {
      appendDesktopLog(`Close guard failed: ${error.stack || error}`)
      closePromptActive = false
    })
  })
  mainWindow.on('closed', () => { mainWindow = null })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (sameOrigin(url, appUrl)) return { action: 'allow' }
    if (isSafeExternalUrl(url)) {
      shell.openExternal(url).catch((error) => appendDesktopLog(`External link failed: ${error.message || error}`))
    } else {
      appendDesktopLog(`Blocked unsafe external URL: ${url}`)
    }
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!sameOrigin(url, appUrl)) event.preventDefault()
  })
  mainWindow.loadURL(`${appUrl}/?hs_release=${encodeURIComponent(EXPECTED_APP_VERSION_PREFIX)}&design=${encodeURIComponent(EXPECTED_DESIGN_ID)}`)
}

function requestGracefulBackendShutdown(timeoutMs = 2500) {
  return new Promise((resolve) => {
    if (!ownsBackend || !backendProcess || !desktopShutdownToken) return resolve(false)
    const request = http.request(`${appUrl}/api/desktop/shutdown`, {
      method: 'POST',
      timeout: timeoutMs,
      headers: { 'X-Desktop-Shutdown-Token': desktopShutdownToken },
    }, (response) => {
      response.resume()
      response.on('end', () => resolve(response.statusCode === 200))
    })
    request.on('timeout', () => { request.destroy(); resolve(false) })
    request.on('error', () => resolve(false))
    request.end()
  })
}

function waitForProcessExit(processRef, timeoutMs = 9000) {
  return new Promise((resolve) => {
    if (!processRef || processRef.exitCode !== null) return resolve(true)
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      processRef.removeListener('exit', onExit)
      resolve(value)
    }
    const onExit = () => finish(true)
    const timer = setTimeout(() => finish(false), timeoutMs)
    processRef.once('exit', onExit)
  })
}

function forceStopBackend(processRef) {
  if (!processRef || processRef.exitCode !== null) return
  appendDesktopLog('Graceful backend shutdown timed out; forcing child-process cleanup')
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(processRef.pid), '/t', '/f'], { windowsHide: true })
  } else {
    try { processRef.kill('SIGKILL') } catch (_) {}
  }
}

async function stopBackendGracefully() {
  if (!ownsBackend || !backendProcess) return
  const processRef = backendProcess
  appendDesktopLog('Requesting graceful shutdown of owned backend')
  const requested = await requestGracefulBackendShutdown()
  if (!requested) appendDesktopLog('Graceful shutdown request was not acknowledged')
  const exited = requested ? await waitForProcessExit(processRef, 10000) : false
  if (!exited) forceStopBackend(processRef)
  backendProcess = null
  ownsBackend = false
  desktopShutdownToken = ''
}

function shutdownApplication(mode = 'quit') {
  if (shutdownPromise) return shutdownPromise
  shutdownPromise = (async () => {
    quitting = true
    await stopBackendGracefully()
    allowQuit = true
    if (mode === 'update') autoUpdater.quitAndInstall(false, true)
    else app.quit()
  })().catch((error) => {
    appendDesktopLog(`Shutdown failed: ${error.stack || error}`)
    allowQuit = true
    app.quit()
  })
  return shutdownPromise
}

process.on('uncaughtException', (error) => {
  appendDesktopLog(`Uncaught exception: ${error.stack || error}`)
  appendDesktopCrash(error, 'desktop')
})
process.on('unhandledRejection', (error) => {
  appendDesktopLog(`Unhandled rejection: ${error?.stack || error}`)
  appendDesktopCrash(error, 'desktop')
})

const lock = app.requestSingleInstanceLock()
if (!lock) app.quit()
else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(async () => {
    try {
      session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
      session.defaultSession.setPermissionCheckHandler(() => false)
      await ensureBackend()
      registerDesktopIpc()
      createWindow()
      configureUpdater()
    } catch (error) {
      appendDesktopLog(`Startup failed: ${error.stack || error}`)
      dialog.showErrorBox('Highlight Studio не запустился', String(error.message || error))
      allowQuit = true
      app.quit()
    }
  })
}

app.on('before-quit', (event) => {
  if (!allowQuit && ownsBackend && backendProcess) {
    event.preventDefault()
    shutdownApplication()
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && !shutdownPromise) shutdownApplication()
})
