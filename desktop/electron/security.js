'use strict'

function parsedUrl(value) {
  try {
    return new URL(String(value))
  } catch (_) {
    return null
  }
}

function normalizedOrigin(value) {
  return parsedUrl(value)?.origin || ''
}

function sameOrigin(candidate, appUrl) {
  const candidateOrigin = normalizedOrigin(candidate)
  const appOrigin = normalizedOrigin(appUrl)
  return Boolean(candidateOrigin && appOrigin && candidateOrigin === appOrigin)
}

function isLoopbackHttpUrl(value) {
  const parsed = parsedUrl(value)
  if (!parsed || !['http:', 'https:'].includes(parsed.protocol)) return false
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return false
  if (parsed.pathname !== '/' && parsed.pathname !== '') return false
  const host = parsed.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase()
  return host === 'localhost' || host === '::1' || /^127(?:\.\d{1,3}){3}$/.test(host)
}


function isSafeUpdateUrl(value) {
  const parsed = parsedUrl(value)
  if (!parsed || parsed.protocol !== 'https:') return false
  if (parsed.username || parsed.password || parsed.hash) return false
  return Boolean(parsed.hostname)
}

function isSafeExternalUrl(value) {
  const parsed = parsedUrl(value)
  if (!parsed) return false
  if (parsed.username || parsed.password) return false
  return parsed.protocol === 'https:' || parsed.protocol === 'mailto:'
}

function isCompatibleHealth(payload, expectedVersion, expectedDesign = '') {
  if (!payload || payload.ok !== true) return false
  const version = String(payload.app_version || '')
  const expected = String(expectedVersion || '')
  // Accept the exact semantic version and its named build suffix, but do not
  // let v10.5.10 pass a check intended for v10.5.1.
  const versionMatches = Boolean(expected && (version === expected || version.startsWith(`${expected}-`) || version.startsWith(`${expected}+`)))
  const designMatches = !expectedDesign || String(payload.design_id || '') === String(expectedDesign)
  return versionMatches && designMatches
}

module.exports = { sameOrigin, isLoopbackHttpUrl, isSafeExternalUrl, isSafeUpdateUrl, isCompatibleHealth }
