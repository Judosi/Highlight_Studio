import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')
const app = fs.readFileSync(path.join(root, 'src/app/App.jsx'), 'utf8')
const html = fs.readFileSync(path.join(root, 'dist/index.html'), 'utf8')
const presentation = fs.readFileSync(path.join(root, 'dist/ui-presentation-101515.js'), 'utf8')
const bundleName = fs.readdirSync(path.join(root, 'dist/assets')).find(name => /^index-.*\.js$/.test(name))
const bundle = fs.readFileSync(path.join(root, 'dist/assets', bundleName), 'utf8')
const release = JSON.parse(fs.readFileSync(path.join(root, 'dist/release.json'), 'utf8'))

test('11.2.7 production has one workflow state owner', () => {
  assert.equal(release.version, '11.2.7')
  assert.equal(release.app_version, 'v11.2.7-quality-recovery-audit')
  assert.equal(release.design_id, 'studio-audited-v15')
  assert.equal(release.production_state_owner, 'react')
  assert.ok(html.includes(bundleName))
  assert.ok(html.includes('ui-presentation-101515.js'))
  assert.ok(!html.includes('ux-workflow-101513.js'))
  assert.ok(!fs.existsSync(path.join(root, 'dist/ux-workflow-101513.js')))
})

test('presentation bridge is presentation-only and cannot own workflow state', () => {
  for (const forbidden of ['fetch(', 'apiFetch', 'location.reload', 'stopImmediatePropagation', 'setInterval', 'sessionStorage', '/api/', '.click()']) {
    assert.ok(!presentation.includes(forbidden), forbidden)
  }
  assert.ok(presentation.includes('Анализируем содержание стрима'))
  assert.ok(presentation.includes('Ориентир по длительности'))
})

test('review add uses atomic revision-aware backend action', () => {
  assert.ok(app.includes('/segments/add'))
  assert.ok(app.includes('expected_revision: segmentsRevision'))
  assert.ok(bundle.includes('/segments/add'))
  assert.ok(bundle.includes('expected_revision'))
  assert.ok(!bundle.includes('let n=await Wa([...C,t]);return n&&yr(`final`),n'))
})

test('source owns result-first review and human-readable workflow', () => {
  for (const marker of ['AI-нарезка готова', 'Альтернативы (', 'Ориентир по длительности, минут', 'humanizePipelineStage', 'refreshGlobalJobs', 'confirmReanalysis']) {
    assert.ok(app.includes(marker), marker)
  }
})

test('new project is a true draft context rather than old-project state', () => {
  for (const marker of ['resetProjectContextForDraft', "beginProjectScope('')", 'setProject(null)', 'setCandidates([])', 'setSegments([])', "localStorage.removeItem('highlightStudioLastProject')"]) {
    assert.ok(app.includes(marker), marker)
  }
  assert.ok(app.includes("beginProject('local', false)"))
  assert.ok(bundle.includes('highlightStudioLastProject'))
  assert.equal((bundle.match(/Z\(`local`,!0\)/g) || []).length, 0)
})

test('completed analysis hydration is React-owned', () => {
  for (const marker of ['lastGlobalTerminalSyncRef', 'analysis job reaches a terminal successful state', 'refreshAll(project.id, scope)', "['one_click', 'analysis', 'analyze', 'ai_analyze']"]) {
    assert.ok(app.includes(marker), marker)
  }
  assert.ok(!presentation.includes('dashboard-state'))
  assert.ok(!presentation.includes('syncCompletedAnalysis'))
})
