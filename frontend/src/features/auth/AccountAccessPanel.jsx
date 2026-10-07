import { useCallback, useEffect, useState } from 'react'
import { History, KeyRound, Laptop, LogOut, Shield, Smartphone, UserRound, UsersRound } from 'lucide-react'
import { API, apiFetch, errorFromPayload, safeJsonResponse } from '../../api/client'

const actionLabels = {
  'auth.register': 'Регистрация пользователя',
  'auth.login': 'Вход в систему',
  'auth.login_failed': 'Неудачная попытка входа',
  'auth.password_changed': 'Изменение пароля',
  'auth.session_revoked': 'Сессия завершена',
  'auth.sessions_revoked_all': 'Выход на всех устройствах',
  'admin.user_updated': 'Изменение прав пользователя',
  'project.member_changed': 'Изменение доступа к проекту',
  'project.member_removed': 'Удаление участника проекта',
  'project.deleted': 'Удаление проекта',
}

function sessionDevice(userAgent = '') {
  return /mobile|android|iphone|ipad/i.test(userAgent) ? Smartphone : Laptop
}

export default function AccountAccessPanel({ user, onLoggedOut }) {
  const [users, setUsers] = useState([])
  const [sessions, setSessions] = useState([])
  const [auditItems, setAuditItems] = useState([])
  const [message, setMessage] = useState('')
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' })
  const isAdmin = user?.global_role === 'admin'

  const loadUsers = useCallback(async () => {
    if (!isAdmin) return
    const response = await apiFetch(`${API}/admin/users`)
    if (response.ok) {
      const payload = await safeJsonResponse(response, { users: [] })
      setUsers(payload.users || [])
    }
  }, [isAdmin])

  const loadSessions = useCallback(async () => {
    const response = await apiFetch(`${API}/auth/sessions`)
    if (response.ok) {
      const payload = await safeJsonResponse(response, { sessions: [] })
      setSessions(payload.sessions || [])
    }
  }, [])

  const loadAudit = useCallback(async () => {
    if (!isAdmin) return
    const response = await apiFetch(`${API}/admin/audit?limit=20`)
    if (response.ok) {
      const payload = await safeJsonResponse(response, { items: [] })
      setAuditItems(payload.items || [])
    }
  }, [isAdmin])

  useEffect(() => {
    loadUsers().catch(() => {})
    loadSessions().catch(() => {})
    loadAudit().catch(() => {})
  }, [loadAudit, loadSessions, loadUsers])

  async function logout() {
    await apiFetch(`${API}/auth/logout`, { method: 'POST' })
    onLoggedOut?.()
  }

  async function logoutEverywhere() {
    if (!window.confirm('Завершить все активные сессии, включая эту?')) return
    const response = await apiFetch(`${API}/auth/logout-all`, { method: 'POST' })
    if (response.ok) onLoggedOut?.()
  }

  async function revokeSession(item) {
    const response = await apiFetch(`${API}/auth/sessions/${item.id}`, { method: 'DELETE' })
    const payload = await safeJsonResponse(response, {})
    if (!response.ok) {
      setMessage(errorFromPayload(payload, 'Не удалось завершить сессию'))
      return
    }
    if (payload.logged_out) onLoggedOut?.()
    else await loadSessions()
  }

  async function changePassword(event) {
    event.preventDefault()
    setMessage('')
    const response = await apiFetch(`${API}/auth/change-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(passwords),
    })
    const payload = await safeJsonResponse(response, {})
    if (!response.ok) {
      setMessage(errorFromPayload(payload, 'Не удалось изменить пароль'))
      return
    }
    setMessage(payload.message || 'Пароль изменён')
    setPasswords({ current_password: '', new_password: '' })
    window.setTimeout(() => onLoggedOut?.(), 700)
  }

  async function updateUser(item, changes) {
    setMessage('')
    const response = await apiFetch(`${API}/admin/users/${item.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        global_role: changes.global_role ?? item.global_role,
        is_active: changes.is_active ?? item.is_active,
      }),
    })
    const payload = await safeJsonResponse(response, {})
    if (!response.ok) {
      setMessage(errorFromPayload(payload, 'Не удалось обновить пользователя'))
      return
    }
    setMessage('Права пользователя обновлены')
    await Promise.all([loadUsers(), loadAudit()])
  }

  return (
    <section className="uCard accountAccess">
      <div className="uCardHead">
        <div>
          <span>Аккаунт</span>
          <h3>{user?.display_name || 'Пользователь'}</h3>
          <p>{user?.email} · {isAdmin ? 'Администратор системы' : 'Пользователь'}</p>
        </div>
        <button onClick={logout}><LogOut size={16} /> Выйти</button>
      </div>

      <div className="accountSecurity">
        <Shield />
        <div>
          <b>Защищённая web-сессия</b>
          <p>Пароль хранится как Argon2id-хеш, а токен сессии недоступен JavaScript.</p>
        </div>
      </div>

      <div className="accountSessions">
        <div className="sectionMiniTitle"><Laptop size={16} /> Активные устройства</div>
        {sessions.map((item) => {
          const DeviceIcon = sessionDevice(item.user_agent)
          return (
            <div className="sessionRow" key={item.id}>
              <DeviceIcon size={18} />
              <div>
                <b>{item.current ? 'Это устройство' : 'Другая сессия'}</b>
                <small>{item.user_agent || 'Неизвестный браузер'} · {item.ip_address || 'IP скрыт'}</small>
                <small>Активность: {new Date(item.last_seen_at).toLocaleString()}</small>
              </div>
              <button type="button" onClick={() => revokeSession(item)}>{item.current ? 'Выйти' : 'Завершить'}</button>
            </div>
          )
        })}
        {sessions.length > 1 && <button type="button" className="secondaryDanger" onClick={logoutEverywhere}>Выйти на всех устройствах</button>}
      </div>

      <form className="passwordForm" onSubmit={changePassword}>
        <div className="sectionMiniTitle"><KeyRound size={16} /> Изменить пароль</div>
        <input type="password" autoComplete="current-password" required value={passwords.current_password} onChange={(event) => setPasswords({ ...passwords, current_password: event.target.value })} placeholder="Текущий пароль" />
        <input type="password" autoComplete="new-password" minLength={10} required value={passwords.new_password} onChange={(event) => setPasswords({ ...passwords, new_password: event.target.value })} placeholder="Новый пароль — минимум 10 символов" />
        <button>Обновить пароль</button>
      </form>

      {isAdmin && (
        <div className="adminUsers">
          <div className="sectionMiniTitle"><UsersRound /> Пользователи</div>
          {users.map((item) => (
            <div className="adminUser" key={item.id}>
              <div className="avatar"><UserRound /></div>
              <div><b>{item.display_name}</b><small>{item.email}</small></div>
              <select value={item.global_role} onChange={(event) => updateUser(item, { global_role: event.target.value })}>
                <option value="user">Пользователь</option>
                <option value="admin">Администратор</option>
              </select>
              <label className="userActive"><input type="checkbox" checked={item.is_active} onChange={(event) => updateUser(item, { is_active: event.target.checked })} /><span>Активен</span></label>
            </div>
          ))}
        </div>
      )}

      {isAdmin && (
        <div className="auditList">
          <div className="sectionMiniTitle"><History /> Последние события безопасности</div>
          {auditItems.length === 0 && <p className="muted">Событий пока нет.</p>}
          {auditItems.map((item) => (
            <div className="auditItem" key={item.id}>
              <div><b>{actionLabels[item.action] || item.action}</b><small>{item.resource_type || 'system'} · {item.resource_id || '—'}</small></div>
              <time>{new Date(item.created_at).toLocaleString()}</time>
            </div>
          ))}
        </div>
      )}

      {message && <p className="muted" role="status">{message}</p>}
    </section>
  )
}
