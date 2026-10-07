'use strict'

const fs = require('node:fs')
const path = require('node:path')

const VIDEO_EXTENSIONS = new Set(['.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v', '.ts'])

function normalizedExistingPath(value) {
  if (typeof value !== 'string' || !value.trim() || value.includes('\0')) return null
  const resolved = path.resolve(value.trim())
  try {
    return fs.existsSync(resolved) ? resolved : null
  } catch (_) {
    return null
  }
}

function existingDirectory(value) {
  const resolved = normalizedExistingPath(value)
  if (!resolved) return null
  try {
    return fs.statSync(resolved).isDirectory() ? resolved : null
  } catch (_) {
    return null
  }
}

function existingFile(value) {
  const resolved = normalizedExistingPath(value)
  if (!resolved) return null
  try {
    return fs.statSync(resolved).isFile() ? resolved : null
  } catch (_) {
    return null
  }
}

function existingVideoFile(value) {
  const resolved = existingFile(value)
  return resolved && VIDEO_EXTENSIONS.has(path.extname(resolved).toLowerCase()) ? resolved : null
}

module.exports = { VIDEO_EXTENSIONS, normalizedExistingPath, existingDirectory, existingFile, existingVideoFile }
