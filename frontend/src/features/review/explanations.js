export function buildLocalMomentExplanation(item, settings = {}) {
  if (!item) return { what: 'Момент не выбран.', why: 'Выбери кандидат или фрагмент, чтобы увидеть объяснение.', value: '', risk: '' }
  const text = `${item.title || ''} ${item.reason || ''} ${item.text_preview || ''}`.toLowerCase()
  const kind = String(item.moment_type || '').toLowerCase()
  let what = item.what_happens || 'AI нашёл потенциально интересный фрагмент.'
  if (!item.what_happens) {
    if (kind.includes('conflict') || /конфликт|спор|охран|полици|крик|нелов|хаос/.test(text)) what = 'В моменте есть напряжение, спор, неловкость или резкая реакция.'
    else if (kind.includes('donation') || text.includes('донат')) what = 'Донат или чат меняет ход разговора и провоцирует реакцию.'
    else if (kind.includes('reaction') || /реакц|шок|жесть|смех|угар|смеш/.test(text)) what = 'Есть заметная эмоция: смех, удивление, шок или сильная реакция.'
    else if (kind.includes('story') || /истори|почему|решил|встрет|после/.test(text)) what = 'Фрагмент похож на мини-историю с причиной и развитием.'
    else if (kind.includes('visual') || Number(item.visual_score || 0) > 0) what = 'Важна не только речь, но и визуальное событие или смена сцены.'
  }
  const why = item.why_selected || `Выбран потому что score=${Number(item.score || 0).toFixed(1)}, hook=${item.hook_potential || 'medium'}, режим=${settings.edit_mode || 'Сбалансированный'}.`
  const value = item.viewer_value || 'Зритель сможет быстро понять, почему этот кусок стоит смотреть, даже без полного стрима.'
  const risk = item.risk || (Number(item.standalone_clarity || 0.7) < 0.55 ? 'Нужен контекст за 5–10 секунд до начала.' : 'Риск низкий: проверь вручную только если score средний.')
  return { what, why, value, risk }
}
