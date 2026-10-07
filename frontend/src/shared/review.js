export function candidateRejectKey(item = {}) {
  return `${item?.id ?? 'clip'}-${Number(item?.start || 0).toFixed(2)}-${Number(item?.end || 0).toFixed(2)}`
}

export function sourceCandidateKey(item = {}) {
  return String(item?.source_candidate_key || candidateRejectKey(item))
}

export function segmentIndexFor(segments = [], item = {}, tolerance = 0.15) {
  if (!item) return -1
  const itemKey = sourceCandidateKey(item)
  const start = Number(item.start)
  const end = Number(item.end)
  return segments.findIndex((segment) => {
    if (segment?.source_candidate_key && String(segment.source_candidate_key) === itemKey) return true
    const segmentStart = Number(segment?.start)
    const segmentEnd = Number(segment?.end)
    return Number.isFinite(start) && Number.isFinite(end)
      && Number.isFinite(segmentStart) && Number.isFinite(segmentEnd)
      && Math.abs(segmentStart - start) < tolerance
      && Math.abs(segmentEnd - end) < tolerance
  })
}

export function withSourceCandidateKey(item = {}) {
  return {
    ...item,
    source_candidate_key: sourceCandidateKey(item),
  }
}

export function findOverlapIndex(segments = [], item = {}, ignoreIndex = -1) {
  const start = Number(item?.start)
  const end = Number(item?.end)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return -1
  return segments.findIndex((segment, index) => {
    if (index === ignoreIndex) return false
    const otherStart = Number(segment?.start)
    const otherEnd = Number(segment?.end)
    if (!Number.isFinite(otherStart) || !Number.isFinite(otherEnd)) return false
    return Math.max(otherStart, start) < Math.min(otherEnd, end)
  })
}

export function parseTrimBounds(startValue, endValue, maxDuration = null) {
  if (String(startValue ?? '').trim() === '' || String(endValue ?? '').trim() === '') {
    return { ok: false, message: 'Укажи корректные числовые границы фрагмента.' }
  }
  const start = Number(startValue)
  const end = Number(endValue)
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return { ok: false, message: 'Укажи корректные числовые границы фрагмента.' }
  }
  if (start < 0) return { ok: false, message: 'Начало фрагмента не может быть меньше нуля.' }
  if (end <= start) return { ok: false, message: 'Конец фрагмента должен быть позже начала.' }
  if (end - start < 0.5) return { ok: false, message: 'Фрагмент должен длиться не меньше 0,5 секунды.' }
  if (Number.isFinite(Number(maxDuration)) && Number(maxDuration) > 0 && end > Number(maxDuration) + 0.05) {
    return { ok: false, message: 'Конец фрагмента выходит за длительность исходного видео.' }
  }
  return { ok: true, start, end }
}
