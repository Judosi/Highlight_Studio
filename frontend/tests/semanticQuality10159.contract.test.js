import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')
const app = fs.readFileSync(path.join(root, 'src/app/App.jsx'), 'utf8')
const presets = fs.readFileSync(path.join(root, 'src/config/taskPresets.js'), 'utf8')
const release = JSON.parse(fs.readFileSync(path.join(root, '../release_identity.json'), 'utf8'))
const distIndex = fs.readFileSync(path.join(root, 'dist/index.html'), 'utf8')
const bundleMatch = distIndex.match(/src="\/assets\/([^"]+\.js)"/)
assert.ok(bundleMatch, 'production JS bundle reference missing')
const distBundle = fs.readFileSync(path.join(root, 'dist/assets', bundleMatch[1]), 'utf8')

test('11.2.7 exposes semantic balanced preset instead of hidden irl_funny fallback', () => {
  assert.equal(release.version, '11.2.7')
  assert.equal(release.app_version, 'v11.2.7-quality-recovery-audit')
  assert.match(presets, /balanced:\s*\{[\s\S]*Сбалансированный \/ смысл/)
  assert.match(presets, /semantic_quality_guard_enabled:\s*true/)
  assert.match(presets, /quality_first_selection_enabled:\s*true/)
  assert.match(presets, /temporal_fairness_enabled:\s*true/)
  assert.match(presets, /text|Qwen|лучшие законченные моменты/i)
})

test('quality controls are part of project defaults and advanced UI', () => {
  for (const key of [
    'semantic_quality_guard_enabled',
    'non_primary_reject_confidence',
    'quality_first_selection_enabled',
    'quality_first_min_score',
    'quality_first_min_confidence',
    'quality_first_min_clarity',
    'temporal_fairness_enabled',
    'temporal_fairness_bucket_seconds',
    'temporal_fairness_blocks_per_bucket',
    'micro_global_score_floor',
  ]) {
    assert.ok(app.includes(key), `${key} missing from App.jsx`)
  }
  assert.match(app, /Качество важнее длительности/)
  assert.match(app, /Равный шанс всему VOD/)
  assert.match(app, /Отсекать reconnect\/replay|Фильтр reconnect\/replay/)
})

test('default prompt treats target as ceiling and rejects technical/replay filler', () => {
  assert.match(app, /30 минут — это цель\/верхняя граница, а не обязанность/)
  assert.match(app, /waiting\/reconnect\/intermission/)
  assert.match(app, /НЕ использовать только ради добора целевой длительности/)
  assert.ok(app.includes("text_model: 'qwen3:8b'"), 'Qwen3 8B must remain unchanged')
})


test('packaged production bundle contains semantic quality defaults', () => {
  for (const marker of [
    'Сбалансированный / смысл',
    'semantic_quality_guard_enabled',
    'quality_first_selection_enabled',
    'temporal_fairness_enabled',
    '30 минут — это цель/верхняя граница',
    'qwen3:8b',
    'Версия 11.2.7',
  ]) {
    assert.ok(distBundle.includes(marker), `${marker} missing from production bundle`)
  }
})
