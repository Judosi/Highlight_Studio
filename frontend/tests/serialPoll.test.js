import test from 'node:test'
import assert from 'node:assert/strict'
import { startSerialPoll } from '../src/lib/serialPoll.js'

test('slow poll has no next timer until the pending request completes', async () => {
  let resolve, calls = 0
  const timers = []
  const stop = startSerialPoll(() => { calls++; return new Promise(r => { resolve = r }) }, 4000,
    { schedule: (fn, ms) => { timers.push({ fn, ms }); return 1 }, cancel: () => {} })
  assert.equal(calls, 1); assert.equal(timers.length, 0)
  resolve(); await Promise.resolve(); await Promise.resolve()
  assert.equal(timers.length, 1); assert.equal(timers[0].ms, 4000)
  stop()
})

test('unmount during a pending poll prevents rescheduling', async () => {
  let resolve, scheduled = 0
  const stop = startSerialPoll(() => new Promise(r => { resolve = r }), 10,
    { schedule: () => { scheduled++ }, cancel: () => {} })
  stop(); resolve(); await Promise.resolve(); await Promise.resolve()
  assert.equal(scheduled, 0)
})

test('failed poll reports the error and schedules recovery', async () => {
  let scheduled = 0, reported
  const stop = startSerialPoll(async () => { throw new Error('offline') }, 10,
    { schedule: () => { scheduled++; return 1 }, cancel: () => {}, onError: e => { reported = e } })
  await Promise.resolve(); await Promise.resolve()
  assert.equal(reported.message, 'offline'); assert.equal(scheduled, 1)
  stop()
})
