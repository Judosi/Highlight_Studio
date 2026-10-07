import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const app = fs.readFileSync(path.join(root, 'src/app/App.jsx'), 'utf8')
const shorts = fs.readFileSync(path.join(root, 'src/features/shorts/ShortsStudio.jsx'), 'utf8')
const distIndex = fs.readFileSync(path.join(root, 'dist/index.html'), 'utf8')
const bridgePath = path.join(root, 'dist/shorts-editor-1126.js')
const bridge = fs.existsSync(bridgePath) ? fs.readFileSync(bridgePath, 'utf8') : ''

function productionBundle() {
  const matches = [...distIndex.matchAll(/src="\/assets\/([^"]+\.js)"/g)]
  return matches.map(match => fs.readFileSync(path.join(root, 'dist/assets', match[1]), 'utf8')).join('\n')
}

test('native Shorts editor source exposes editable fields, safe zone and one-item regeneration', () => {
  assert.match(shorts, /ssEditor/)
  assert.match(app, /submitShort/)
  assert.match(app, /submitShort\(index,payload,true\)/)
  assert.match(shorts, /caption_text/)
  assert.match(shorts, /reframe_mode/)
  assert.match(shorts, /ssGuides/)
  assert.match(app, /Учитывать смех и эмоции/)
  assert.match(app, /shorts_recognition_dictionary/)
  assert.match(app, /shorts_precise_alignment/)
})

test('offline production bridge never monkey-patches fetch and follows active React project safely', () => {
  assert.doesNotMatch(distIndex, /shorts-editor-1126\.js/, 'native React editor must be the only active editor')
  assert.match(bridge, /highlightStudioLastProject/)
  assert.match(bridge, /dashboard-state/)
  assert.match(bridge, /highlight_studio_csrf/)
  assert.match(bridge, /X-CSRF-Token/)
  assert.match(bridge, /caption_text/)
  assert.match(bridge, /shortSafeZoneOverlay/)
  assert.match(bridge, /shorts_caption_quality/)
  assert.match(bridge, /shorts_emotion_events_enabled/)
  assert.match(bridge, /shorts_funny_search_enabled/)
  assert.match(bridge, /shorts_recognition_dictionary/)
  assert.match(bridge, /shorts_count/)
  assert.doesNotMatch(bridge, /window\.fetch\s*=/)
})

test('production main bundle itself does not contain a global fetch monkey patch', () => {
  assert.doesNotMatch(productionBundle(), /window\.fetch\s*=/)
})
