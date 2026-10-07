const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { sameOrigin, isLoopbackHttpUrl, isSafeExternalUrl, isSafeUpdateUrl, isCompatibleHealth } = require('../security')

test('sameOrigin compares parsed origins instead of an unsafe string prefix', () => {
  const appUrl = 'http://127.0.0.1:8000'
  assert.equal(sameOrigin('http://127.0.0.1:8000/projects/1', appUrl), true)
  assert.equal(sameOrigin('http://127.0.0.1:8000.evil.example/path', appUrl), false)
  assert.equal(sameOrigin('http://127.0.0.1:8001', appUrl), false)
  assert.equal(sameOrigin('javascript:alert(1)', appUrl), false)
})

test('application backend URL is restricted to loopback HTTP(S)', () => {
  assert.equal(isLoopbackHttpUrl('http://127.0.0.1:8000'), true)
  assert.equal(isLoopbackHttpUrl('https://localhost:9443'), true)
  assert.equal(isLoopbackHttpUrl('http://[::1]:8000'), true)
  assert.equal(isLoopbackHttpUrl('http://127.0.0.1.evil.example:8000'), false)
  assert.equal(isLoopbackHttpUrl('https://example.com'), false)
  assert.equal(isLoopbackHttpUrl('file:///tmp/index.html'), false)
  assert.equal(isLoopbackHttpUrl('http://user:pass@127.0.0.1:8000'), false)
  assert.equal(isLoopbackHttpUrl('http://127.0.0.1:8000/app'), false)
  assert.equal(isLoopbackHttpUrl('http://127.0.0.1:8000?next=/api/health'), false)
  assert.equal(isLoopbackHttpUrl('http://127.0.0.1:8000/#section'), false)
})


test('update feed requires HTTPS and rejects embedded credentials', () => {
  assert.equal(isSafeUpdateUrl('https://updates.example.com/highlight-studio'), true)
  assert.equal(isSafeUpdateUrl('http://updates.example.com/highlight-studio'), false)
  assert.equal(isSafeUpdateUrl('https://user:pass@updates.example.com/feed'), false)
  assert.equal(isSafeUpdateUrl('file:///tmp/latest.yml'), false)
})

test('external links allow only explicit browser/mail protocols', () => {
  assert.equal(isSafeExternalUrl('https://example.com/help'), true)
  assert.equal(isSafeExternalUrl('http://example.com'), false)
  assert.equal(isSafeExternalUrl('mailto:support@example.com'), true)
  assert.equal(isSafeExternalUrl('javascript:alert(1)'), false)
  assert.equal(isSafeExternalUrl('file:///etc/passwd'), false)
})

test('health payload must belong to the exact compatible app version', () => {
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.12.0-shorts-studio' }, 'v10.12.0'), true)
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.12.0' }, 'v10.12.0'), true)
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.9.20-other' }, 'v10.12.0'), false)
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.4.0-ux-simplification' }, 'v10.12.0'), false)
  assert.equal(isCompatibleHealth({ ok: false, app_version: 'v10.12.0-shorts-studio' }, 'v10.12.0'), false)
  assert.equal(isCompatibleHealth({}, 'v10.12.0'), false)
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.14.4-build', design_id: 'old-horizontal-stepper' }, 'v10.14.4', 'studio-sidebar-layout-v4'), false)
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.14.4-build', design_id: 'studio-sidebar-layout-v4' }, 'v10.14.4', 'studio-sidebar-layout-v4'), true)
})

test('external commerce links reject insecure HTTP and embedded credentials', () => {
  assert.equal(isSafeExternalUrl('https://billing.example.com/checkout'), true)
  assert.equal(isSafeExternalUrl('http://billing.example.com/checkout'), false)
  assert.equal(isSafeExternalUrl('https://user:pass@billing.example.com/checkout'), false)
})

test('release channel configuration uses explicit stable and beta URLs', () => {
  const fs = require('node:fs')
  const path = require('node:path')
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'release-channel.json'), 'utf8'))
  assert.equal(config.channel, 'stable')
  assert.equal(typeof config.stableUpdateUrl, 'string')
  assert.equal(typeof config.betaUpdateUrl, 'string')
  assert.equal(config.allowChannelSwitch, true)
})


test('current release identity matches backend/design contract', () => {
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v11.2.7-quality-recovery-audit', design_id: 'studio-audited-v15' }, 'v11.2.7-quality-recovery-audit', 'studio-audited-v15'), true)
  assert.equal(isCompatibleHealth({ ok: true, app_version: 'v10.15.3-frontend-audit', design_id: 'studio-audited-v15' }, 'v11.2.7-quality-recovery-audit', 'studio-audited-v15'), false)
})

test('packaged Electron resources include canonical release identity', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'))
  const extra = packageJson.build?.extraResources || []
  assert.ok(extra.some(item => item?.from === '../../release_identity.json' && item?.to === 'app/release_identity.json'))
})
