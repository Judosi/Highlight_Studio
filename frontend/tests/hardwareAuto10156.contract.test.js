import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const app = fs.readFileSync(new URL('../src/app/App.jsx', import.meta.url), 'utf8')

test('hardware auto optimizer is exposed as the default path', () => {
  assert.match(app, /hardware_auto_optimize:\s*true/)
  assert.match(app, /whisper_device:\s*'auto'/)
  assert.match(app, /whisper_compute:\s*'auto'/)
  assert.match(app, /video_encoder:\s*'auto'/)
})

test('hardware presets are capability based rather than GTX1050Ti-only buttons', () => {
  assert.match(app, /auto_fast/)
  assert.match(app, /auto_balanced/)
  assert.match(app, /auto_quality/)
  assert.doesNotMatch(app, /applyHardwarePreset\('gtx1050ti_/)
})

test('advanced UI lets effective CPU GPU controls remain inspectable', () => {
  assert.match(app, /cpu_worker_limit/)
  assert.match(app, /gpu_job_limit/)
  assert.match(app, /gpu_vram_reserve_mb/)
  assert.match(app, /ctranslate2_cuda/)
  assert.match(app, /ffmpeg_nvenc/)
})
