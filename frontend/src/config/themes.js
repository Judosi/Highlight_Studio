export const THEME_OPTIONS = [
  {
    id: 'graphite',
    label: 'Graphite',
    description: 'Нейтральная профессиональная тема для долгого монтажа.',
  },
  {
    id: 'midnight',
    label: 'Midnight',
    description: 'Фирменная синяя тема с более заметными AI-акцентами.',
  },
  {
    id: 'high-contrast',
    label: 'Высокий контраст',
    description: 'Усиленный текст, границы и фокус для лучшей читаемости.',
  },
]

export const DEFAULT_THEME = 'graphite'

export function normalizeTheme(value) {
  return THEME_OPTIONS.some(theme => theme.id === value) ? value : DEFAULT_THEME
}
