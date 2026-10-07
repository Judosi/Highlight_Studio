import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import assert from 'node:assert/strict'

const panel = readFileSync(new URL('../src/features/release/MassReleasePanel.jsx', import.meta.url), 'utf8')
const preload = readFileSync(new URL('../../desktop/electron/preload.js', import.meta.url), 'utf8')

test('mass release support panel exposes crash privacy and release gate', () => {
  assert.match(panel, /crash-reports\/status/)
  assert.match(panel, /release-readiness/)
  assert.match(panel, /Stable — рекомендуется/)
  assert.match(panel, /Создать отчёт/)
})

test('desktop bridge exposes update channels and pre-update backup', () => {
  assert.match(preload, /getUpdateChannel/)
  assert.match(preload, /setUpdateChannel/)
  assert.match(preload, /createUpdateBackup/)
})

test('frontend error boundary records a privacy-safe crash id', () => {
  const source = readFileSync(new URL('../src/components/ErrorBoundary.jsx', import.meta.url), 'utf8')
  assert.match(source, /crash-reports\/frontend/)
  assert.match(source, /crashId/)
  assert.doesNotMatch(source, /<pre/)
})
