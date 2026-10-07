const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { normalizedExistingPath, existingDirectory, existingFile, existingVideoFile } = require('../ipc_policy')

test('desktop IPC accepts only existing paths and supported video files', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'highlight-studio-ipc-'))
  const video = path.join(root, 'clip.mp4')
  const text = path.join(root, 'notes.txt')
  fs.writeFileSync(video, 'video')
  fs.writeFileSync(text, 'text')

  assert.equal(normalizedExistingPath(root), path.resolve(root))
  assert.equal(existingDirectory(root), path.resolve(root))
  assert.equal(existingFile(video), path.resolve(video))
  assert.equal(existingVideoFile(video), path.resolve(video))
  assert.equal(existingVideoFile(text), null)
  assert.equal(normalizedExistingPath(path.join(root, 'missing.mp4')), null)
  assert.equal(normalizedExistingPath('\0bad'), null)

  fs.rmSync(root, { recursive: true, force: true })
})
