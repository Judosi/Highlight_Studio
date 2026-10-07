import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const source = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')

test('manual workflow navigation refreshes backend state before rejecting review/export', () => {
  assert.match(source, /async function openWorkflowStep\(step\)/)
  assert.match(source, /\['review', 'export'\]\.includes\(step\.id\)/)
  assert.match(source, /const data = await refreshAll\(projectId, scope\)/)
  assert.match(source, /setActiveStep\('review'\)/)
})

test('add candidate is project-generation guarded', () => {
  const start = source.indexOf('async function addCandidate(c)')
  const end = source.indexOf('async function removeSegment', start)
  const body = source.slice(start, end)
  assert.match(body, /captureProjectScope\(projectId\)/)
  assert.match(body, /isProjectScopeCurrent\(scope\)/)
  assert.match(body, /\/segments\/add/)
})


test('workflow step buttons stay clickable so stale state can be revalidated', () => {
  assert.match(source, /data-locked=\{step\.locked \? 'true' : 'false'\}/)
  assert.doesNotMatch(source, /aria-disabled=\{step\.locked\}/)
})
