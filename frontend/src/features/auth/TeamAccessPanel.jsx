import { useCallback, useEffect, useState } from 'react'
import { Trash2, UserPlus } from 'lucide-react'
import { API, apiFetch, errorFromPayload, safeJsonResponse } from '../../api/client'

const roleLabels = {
  owner: 'Владелец',
  editor: 'Редактор',
  viewer: 'Зритель',
}

export default function TeamAccessPanel({ project }) {
  const projectId = project?.id || ''
  const [members, setMembers] = useState([])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('editor')
  const [message, setMessage] = useState('')
  const [busyUserId, setBusyUserId] = useState('')

  const load = useCallback(async () => {
    if (!projectId) return
    const response = await apiFetch(`${API}/projects/${projectId}/members`)
    const payload = await safeJsonResponse(response, { members: [] })
    if (!response.ok) {
      setMessage(errorFromPayload(payload, 'Не удалось загрузить участников'))
      return
    }
    setMembers(payload.members || [])
  }, [projectId])

  useEffect(() => { load().catch(() => {}) }, [load])

  async function save(event) {
    event.preventDefault()
    setMessage('')
    const response = await apiFetch(`${API}/projects/${projectId}/members`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, role }),
    })
    const payload = await safeJsonResponse(response, {})
    if (!response.ok) {
      setMessage(errorFromPayload(payload, 'Не удалось изменить доступ'))
      return
    }
    setEmail('')
    setMessage('Доступ обновлён')
    await load()
  }

  async function remove(item) {
    if (!window.confirm(`Убрать ${item.user.display_name || item.user.email} из проекта?`)) return
    setBusyUserId(item.user.id)
    setMessage('')
    const response = await apiFetch(`${API}/projects/${projectId}/members/${item.user.id}`, { method: 'DELETE' })
    const payload = await safeJsonResponse(response, {})
    setBusyUserId('')
    if (!response.ok) {
      setMessage(errorFromPayload(payload, 'Не удалось удалить участника'))
      return
    }
    setMessage('Участник удалён')
    await load()
  }

  if (!projectId) return null
  return (
    <section className="uCard teamAccess">
      <div className="uCardHead">
        <div>
          <span>Команда</span>
          <h3>Доступ к проекту</h3>
          <p>Владелец управляет командой. Редактор может монтировать, зритель — смотреть и скачивать.</p>
        </div>
      </div>

      <div className="roleGuide">
        <article><b>Владелец</b><p>Полный доступ, управление командой и удаление проекта.</p></article>
        <article><b>Редактор</b><p>Анализ, монтаж, рендер и изменение настроек проекта.</p></article>
        <article><b>Зритель</b><p>Безопасный просмотр результатов без права изменений.</p></article>
      </div>

      <div className="teamMembers">
        {members.map((item) => (
          <div className="teamMember" key={item.user.id}>
            <div className="avatar">{(item.user.display_name || item.user.email)[0].toUpperCase()}</div>
            <div><b>{item.user.display_name}</b><small>{item.user.email}</small></div>
            <span className={`roleBadge ${item.role}`}>{roleLabels[item.role] || item.role}</span>
            <button
              type="button"
              className="iconDanger"
              aria-label={`Удалить ${item.user.display_name}`}
              disabled={busyUserId === item.user.id}
              onClick={() => remove(item)}
            >
              <Trash2 size={15} />
            </button>
          </div>
        ))}
      </div>

      <form className="teamInvite" onSubmit={save}>
        <div className="sectionMiniTitle"><UserPlus size={16} /> Добавить зарегистрированного пользователя</div>
        <input
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="Email пользователя"
        />
        <select value={role} onChange={(event) => setRole(event.target.value)}>
          <option value="editor">Редактор — монтаж</option>
          <option value="viewer">Зритель — просмотр</option>
          <option value="owner">Владелец — полный доступ</option>
        </select>
        <button>Добавить или изменить</button>
      </form>
      {message && <p className="muted" role="status">{message}</p>}
    </section>
  )
}
