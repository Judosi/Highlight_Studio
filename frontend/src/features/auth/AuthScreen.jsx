import { useEffect, useState } from 'react'
import { ArrowRight, Eye, EyeOff, Film, LockKeyhole, Mail, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import { API, apiFetch, errorFromPayload, safeJsonResponse } from '../../api/client'

function initialFlow() {
  const params = new URLSearchParams(window.location.search)
  if (window.location.pathname.includes('reset-password') && params.get('token')) {
    return { mode: 'reset', token: params.get('token') }
  }
  if (window.location.pathname.includes('verify-email') && params.get('token')) {
    return { mode: 'verify', token: params.get('token') }
  }
  return { mode: 'login', token: '' }
}

export default function AuthScreen({ status, onAuthenticated }) {
  const [flow] = useState(() => initialFlow())
  const registrationAllowed = Boolean(status?.registration_allowed)
  const [mode, setMode] = useState(flow.mode)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [form, setForm] = useState({ email: '', display_name: '', password: '', remember: true })
  const [showPassword, setShowPassword] = useState(false)

  useEffect(() => {
    if (!registrationAllowed && mode === 'register') setMode('login')
  }, [registrationAllowed, mode])

  useEffect(() => {
    if (mode !== 'verify' || !flow.token) return
    let active = true
    setBusy(true)
    apiFetch(`${API}/auth/verify-email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: flow.token }),
    })
      .then(async (response) => {
        const payload = await safeJsonResponse(response, {})
        if (!response.ok) throw new Error(errorFromPayload(payload, 'Не удалось подтвердить email'))
        if (active) setMessage(payload.message || 'Email подтверждён. Теперь можно войти.')
      })
      .catch((caught) => { if (active) setError(caught.message) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [flow.token, mode])

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      if (mode === 'forgot') {
        const response = await apiFetch(`${API}/auth/request-password-reset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: form.email }),
        })
        const payload = await safeJsonResponse(response, {})
        if (!response.ok) throw new Error(errorFromPayload(payload, 'Не удалось запросить сброс пароля'))
        setMessage(payload.message || 'Проверь почту.')
        return
      }
      if (mode === 'reset') {
        const response = await apiFetch(`${API}/auth/reset-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: flow.token, new_password: form.password }),
        })
        const payload = await safeJsonResponse(response, {})
        if (!response.ok) throw new Error(errorFromPayload(payload, 'Не удалось изменить пароль'))
        window.history.replaceState({}, '', '/')
        setMode('login')
        setForm({ ...form, password: '' })
        setMessage(payload.message || 'Пароль изменён. Войди снова.')
        return
      }
      const action = mode === 'login' ? 'login' : 'register'
      const response = await apiFetch(`${API}/auth/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const payload = await safeJsonResponse(response, {})
      if (!response.ok) throw new Error(errorFromPayload(payload, 'Не удалось выполнить вход'))
      if (payload.requires_verification) {
        setMode('login')
        setForm({ ...form, password: '' })
        setMessage(payload.verification_email_sent
          ? 'Аккаунт создан. Подтверди email по ссылке из письма.'
          : 'Аккаунт создан, но почтовый сервер не настроен. Обратись к администратору.')
        return
      }
      onAuthenticated(payload.user)
    } catch (caught) {
      setError(caught.message)
    } finally {
      setBusy(false)
    }
  }

  function switchMode(nextMode) {
    if (mode === 'verify' || mode === 'reset') window.history.replaceState({}, '', '/')
    setMode(nextMode)
    setError('')
    setMessage('')
    setForm({ ...form, password: '' })
  }

  const titles = {
    login: ['С возвращением', 'Войти в Highlight Studio', 'Продолжи работу над своими проектами.'],
    register: ['Создание аккаунта', 'Начать работу', 'Первый зарегистрированный пользователь станет администратором.'],
    forgot: ['Восстановление доступа', 'Сбросить пароль', 'Введи email — мы отправим безопасную ссылку для смены пароля.'],
    reset: ['Новый пароль', 'Восстановить доступ', 'Придумай новый пароль длиной не менее 10 символов.'],
    verify: ['Подтверждение аккаунта', 'Проверяем email', 'Это займёт несколько секунд.'],
  }
  const [eyebrow, title, description] = titles[mode] || titles.login

  return (
    <main className="authPage">
      <section className="authVisual">
        <div className="authBrand">
          <span className="authLogo"><Film size={26} /></span>
          <div><b>Highlight Studio</b><small>AI VIDEO EDITOR</small></div>
        </div>
        <div className="authHero">
          <span className="authEyebrow"><Sparkles size={15} /> Умный монтаж длинных видео</span>
          <h1>Из стрима — в готовый ролик без часов ручного просмотра.</h1>
          <p>Командная работа, безопасные проекты и понятный путь от Twitch VOD до публикации.</p>
          <div className="authBenefits">
            <span><ShieldCheck /> Проекты видят только участники</span>
            <span><LockKeyhole /> Защищённые сессии и восстановление доступа</span>
            <span><UserRound /> Владелец, редактор и зритель</span>
          </div>
        </div>
        <div className="authGlow" />
      </section>

      <section className="authPanel">
        <form className="authCard" onSubmit={submit}>
          <div className="authCardHead">
            <span>{eyebrow}</span>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>

          {mode === 'register' && (
            <label>
              <span>Имя</span>
              <div className="authInput">
                <UserRound />
                <input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Как к тебе обращаться" />
              </div>
            </label>
          )}

          {['login', 'register', 'forgot'].includes(mode) && (
            <label>
              <span>Email</span>
              <div className="authInput">
                <Mail />
                <input type="email" autoComplete="email" required value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="name@example.com" />
              </div>
            </label>
          )}

          {['login', 'register', 'reset'].includes(mode) && (
            <label>
              <span>{mode === 'reset' ? 'Новый пароль' : 'Пароль'}</span>
              <div className="authInput">
                <LockKeyhole />
                <input type={showPassword ? "text" : "password"} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={mode === 'login' ? 1 : 10} required value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder={mode === 'login' ? 'Введите пароль' : 'Минимум 10 символов'} />
                <button className="passwordReveal" type="button" aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff /> : <Eye />}</button>
              </div>
              {mode !== 'login' && <small className={`passwordStrength strength${Math.min(4, Math.floor(form.password.length / 3))}`}>Надёжность: {form.password.length < 10 ? 'нужно ещё символов' : form.password.length < 14 ? 'средняя' : 'хорошая'}</small>}
            </label>
          )}

          {['login', 'register'].includes(mode) && (
            <label className="authRemember">
              <input type="checkbox" checked={form.remember} onChange={(event) => setForm({ ...form, remember: event.target.checked })} />
              <span>Оставаться в системе</span>
            </label>
          )}

          {message && <div className="authSuccess" role="status">{message}</div>}
          {error && <div className="authError" role="alert">{error}</div>}

          {mode !== 'verify' && (
            <button className="authSubmit" disabled={busy}>
              {busy ? 'Подожди…' : mode === 'login' ? 'Войти' : mode === 'register' ? 'Создать аккаунт' : mode === 'forgot' ? 'Отправить инструкцию' : 'Изменить пароль'}
              <ArrowRight />
            </button>
          )}

          {mode === 'verify' && (
            <button className="authSubmit" type="button" disabled={busy} onClick={() => switchMode('login')}>
              {busy ? 'Проверяем…' : 'Перейти ко входу'} <ArrowRight />
            </button>
          )}

          <div className="authSwitch">
            {mode === 'login' && (
              <>
                <button type="button" onClick={() => switchMode('forgot')}>Забыли пароль?</button>
                {registrationAllowed
                  ? <> · Нет аккаунта? <button type="button" onClick={() => switchMode('register')}>Регистрация</button></>
                  : <span> · Регистрация закрыта администратором.</span>}
              </>
            )}
            {mode === 'register' && <>Уже зарегистрирован? <button type="button" onClick={() => switchMode('login')}>Войти</button></>}
            {['forgot', 'reset', 'verify'].includes(mode) && <button type="button" onClick={() => switchMode('login')}>Вернуться ко входу</button>}
          </div>
        </form>
      </section>
    </main>
  )
}
