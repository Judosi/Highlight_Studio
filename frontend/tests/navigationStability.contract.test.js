import { verifyProductionBuild } from './productionBuild.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8')
const app = read('src/app/App.jsx')
const html = read('dist/index.html')
const css = read('dist/studio-final-101513.css')
const bundleName = fs.readdirSync(path.join(root, 'dist/assets')).find(name => /^index-.*\.js$/.test(name))
const bundle = read(`dist/assets/${bundleName}`)
const release = JSON.parse(read('dist/release.json'))

test('release 11.2.7 loads one release-specific final stylesheet after v3', () => {
  assert.equal(release.version, '11.2.7')
  assert.equal(release.app_version, 'v11.2.7-quality-recovery-audit')
  assert.equal(release.design_id, 'studio-audited-v15')
  assert.ok(html.includes('/studio-v3.css?v=11.2.7'))
  assert.ok(html.includes('/studio-final-101513.css'))
  assert.ok(html.lastIndexOf('studio-final-101513.css') > html.lastIndexOf('studio-v3.css'))
  for (const legacy of ['studio-v4.css', 'studio-v5.css', 'studio-v6.css', 'studio-v7.css']) {
    assert.equal(html.includes(legacy), false, `${legacy} must not be loaded by production HTML`)
  }
})

test('compact sidebar has an explicit physical width and recoverable controls', () => {
  assert.match(css, /--hs-sidebar-compact:\s*82px/)
  assert.match(css, /sidebarIsCompact[\s\S]*?grid-template-columns:\s*var\(--hs-sidebar-compact\) minmax\(0, 1fr\) !important/)
  assert.match(css, /> \.studioSidebar\.compact[\s\S]*?width:\s*var\(--hs-sidebar-compact\) !important/)
  assert.match(css, /overflow-x:\s*hidden !important/)
  assert.match(app, /aria-label=\{sidebarCompact \? 'Развернуть меню' : 'Свернуть меню'\}/)
})

test('new project can enter Import before a project exists', () => {
  assert.match(app, /\['projects', 'import', 'reports', 'settings'\]\.includes\(stepId\)/)
  assert.match(app, /className="newProjectButton"[\s\S]*?beginProject\('local', true\)/)
  assert.match(app, /function beginProject\(source = 'local', openPicker = false\)[\s\S]*?setActiveStep\('import'\)/)
  verifyProductionBuild()
})

test('startup workflow guard waits until last project restoration completes', () => {
  assert.match(app, /if \(appBooting\) return[\s\S]*?resolveWorkflowAccessStep\(activeStep\)/)
  assert.match(app, /\[appBooting, activeStep, project\?\.id/)
  // Production bundle is patched too; otherwise source-only fixes are invisible to users.
  verifyProductionBuild()
})

test('layout migration resets known broken compact state once without resetting every release', () => {
  assert.match(app, /LAYOUT_PREFERENCES_VERSION = 'stable-sidebar-v3'/)
  assert.match(app, /storage\.setItem\(SIDEBAR_COMPACT_KEY, 'false'\)/)
  assert.doesNotMatch(app, /LAYOUT_PREFERENCES_VERSION = '10\.15\.3'/)
})

test('stale project responses cannot overwrite a newly selected project', () => {
  assert.match(app, /function beginProjectScope\(projectId\)/)
  assert.match(app, /async function loadProjectById[\s\S]*?beginProjectScope\(id\)[\s\S]*?!isProjectScopeCurrent\(scope\)/)
  assert.match(app, /async function refreshAll[\s\S]*?captureProjectScope\(id\)[\s\S]*?!isProjectScopeCurrent\(scope\)/)
  assert.match(bundle, /highlightStudioLastProject/)
})
