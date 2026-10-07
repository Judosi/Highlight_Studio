import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const css = fs.readFileSync(new URL('../src/styles/redesign.css', import.meta.url), 'utf8')

test('readable workspace is the upgrade default and density remains switchable', () => {
  assert.match(app, /density: 'comfortable'/)
  assert.match(app, /storage\.setItem\(UI_DENSITY_KEY, 'comfortable'\)/)
  assert.match(app, /storage\.getItem\(UI_DENSITY_KEY\) === 'compact' \? 'compact' : 'comfortable'/)
  assert.match(app, /density-\$\{uiDensity\}/)
  assert.match(app, /Комфортный вид/)
  assert.match(app, /Компактный вид/)
})

test('idle task center is hidden and editing controls use the compact workspace contract', () => {
  assert.match(app, /if \(!taskCenterOpen\) return null/)
  assert.match(css, /\.studioApp\.density-compact \.studioLayout/)
  assert.match(css, /\.studioApp\.density-compact button/)
  assert.match(css, /\.studioApp \.jobBar\.taskCenter/)
})
