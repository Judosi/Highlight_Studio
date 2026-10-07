import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const release = JSON.parse(fs.readFileSync(new URL('../public/release.json', import.meta.url), 'utf8'))

test('11.2.7 keeps quality-first hardware defaults and Qwen3 8B', () => {
  assert.equal(release.version, '11.2.7')
  assert.equal(release.app_version, 'v11.2.7-quality-recovery-audit')
  assert.match(app, /hardware_quality_guard_enabled:\s*true/)
  assert.match(app, /hardware_decode:\s*'auto'/)
  assert.match(app, /micro_batch_size:\s*8/)
  assert.match(app, /text_model:\s*'qwen3:8b'/)
})

test('active jobs use lightweight progress polling then full refresh on terminal state', () => {
  assert.match(app, /\/progress-state/)
  assert.match(app, /busyState \? await refreshProgress\(project\.id\) : await refreshAll\(project\.id\)/)
  assert.match(app, /data\?\.terminal\) await refreshAll\(project\.id\)/)
})
