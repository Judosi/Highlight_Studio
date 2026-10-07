export function secondsToTc(sec) {
  sec = Math.max(0, Number(sec) || 0)
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = (sec % 60).toFixed(2).padStart(5, '0')
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${s}`
}

export function formatDuration(sec) {
  sec = Number(sec)
  if (!Number.isFinite(sec) || sec <= 0) return '—'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  if (h > 0) return `${h}ч ${String(m).padStart(2, '0')}м`
  if (m > 0) return `${m}м ${String(s).padStart(2, '0')}с`
  return `${s}с`
}

export function progressKindLabel(source) {
  if (source === 'real_counter') return 'реальный счётчик'
  if (source === 'heartbeat') return 'этап выполняется'
  if (source === 'optimistic_ui') return 'команда запущена'
  if (source === 'estimated_global') return 'оценка по этапу'
  if (source === 'done') return 'готово'
  if (source === 'error') return 'ошибка'
  if (source === 'cancelled') return 'остановлено'
  return 'ожидание'
}
