import React from 'react'

import { API, apiFetch } from '../api/client'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null, crashId: '' }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Highlight Studio UI error', error, info)
    apiFetch(`${API}/crash-reports/frontend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: String(error?.message || error || 'Frontend error').slice(0, 2000),
        stack: String(error?.stack || info?.componentStack || '').slice(0, 8000),
        route: typeof window !== 'undefined' ? window.location.pathname : '',
      }),
    }).then(response => response.ok ? response.json() : null)
      .then(payload => payload?.crash_id && this.setState({ crashId: payload.crash_id }))
      .catch(() => {})
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main style={{maxWidth: 760, margin: '64px auto', padding: 24, fontFamily: 'system-ui'}}>
        <h1>Интерфейс столкнулся с ошибкой</h1>
        <p>Проект и фоновые файлы не удалены. Перезагрузи окно; если ошибка повторится, создай отчёт для поддержки.</p>
        {this.state.crashId && <p>Код ошибки: <code>{this.state.crashId}</code></p>}
        <button onClick={() => window.location.reload()}>Перезагрузить интерфейс</button>
      </main>
    )
  }
}
