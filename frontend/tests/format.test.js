import test from 'node:test'
import assert from 'node:assert/strict'
import { secondsToTc, formatDuration, progressKindLabel } from '../src/shared/format.js'

test('secondsToTc formats stable timeline values', () => {
  assert.equal(secondsToTc(0), '00:00:00.00')
  assert.equal(secondsToTc(3723.5), '01:02:03.50')
})

test('formatDuration handles invalid and human-readable values', () => {
  assert.equal(formatDuration(0), '—')
  assert.equal(formatDuration(65), '1м 05с')
  assert.equal(formatDuration(3661), '1ч 01м')
})

test('progress labels keep user-facing Russian wording', () => {
  assert.equal(progressKindLabel('done'), 'готово')
  assert.equal(progressKindLabel('real_counter'), 'реальный счётчик')
  assert.equal(progressKindLabel('unknown'), 'ожидание')
})
