import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const root = path.resolve(import.meta.dirname, '../..')
const requireFromDesktop = createRequire(path.join(root, 'desktop/electron/package.json'))
const asar = requireFromDesktop('@electron/asar')
const archive = path.resolve(process.argv[2] || '')
if (!fs.statSync(archive).isFile()) throw new Error(`Electron app.asar not found: ${archive}`)

const entries = asar.listPackage(archive).map(name => name.replace(/^[/\\]+/, '').replaceAll('\\', '/'))
const required = [
  'main.js',
  'preload.js',
  'security.js',
  'ipc_policy.js',
  'package.json',
  'paid-beta-channel.json',
  'release-channel.json',
]
for (const name of required) {
  if (!entries.includes(name)) throw new Error(`Electron app.asar is missing ${name}`)
}

const forbidden = entries.filter(name => {
  const lower = name.toLowerCase()
  const base = path.posix.basename(lower)
  return base === '.env' || base.startsWith('.env.') || ['.pfx', '.p12'].includes(path.posix.extname(base)) ||
    ['license.json', 'trial.json', 'local_auth_token.txt', 'jobs.sqlite3', 'activation.sqlite3'].includes(base) ||
    /(?:private|admin)[-_ ]?(?:key|token)/.test(base) || lower.includes('/projects/') || lower.includes('/admin-tools/')
})
if (forbidden.length) throw new Error(`Electron app.asar contains forbidden secrets, user data, or admin artifacts: ${forbidden.join(', ')}`)

function readJson(name) {
  return JSON.parse(asar.extractFile(archive, name).toString('utf8').replace(/^\uFEFF/, ''))
}

const paidBeta = readJson('paid-beta-channel.json')
if (!paidBeta.licensePublicKeyB64 || paidBeta.trialDays < 1) {
  throw new Error('Packaged Paid Beta config lost its public verification key or trial policy.')
}
for (const key of Object.keys(paidBeta)) {
  if (/(?:private|admin).*(?:key|token)/i.test(key)) throw new Error(`Forbidden private field in Paid Beta config: ${key}`)
}
const releaseChannel = readJson('release-channel.json')
if (!['stable', 'beta'].includes(releaseChannel.channel)) throw new Error('Packaged release channel is invalid.')

console.log(JSON.stringify({
  ok: true,
  archive,
  entries: entries.length,
  license_public_key_present: true,
  private_keys_present: false,
  user_data_present: false,
}))
