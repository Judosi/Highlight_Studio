import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import fs from 'node:fs'

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

test('PRODUCTION BUNDLE: Review Studio keeps candidate identity, rolls back failed saves and scopes rejections by project', { timeout: 30000 }, async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: 'http://127.0.0.1:8000/', runScripts: 'outside-only',
  })
  const globals = ['window', 'document', 'localStorage', 'HTMLElement', 'Node', 'Event', 'KeyboardEvent', 'MutationObserver']
  for (const key of globals) globalThis[key] = dom.window[key]
  Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
  globalThis.Headers = Headers
  globalThis.Response = Response
  globalThis.Request = Request
  globalThis.FormData = FormData
  globalThis.confirm = () => true
  globalThis.window.open = () => null
  Object.defineProperty(dom.window.HTMLMediaElement.prototype, 'play', {
    configurable: true,
    value() { return Promise.resolve() },
  })
  Object.defineProperty(dom.window.HTMLMediaElement.prototype, 'pause', {
    configurable: true,
    value() {},
  })

  const candidates = Array.from({ length: 65 }, (_, index) => ({
    id: index + 1,
    start: 10 + index * 10,
    end: 15 + index * 10,
    score: 8,
    title: `Candidate ${index + 1}`,
    reason: 'funny',
  }))
  const projects = [
    { id: 'p1', name: 'Project One', source_type: 'local', source_ready: true, source_video_path: '/tmp/input1.mp4', freshness: { candidates_current: true, analysis_current: true }, settings: { target_minutes: 30, task_preset_label: 'Сбалансированный' } },
    { id: 'p2', name: 'Project Two', source_type: 'local', source_ready: true, source_video_path: '/tmp/input2.mp4', freshness: { candidates_current: true, analysis_current: true }, settings: { target_minutes: 30, task_preset_label: 'Сбалансированный' } },
  ]
  const projectSegments = {
    p1: [{ id: 1, start: 100, end: 105, score: 9, title: 'Existing unrelated' }],
    p2: [],
  }
  let failNextSegmentSave = true
  let lastSegmentPayload = null

  const dashboard = (projectId) => ({
    ok: true,
    project: projects.find((item) => item.id === projectId),
    status: { state: 'idle', progress: 0 },
    candidates,
    segments: projectSegments[projectId],
    outputs: [],
    preview_status: { exists: false },
    project_history: { items: [] },
    ai_quality_audit: { score: 80, label: 'good' },
    poll_after_ms: 60000,
  })

  globalThis.fetch = async (input, options = {}) => {
    const url = String(input)
    const method = options.method || 'GET'
    if (url.endsWith('/api/health')) return jsonResponse({ ok: true, app_version: 'v11.2.7-quality-recovery-audit', design_id: 'studio-audited-v15' })
    if (url.endsWith('/api/projects')) return jsonResponse(projects)
    if (url.endsWith('/api/twitch/tools')) return jsonResponse({ ok: true, tools: {} })
    if (url.endsWith('/api/system-check')) return jsonResponse({ ok: true, checks: { ffmpeg: { ok: true }, ffprobe: { ok: true }, faster_whisper: { ok: true }, ollama: { ok: true, text_model_ok: true } }, recommendations: [] })
    if (url.endsWith('/api/onboarding')) return jsonResponse({ ok: true, completed: true, ai_mode: 'local' })
    const dashboardMatch = url.match(/\/api\/projects\/(p1|p2)\/dashboard-state$/)
    if (dashboardMatch) return jsonResponse(dashboard(dashboardMatch[1]))
    const projectMatch = url.match(/\/api\/projects\/(p1|p2)$/)
    if (projectMatch && method === 'GET') return jsonResponse(projects.find((item) => item.id === projectMatch[1]))
    const segmentsMatch = url.match(/\/api\/projects\/(p1|p2)\/segments$/)
    if (segmentsMatch && method === 'PUT') {
      lastSegmentPayload = JSON.parse(options.body)
      if (failNextSegmentSave) {
        failNextSegmentSave = false
        return jsonResponse({ ok: false, message: 'simulated write failure' }, 500)
      }
      projectSegments[segmentsMatch[1]] = lastSegmentPayload
      return jsonResponse(lastSegmentPayload)
    }
    const segmentAddMatch = url.match(/\/api\/projects\/(p1|p2)\/segments\/add$/)
    if (segmentAddMatch && method === 'POST') {
      const payload = JSON.parse(options.body)
      lastSegmentPayload = [...projectSegments[segmentAddMatch[1]], payload.item]
      // Keep the request pending long enough to verify the immediate UI state
      // shown for a real multi-gigabyte project save.
      await new Promise(resolve => setTimeout(resolve, 80))
      if (failNextSegmentSave) {
        failNextSegmentSave = false
        return jsonResponse({ ok: false, message: 'simulated write failure' }, 500)
      }
      projectSegments[segmentAddMatch[1]] = lastSegmentPayload
      return jsonResponse({ ok: true, added: true, message: 'saved', segments: lastSegmentPayload, segments_revision: 'rev-after' })
    }
    const removeMatch = url.match(/\/api\/projects\/(p1|p2)\/segments\/remove$/)
    if (removeMatch && method === 'POST') {
      const { item } = JSON.parse(options.body)
      projectSegments[removeMatch[1]] = projectSegments[removeMatch[1]].filter(s => !(s.id === item.id && s.start === item.start && s.end === item.end))
      return jsonResponse({ ok: true, segments: projectSegments[removeMatch[1]], segments_revision: 'rev-removed' })
    }
    if (/\/api\/projects\/(p1|p2)\/feedback$/.test(url)) return jsonResponse({ ok: true })
    return jsonResponse({ ok: true })
  }

  const entry = fs.readFileSync(new URL('../dist/index.html', import.meta.url), 'utf8').match(/src="(\/assets\/[^"]+\.js)"/)[1]
  const bundle = fs.readFileSync(new URL('../dist' + entry, import.meta.url), 'utf8')
  Object.assign(dom.window, { fetch: globalThis.fetch, Headers, Response, Request })
  let view = null
  try {
    const { screen, waitFor, within } = await import('@testing-library/react')
    const { default: userEvent } = await import('@testing-library/user-event')
    dom.window.eval(bundle)
    const user = userEvent.setup({ document: dom.window.document })
    view = null

    const collapseSidebar = await waitFor(() => screen.getByRole('button', { name: 'Свернуть меню' }))
    assert.equal(collapseSidebar.getAttribute('aria-expanded'), 'true')
    assert.equal(document.querySelector('.studioAppV2')?.dataset.density, 'comfortable')
    await user.click(collapseSidebar)
    const expandSidebar = await waitFor(() => screen.getByRole('button', { name: 'Развернуть меню' }))
    assert.equal(expandSidebar.getAttribute('aria-expanded'), 'false')
    assert.equal(document.querySelector('.studioAppV2')?.dataset.sidebarState, 'compact')
    await user.click(expandSidebar)
    await waitFor(() => assert.equal(document.querySelector('.studioAppV2')?.dataset.sidebarState, 'expanded'))

    const projectOne = await waitFor(() => screen.getByRole('button', { name: /Project One/ }))
    await user.click(projectOne)
    const reviewStep = await waitFor(() => {
      const button = screen.getByRole('button', { name: /Монтаж/ })
      assert.equal(button.getAttribute('data-locked'), 'false')
      return button
    })
    await user.click(reviewStep)
    const finalTab = await waitFor(() => screen.getByRole('tab', { name: /Итоговая нарезка/ }))
    assert.equal(finalTab.getAttribute('aria-selected'), 'true')
    await user.click(screen.getByRole('tab', { name: /Альтернативы/ }))
    await waitFor(() => screen.getAllByText('Candidate 1'))

    const getCandidateCard = () => screen.queryAllByText('Candidate 1').map(node => node.closest('.uMomentCard')).find(Boolean)
    let candidateCard = getCandidateCard()
    let addButton = within(candidateCard).getByRole('button', { name: 'В монтаж' })
    assert.equal(addButton.disabled, false, 'same numeric id must not make an unrelated candidate look added')
    assert.ok(screen.getByText('Показано 60 из 65'))

    const failedClick = user.click(addButton)
    const pendingButton = await waitFor(() => within(getCandidateCard()).getByRole('button', { name: 'Добавляю…' }))
    assert.equal(pendingButton.disabled, true, 'a slow save must provide immediate feedback and block duplicate clicks')
    await failedClick
    await waitFor(() => screen.getByText(/Не удалось добавить момент/))
    candidateCard = getCandidateCard()
    addButton = within(candidateCard).getByRole('button', { name: 'В монтаж' })
    assert.equal(addButton.disabled, false, 'failed save must roll the optimistic montage state back')

    const successfulClick = user.click(addButton)
    await waitFor(() => within(getCandidateCard()).getByRole('button', { name: 'Добавляю…' }))
    await successfulClick
    await waitFor(() => screen.getByRole('tab', { name: /Альтернативы \(65\)/ }))
    assert.equal(lastSegmentPayload.find((item) => item.start === 10)?.source_candidate_key, '1-10.00-15.00')
    await user.click(screen.getByRole('tab', { name: /Альтернативы \(65\)/ }))
    await waitFor(() => within(getCandidateCard()).getByRole('button', { name: 'В монтаже ✓' }))
    candidateCard = getCandidateCard()

    await user.click(within(candidateCard).getByRole('button', { name: 'В плеер' }))
    await user.click(screen.getByRole('button', { name: 'Расширить конец' }))
    await waitFor(() => assert.equal(lastSegmentPayload.find((item) => item.source_candidate_key === '1-10.00-15.00')?.end, 20))
    await user.click(screen.getByRole('button', { name: 'Расширить конец' }))
    await waitFor(() => assert.equal(lastSegmentPayload.find((item) => item.source_candidate_key === '1-10.00-15.00')?.end, 25), { timeout: 5000 })
    await user.click(screen.getByRole('button', { name: 'Отклонить' }))
    await waitFor(() => assert.equal(Boolean(getCandidateCard()), false))

    await user.click(screen.getByRole('button', { name: /Источник/ }))
    const secondProjectSelect = await waitFor(() => document.querySelector('.uCompactActions select'))
    await user.selectOptions(secondProjectSelect, 'p2')
    await waitFor(() => {
      assert.equal(document.querySelector('.topProjectIdentity b')?.textContent, 'Project Two')
    })
    await user.click(screen.getByRole('button', { name: /Монтаж/ }))
    await waitFor(() => screen.getAllByText('Candidate 1'))
    assert.ok(screen.getAllByText('Candidate 1').length > 0, 'a rejection from project one must not leak into project two')
    assert.equal(document.querySelectorAll('.errorBoundary').length, 0)
  } finally {
    view?.unmount()

    dom.window.close()
    for (const key of globals) delete globalThis[key]
    delete globalThis.confirm
  }
})
