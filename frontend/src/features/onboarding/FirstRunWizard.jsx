import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, FolderOpen, HardDrive, ShieldCheck, Sparkles } from 'lucide-react'

import { API, apiFetch, errorFromPayload, safeJsonResponse } from '../../api/client'
import {
  chooseNativeProjectDirectory,
  setNativeProjectsDirectory,
  installNativeOllama,
  pullNativeDefaultOllamaModel,
  subscribeNativeLocalAiStatus,
} from '../../shared/desktop'

const steps = [
  { id: 'welcome', title: 'Добро пожаловать' },
  { id: 'system', title: 'Проверка компьютера' },
  { id: 'storage', title: 'Папка проектов' },
  { id: 'ai', title: 'Локальный AI' },
]

function checkOk(checks, key) {
  return Boolean(checks?.[key]?.ok)
}

export default function FirstRunWizard({ open, systemCheck, systemCheckLoading, desktopInfo, onRunSystemCheck, onComplete, onClose }) {
  const dialogRef = useRef(null)
  const previousFocusRef = useRef(null)
  const onCloseRef = useRef(onClose)
  const [index, setIndex] = useState(0)
  const [projectsDir, setProjectsDir] = useState(desktopInfo?.projectsDir || '')
  const [telemetryEnabled, setTelemetryEnabled] = useState(false)
  const [acceptedPrivacy, setAcceptedPrivacy] = useState(false)
  const [acceptedTerms, setAcceptedTerms] = useState(false)
  const [saving, setSaving] = useState(false)
  const [aiSetupBusy, setAiSetupBusy] = useState(false)
  const [message, setMessage] = useState('')
  const checks = useMemo(() => systemCheck?.checks || {}, [systemCheck])
  const requiredReady = checkOk(checks, 'ffmpeg') && checkOk(checks, 'ffprobe') && checkOk(checks, 'faster_whisper')
  const ollamaState = checks?.ollama_server || {}
  const ollamaReady = Boolean(ollamaState.ok)
  const textModelReady = Boolean(ollamaState.text_model_ok)
  const current = steps[index]

  useEffect(() => subscribeNativeLocalAiStatus(payload => {
    if (payload?.message) setMessage(payload.message)
  }), [])

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return
    previousFocusRef.current = document.activeElement
    const dialog = dialogRef.current
    dialog?.focus()
    const handleKeyDown = event => {
      if (event.key === 'Escape' && onCloseRef.current) {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])')]
      if (!focusable.length) {
        event.preventDefault()
        dialog.focus()
        return
      }
      const first = focusable[0]
      const last = focusable.at(-1)
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocusRef.current?.focus?.()
    }
  }, [open])

  const readiness = useMemo(() => [
    ['Обработка видео', checkOk(checks, 'ffmpeg') && checkOk(checks, 'ffprobe')],
    ['Распознавание речи', checkOk(checks, 'faster_whisper')],
    ['Локальный AI', ollamaReady && textModelReady],
  ], [checks, ollamaReady, textModelReady])

  if (!open) return null

  async function chooseProjectsDir() {
    setMessage('')
    const result = await chooseNativeProjectDirectory()
    if (!result?.ok) return
    const saved = await setNativeProjectsDirectory(result.path)
    if (saved?.ok) {
      setProjectsDir(saved.path)
      setMessage('Папка сохранена. Она будет использоваться после следующего запуска приложения.')
    } else {
      setMessage(saved?.message || 'Не удалось сохранить папку проектов.')
    }
  }


  async function setupLocalAi(action) {
    setAiSetupBusy(true)
    setMessage(action === 'install' ? 'Устанавливаем Ollama через Windows Package Manager…' : 'Скачиваем рекомендуемую модель. Это может занять много времени и несколько гигабайт.')
    try {
      const result = action === 'install' ? await installNativeOllama() : await pullNativeDefaultOllamaModel()
      if (!result?.ok) throw new Error(result?.message || result?.output || 'Операция не завершилась')
      setMessage(action === 'install' ? 'Ollama установлена. Запусти повторную проверку; Windows может потребовать перезапуск приложения.' : 'Модель qwen3:8b скачана. Повторяем проверку…')
      await onRunSystemCheck?.(false)
    } catch (error) {
      setMessage(`Не удалось выполнить автоматическую настройку: ${error.message}`)
    } finally {
      setAiSetupBusy(false)
    }
  }
  async function finish() {
    setSaving(true)
    setMessage('')
    try {
      const response = await apiFetch(`${API}/onboarding/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ai_mode: 'local', telemetry_enabled: telemetryEnabled, accepted_privacy: acceptedPrivacy, accepted_terms: acceptedTerms }),
      })
      const payload = await safeJsonResponse(response, null)
      if (!response.ok) throw new Error(errorFromPayload(payload, 'Не удалось завершить настройку'))
      onComplete?.(payload)
    } catch (error) {
      setMessage(error.message)
    } finally {
      setSaving(false)
    }
  }

  return <div className="firstRunOverlay" role="dialog" aria-modal="true" aria-labelledby="first-run-title" aria-describedby="first-run-progress">
    <section ref={dialogRef} className="firstRunWizard" tabIndex="-1">
      <aside className="firstRunSteps">
        <div className="wizardBrand"><Sparkles size={22}/><div><b>Highlight Studio</b><span>Первый запуск</span></div></div>
        {steps.map((step, stepIndex) => <div key={step.id} aria-current={stepIndex === index ? 'step' : undefined} className={`wizardStep ${stepIndex === index ? 'active' : ''} ${stepIndex < index ? 'done' : ''}`}>
          <span>{stepIndex < index ? <Check size={14}/> : stepIndex + 1}</span><b>{step.title}</b>
        </div>)}
        <p>Видео и транскрипты остаются на этом компьютере.</p>
      </aside>
      <div className="firstRunContent">
        <div className="wizardTopline"><span id="first-run-progress">Шаг {index + 1} из {steps.length}</span>{onClose && <button className="textButton" onClick={onClose}>Настроить позже</button>}</div>

        {current.id === 'welcome' && <div className="wizardPanel">
          <div className="wizardIcon"><Sparkles size={32}/></div>
          <h2 id="first-run-title">Подготовим приложение к первому проекту</h2>
          <p>Highlight Studio найдёт сильные моменты в длинном видео, поможет проверить монтаж и соберёт итоговый ролик. Настройка займёт несколько простых шагов.</p>
          <div className="wizardBenefits"><div><ShieldCheck/><span><b>Локальная обработка</b><small>Исходные видео не отправляются автоматически в облако.</small></span></div><div><HardDrive/><span><b>Большие VOD</b><small>Проекты сохраняются в отдельной пользовательской папке.</small></span></div></div>
        </div>}

        {current.id === 'system' && <div className="wizardPanel">
          <div className="wizardIcon"><ShieldCheck size={32}/></div>
          <h2 id="first-run-title">Проверим компоненты</h2>
          <p>Программа проверит встроенный видеодвижок, распознавание речи и локальный AI. Проверка ничего не скачивает.</p>
          <div className="wizardChecks">{readiness.map(([label, ok]) => <div key={label} className={ok ? 'ok' : 'warn'}><span>{ok ? '✓' : '!'}</span><b>{label}</b><small>{ok ? 'Готово' : 'Нужна настройка'}</small></div>)}</div>
          <button onClick={() => onRunSystemCheck?.(false)} disabled={systemCheckLoading}>{systemCheckLoading ? 'Проверяю…' : 'Запустить проверку'}</button>
          {!requiredReady && systemCheck && <p className="wizardWarning">Для обработки видео нужны FFmpeg, FFprobe и модуль распознавания. В установленной сборке они должны быть встроены.</p>}
        </div>}

        {current.id === 'storage' && <div className="wizardPanel">
          <div className="wizardIcon"><FolderOpen size={32}/></div>
          <h2 id="first-run-title">Где хранить проекты</h2>
          <p>Исходники и результаты могут занимать много места. Выбери диск с достаточным свободным пространством.</p>
          <div className="wizardPath"><span>{projectsDir || desktopInfo?.projectsDir || 'Папка будет выбрана автоматически'}</span>{desktopInfo?.desktop && <button onClick={chooseProjectsDir}>Изменить</button>}</div>
          {!desktopInfo?.desktop && <p className="wizardHint">В браузерном режиме используется папка проектов, настроенная локальным движком.</p>}
        </div>}

        {current.id === 'ai' && <div className="wizardPanel">
          <div className="wizardIcon"><Sparkles size={32}/></div>
          <h2 id="first-run-title">Локальный AI</h2>
          <p>Для смыслового отбора моментов используется Ollama. Если она пока не установлена, интерфейс всё равно откроется, а диагностика подскажет следующий шаг.</p>
          <div className={`wizardAiState ${ollamaReady && textModelReady ? 'ok' : 'warn'}`}><b>{ollamaReady ? (textModelReady ? 'Ollama и текстовая модель готовы' : 'Ollama подключена, но модель не скачана') : 'Ollama пока не отвечает'}</b><span>{ollamaReady && textModelReady ? 'Можно запускать полный AI-анализ.' : 'Автоматическая настройка доступна в Windows desktop-версии.'}</span></div>
          {desktopInfo?.desktop && !ollamaReady && <button onClick={() => setupLocalAi('install')} disabled={aiSetupBusy}>{aiSetupBusy ? 'Настраиваю…' : 'Установить Ollama автоматически'}</button>}
          {desktopInfo?.desktop && ollamaReady && !textModelReady && <button onClick={() => setupLocalAi('model')} disabled={aiSetupBusy}>{aiSetupBusy ? 'Скачиваю модель…' : 'Скачать рекомендуемую модель (qwen3:8b)'}</button>}
          <label className="wizardConsent"><input type="checkbox" checked={acceptedPrivacy} onChange={event => setAcceptedPrivacy(event.target.checked)}/><span><b>Я принимаю политику конфиденциальности</b><small>Видео и транскрипты остаются локально, если пользователь отдельно не запускает облачную функцию.</small></span></label>
          <label className="wizardConsent"><input type="checkbox" checked={acceptedTerms} onChange={event => setAcceptedTerms(event.target.checked)}/><span><b>Я принимаю условия использования</b><small>Пробный период может быть ограничен по времени; проекты и готовые файлы остаются доступны после его завершения.</small></span></label>
          <label className="wizardConsent"><input type="checkbox" checked={telemetryEnabled} onChange={event => setTelemetryEnabled(event.target.checked)} disabled={!acceptedPrivacy || !acceptedTerms}/><span><b>Отправлять анонимные технические события</b><small>Только этап, результат и код ошибки. Видео, аудио, транскрипты, названия проектов, пути, ключи и cookies не отправляются.</small></span></label>
        </div>}

        {message && <div className="wizardMessage" role="status" aria-live="polite">{message}</div>}
        <footer className="wizardFooter">
          <button onClick={() => setIndex(value => Math.max(0, value - 1))} disabled={index === 0}><ChevronLeft size={16}/> Назад</button>
          {index < steps.length - 1
            ? <button className="primaryStrong" onClick={() => setIndex(value => Math.min(steps.length - 1, value + 1))}>Продолжить <ChevronRight size={16}/></button>
            : <button className="primaryStrong" onClick={finish} disabled={saving || !acceptedPrivacy || !acceptedTerms}>{saving ? 'Сохраняю…' : 'Завершить настройку'}</button>}
        </footer>
      </div>
    </section>
  </div>
}
