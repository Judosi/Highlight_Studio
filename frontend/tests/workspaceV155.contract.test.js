import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const css = fs.readFileSync(new URL('../src/styles/studio-final.css', import.meta.url), 'utf8')
const distCss = fs.readFileSync(new URL('../dist/studio-final-101513.css', import.meta.url), 'utf8')
const distHtml = fs.readFileSync(new URL('../dist/index.html', import.meta.url), 'utf8')
const release = JSON.parse(fs.readFileSync(new URL('../dist/release.json', import.meta.url), 'utf8'))

test('v11.2.7 audited stylesheet is the final production geometry owner', () => {
  assert.equal(release.version, '11.2.7')
  assert.equal(release.app_version, 'v11.2.7-quality-recovery-audit')
  assert.equal(release.design_id, 'studio-audited-v15')
  assert.match(distHtml, /studio-v3\.css\?v=11\.2\.7[\s\S]*studio-final-101513\.css/)
  assert.equal(css, distCss, 'src and packaged final stylesheet must be identical')
})

test('workflow uses full available width and clamps horizontal overflow', () => {
  assert.match(css, /\.studioStage \{ width: 100% !important; overflow-x: clip !important; \}/)
  assert.match(css, /\.studioLayoutV2 \.studioWorkspace,[\s\S]*?width: 100% !important;[\s\S]*?max-width: none !important/)
  assert.match(css, /\.studioApp\.studioAppV2 \.uPage \{ width: 100% !important;/)
  assert.match(css, /projectSwitcherCopy b[\s\S]*?text-overflow: ellipsis !important/)
})

test('task center is an explicit fixed drawer and never pushes the workspace', () => {
  assert.match(css, /\.jobBar\.taskCenter,[\s\S]*?position: fixed !important/)
  assert.match(css, /\.taskCenter \.phaseRail \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\) !important; overflow: hidden !important; \}/)
  assert.match(app, /if \(!taskCenterOpen\) return null/)
  assert.doesNotMatch(app, /setTaskCenterOpen\(true\)[\s\S]{0,100}if \(!busy\)/)
})

test('analysis phase rail wraps text and has responsive columns', () => {
  assert.match(css, /\.analysisPipelineMap \{[\s\S]*?repeat\(5, minmax\(0, 1fr\)\)/)
  assert.match(css, /\.analysisPipelineMap b,[\s\S]*?white-space: normal !important[\s\S]*?overflow-wrap: anywhere !important/)
  assert.match(css, /@media \(max-width: 1320px\)[\s\S]*?\.analysisPipelineMap \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\) !important; \}/)
})

test('format, source and action areas have deterministic responsive geometry', () => {
  assert.match(css, /\.formatPage \.uPresetGrid \{[\s\S]*?repeat\(5, minmax\(0, 1fr\)\)/)
  assert.match(css, /\.formatPage \.styleFormGrid \{[\s\S]*?repeat\(4, minmax\(0, 1fr\)\)/)
  assert.match(css, /\.studioAppV2 \.sourceLayout \{[\s\S]*?minmax\(300px, 360px\)/)
  assert.match(css, /\.pageActionBar,[\s\S]*?position: static !important/)
})
