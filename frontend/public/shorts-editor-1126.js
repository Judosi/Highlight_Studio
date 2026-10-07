(() => {
  'use strict'

  // Production compatibility bridge for the 11.2.6 frontend bundle.
  // IMPORTANT: this module never replaces window.fetch.  It reads the same
  // project id kept by the React app in localStorage and polls only while
  // Shorts Studio is visible.  The source React app contains the native V5.1
  // editor; this bridge keeps packaged/offline builds feature-complete when a
  // Vite rebuild is unavailable.
  const API = '/api'
  const state = {
    projectId: '',
    candidates: [],
    settings: {},
    outputs: [],
    signature: '',
    requestInFlight: false,
    lastError: '',
  }

  function cookieValue(name) {
    const prefix = `${name}=`
    const value = document.cookie.split(';').map(x => x.trim()).find(x => x.startsWith(prefix))
    return value ? value.slice(prefix.length) : ''
  }

  async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {})
    const method = String(options.method || 'GET').toUpperCase()
    const csrf = decodeURIComponent(cookieValue('highlight_studio_csrf'))
    if (csrf && !['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrf)
    return fetch(url, { ...options, headers, credentials: 'include' })
  }

  async function responseJson(response) {
    const text = await response.text()
    if (!text) return null
    try { return JSON.parse(text) } catch (_) { return { ok: false, message: text.slice(0, 500) } }
  }

  function currentProjectId() {
    try { return String(localStorage.getItem('highlightStudioLastProject') || '').trim() } catch (_) { return '' }
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[char])
  }

  function numeric(value, fallback = 0) {
    const result = Number(value)
    return Number.isFinite(result) ? result : fallback
  }

  function shortSignature(projectId, candidates, settings, outputs) {
    const reduced = {
      projectId,
      candidates: (candidates || []).map(item => [
        item?.title, item?.start, item?.end, item?.caption_text, item?.text_preview,
        item?.reframe_mode, item?.short_score, item?.funny_score, item?.hook_score,
      ]),
      settings: {
        shorts_count: settings?.shorts_count,
        shorts_caption_quality: settings?.shorts_caption_quality,
        shorts_caption_font_size: settings?.shorts_caption_font_size,
        shorts_caption_max_words: settings?.shorts_caption_max_words,
        shorts_recognition_dictionary: settings?.shorts_recognition_dictionary,
        shorts_reframe_mode: settings?.shorts_reframe_mode,
        shorts_funny_search_enabled: settings?.shorts_funny_search_enabled,
        shorts_emotion_events_enabled: settings?.shorts_emotion_events_enabled,
        shorts_precise_alignment: settings?.shorts_precise_alignment,
        shorts_dynamic_captions: settings?.shorts_dynamic_captions,
      },
      outputs: (outputs || []).map(item => [item?.name, item?.path, item?.size_mb]),
    }
    return JSON.stringify(reduced)
  }

  function setStatus(message, type = '') {
    const node = document.querySelector('[data-shorts-v51-status]')
    if (!node) return
    node.textContent = message || ''
    node.dataset.type = type
  }

  async function refresh({ force = false } = {}) {
    const host = document.querySelector('.shortsStudioCard')
    const projectId = currentProjectId()
    if (!host || !projectId || state.requestInFlight) return
    if (projectId !== state.projectId) {
      state.projectId = projectId
      state.signature = ''
      state.candidates = []
      state.settings = {}
      state.outputs = []
      removeBridgeUi()
    }
    state.requestInFlight = true
    try {
      const response = await apiFetch(`${API}/projects/${encodeURIComponent(projectId)}/dashboard-state`)
      const payload = await responseJson(response)
      if (!response.ok || !payload || payload.ok === false) {
        throw new Error(payload?.message || payload?.error || `HTTP ${response.status}`)
      }
      const candidates = Array.isArray(payload.factory?.shorts) ? payload.factory.shorts : []
      const settings = payload.project?.settings && typeof payload.project.settings === 'object' ? payload.project.settings : {}
      const outputs = Array.isArray(payload.outputs) ? payload.outputs : []
      const signature = shortSignature(projectId, candidates, settings, outputs)
      state.candidates = candidates
      state.settings = settings
      state.outputs = outputs
      state.lastError = ''
      if (force || signature !== state.signature) {
        state.signature = signature
        render()
      }
    } catch (error) {
      state.lastError = String(error?.message || error)
      setStatus(`Не удалось обновить Shorts Studio: ${state.lastError}`, 'error')
    } finally {
      state.requestInFlight = false
    }
  }

  function removeBridgeUi() {
    document.querySelectorAll('[data-shorts-v51-bridge]').forEach(node => node.remove())
  }

  async function saveSettings(patch, control) {
    if (!state.projectId) return
    const previousText = control?.textContent
    if (control) control.disabled = true
    setStatus('Сохраняю настройки Shorts…')
    try {
      const merged = { ...state.settings, ...patch }
      const response = await apiFetch(`${API}/projects/${encodeURIComponent(state.projectId)}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(merged),
      })
      const payload = await responseJson(response)
      if (!response.ok || !payload || payload.ok === false) throw new Error(payload?.message || payload?.error || `HTTP ${response.status}`)
      state.settings = { ...merged, ...(payload || {}) }
      state.signature = ''
      setStatus('Настройки Shorts сохранены.', 'ok')
      await refresh({ force: true })
    } catch (error) {
      setStatus(`Настройки не сохранены: ${error?.message || error}`, 'error')
    } finally {
      if (control) {
        control.disabled = false
        if (previousText != null && control.tagName === 'BUTTON') control.textContent = previousText
      }
    }
  }

  function settingField(label, control) {
    const wrapper = document.createElement('label')
    wrapper.className = 'shortV51Setting'
    const title = document.createElement('span')
    title.textContent = label
    wrapper.append(title, control)
    return wrapper
  }

  function selectControl(value, choices, onChange) {
    const select = document.createElement('select')
    choices.forEach(([key, label]) => {
      const option = document.createElement('option')
      option.value = key
      option.textContent = label
      select.appendChild(option)
    })
    select.value = String(value ?? choices[0]?.[0] ?? '')
    select.addEventListener('change', () => onChange(select.value, select))
    return select
  }

  function numberControl(value, min, max, onChange) {
    const input = document.createElement('input')
    input.type = 'number'
    input.min = String(min)
    input.max = String(max)
    input.value = String(value ?? min)
    input.addEventListener('change', () => onChange(numeric(input.value, min), input))
    return input
  }

  function textSettingControl(value, placeholder, onChange) {
    const input = document.createElement('input')
    input.type = 'text'
    input.value = String(value ?? '')
    input.placeholder = placeholder || ''
    input.addEventListener('change', () => onChange(input.value, input))
    return input
  }

  function checkboxControl(value, onChange) {
    const input = document.createElement('input')
    input.type = 'checkbox'
    input.checked = Boolean(value)
    input.addEventListener('change', () => onChange(input.checked, input))
    return input
  }

  function renderSettingsPanel(host) {
    // A future native rebuild already contains these controls. In that case the
    // bridge stays out of the way rather than duplicating the React UI.
    if (host.textContent.includes('Качество субтитров') && host.textContent.includes('Учитывать смех и эмоции')) return

    const panel = document.createElement('section')
    panel.className = 'shortV51Panel'
    panel.dataset.shortsV51Bridge = 'settings'
    const head = document.createElement('div')
    head.className = 'shortV51Head'
    head.innerHTML = '<div><b>Shorts Quality Engine V5.1</b><small>Точная речь, эмоции, Smart Face и безопасные субтитры</small></div><span class="safeBadge ok">V5.1</span>'
    const grid = document.createElement('div')
    grid.className = 'shortV51SettingsGrid'

    grid.append(
      settingField('Количество Shorts', numberControl(state.settings.shorts_count || 5, 1, 50, (value, control) => saveSettings({ shorts_count: value }, control))),
      settingField('Качество субтитров', selectControl(state.settings.shorts_caption_quality || 'high', [
        ['fast', 'Быстро'], ['high', 'Высоко — Whisper Small'], ['max', 'Максимум — + WhisperX'],
      ], (value, control) => saveSettings({ shorts_caption_quality: value }, control))),
      settingField('Размер субтитров', numberControl(state.settings.shorts_caption_font_size || 72, 48, 96, (value, control) => saveSettings({ shorts_caption_font_size: value }, control))),
      settingField('Слов одновременно', numberControl(state.settings.shorts_caption_max_words || 4, 3, 8, (value, control) => saveSettings({ shorts_caption_max_words: value }, control))),
      settingField('Кадрирование', selectControl(state.settings.shorts_reframe_mode || 'auto', [
        ['auto', 'Auto'], ['smart_face', 'Smart Face'], ['gameplay_facecam', 'Gameplay + Facecam'],
        ['blur_background', 'Полный кадр + фон'], ['smart_zoom', 'Smart Zoom'], ['center_crop', 'Center crop'], ['fit', 'Fit'],
      ], (value, control) => saveSettings({ shorts_reframe_mode: value }, control))),
      settingField('Словарь распознавания', textSettingControl(state.settings.shorts_recognition_dictionary || '', 'ники, имена, игры, сленг', (value, control) => saveSettings({ shorts_recognition_dictionary: value }, control))),
      settingField('Искать смешные моменты', checkboxControl(state.settings.shorts_funny_search_enabled !== false, (value, control) => saveSettings({ shorts_funny_search_enabled: value }, control))),
      settingField('Динамические captions', checkboxControl(state.settings.shorts_dynamic_captions !== false, (value, control) => saveSettings({ shorts_dynamic_captions: value }, control))),
      settingField('Смех и эмоции (optional SenseVoice)', checkboxControl(state.settings.shorts_emotion_events_enabled !== false, (value, control) => saveSettings({ shorts_emotion_events_enabled: value }, control))),
      settingField('Точное выравнивание (optional WhisperX)', checkboxControl(Boolean(state.settings.shorts_precise_alignment), (value, control) => saveSettings({ shorts_precise_alignment: value }, control)))
    )

    const status = document.createElement('div')
    status.className = 'shortV51Status'
    status.dataset.shortsV51Status = 'true'
    status.textContent = 'Настройки применяются только к нужным Shorts-стадиям; ASR-кэш инвалидируется выборочно.'
    panel.append(head, grid, status)
    const editor = host.querySelector('.shortCandidateEditor')
    if (editor) host.insertBefore(panel, editor)
    else {
      const action = host.querySelector('.pageActionBar')
      host.insertBefore(panel, action || null)
    }
  }

  function textInput(label, value, type, onInput) {
    const wrapper = document.createElement('label')
    if (type === 'textarea') wrapper.className = 'wide'
    const title = document.createElement('span')
    title.textContent = label
    const control = document.createElement(type === 'textarea' ? 'textarea' : 'input')
    if (type !== 'textarea') control.type = type || 'text'
    if (type === 'textarea') control.rows = 3
    control.value = value ?? ''
    control.addEventListener('input', () => onInput(type === 'number' ? numeric(control.value, 0) : control.value))
    wrapper.append(title, control)
    return wrapper
  }

  function safeZonePreview(source, index) {
    const preview = document.createElement('div')
    preview.className = 'shortCandidatePreview'
    const expectedName = `short_${String(index).padStart(2, '0')}.mp4`
    const output = (state.outputs || []).find(item => String(item?.name || '').toLowerCase() === expectedName)
    if (output?.url) {
      const video = document.createElement('video')
      video.controls = true
      video.preload = 'metadata'
      video.src = String(output.url)
      preview.appendChild(video)
    } else {
      const label = document.createElement('span')
      label.innerHTML = `<b>${escapeHtml(source?.title || 'Short')}</b><small>${numeric(source?.short_score, 0).toFixed(1)}/10 · safe-zone preview</small>`
      preview.appendChild(label)
    }
    const overlay = document.createElement('div')
    overlay.className = 'shortSafeZoneOverlay'
    overlay.setAttribute('aria-hidden', 'true')
    overlay.innerHTML = '<i class="shortSafeRight"></i><i class="shortSafeBottom"></i><i class="shortSafeCaption"><em>CAPTION SAFE</em></i>'
    preview.appendChild(overlay)
    return preview
  }

  async function regenerate(index, draft, button) {
    if (!state.projectId) return
    button.disabled = true
    const oldText = button.textContent
    button.textContent = 'Запускаю…'
    setStatus(`Перегенерирую только Short ${index}…`)
    try {
      const response = await apiFetch(`${API}/projects/${encodeURIComponent(state.projectId)}/shorts/${index}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: String(draft.title || `Short ${index}`).trim(),
          hook_text: String(draft.hook_text || draft.hook || ''),
          caption_text: String(draft.caption_text ?? draft.text_preview ?? ''),
          start: numeric(draft.start, 0),
          end: numeric(draft.end, 0),
          reframe_mode: String(draft.reframe_mode || state.settings.shorts_reframe_mode || 'auto'),
        }),
      })
      const payload = await responseJson(response)
      if (!response.ok || !payload || payload.ok === false) throw new Error(payload?.message || payload?.error || `HTTP ${response.status}`)
      setStatus(`Short ${index} запущен на атомарную перегенерацию. Остальные Shorts не изменяются.`, 'ok')
      button.textContent = `Short ${index} запущен`
      setTimeout(() => refresh({ force: true }), 900)
    } catch (error) {
      setStatus(`Short ${index}: ${error?.message || error}`, 'error')
      button.textContent = oldText
    } finally {
      button.disabled = false
    }
  }

  function renderEditor(host) {
    // Do not duplicate a native React editor in a future rebuilt bundle.
    if (host.querySelector('.shortCandidateEditor:not([data-shorts-v51-bridge])')) return
    const old = host.querySelector('[data-shorts-v51-bridge="editor"]')
    if (old) old.remove()
    if (!state.candidates.length) return

    const editor = document.createElement('div')
    editor.className = 'shortCandidateEditor'
    editor.dataset.shortsV51Bridge = 'editor'
    state.candidates.forEach((source, offset) => {
      const index = offset + 1
      const draft = { ...source }
      const card = document.createElement('article')
      card.className = 'shortCandidateCard'
      const preview = safeZonePreview(source, index)
      const fields = document.createElement('div')
      fields.className = 'shortCandidateFields'
      fields.append(
        textInput('Название', draft.title || `Short ${index}`, 'text', value => { draft.title = value }),
        textInput('Начало, сек', draft.start, 'number', value => { draft.start = value }),
        textInput('Конец, сек', draft.end, 'number', value => { draft.end = value }),
      )
      const reframe = settingField('Кадрирование', selectControl(draft.reframe_mode || state.settings.shorts_reframe_mode || 'auto', [
        ['auto', 'Auto'], ['smart_face', 'Smart Face'], ['gameplay_facecam', 'Gameplay + Facecam'],
        ['blur_background', 'Полный кадр + фон'], ['smart_zoom', 'Smart Zoom'], ['center_crop', 'Center crop'], ['fit', 'Fit'],
      ], value => { draft.reframe_mode = value }))
      fields.append(reframe)
      fields.append(textInput('Текст / субтитры', draft.caption_text ?? draft.text_preview ?? '', 'textarea', value => { draft.caption_text = value }))

      const actionBox = document.createElement('div')
      actionBox.className = 'shortV51Actions'
      const score = document.createElement('small')
      score.textContent = `Funny ${numeric(source.funny_score).toFixed(1)} · Hook ${numeric(source.hook_score).toFixed(1)} · Payoff ${numeric(source.payoff_score).toFixed(1)}`
      const button = document.createElement('button')
      button.className = 'primary'
      button.textContent = `Перегенерировать только Short ${index}`
      button.addEventListener('click', () => regenerate(index, draft, button))
      actionBox.append(score, button)
      card.append(preview, fields, actionBox)
      editor.appendChild(card)
    })
    const action = host.querySelector('.pageActionBar')
    host.insertBefore(editor, action || null)
  }

  function render() {
    const host = document.querySelector('.shortsStudioCard')
    if (!host || !state.projectId) return
    renderSettingsPanel(host)
    renderEditor(host)
  }

  const observer = new MutationObserver(() => {
    const id = currentProjectId()
    if (id !== state.projectId || (document.querySelector('.shortsStudioCard') && !document.querySelector('[data-shorts-v51-bridge]'))) {
      refresh({ force: true })
    }
  })
  observer.observe(document.documentElement, { childList: true, subtree: true })

  setInterval(() => refresh(), 3000)
  refresh({ force: true })
})()
