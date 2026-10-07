import test from 'node:test'
import assert from 'node:assert/strict'

import {
  AUTH_TOKEN_KEY,
  LEGACY_LOCAL_SETTINGS_KEYS,
  LOCAL_SETTINGS_KEY,
  defaultApiBase,
  loadSafeLocalSettings,
  persistSafeLocalSettings,
  sanitizeSettingsForStorage,
} from '../src/api/client.js'

class MemoryStorage {
  constructor(entries = {}) {
    this.values = new Map(Object.entries(entries))
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null
  }

  setItem(key, value) {
    this.values.set(key, String(value))
  }

  removeItem(key) {
    this.values.delete(key)
  }
}

test('production HTTP locations use the deployed same-origin API', () => {
  assert.equal(defaultApiBase({
    protocol: 'https:',
    hostname: 'studio.example.com',
    port: '',
    origin: 'https://studio.example.com',
  }), 'https://studio.example.com/api')
  assert.equal(defaultApiBase({
    protocol: 'http:',
    hostname: '127.0.0.1',
    port: '8133',
    origin: 'http://127.0.0.1:8133',
  }), 'http://127.0.0.1:8133/api')
  assert.equal(defaultApiBase({
    protocol: 'http:',
    hostname: '127.0.0.1',
    port: '5173',
    origin: 'http://127.0.0.1:5173',
  }), 'http://127.0.0.1:8000/api')
})

test('settings persistence recursively removes client secrets', () => {
  const safe = sanitizeSettingsForStorage({
    target_minutes: 30,
    openai_api_key: 'sk-secret',
    nested: {
      service_token: 'token-secret',
      password: 'password-secret',
      quality: 'high',
    },
  })
  assert.deepEqual(safe, { target_minutes: 30, nested: { quality: 'high' } })
})

test('legacy local settings migrate without secrets and old tokens are erased', () => {
  const legacyKey = LEGACY_LOCAL_SETTINGS_KEYS[0]
  const storage = new MemoryStorage({
    [legacyKey]: JSON.stringify({ target_minutes: 20, openai_api_key: 'sk-legacy' }),
    [AUTH_TOKEN_KEY]: 'legacy-local-auth-token',
  })
  assert.deepEqual(loadSafeLocalSettings(storage), { target_minutes: 20 })
  assert.equal(storage.getItem(legacyKey), null)
  assert.equal(storage.getItem(AUTH_TOKEN_KEY), null)
  assert.deepEqual(JSON.parse(storage.getItem(LOCAL_SETTINGS_KEY)), { target_minutes: 20 })

  persistSafeLocalSettings({ target_minutes: 25, nested: { api_key: 'nope' } }, storage)
  assert.deepEqual(JSON.parse(storage.getItem(LOCAL_SETTINGS_KEY)), { target_minutes: 25, nested: {} })
})
