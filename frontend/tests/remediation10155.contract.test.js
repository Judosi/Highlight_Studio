import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8')
const app = read('src/app/App.jsx')
const release = JSON.parse(read('dist/release.json'))
const html = read('dist/index.html')
const bundleName = fs.readdirSync(path.join(root, 'dist/assets')).find(name => /^index-.*\.js$/.test(name))
const bundle = read(`dist/assets/${bundleName}`)

test('11.2.7 packaged frontend identity is unique and current', () => {
  assert.equal(release.version, '11.2.7')
  assert.equal(release.app_version, 'v11.2.7-quality-recovery-audit')
  assert.ok(html.includes(bundleName))
  assert.ok(html.includes('studio-final-101513.css'))
  assert.equal(html.includes('10153'), false)
})

test('project-scoped requests use a generation guard', () => {
  assert.match(app, /function beginProjectScope\(projectId\)/)
  assert.match(app, /function captureProjectScope\(projectId = project\?\.id\)/)
  assert.match(app, /function isProjectScopeCurrent\(scope\)/)
  assert.match(app, /async function loadProjectById[\s\S]*?beginProjectScope\(id\)[\s\S]*?!isProjectScopeCurrent\(scope\)/)
  assert.match(app, /async function saveSettings[\s\S]*?captureProjectScope\(projectId\)[\s\S]*?!isProjectScopeCurrent\(scope\)/)
  assert.match(app, /async function applySmartAutopilot[\s\S]*?captureProjectScope\(projectId\)[\s\S]*?!isProjectScopeCurrent\(scope\)/)
})

test('segment writes use optimistic concurrency in source and packaged bundle', () => {
  assert.match(app, /X-Segments-Revision/)
  assert.match(app, /[A-Za-z_$][\\w$]*\.status === 409 \\|\\| [A-Za-z_$][\\w$]*\.status === 428/)
  assert.match(bundle, /X-Segments-Revision/)
})

test('stale render is not offered to YouTube publisher', () => {
  assert.match(app, /\.filter\([^\n]{0,220}\.current !== false\)/)
  assert.match(bundle, /current!==!1/)
})

test('Shorts center crop option is described honestly as non-tracking', () => {
  assert.match(app, /smart_zoom[\s\S]{0,180}без слежения/)
})
