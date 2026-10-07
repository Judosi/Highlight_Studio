import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const main = fs.readFileSync(new URL('../src/main.jsx', import.meta.url), 'utf8')
const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8')
const css = fs.readFileSync(new URL('../src/styles/redesign.css', import.meta.url), 'utf8')
const workspaceCss = fs.readFileSync(new URL('../src/styles/workspace.css', import.meta.url), 'utf8')
const studioCss = fs.readFileSync(new URL('../src/styles/studio-v2.css', import.meta.url), 'utf8')
const wizard = fs.readFileSync(new URL('../src/features/onboarding/FirstRunWizard.jsx', import.meta.url), 'utf8')

test('project home and five-state workflow define the primary information architecture', () => {
  assert.match(app, /function renderProjectsHome\(\)/)
  assert.match(app, /className=\{`studioSidebar/)
  assert.match(app, /className="sidebarWorkflowList"/)
  assert.match(app, /aria-current=\{activeStep === step\.id \? 'step'/)
  assert.match(app, /data-locked=\{step\.locked \? \'true\' : \'false\'\}/)
  for (const label of ['Источник', 'Формат', 'Анализ', 'Монтаж', 'Экспорт']) {
    assert.match(app, new RegExp(`title: '${label}'`))
  }
})

test('task center stays hidden while idle and progress exposes an accessible value', () => {
  assert.match(app, /if \(!taskCenterOpen\) return null/)
  assert.match(app, /aria-label="Центр задач"/)
  assert.match(app, /role="progressbar"/)
  assert.match(app, /aria-valuenow=/)
})

test('export separates video, shorts and YouTube without the floating publisher entry', () => {
  assert.match(app, /aria-label="Результаты и публикация"/)
  assert.match(app, /setExportView\('video'\)/)
  assert.match(app, /setExportView\('shorts'\)/)
  assert.match(app, /setExportView\('youtube'\)/)
  assert.doesNotMatch(html, /hs-youtube-entry|youtube-entry\.js/)
})

test('the final design layer owns native controls, focus and reduced motion', () => {
  assert.match(main, /styles\/redesign\.css/)
  assert.match(main, /styles\/workspace\.css/)
  assert.match(main, /styles\/studio-v2\.css/)
  assert.match(css, /input\[type="checkbox"\],[\s\S]*appearance: auto/)
  assert.match(css, /:focus-visible/)
  assert.match(workspaceCss, /@media \(prefers-reduced-motion: reduce\)/)
  assert.match(workspaceCss, /\.workflowSidebar/)
  assert.match(studioCss, /\.studioSidebar/)
  assert.match(studioCss, /\.studioApp\.studioAppV2\.density-compact > \.studioSidebar/)
  assert.match(studioCss, /\.studioSidebar\.compact \.sidebarCollapse \{[^}]*display: grid/s)
  assert.match(studioCss, /\.studioSidebar \.sidebarMobileClose,[\s\S]*display: grid/)
  assert.match(studioCss, /@media \(max-width: 900px\)/)
  assert.match(css, /@media \(forced-colors: active\)/)
})

test('sidebar state is reversible and the repaired layout starts readable', () => {
  assert.match(app, /LAYOUT_PREFERENCES_VERSION = 'stable-sidebar-v3'/)
  assert.match(app, /storage\.setItem\(SIDEBAR_COMPACT_KEY, 'false'\)/)
  assert.match(app, /storage\.setItem\(UI_DENSITY_KEY, 'comfortable'\)/)
  assert.match(app, /aria-expanded=\{!sidebarCompact\}/)
  assert.match(app, /data-sidebar-state=\{sidebarCompact \? 'compact' : 'expanded'\}/)
  assert.match(app, /className="sidebarMobileClose"/)
  assert.doesNotMatch(studioCss, /\.studioSidebar\.compact \.sidebarCollapse,\s*\.studioSidebar\.compact \.projectSwitcherCopy/)
})

test('first-run dialog traps and restores keyboard focus', () => {
  assert.match(wizard, /role="dialog"/)
  assert.match(wizard, /event\.key === 'Escape'/)
  assert.match(wizard, /event\.key !== 'Tab'/)
  assert.match(wizard, /previousFocusRef\.current\?\.focus/)
})
