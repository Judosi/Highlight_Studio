export const REFRAME_CHOICES = [
  ['auto', 'Автоматически'], ['blur_background', 'Весь кадр + размытый фон'],
  ['smart_zoom', 'Крупнее по центру + фон'], ['center_crop', 'Обрезать по центру'],
  ['fit', 'Весь кадр с полями'], ['smart_face', 'Следить за лицом'],
  ['gameplay_facecam', 'Игра + веб-камера'],
]

export const REFRAME_HELP = {
  auto: 'Выбирает компоновку по найденным лицам: веб-камера в углу, слежение или увеличенный центр с фоном. Проверь результат после сборки.',
  blur_background: 'Сохраняет весь исходный кадр. Свободное место заполняется размытым фоном. Подходит для компаний и общих планов.',
  smart_zoom: 'Немного увеличивает центр на размытом фоне. Края могут обрезаться; камера не следует за человеком.',
  center_crop: 'Заполняет вертикальный кадр и обрезает края. Центр неподвижен — проверь, не обрезан ли человек.',
  fit: 'Сохраняет весь кадр без размытия. Свободное место заполняется чёрными полями.',
  smart_face: 'Камера плавно следует за основным найденным лицом. Если лицо не найдено, используется увеличенный центр с фоном.',
  gameplay_facecam: 'Ищет устойчивую веб-камеру в углу: лицо и игра размещаются друг над другом. Если веб-камера не найдена, используется слежение за лицом или увеличенный центр.',
}

export function reframeResult(report) {
  if (!report?.resolved_mode) return null
  const names = Object.fromEntries([...REFRAME_CHOICES, ['horizontal','Исходный формат']])
  const reasons = {
    optional_face_tracking_unavailable: 'Детектор лиц не удалось запустить. Перезапусти приложение через START_HERE.bat; подробности — в журнале задачи.',
    no_face_fallback: 'Лицо не найдено. Использован увеличенный центр с фоном.',
    auto_safe_fallback: 'Недостаточно уверенных обнаружений лица. Использован увеличенный центр с фоном.',
    facecam_not_stable_using_face_tracking: 'Устойчивая веб-камера в углу не найдена. Использовано слежение за лицом.',
    stable_corner_facecam: 'Веб-камера найдена в углу. Проверь, что в отдельную область попало нужное лицо.',
    auto_gameplay_facecam: 'Автоматически найдена веб-камера в углу.',
    face_tracking: 'Кадрирование построено по найденному лицу.',
    auto_face_tracking: 'Автоматически выбрано кадрирование по лицу.',
  }
  return {requested:names[report.requested_mode] || report.requested_mode,
    applied:names[report.resolved_mode] || report.resolved_mode,
    note:reasons[report.reason] || '',
    fallback:['optional_face_tracking_unavailable','no_face_fallback','auto_safe_fallback','facecam_not_stable_using_face_tracking'].includes(report.reason)}
}

export function parseShortTime(value) {
  const text = String(value ?? '').trim().replace(',', '.')
  if (!/^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$/.test(text)) return null
  const parts = text.split(':').map(Number)
  if (parts.slice(1).some(number => number >= 60)) return null
  const seconds = parts.reduce((total, part) => total * 60 + part, 0)
  return Number.isFinite(seconds) ? Math.round(seconds * 1000) / 1000 : null
}

export function formatShortTime(value) {
  const total = Math.round(Math.max(0, Number(value) || 0) * 1000)
  const hours = Math.floor(total / 3600000)
  const minutes = Math.floor(total / 60000) % 60
  const seconds = Math.floor(total / 1000) % 60
  const fraction = String(total % 1000).padStart(3,'0').replace(/0$/, '')
  return `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}.${fraction}`
}

export function shortPayload(draft, settings = {}, sourceDuration = 0) {
  const start = parseShortTime(draft.start)
  const end = parseShortTime(draft.end)
  const title = String(draft.title || '').trim()
  if (!title || title.length > 180) throw new Error('Укажи название: от 1 до 180 символов.')
  if (start === null || end === null) throw new Error('Укажи время в формате 00:00:00.00 или в секундах.')
  if (end <= start) throw new Error('Конец должен быть позже начала.')
  const min = 1
  const max = Math.max(min, Math.min(180, Number(settings.shorts_max_seconds) || 60))
  if (sourceDuration > 0 && end > sourceDuration) throw new Error('Конец выходит за длительность исходного видео.')
  if (end - start < min - .001 || end - start > max + .001) throw new Error(`Длительность ролика должна быть от ${min} до ${max} секунд.`)
  const caption = draft.caption_text ?? null
  if (caption !== null && String(caption).length > 2000) throw new Error('Текст субтитров: не больше 2000 символов.')
  return {title,start,end,reframe_mode:draft.reframe_mode || settings.shorts_reframe_mode || 'auto',
    hook_text:String(draft.hook_text ?? draft.hook ?? '').slice(0,240),caption_text:caption}
}

export function shortIdentity(candidate) {
  return JSON.stringify([candidate?.title, candidate?.start, candidate?.end, candidate?.source_candidate_id ?? candidate?.id])
}

export function outputUrl(output, authQuery) {
  if (!output?.url) return ''
  const url = `${output.url}${authQuery(output.url)}`
  return `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(output.revision || output.modified_at || output.size_mb || '')}`
}

export function shortIndexFromOutput(output) {
  const match = String(output?.name || '').match(/^short_(\d+)\.mp4$/i)
  return match && Number(match[1]) > 0 ? Number(match[1]) : null
}
