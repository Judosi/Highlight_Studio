import test from 'node:test'
import assert from 'node:assert/strict'
import { classifyNotice, hardwareProfileLabel } from '../src/shared/ux.js'

test('notifications use semantic colors instead of showing every message as an error', () => {
  assert.equal(classifyNotice('Настройки сохранены.'), 'success')
  assert.equal(classifyNotice('Не удалось сохранить проект.'), 'error')
  assert.equal(classifyNotice('Нужно проверить модель.'), 'warning')
  assert.equal(classifyNotice('Идёт анализ видео.'), 'info')
  assert.equal(classifyNotice('Проект создан, но есть предупреждение.'), 'warning')
  assert.equal(classifyNotice('Настройки применены только на экране, но не сохранились.'), 'error')
  assert.equal(classifyNotice('Backend не отвечает.'), 'error')
  assert.equal(classifyNotice('Сервер не отвечает, попробуйте снова.'), 'error')
})

test('hardware labels do not expose a hardcoded developer computer', () => {
  assert.equal(hardwareProfileLabel('Auto'), 'Автоматический режим')
  assert.equal(hardwareProfileLabel('GTX1050Ti_16GB_Ryzen2600'), 'Сбалансированный профиль')
})

test('Shorts Studio exposes safe reframing, captions and loudness controls', async () => {
  const { readFile } = await import('node:fs/promises')
  const app = await readFile(new URL('../src/app/App.jsx', import.meta.url), 'utf8')
  assert.match(app, /Полный кадр \+ размытый фон/)
  assert.match(app, /Вшивать субтитры в Shorts/)
  assert.match(app, /Выравнивать громкость Shorts/)
  assert.match(app, /shorts_max_seconds/)
})
