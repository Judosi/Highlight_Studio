import test from 'node:test'
import assert from 'node:assert/strict'
import {
  candidateRejectKey,
  findOverlapIndex,
  parseTrimBounds,
  segmentIndexFor,
  sourceCandidateKey,
  withSourceCandidateKey,
} from '../src/shared/review.js'

test('candidate identity does not rely on renumbered segment id', () => {
  const segments = [{ id: 1, start: 100, end: 105 }]
  const candidate = { id: 1, start: 10, end: 15 }
  assert.equal(segmentIndexFor(segments, candidate), -1)
})

test('candidate remains linked after its trim bounds change', () => {
  const candidate = { id: 9, start: 10, end: 15 }
  const stored = { ...withSourceCandidateKey(candidate), id: 1, start: 9, end: 16 }
  assert.equal(stored.source_candidate_key, candidateRejectKey(candidate))
  assert.equal(sourceCandidateKey(candidate), candidateRejectKey(candidate))
  assert.equal(segmentIndexFor([stored], candidate), 0)
})

test('overlap guard ignores only the segment being edited', () => {
  const segments = [{ start: 0, end: 10 }, { start: 20, end: 30 }]
  assert.equal(findOverlapIndex(segments, { start: 9, end: 12 }), 0)
  assert.equal(findOverlapIndex(segments, { start: 8, end: 12 }, 0), -1)
  assert.equal(findOverlapIndex(segments, { start: 10, end: 20 }), -1)
})

test('trim bounds reject invalid values and source overrun', () => {
  assert.deepEqual(parseTrimBounds('', '5'), {
    ok: false,
    message: 'Укажи корректные числовые границы фрагмента.',
  })
  assert.equal(parseTrimBounds('5', '5').ok, false)
  assert.equal(parseTrimBounds('5', '5.2').ok, false)
  assert.equal(parseTrimBounds('5', '11', 10).ok, false)
  assert.deepEqual(parseTrimBounds('5', '9.5', 10), { ok: true, start: 5, end: 9.5 })
})
