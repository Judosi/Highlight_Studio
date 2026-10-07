import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
const root = path.resolve(import.meta.dirname, '../..')
function files(relative) {
  const absolute = path.join(root, relative)
  if (!fs.statSync(absolute).isDirectory()) return [relative]
  return fs.readdirSync(absolute).sort().flatMap(name => files(`${relative}/${name}`))
}
function hashes(paths) {
  return Object.fromEntries(paths.sort().map(file => [file, crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex')]))
}
const inputs = ['release_identity.json', 'frontend/src', 'frontend/public', 'frontend/scripts',
  'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json', 'frontend/vite.config.js'].flatMap(files)
const outputs = files('frontend/dist').filter(file => !file.endsWith('/build-manifest.json'))
fs.writeFileSync(path.join(root, 'frontend/dist/build-manifest.json'), JSON.stringify({
  schema: 1, identity: JSON.parse(fs.readFileSync(path.join(root, 'release_identity.json'))),
  inputs: hashes(inputs), outputs: hashes(outputs),
}, null, 2) + '\n')
