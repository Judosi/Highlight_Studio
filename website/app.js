(async () => {
  let config = {}
  try { config = await fetch('./config.json', { cache: 'no-store' }).then(response => response.ok ? response.json() : {}) } catch (_) {}
  const links = { download: config.downloadUrl, checkout: config.checkoutUrl, privacy: config.privacyUrl, terms: config.termsUrl, support: config.supportUrl }
  for (const [name, url] of Object.entries(links)) {
    if (!url || !String(url).startsWith('https://')) continue
    document.querySelectorAll(`[data-link="${name}"]`).forEach(element => { element.href = url })
  }
  if (config.creatorPrice) document.querySelectorAll('[data-price="creator"]').forEach(element => { element.textContent = config.creatorPrice })
  if (config.proPrice) document.querySelectorAll('[data-price="pro"]').forEach(element => { element.textContent = config.proPrice })
})()
