import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const auth = readFileSync(new URL('../src/features/auth/AuthScreen.jsx', import.meta.url), 'utf8')
const team = readFileSync(new URL('../src/features/auth/TeamAccessPanel.jsx', import.meta.url), 'utf8')
const account = readFileSync(new URL('../src/features/auth/AccountAccessPanel.jsx', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')

test('web accounts UI includes login, registration, recovery and email verification', () => {
  assert.match(auth, /mode === 'login' \? 'login' : 'register'/)
  assert.match(auth, /\$\{API\}\/auth\/\$\{action\}/)
  assert.match(auth, /request-password-reset/)
  assert.match(auth, /reset-password/)
  assert.match(auth, /verify-email/)
  assert.match(auth, /autoComplete=\{mode === 'login' \? 'current-password'/)
})

test('project team panel exposes owner, editor and viewer roles', () => {
  assert.match(team, /owner/)
  assert.match(team, /editor/)
  assert.match(team, /viewer/)
  assert.match(team, /Доступ к проекту/)
  assert.match(team, /Удалить/)
})

test('account panel exposes password change and admin audit controls', () => {
  assert.match(account, /change-password/)
  assert.match(account, /admin\/users/)
  assert.match(account, /admin\/audit/)
  assert.match(account, /Выйти/)
})

test('main app gates the workspace behind web authentication and project access', () => {
  assert.match(app, /\$\{API\}\/auth\/status/)
  assert.match(app, /AuthScreen/)
  assert.match(app, /projectAccess/)
  assert.match(app, /TeamAccessPanel/)
})

test('account security UI manages active sessions and password visibility', () => {
  assert.match(account, /\/auth\/sessions/)
  assert.match(account, /logout-all/)
  assert.match(account, /Активные устройства/)
  assert.match(auth, /Показать пароль/)
  assert.match(auth, /passwordStrength/)
})
