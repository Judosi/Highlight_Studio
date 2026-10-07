import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
const css = fs.readFileSync(new URL('../src/styles/theme.css', import.meta.url), 'utf8')
const themes = fs.readFileSync(new URL('../src/config/themes.js', import.meta.url), 'utf8')

function channel(value) {
  const normalized = value / 255
  return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
}

function luminance(hex) {
  const value = hex.replace('#', '')
  const expanded = value.length === 3 ? value.split('').map(part => part + part).join('') : value
  const [r, g, b] = [0, 2, 4].map(offset => channel(Number.parseInt(expanded.slice(offset, offset + 2), 16)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a, b) {
  const first = luminance(a)
  const second = luminance(b)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

function block(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = css.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\n\\}`, 'm'))
  assert.ok(match, `theme block ${selector} must exist`)
  return match[1]
}

function token(source, name) {
  const match = source.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))
  assert.ok(match, `token --${name} must be a six-digit hex color`)
  return match[1]
}

test('Graphite is the default and all three themes are user-selectable and persistent', () => {
  assert.match(themes, /DEFAULT_THEME = 'graphite'/)
  assert.match(themes, /id: 'graphite'/)
  assert.match(themes, /id: 'midnight'/)
  assert.match(themes, /id: 'high-contrast'/)
  assert.match(app, /highlightStudioTheme/)
  assert.match(app, /root\.dataset\.hsTheme = uiTheme/)
  assert.match(app, /role="radiogroup" aria-label="Цветовая тема"/)
  assert.match(app, /data-theme=\{uiTheme\}/)
})

test('semantic design tokens replace the old gradient-first control system', () => {
  for (const name of [
    'color-canvas', 'color-surface-1', 'color-surface-2',
    'color-border-subtle', 'color-border-default',
    'color-text-primary', 'color-text-secondary',
    'color-action-primary', 'color-action-primary-hover',
    'color-ai', 'color-success', 'color-warning', 'color-danger', 'color-focus',
  ]) assert.match(css, new RegExp(`--${name}:`))
  assert.match(css, /button\.primary[\s\S]*background: var\(--color-action-primary\)/)
  assert.match(css, /\.uMainClipPlayer[\s\S]*background: #020304/)
})

test('published theme text and primary actions meet WCAG AA contrast targets', () => {
  const selectors = [
    ':root,\n:root[data-hs-theme="graphite"]',
    ':root[data-hs-theme="midnight"]',
    ':root[data-hs-theme="high-contrast"]',
  ]
  for (const selector of selectors) {
    const source = block(selector)
    const canvas = token(source, 'color-canvas')
    const text = token(source, 'color-text-primary')
    const secondary = token(source, 'color-text-secondary')
    const primary = token(source, 'color-action-primary')
    const onPrimary = selector.includes('graphite') ? token(source, 'color-action-on-primary') : '#ffffff'
    assert.ok(contrast(text, canvas) >= 7, `${selector}: primary text should meet AAA on canvas`)
    assert.ok(contrast(secondary, canvas) >= 4.5, `${selector}: secondary text should meet AA on canvas`)
    assert.ok(contrast(onPrimary, primary) >= 4.5, `${selector}: button text should meet AA`)
  }
})
