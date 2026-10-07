import { useEffect, useMemo, useState } from 'react'
import { BadgeCheck, BarChart3, CreditCard, KeyRound, MessageSquare, ShieldCheck } from 'lucide-react'

import { API, apiFetch, errorFromPayload, safeJsonResponse } from '../../api/client'
import { openNativeExternalUrl } from '../../shared/desktop'

async function requestJson(path, options) {
  const response = await apiFetch(`${API}${path}`, options)
  const payload = await safeJsonResponse(response, null)
  if (!response.ok) throw new Error(errorFromPayload(payload, `HTTP ${response.status}`))
  return payload
}

function stateLabel(status) {
  if (status?.state === 'active') return 'Лицензия активна'
  if (status?.state === 'trial') return `Пробный период: ${status.days_remaining ?? 0} дн.`
  if (status?.state === 'not_started') return 'Пробный период готов'
  return 'Нужна активация'
}

export default function PaidBetaPanel({ initialLicense, onLicenseChange }) {
  const [license, setLicense] = useState(initialLicense || null)
  const [privacy, setPrivacy] = useState(null)
  const [commerce, setCommerce] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [telemetry, setTelemetry] = useState(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [feedback, setFeedback] = useState({ category: 'quality', rating: 5, message: '', allow_contact: false, email: '' })

  const active = Boolean(license?.can_use)
  const statusClass = license?.state === 'active' ? 'ok' : ['trial', 'not_started'].includes(license?.state) ? 'warn' : 'bad'
  const canSendFeedback = feedback.message.trim().length >= 3
  const retention = useMemo(() => metrics?.candidate_to_final_percent ?? 0, [metrics])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      requestJson('/license/status'),
      requestJson('/privacy/status'),
      requestJson('/commerce/config'),
      requestJson('/beta/metrics'),
      requestJson('/telemetry/status'),
    ]).then(([nextLicense, nextPrivacy, nextCommerce, nextMetrics, nextTelemetry]) => {
      if (cancelled) return
      setLicense(nextLicense)
      setPrivacy(nextPrivacy)
      setCommerce(nextCommerce)
      setMetrics(nextMetrics)
      setTelemetry(nextTelemetry)
      onLicenseChange?.(nextLicense)
    }).catch(error => !cancelled && setMessage(error.message))
    return () => { cancelled = true }
  }, [onLicenseChange])

  async function runLicenseAction(action) {
    setBusy(true)
    setMessage('')
    try {
      const options = action === 'activate'
        ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) }
        : { method: 'POST' }
      const next = await requestJson(`/license/${action}`, options)
      setLicense(next)
      onLicenseChange?.(next)
      setMessage(next.message || 'Готово')
      if (action === 'activate') setKey('')
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function updateTelemetry(enabled) {
    if (!privacy) return
    setBusy(true)
    try {
      const next = await requestJson('/privacy/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          telemetry_enabled: enabled,
          crash_reports_enabled: privacy.crash_reports_enabled,
          accepted_privacy: Boolean(privacy.accepted_privacy),
          accepted_terms: Boolean(privacy.accepted_terms),
        }),
      })
      setPrivacy(next)
      setTelemetry(await requestJson('/telemetry/status'))
      setMessage(enabled ? 'Анонимная техническая диагностика включена.' : 'Диагностика отключена.')
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function flushTelemetry() {
    setBusy(true)
    setMessage('')
    try {
      const result = await requestJson('/telemetry/flush', { method: 'POST' })
      setTelemetry(result)
      setMessage(result.message || 'Диагностика отправлена.')
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function submitFeedback() {
    setBusy(true)
    setMessage('')
    try {
      const result = await requestJson('/beta/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(feedback),
      })
      setMessage(result.message)
      setFeedback(current => ({ ...current, message: '' }))
      setMetrics(await requestJson('/beta/metrics'))
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function openUrl(url) {
    if (!url) return
    const result = await openNativeExternalUrl(url)
    if (!result && typeof window !== 'undefined') window.open(url, '_blank', 'noopener,noreferrer')
  }

  return <section className="uCard paidBetaPanel">
    <div className="uCardHead">
      <div><span>Лицензия</span><h3>Пробный период и активация</h3><p>Пробный период и активная лицензия открывают AI-анализ, Twitch и рендер. Старые проекты остаются доступными.</p></div>
      <div className={`licenseState ${statusClass}`}><BadgeCheck size={18}/><b>{stateLabel(license)}</b></div>
    </div>

    <div className="paidBetaGrid">
      <div className="paidBetaBlock">
        <div className="paidBetaTitle"><KeyRound size={18}/><b>Активация</b></div>
        <p>{license?.message || 'Проверяем лицензию…'}</p>
        <small>Код устройства: {license?.device_id || '—'} · Тариф: {license?.plan || '—'}</small>
        {license?.device_code && <button className="textButton" onClick={async () => { await navigator.clipboard?.writeText?.(license.device_code); setMessage('Полный код устройства скопирован.') }}>Скопировать код устройства</button>}
        {license?.state !== 'active' && <div className="licenseInputRow"><input value={key} onChange={event => setKey(event.target.value)} placeholder="Вставь лицензионный ключ"/><button onClick={() => runLicenseAction('activate')} disabled={busy || key.trim().length < 8}>Активировать</button></div>}
        <div className="paidBetaActions">
          {license?.state === 'active' && <button onClick={() => runLicenseAction('refresh')} disabled={busy}>Проверить лицензию</button>}
          {license?.state === 'active' && <button className="textButton" onClick={() => runLicenseAction('deactivate')} disabled={busy}>Отвязать устройство</button>}
          {!active && commerce?.checkout_url && <button className="primaryStrong" onClick={() => openUrl(commerce.checkout_url)}><CreditCard size={16}/> Купить лицензию</button>}
          {commerce?.account_url && <button onClick={() => openUrl(commerce.account_url)}>Личный кабинет</button>}
        </div>
      </div>

      <div className="paidBetaBlock">
        <div className="paidBetaTitle"><ShieldCheck size={18}/><b>Приватность</b></div>
        <label className="betaToggle"><input type="checkbox" checked={Boolean(privacy?.telemetry_enabled)} onChange={event => updateTelemetry(event.target.checked)} disabled={busy || !privacy?.accepted_privacy || !privacy?.accepted_terms}/><span><b>Анонимная техническая диагностика</b><small>Только этапы, результат и код ошибки. Без видео, транскриптов, названий и локальных путей.</small></span></label>
        {privacy?.telemetry_enabled && <small>В очереди: {telemetry?.queued_events ?? 0}. {telemetry?.network_upload_configured ? 'HTTPS-сервер настроен.' : 'Сервер не настроен — данные остаются только локально.'}</small>}
        <div className="paidBetaActions">{privacy?.telemetry_enabled && telemetry?.network_upload_configured && <button onClick={flushTelemetry} disabled={busy || !(telemetry?.queued_events > 0)}>Отправить диагностику сейчас</button>}{commerce?.privacy_url && <button onClick={() => openUrl(commerce.privacy_url)}>Политика конфиденциальности</button>}{commerce?.terms_url && <button onClick={() => openUrl(commerce.terms_url)}>Условия использования</button>}</div>
      </div>

      <div className="paidBetaBlock metricsBlock">
        <div className="paidBetaTitle"><BarChart3 size={18}/><b>Локальные beta-метрики</b></div>
        <div className="betaMetricTiles"><div><strong>{metrics?.projects ?? 0}</strong><span>проектов</span></div><div><strong>{metrics?.render_outputs ?? 0}</strong><span>готовых роликов</span></div><div><strong>{retention}%</strong><span>кандидатов в финале</span></div><div><strong>{metrics?.feedback_items ?? 0}</strong><span>оценок моментов</span></div></div>
        <small>{metrics?.note || 'Метрики рассчитываются только на этом компьютере.'}</small>
      </div>

      <div className="paidBetaBlock feedbackBlock">
        <div className="paidBetaTitle"><MessageSquare size={18}/><b>Отзыв о продукте</b></div>
        <div className="betaFeedbackControls"><select value={feedback.category} onChange={event => setFeedback(current => ({ ...current, category: event.target.value }))}><option value="quality">Качество моментов</option><option value="bug">Ошибка</option><option value="speed">Скорость</option><option value="usability">Удобство</option><option value="idea">Идея</option><option value="other">Другое</option></select><select value={feedback.rating} onChange={event => setFeedback(current => ({ ...current, rating: Number(event.target.value) }))}>{[5,4,3,2,1].map(value => <option key={value} value={value}>{value} / 5</option>)}</select></div>
        <textarea value={feedback.message} onChange={event => setFeedback(current => ({ ...current, message: event.target.value }))} placeholder="Что сработало хорошо или что нужно исправить?" maxLength={2000}/>
        <label className="betaToggle compact"><input type="checkbox" checked={feedback.allow_contact} onChange={event => setFeedback(current => ({ ...current, allow_contact: event.target.checked }))}/><span>Можно связаться со мной по этому отзыву</span></label>
        {feedback.allow_contact && <input value={feedback.email} onChange={event => setFeedback(current => ({ ...current, email: event.target.value }))} placeholder="Email для ответа"/>}
        <div className="paidBetaActions"><button className="primaryStrong" onClick={submitFeedback} disabled={busy || !canSendFeedback}>Отправить отзыв</button>{commerce?.support_url && <button onClick={() => openUrl(commerce.support_url)}>Поддержка</button>}</div>
      </div>
    </div>
    {message && <div className="betaMessage">{message}</div>}
  </section>
}
