import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createReviewRequestGuard } from '../src/lib/reviewRequestGuard.js'

const source = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const start = source.indexOf('  async function removeSegment(i)')
const end = source.indexOf('  async function upsertAdjustedClip', start)
const handler = source.slice(start, end)
function runtime(response) {
  const guard = createReviewRequestGuard(); guard.select('p')
  const state = { segments: [{ id: 1, start: 10, end: 20 }, { id: 2, start: 30, end: 40 }], undo: [], redo: ['old'], requests: [], errors: [], refreshes: 0, current: true }
  const ctx = vm.createContext({
    project: { id: 'p' }, segments: state.segments, segmentsRevision: 'rev', API: '/api',
    reviewRequestGuardRef: { current: guard },
    captureProjectScope: () => ({}), isProjectScopeCurrent: () => state.current,
    apiFetch: async (url, options) => { state.requests.push({ url, options }); return typeof response === 'function' ? response() : response },
    safeJsonResponse: async r => r.payload,
    refreshAll: async () => { state.refreshes++ },
    errorFromPayload: d => d.message, humanizeErrorMessage: e => e,
    setSegmentUndoStack: f => { state.undo = f(state.undo) },
    setSegmentRedoStack: v => { state.redo = v },
    setSegments: v => { state.segments = v },
    setSegmentsRevision: v => { state.revision = v },
    setPreviewClip: v => { state.preview = v }, setSelectedTimelineId: () => {},
    setNotice: v => { state.notice = v }, setError: v => { state.errors.push(v) },
  })
  vm.runInContext(handler, ctx)
  return { state, guard, remove: i => ctx.removeSegment(i) }
}

test('delete sends exact clip, persists reply and records undo', async () => {
  const saved = [{ id: 1, start: 30, end: 40 }]
  const r = runtime({ ok: true, status: 200, payload: { segments: saved, segments_revision: 'new' } })
  await r.remove(0)
  assert.equal(r.state.requests[0].url, '/api/projects/p/segments/remove')
  assert.deepEqual(JSON.parse(r.state.requests[0].options.body), { item: { id: 1, start: 10, end: 20 }, expected_revision: 'rev' })
  assert.deepEqual(r.state.segments, saved)
  assert.equal(r.state.undo[0].length, 2)
  assert.equal(r.state.revision, 'new')
  assert.equal(r.state.notice.type, 'success')
  assert.ok(r.guard.begin('segments'), 'save lock must be released')
})

test('failed deletion leaves timeline intact and reports error', async () => {
  const r = runtime({ ok: false, status: 500, payload: { message: 'disk error' } })
  assert.equal(await r.remove(0), false)
  assert.equal(r.state.segments.length, 2)
  assert.equal(r.state.undo.length, 0)
  assert.match(r.state.errors[0], /disk error/)
})

test('conflicting deletion refreshes and does not silently retry against another revision', async () => {
  const r = runtime({ ok: false, status: 409, payload: { detail: { message: 'changed' } } })
  assert.equal(await r.remove(0), false)
  assert.equal(r.state.refreshes, 1)
  assert.equal(r.state.requests.length, 1)
  assert.equal(r.state.segments.length, 2)
})

test('late deletion response cannot alter another project', async () => {
  let resolve
  const pending = new Promise(r => { resolve = r })
  const r = runtime(() => pending)
  const call = r.remove(0)
  r.state.current = false
  resolve({ ok: true, status: 200, payload: { segments: [], segments_revision: 'new' } })
  assert.equal(await call, false)
  assert.equal(r.state.segments.length, 2)
  assert.equal(r.state.undo.length, 0)
})
