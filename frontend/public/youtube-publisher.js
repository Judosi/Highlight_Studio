const API = '/api'
const $ = id => document.getElementById(id)
const state = { projects: [], outputs: [], creatorPack: null, status: null, projectId: '', report: null }

function showNotice(message, type = 'ok') {
  const el = $('notice')
  el.textContent = message
  el.className = `notice ${type}`
  clearTimeout(showNotice.timer)
  showNotice.timer = setTimeout(() => { el.className = 'notice hidden' }, 7000)
}

function cookieValue(name) {
  const prefix = `${name}=`
  return document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(prefix))?.slice(prefix.length) || ''
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {})
  const method = String(options.method || 'GET').toUpperCase()
  const csrf = decodeURIComponent(cookieValue('highlight_studio_csrf'))
  if (csrf && !['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`${API}${path}`, { ...options, headers, credentials: 'include' })
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : (data?.detail?.message || data?.message || `HTTP ${response.status}`))
  }
  return data
}

function tags() {
  return $('tagsInput').value.split(',').map(value => value.trim()).filter(Boolean)
}

function currentOutput() {
  return state.outputs.find(item => item.path === $('fileSelect').value)
}

function baseItem(file, title) {
  return {
    file_path: file.path,
    title: title || file.name.replace(/\.[^.]+$/, ''),
    description: $('descriptionInput').value,
    tags: tags(),
    privacy_status: $('privacySelect').value,
    category_id: $('categorySelect').value,
    notify_subscribers: $('notifyInput').checked,
    made_for_kids: $('kidsInput').checked,
    contains_synthetic_media: $('syntheticInput').checked,
    publish_at: '',
  }
}

function setBusy(value) {
  for (const id of ['connectButton', 'disconnectButton', 'uploadSelectedButton', 'uploadShortsButton']) $(id).disabled = value
}

async function refreshChannel(refresh = false) {
  try {
    state.status = await api(`/youtube/status${refresh ? '?refresh=true' : ''}`)
    const connected = Boolean(state.status.connected)
    $('channelBadge').textContent = connected ? (state.status.channel_title || 'Подключён') : 'Не подключён'
    $('channelBadge').className = `badge ${connected ? 'ok' : 'warn'}`
    $('channelInfo').textContent = connected
      ? `Канал: ${state.status.channel_title} · ${state.status.channel_id}`
      : (state.status.credentials_configured ? 'OAuth JSON сохранён. Нажмите «Подключить канал».' : 'Загрузите OAuth Client JSON типа Desktop app.')
    $('connectButton').disabled = !state.status.credentials_configured || connected
    $('disconnectButton').disabled = !connected
    $('uploadSelectedButton').disabled = !connected
    $('uploadShortsButton').disabled = !connected || !state.outputs.some(item => item.kind === 'short')
    if (state.status.error) showNotice(state.status.error, 'error')
  } catch (error) {
    showNotice(error.message, 'error')
  }
}

async function loadProjects() {
  await fetch(`${API}/health`, { credentials: 'include' })
  state.projects = await api('/projects')
  const select = $('projectSelect')
  select.innerHTML = '<option value="">Выберите проект</option>' + state.projects
    .map(project => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name || project.id)}</option>`)
    .join('')
  if (state.projects.length) {
    select.value = state.projects[0].id
    await loadProject(select.value)
  }
}

async function loadProject(id) {
  state.projectId = id
  state.outputs = []
  state.creatorPack = null
  $('fileSelect').innerHTML = '<option value="">Загрузка...</option>'
  if (!id) return
  const [outputs, pack] = await Promise.all([
    api(`/projects/${id}/outputs`),
    api(`/projects/${id}/creator-pack`).catch(() => null),
  ])
  state.outputs = (outputs || []).filter(item => item.kind === 'video' || item.kind === 'short')
  state.creatorPack = pack
  const select = $('fileSelect')
  select.innerHTML = '<option value="">Выберите видео</option>' + state.outputs
    .map(item => `<option value="${escapeHtml(item.path)}">${item.kind === 'short' ? 'Shorts' : 'Видео'} · ${escapeHtml(item.name)} · ${item.size_mb} MB</option>`)
    .join('')
  const preferred = state.outputs.find(item => item.path === 'outputs/highlight_final.mp4' || item.path === 'highlight_final.mp4') || state.outputs[0]
  if (preferred) {
    select.value = preferred.path
    updateFileStats()
    fillMetadata(false)
  }
  $('uploadShortsButton').textContent = `Загрузить все Shorts (${state.outputs.filter(item => item.kind === 'short').length})`
  await refreshReport()
  await refreshChannel(false)
}

function updateFileStats() {
  const file = currentOutput()
  $('fileStats').textContent = file ? `${file.kind === 'short' ? 'Shorts' : 'Обычное видео'} · ${file.path} · ${file.size_mb} MB` : 'Выберите готовый файл.'
}

function fillMetadata(notify = true) {
  const file = currentOutput()
  if (!file) return
  const isShort = file.kind === 'short'
  const pack = state.creatorPack || {}
  const title = isShort ? (pack.short_titles?.[0] || pack.titles?.[0] || file.name) : (pack.titles?.[0] || file.name)
  $('titleInput').value = String(title || '').slice(0, 100)
  $('descriptionInput').value = isShort ? (pack.short_summary || pack.description || '') : (pack.description || '')
  $('tagsInput').value = (pack.tags || pack.hashtags || []).map(value => String(value).replace(/^#/, '')).join(', ')
  if (notify) showNotice('Метаданные перенесены из Creator Pack.')
}

async function saveCredentials(file) {
  setBusy(true)
  try {
    const form = new FormData()
    form.append('file', file)
    await api('/youtube/credentials', { method: 'POST', body: form })
    showNotice('OAuth JSON сохранён. Теперь подключите канал.')
    await refreshChannel()
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    setBusy(false)
    $('credentialsFile').value = ''
  }
}

async function connect() {
  setBusy(true)
  try {
    const data = await api('/youtube/connect', { method: 'POST' })
    window.open(data.authorization_url, '_blank', 'noopener,noreferrer')
    showNotice('Подтвердите доступ в Google, затем обновите статус.')
    setTimeout(() => refreshChannel(true), 4000)
    setTimeout(() => refreshChannel(true), 9000)
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    setBusy(false)
  }
}

async function disconnect() {
  setBusy(true)
  try {
    await api('/youtube/disconnect', { method: 'POST' })
    showNotice('Канал отключён.')
    await refreshChannel()
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    setBusy(false)
  }
}

async function upload(items) {
  if (!state.projectId) return showNotice('Выберите проект.', 'error')
  setBusy(true)
  try {
    const data = await api(`/projects/${state.projectId}/youtube-upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    })
    if (data.started === false) throw new Error(data.message || 'Другая задача уже выполняется')
    showNotice('Загрузка запущена.')
    pollJob()
  } catch (error) {
    showNotice(error.message, 'error')
    setBusy(false)
  }
}

async function uploadSelected() {
  const file = currentOutput()
  if (!file) return showNotice('Выберите видео.', 'error')
  const title = $('titleInput').value.trim()
  if (!title) return showNotice('Введите название.', 'error')
  await upload([baseItem(file, title)])
}

async function uploadShorts() {
  const shorts = state.outputs.filter(item => item.kind === 'short')
  if (!shorts.length) return showNotice('Сначала создайте Shorts.', 'error')
  const pack = state.creatorPack || {}
  const titles = pack.short_titles || pack.titles || []
  const items = shorts.map((file, index) => baseItem(
    file,
    String(titles[index] || titles[index % Math.max(1, titles.length)] || `${file.name.replace(/\.[^.]+$/, '')} ${index + 1}`).slice(0, 100)
  ))
  items.forEach(item => { item.notify_subscribers = false })
  await upload(items)
}

async function pollJob() {
  if (!state.projectId) return
  try {
    const status = await api(`/projects/${state.projectId}/status`)
    $('jobTitle').textContent = status.state === 'running' || status.state === 'queued' ? 'Загрузка выполняется' : (status.message || 'Статус')
    $('jobMessage').textContent = status.message || status.stage || ''
    const percent = Math.max(0, Math.min(100, Number(status.progress || 0)))
    $('progressBar').style.width = `${percent}%`
    $('uploadProgress').setAttribute('aria-valuenow', String(Math.round(percent)))
    if (status.state === 'running' || status.state === 'queued') {
      setTimeout(pollJob, 1500)
    } else {
      setBusy(false)
      await refreshReport()
      if (status.state === 'error') showNotice(status.message || 'Ошибка загрузки', 'error')
    }
  } catch (error) {
    setBusy(false)
    showNotice(error.message, 'error')
  }
}

async function refreshReport() {
  if (!state.projectId) return
  try {
    const report = await api(`/projects/${state.projectId}/youtube-upload-report`)
    state.report = report
    renderResults(report)
  } catch {
    renderResults(null)
  }
}

function safeYoutubeUrl(value) {
  try {
    const url = new URL(String(value || ''))
    const host = url.hostname.toLowerCase()
    if (url.protocol === 'https:' && (host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com'))) return url.href
  } catch {}
  return '#'
}

function renderResults(report) {
  const element = $('results')
  const uploaded = report?.uploaded || []
  const failed = report?.failed || []
  if (!uploaded.length && !failed.length) {
    element.innerHTML = '<p class="muted">История загрузки пока пуста.</p>'
    return
  }
  element.innerHTML = uploaded
    .map(item => `<div class="result">✓ <a target="_blank" rel="noreferrer" href="${escapeHtml(safeYoutubeUrl(item.youtube_url))}">${escapeHtml(item.title)}</a> · ${escapeHtml(item.privacy_status)}</div>`)
    .join('') + failed
    .map(item => `<div class="result">✕ ${escapeHtml(item.file_path)}: ${escapeHtml(item.error)}</div>`)
    .join('')
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[character])
}

$('credentialsFile').addEventListener('change', event => {
  const file = event.target.files?.[0]
  if (file) saveCredentials(file)
})
$('connectButton').addEventListener('click', connect)
$('refreshChannelButton').addEventListener('click', () => refreshChannel(true))
$('disconnectButton').addEventListener('click', disconnect)
$('projectSelect').addEventListener('change', event => loadProject(event.target.value))
$('fileSelect').addEventListener('change', () => {
  updateFileStats()
  fillMetadata(false)
})
$('fillMetadataButton').addEventListener('click', () => fillMetadata(true))
$('uploadSelectedButton').addEventListener('click', uploadSelected)
$('uploadShortsButton').addEventListener('click', uploadShorts)
$('refreshReportButton').addEventListener('click', async () => {
  await refreshReport()
  await pollJob()
})

loadProjects().then(() => refreshChannel(false)).catch(error => showNotice(error.message, 'error'))
