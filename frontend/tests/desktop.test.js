import test from 'node:test'
import assert from 'node:assert/strict'

async function freshDesktopModule(tag) {
  return import(`../src/shared/desktop.js?${tag}`)
}

test('desktop bridge helpers are inert in a normal browser', async () => {
  delete globalThis.window
  const desktop = await freshDesktopModule('browser')
  assert.equal(desktop.isDesktopRuntime(), false)
  assert.equal(await desktop.chooseNativeVideo(), null)
  assert.equal(await desktop.openNativePath('/tmp/file.mp4'), null)
  assert.equal(await desktop.getDesktopInfo(), null)
  assert.equal(await desktop.getNativeUpdateStatus(), null)
  assert.equal(await desktop.chooseNativeProjectDirectory(), null)
})

test('desktop bridge helpers delegate only through the isolated preload API', async () => {
  const calls = []
  globalThis.window = {
    highlightStudioDesktop: {
      isDesktop: true,
      chooseVideo: async () => ({ ok: true, path: 'C:/video.mp4' }),
      openPath: async (value) => { calls.push(['open', value]); return { ok: true } },
      showItemInFolder: async (value) => { calls.push(['reveal', value]); return { ok: true } },
      getInfo: async () => ({ ok: true, desktop: true }),
      chooseProjectDirectory: async () => ({ ok: true, path: 'D:/Projects' }),
      setProjectsDirectory: async (value) => { calls.push(['projects', value]); return { ok: true, path: value } },
      getUpdateStatus: async () => ({ ok: true, supported: true, status: 'current' }),
      checkForUpdates: async () => ({ ok: true, status: 'checking' }),
      downloadUpdate: async () => ({ ok: true, status: 'downloading' }),
      installUpdate: async () => ({ ok: true, status: 'downloaded' }),
      onUpdateStatus: () => () => {},
      onLocalAiStatus: () => () => {},
      installOllama: async () => ({ ok: true }),
      pullDefaultOllamaModel: async () => ({ ok: true }),
    },
  }
  const desktop = await freshDesktopModule('desktop')
  assert.equal(desktop.isDesktopRuntime(), true)
  assert.equal((await desktop.chooseNativeVideo()).path, 'C:/video.mp4')
  assert.equal((await desktop.getDesktopInfo()).desktop, true)
  assert.equal((await desktop.chooseNativeProjectDirectory()).path, 'D:/Projects')
  assert.equal((await desktop.getNativeUpdateStatus()).status, 'current')
  await desktop.setNativeProjectsDirectory('D:/Projects')
  assert.equal((await desktop.checkNativeUpdates()).status, 'checking')
  assert.equal((await desktop.downloadNativeUpdate()).status, 'downloading')
  assert.equal((await desktop.installNativeUpdate()).status, 'downloaded')
  assert.equal((await desktop.installNativeOllama()).ok, true)
  assert.equal((await desktop.pullNativeDefaultOllamaModel()).ok, true)
  await desktop.openNativePath('C:/out.mp4')
  await desktop.revealNativeFile('C:/out.mp4')
  assert.deepEqual(calls, [['projects', 'D:/Projects'], ['open', 'C:/out.mp4'], ['reveal', 'C:/out.mp4']])
  delete globalThis.window
})
