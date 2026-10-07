import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const panel = fs.readFileSync(new URL('../src/features/paidBeta/PaidBetaPanel.jsx', import.meta.url), 'utf8')
const wizard = fs.readFileSync(new URL('../src/features/onboarding/FirstRunWizard.jsx', import.meta.url), 'utf8')
const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')

test('Paid Beta UI exposes activation, privacy, local metrics and structured feedback', () => {
  assert.match(panel, /license\/\$\{action\}/)
  assert.match(panel, /\/privacy\/preferences/)
  assert.match(panel, /\/beta\/metrics/)
  assert.match(panel, /\/beta\/feedback/)
  assert.match(panel, /\/telemetry\/status/)
  assert.match(panel, /\/telemetry\/flush/)
  assert.match(panel, /Без видео, транскриптов/)
})

test('first-run wizard requires privacy and Paid Beta terms', () => {
  assert.match(wizard, /accepted_privacy/)
  assert.match(wizard, /accepted_terms/)
  assert.match(wizard, /disabled=\{saving \|\| !acceptedPrivacy \|\| !acceptedTerms\}/)
})

test('main interface identifies the launch operations build and license state', () => {
  assert.match(app, /Highlight Studio v11\.2\.7/)
  assert.match(app, /licenseStatus/)
  assert.match(app, /PaidBetaPanel/)
})
