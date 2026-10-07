import { verifyProductionBuild } from './productionBuild.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const app = fs.readFileSync(path.join(root, 'src/app/App.jsx'), 'utf8')

test('review access is based on persisted analysis completion, not candidate array length', () => {
  assert.match(app, /const analysisCurrent = Boolean\([\s\S]*?freshness\?\.analysis_current/)
  assert.match(app, /if \(stepId === 'review'\)[\s\S]*?if \(!analysisCurrent\) return 'analysis'[\s\S]*?return 'review'/)
  assert.match(app, /id: 'review'[\s\S]*?locked: !analysisCurrent/)
  assert.doesNotMatch(app, /id: 'review'[\s\S]{0,500}?locked: candidates\.length === 0/)
})

test('analysis completion keeps navigation manual and supports a zero-candidate review state', () => {
  assert.match(app, /const analysisDone = analysisCurrent && !busy/)
  assert.match(app, /проверка необязательна/)
  assert.match(app, /AI-нарезка готова/)
  assert.match(app, /setActiveStep\('review'\)/)
  assert.match(app, /Кандидаты не найдены или ещё не подгрузились/)
})

test('terminal lightweight progress is always hydrated by a full dashboard refresh', () => {
  assert.match(app, /lastTerminalHydrationRef/)
  assert.match(app, /\['done', 'error', 'cancelled'\]\.includes\(state\)/)
  assert.match(app, /refreshAll\(project\.id\)/)
})

test('opening a project preserves the last user workflow step instead of jumping to the newest stage', () => {
  assert.match(app, /const preferred = localStorage\.getItem\('highlightStudioLastStep'\) \|\| 'analysis'/)
  assert.match(app, /Этапы больше не переключаются автоматически/)
  assert.doesNotMatch(app, /fresh\.render_current[\s\S]{0,250}\? 'export'/)
})

test('packaged production bundle keeps manual review navigation and zero-candidate review available', () => {
  const assets = path.join(root, 'dist/assets')
  const bundleName = fs.readdirSync(assets).find(name => /^index-.*\.js$/.test(name))
  assert.ok(bundleName, '11.2.7 production bundle is missing')
  const bundle = fs.readFileSync(path.join(assets, bundleName), 'utf8')
  verifyProductionBuild()
  assert.match(bundle, /Анализ завершён · 0 моментов/)
  assert.match(bundle, /AI-нарезка готова/)
  assert.match(bundle, /Кандидаты не найдены или ещё не подгрузились/)
  assert.equal(fs.existsSync(path.join(root, 'dist/ux-workflow-101513.js')), false, 'legacy workflow compatibility layer must be absent')
  const presentation = fs.readFileSync(path.join(root, 'dist/ui-presentation-101515.js'), 'utf8')
  assert.match(presentation, /Альтернативы/)
  assert.doesNotMatch(presentation, /fetch\(|apiFetch|location\.reload|setInterval|sessionStorage|\/api\//)
})
