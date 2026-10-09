import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import React from 'react'
import { createServer } from 'vite'

test('first-run wizard guides the user and saves onboarding choices', { timeout: 30000 }, async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://127.0.0.1:8000/' })
  const globals = ['window', 'document', 'localStorage', 'HTMLElement', 'Node', 'Event', 'MouseEvent', 'MutationObserver']
  for (const key of globals) globalThis[key] = dom.window[key]
  Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
  globalThis.Headers = Headers
  globalThis.Response = Response
  globalThis.Request = Request
  let savedPayload = null
  globalThis.fetch = async (_url, options = {}) => {
    savedPayload = JSON.parse(options.body || '{}')
    return new Response(JSON.stringify({ ok: true, completed: true, ai_mode: 'local', telemetry_enabled: savedPayload.telemetry_enabled }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }

  const vite = await createServer({ root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom', logLevel: 'silent' })
  let view = null
  try {
    const { render, screen, waitFor } = await import('@testing-library/react')
    const { default: userEvent } = await import('@testing-library/user-event')
    const { default: FirstRunWizard } = await vite.ssrLoadModule('/src/features/onboarding/FirstRunWizard.jsx')
    const user = userEvent.setup({ document: dom.window.document })
    let completed = null
    view = render(React.createElement(FirstRunWizard, {
      open: true,
      systemCheck: {
        checks: {
          ffmpeg: { ok: true },
          ffprobe: { ok: true },
          faster_whisper: { ok: true },
          ollama_server: { ok: true, text_model_ok: true },
        },
      },
      systemCheckLoading: false,
      desktopInfo: { desktop: true, projectsDir: 'D:/Highlight Projects' },
      onRunSystemCheck: async () => null,
      onComplete: payload => { completed = payload },
    }), { container: document.getElementById('root') })

    assert.ok(screen.getByText('Подготовим приложение к первому проекту'))
    await user.click(screen.getByRole('button', { name: /Продолжить/ }))
    assert.ok(screen.getByText('Проверим компоненты'))
    await user.click(screen.getByRole('button', { name: /Продолжить/ }))
    assert.ok(screen.getByText('Где хранить проекты'))
    await user.click(screen.getByRole('button', { name: /Продолжить/ }))
    assert.ok(screen.getByRole('heading', { name: 'Локальный AI' }))
    const checkboxes = screen.getAllByRole('checkbox')
    await user.click(checkboxes[0])
    await user.click(checkboxes[1])
    await user.click(checkboxes[2])
    await user.click(screen.getByRole('button', { name: 'Завершить настройку' }))
    await waitFor(() => assert.equal(completed?.completed, true))
    assert.equal(savedPayload.ai_mode, 'local')
    assert.equal(savedPayload.telemetry_enabled, true)
    assert.equal(savedPayload.accepted_privacy, true)
    assert.equal(savedPayload.accepted_terms, true)
  } finally {
    view?.unmount()
    await vite.close()
    dom.window.close()
    for (const key of globals) delete globalThis[key]
  }
})
