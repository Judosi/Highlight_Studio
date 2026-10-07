const ERROR_WORDS = /ошиб|не удалось|не сохрани|не сработ|не создал|не запуст|не примен|не выгруз|не загруз|не открыл|не обнов|не восстанов|backend не отвечает|сервер не отвечает|fail|500|offline|поврежд/i
const WARNING_WORDS = /предупреж|нужно|не найден|недоступ|не отвечает|нельзя|ожида|проверь|только на экране|не подготов|нет соединения/i
const SUCCESS_WORDS = /готов|успеш|сохран|создан|примен[её]н|скопирован|пройдена|запущен|обновл[её]н|восстановлен|нормализован|добавлен|очищен|открыт проект/i

export function classifyNotice(message = '') {
  const text = String(message)
  // Negative or cautionary wording must win over words such as “создан” or
  // “сохранён”; otherwise “Проект создан, но есть предупреждение” looked green.
  if (ERROR_WORDS.test(text)) return 'error'
  if (WARNING_WORDS.test(text)) return 'warning'
  if (SUCCESS_WORDS.test(text)) return 'success'
  return 'info'
}

export function normalizeNotice(value) {
  if (!value) return null
  if (typeof value === 'object') {
    const type = value.type || classifyNotice(value.message || value.title || '')
    return {
      id: value.id || `notice-${Date.now()}`,
      type,
      title: value.title || {
        error: 'Не удалось выполнить действие',
        warning: 'Нужно внимание',
        success: 'Готово',
        info: 'Информация',
      }[type],
      message: String(value.message || ''),
      action: value.action || null,
      persistent: Boolean(value.persistent),
    }
  }
  const message = String(value)
  const type = classifyNotice(message)
  return {
    id: `notice-${Date.now()}`,
    type,
    title: {
      error: 'Не удалось выполнить действие',
      warning: 'Нужно внимание',
      success: 'Готово',
      info: 'Информация',
    }[type],
    message,
    action: null,
    persistent: type === 'error',
  }
}

export function hardwareProfileLabel(value = 'Auto') {
  const labels = {
    Auto: 'Автоматический режим',
    GTX1050Ti_16GB_Ryzen2600: 'Сбалансированный профиль',
    LowVRAM: 'Экономный профиль',
    CPU_Stable: 'Только процессор',
  }
  return labels[value] || 'Профиль подобран'
}
