import { useEffect, useState } from 'react'
import { BookOpen, Bug, Download, LifeBuoy, RefreshCw, ShieldCheck } from 'lucide-react'

import { API, apiFetch, errorFromPayload, safeJsonResponse } from '../../api/client'
import { getNativeUpdateChannel, openNativeExternalUrl, setNativeUpdateChannel } from '../../shared/desktop'

async function requestJson(path, options) {
  const response = await apiFetch(`${API}${path}`, options)
  const payload = await safeJsonResponse(response, null)
  if (!response.ok) throw new Error(errorFromPayload(payload, `HTTP ${response.status}`))
  return payload
}

export default function MassReleasePanel({ productMode = 'stable', onCreateSupportBundle }) {
  const [privacy, setPrivacy] = useState(null)
  const [crashes, setCrashes] = useState(null)
  const [commerce, setCommerce] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [channel, setChannel] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function refresh() {
    const [nextPrivacy, nextCrashes, nextCommerce, nextReadiness, nextChannel] = await Promise.all([
      requestJson('/privacy/status'),
      requestJson('/crash-reports/status'),
      requestJson('/commerce/config'),
      requestJson('/release-readiness'),
      getNativeUpdateChannel(),
    ])
    setPrivacy(nextPrivacy)
    setCrashes(nextCrashes)
    setCommerce(nextCommerce)
    setReadiness(nextReadiness)
    setChannel(nextChannel)
  }

  useEffect(() => { refresh().catch(error => setMessage(error.message)) }, [])

  async function updateCrashConsent(enabled) {
    if (!privacy) return
    setBusy(true)
    setMessage('')
    try {
      const next = await requestJson('/privacy/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          telemetry_enabled: Boolean(privacy.telemetry_enabled),
          crash_reports_enabled: enabled,
          accepted_privacy: Boolean(privacy.accepted_privacy),
          accepted_terms: Boolean(privacy.accepted_terms),
        }),
      })
      setPrivacy(next)
      setCrashes(await requestJson('/crash-reports/status'))
      setMessage(enabled ? 'Анонимная отправка crash reports включена.' : 'Отправка crash reports отключена. Локальные отчёты можно удалить ниже.')
    } catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }

  async function crashAction(action) {
    setBusy(true)
    setMessage('')
    try {
      const result = await requestJson('/crash-reports' + (action === 'flush' ? '/flush' : ''), { method: action === 'clear' ? 'DELETE' : 'POST' })
      setCrashes(result)
      setMessage(result.message || (action === 'clear' ? 'Локальные crash reports удалены.' : 'Готово.'))
    } catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }

  async function changeChannel(nextChannel) {
    setBusy(true)
    try {
      const result = await setNativeUpdateChannel(nextChannel)
      if (!result) throw new Error('Каналы обновлений доступны только в desktop-приложении.')
      setChannel(result)
      setMessage(result.message || 'Канал обновлений сохранён.')
    } catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }

  async function openUrl(url) {
    if (!url) return
    const opened = await openNativeExternalUrl(url)
    if (!opened && typeof window !== 'undefined') window.open(url, '_blank', 'noopener,noreferrer')
  }

  const blockers = readiness?.checks?.filter(item => !item.ok && item.severity === 'blocker') || []

  return <section className="uCard massReleasePanel">
    <div className="uCardHead">
      <div><span>Поддержка и надёжность</span><h3>Помощь, обновления и отчёты об ошибках</h3><p>Пользовательские видео и транскрипты не включаются в автоматические технические отчёты.</p></div>
      <div className="safeBadge ok"><ShieldCheck size={16}/> Локальная защита</div>
    </div>

    <div className="paidBetaGrid">
      <div className="paidBetaBlock">
        <div className="paidBetaTitle"><LifeBuoy size={18}/><b>Поддержка</b></div>
        <p>При проблеме сначала создай безопасный диагностический ZIP. Он содержит версии компонентов и обезличенные логи.</p>
        <div className="paidBetaActions"><button onClick={onCreateSupportBundle} disabled={busy}>Создать отчёт</button>{commerce?.support_url && <button onClick={() => openUrl(commerce.support_url)}>Открыть поддержку</button>}</div>
      </div>

      <div className="paidBetaBlock">
        <div className="paidBetaTitle"><Bug size={18}/><b>Crash reports</b></div>
        <label className="betaToggle"><input type="checkbox" checked={Boolean(privacy?.crash_reports_enabled)} onChange={event => updateCrashConsent(event.target.checked)} disabled={busy || !privacy?.accepted_privacy || !privacy?.accepted_terms}/><span><b>Разрешить анонимную отправку падений</b><small>Только тип ошибки, этап и версия. Без контента проекта и исходных путей.</small></span></label>
        <small>Локально сохранено: {crashes?.queued_reports ?? 0}. {crashes?.network_upload_configured ? 'HTTPS-сервер настроен.' : 'Сервер не настроен — ничего не отправляется.'}</small>
        <div className="paidBetaActions">{crashes?.crash_reports_enabled && crashes?.network_upload_configured && <button onClick={() => crashAction('flush')} disabled={busy || !(crashes?.queued_reports > 0)}>Отправить сейчас</button>}<button className="textButton" onClick={() => crashAction('clear')} disabled={busy || !(crashes?.queued_reports > 0)}>Удалить локальные отчёты</button></div>
      </div>

      <div className="paidBetaBlock">
        <div className="paidBetaTitle"><RefreshCw size={18}/><b>Канал обновлений</b></div>
        <p>Stable получает только проверенные версии. Beta — ранние функции и более высокий риск ошибок.</p>
        <select value={channel?.channel || 'stable'} onChange={event => changeChannel(event.target.value)} disabled={busy || channel?.allowSwitch === false}><option value="stable">Stable — рекомендуется</option><option value="beta">Beta — ранний доступ</option></select>
        {channel?.restartRequired && <small>Перезапусти приложение, чтобы применить канал.</small>}
      </div>

      <div className="paidBetaBlock">
        <div className="paidBetaTitle"><BookOpen size={18}/><b>Быстрая помощь</b></div>
        <details><summary>Анализ идёт слишком долго</summary><p>Переключись на «Быстрее», закрой тяжёлые программы и проверь свободное место. В простом режиме программа сама снизит нагрузку.</p></details>
        <details><summary>Не запускается локальный AI</summary><p>Открой мастер первого запуска, проверь Ollama и скачанную модель. Затем повтори системную проверку.</p></details>
        <details><summary>Рендер остановился</summary><p>Открой Диагностику проекта. После аварийного закрытия Highlight Studio предложит продолжить с последнего checkpoint.</p></details>
      </div>
    </div>

    {productMode === 'pro' && readiness && <div className="releaseGateSummary">
      <div><Download size={18}/><b>Внутренний mass-release gate: {readiness.score}/100</b><span>{readiness.ready_for_mass_release ? 'Все обязательные условия подтверждены.' : `Осталось блокеров: ${blockers.length}.`}</span></div>
      {!readiness.ready_for_mass_release && <details><summary>Показать незакрытые условия</summary><ul>{blockers.slice(0, 12).map(item => <li key={item.id}>{item.message}</li>)}</ul></details>}
    </div>}
    {message && <div className="betaMessage">{message}</div>}
  </section>
}
