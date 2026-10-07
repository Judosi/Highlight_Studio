import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const client = fs.readFileSync(new URL('../src/api/client.js', import.meta.url), 'utf8')

test('analysis launch does not block the POST behind dashboard refresh', () => {
  const start = app.indexOf('async function runOneClickPipeline()')
  const end = app.indexOf('async function runSimple(', start)
  assert.ok(start >= 0 && end > start)
  const body = app.slice(start, end)
  assert.ok(body.includes("startOptimisticJob('one_click')"))
  assert.ok(body.includes("saveSettings(prepared, { refresh: false, announce: false })"))
  assert.ok(body.includes('`/one-click`') || body.includes('/one-click'))
  assert.ok(body.indexOf("saveSettings(prepared, { refresh: false, announce: false })") < body.indexOf('/one-click'))
  assert.ok(body.indexOf('/one-click') < body.indexOf('await refreshAll(projectId, scope)'))
})

test('analysis button remains clickable enough to explain stale source/format state', () => {
  assert.match(app, /onClick=\{runOneClickPipeline\}\s+disabled=\{!project\}>Начать анализ<\/button>/)
  assert.ok(app.includes("title: 'Источник ещё не готов'"))
  assert.ok(app.includes("title: 'Сначала выбери формат'"))
})

test('legacy safe analysis profile is normalized and is no longer offered in selects', () => {
  assert.ok(app.includes("base.analysis_profile === 'safe' ? 'fast' : 'balanced'"))
  assert.ok(!app.includes('<option value="safe">'))
})

test('structured preflight errors expose recommendation and first failed check', () => {
  assert.ok(client.includes('detail.recommendation || detail.hint'))
  assert.ok(client.includes("firstError ? `Проблема: ${firstError}` : ''"))
})
