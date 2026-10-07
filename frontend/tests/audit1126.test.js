import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createReviewRequestGuard } from '../src/lib/reviewRequestGuard.js'

const source = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
function runtime() {
  const guard = createReviewRequestGuard(); guard.select('A')
  const applied = [], notices = [], errors = [], requests = []
  const ctx = vm.createContext({
    project: { id: 'A' }, API: '/api', reviewRequestGuardRef: { current: guard },
    captureProjectScope: () => guard.read('scope'), isProjectScopeCurrent: token => guard.current(token),
    safeJsonResponse: r => r.json(), errorFromPayload: (_, fallback) => fallback,
    setError: e => errors.push(e), notifyError: (title, e) => errors.push(e.message),
    setNotice: n => notices.push(n), applyDashboardState: d => applied.push(d), setCreatorPack: d => applied.push(d),
    apiFetch: () => { const req = deferred(); requests.push(req); return req.promise },
  })
  // Execute the real source functions with controlled IO. Never bind tests to
  // generated minifier symbols; production DOM behavior is tested separately.
  for (const name of ['refreshAll', 'recomputeCreatorPack']) {
    const start = source.indexOf(`  async function ${name}(`)
    const end = source.indexOf('\n  }', start) + 4
    assert.ok(start >= 0 && end > start)
    vm.runInContext(source.slice(start, end), ctx)
  }
  return { ctx, guard, applied, notices, errors, requests }
}
const response = data => ({ ok: true, json: async () => data })

test('dashboard rejects responses started before a saved edit', async () => {
  const r = runtime(); const poll = r.ctx.refreshAll('A')
  const edit = r.guard.begin('segments'); r.guard.end(edit)
  r.requests[0].resolve(response({ segments: ['old'] })); await poll
  assert.equal(r.applied.length, 0)
})
test('dashboard accepts only latest same-project request', async () => {
  const r = runtime(); const first = r.ctx.refreshAll('A'); const second = r.ctx.refreshAll('A')
  r.requests[1].resolve(response({ segments: ['new'] })); await second
  r.requests[0].resolve(response({ segments: ['old'] })); await first
  assert.equal(r.applied.length, 1); assert.equal(r.applied[0].segments[0], 'new')
})
test('creator-pack handles same-tick double click and reports fallback', async () => {
  const r = runtime(); const first = r.ctx.recomputeCreatorPack(); await r.ctx.recomputeCreatorPack()
  assert.equal(r.requests.length, 1); assert.equal(r.notices[0].type, 'info')
  r.requests[0].resolve(response({ ok: true, warning: 'AI недоступен: локальный текст', source: 'local' }))
  await first; assert.equal(r.notices.at(-1).type, 'warning')
})
test('creator-pack never announces failure as successful metadata', async () => {
  const r = runtime(); const first = r.ctx.recomputeCreatorPack()
  r.requests[0].resolve(response({ ok: false, error: 'Нет фактов' })); await first
  assert.equal(r.applied.length, 0); assert.deepEqual(r.errors, ['Нет фактов'])
  assert.ok(!r.notices.some(n => n.type === 'success'))
})
test('creator-pack response cannot cross A to B to A navigation', async () => {
  const r = runtime(); const first = r.ctx.recomputeCreatorPack(); r.guard.select('B'); r.guard.select('A')
  r.requests[0].resolve(response({ ok: true, titles: ['stale'] })); await first
  assert.equal(r.applied.length, 0)
})
test('segment mutation lock is synchronous and released only by its owner', () => {
  const g = createReviewRequestGuard(); g.select('A')
  const first = g.begin('segments'); assert.ok(first); assert.equal(g.begin('segments'), null)
  g.end({ ...first }); assert.equal(g.begin('segments'), null)
  g.end(first); assert.ok(g.begin('segments'))
})
