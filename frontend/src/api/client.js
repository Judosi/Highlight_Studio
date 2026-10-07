export function defaultApiBase(location = typeof window === 'undefined' ? null : window.location) {
  if (!location) return 'http://127.0.0.1:8000/api'
  const { protocol, hostname, port, origin } = location
  const loopback = hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '::1'
  const viteDev = loopback && (port === '5173' || port === '4173')
  if ((protocol === 'http:' || protocol === 'https:') && !viteDev) return `${origin}/api`
  return 'http://127.0.0.1:8000/api'
}

export const API = import.meta.env?.VITE_API_BASE || defaultApiBase()
export const AUTH_TOKEN_KEY = 'highlightStudioLocalToken'
export const LOCAL_SETTINGS_KEY = 'highlightStudioDraftSettingsV10144'
export const LEGACY_LOCAL_SETTINGS_KEYS = ['highlightStudioDraftSettingsV10143', 'highlightStudioDraftSettingsV10141', 'highlightStudioDraftSettingsV10140', 'highlightStudioDraftSettingsV10134', 'highlightStudioDraftSettingsV998']

function browserStorage() {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch (_) {
    return null
  }
}

function sensitiveStorageKey(key) {
  const lowered = String(key).toLowerCase()
  return [
    'api_key',
    'authorization',
    'cookie',
    'password',
    'secret',
    'token',
  ].includes(lowered) || ['_api_key', '_password', '_secret', '_token'].some(suffix => lowered.endsWith(suffix))
}

export function sanitizeSettingsForStorage(value) {
  if (Array.isArray(value)) return value.map(sanitizeSettingsForStorage)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !sensitiveStorageKey(key))
      .map(([key, item]) => [key, sanitizeSettingsForStorage(item)])
  )
}

export function clearLegacyClientSecrets(storage = browserStorage()) {
  if (!storage) return
  try {
    storage.removeItem(AUTH_TOKEN_KEY)
  } catch (_) {}
}

export function loadSafeLocalSettings(storage = browserStorage()) {
  if (!storage) return {}
  let loaded = {}
  try {
    const sourceKey = [LOCAL_SETTINGS_KEY, ...LEGACY_LOCAL_SETTINGS_KEYS].find(key => storage.getItem(key))
    if (sourceKey) loaded = sanitizeSettingsForStorage(JSON.parse(storage.getItem(sourceKey) || '{}'))
    storage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(loaded))
    for (const key of LEGACY_LOCAL_SETTINGS_KEYS) storage.removeItem(key)
    clearLegacyClientSecrets(storage)
  } catch (_) {
    loaded = {}
    try {
      storage.removeItem(LOCAL_SETTINGS_KEY)
      for (const key of LEGACY_LOCAL_SETTINGS_KEYS) storage.removeItem(key)
      clearLegacyClientSecrets(storage)
    } catch (_) {}
  }
  return loaded
}

export function persistSafeLocalSettings(value, storage = browserStorage()) {
  const safe = sanitizeSettingsForStorage(value || {})
  if (storage) {
    try {
      storage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(safe))
      for (const key of LEGACY_LOCAL_SETTINGS_KEYS) storage.removeItem(key)
      clearLegacyClientSecrets(storage)
    } catch (_) {}
  }
  return safe
}

export function clearLocalSettings(storage = browserStorage()) {
  if (!storage) return
  try {
    storage.removeItem(LOCAL_SETTINGS_KEY)
    for (const key of LEGACY_LOCAL_SETTINGS_KEYS) storage.removeItem(key)
    clearLegacyClientSecrets(storage)
  } catch (_) {}
}

function cookieValue(name) {
  if (typeof document === 'undefined') return ''
  const prefix = `${name}=`
  return document.cookie.split(';').map(x => x.trim()).find(x => x.startsWith(prefix))?.slice(prefix.length) || ''
}

export async function apiFetch(url, options = {}) {
  clearLegacyClientSecrets()
  const headers = new Headers(options.headers || {})
  const method = String(options.method || 'GET').toUpperCase()
  const csrf = decodeURIComponent(cookieValue('highlight_studio_csrf'))
  if (csrf && !['GET','HEAD','OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrf)
  return fetch(url, { ...options, headers, credentials: 'include' })
}

export async function safeJsonResponse(response, fallback = null) {
  const contentType = response.headers?.get?.('content-type') || ''
  const text = await response.text()
  if (!text) return fallback
  try {
    return JSON.parse(text)
  } catch (_) {
    const message = text.slice(0, 800) || `HTTP ${response.status}`
    return {
      ok: false,
      message,
      non_json_response: true,
      status: response.status,
      content_type: contentType,
    }
  }
}

export function errorFromPayload(payload, fallback = 'Ошибка запроса') {
  if (!payload) return fallback
  const detail = payload.detail
  if (detail && typeof detail === 'object') {
    const firstError = Array.isArray(detail.errors) && detail.errors.length
      ? (detail.errors[0]?.label || detail.errors[0]?.name || detail.errors[0]?.message || '')
      : ''
    return [
      detail.message,
      detail.checked_path ? `Проверено: ${detail.checked_path}` : '',
      detail.recommendation || detail.hint || '',
      firstError ? `Проблема: ${firstError}` : '',
    ].filter(Boolean).join(' · ')
  }
  if (typeof detail === 'string') return detail
  return payload.message || payload.error || fallback
}

export function authQuery() {
  // File/video links use the HttpOnly auth cookie set by /api/health.
  // Never place local secrets into URLs, browser history, logs or Referer.
  return ''
}
