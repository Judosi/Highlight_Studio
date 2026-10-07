// Shared by source UI and the release bundle. No network or UI side effects.
export function createReviewRequestGuard() {
  let id = '', generation = 0, revision = 0
  const reads = new Map(), mutations = new Map()
  function select(next) {
    next = String(next || '')
    if (next !== id) { id = next; generation++; revision++; reads.clear() }
  }
  function current(token) {
    return Boolean(token && token.id === id && token.generation === generation)
  }
  return {
    select,
    current,
    read(channel) {
      const seq = (reads.get(channel) || 0) + 1
      reads.set(channel, seq)
      return { id, generation, revision, channel, seq }
    },
    accepts(token) {
      return current(token) && token.revision === revision
        && token.seq === reads.get(token.channel) && !mutations.has(`${id}:segments`)
    },
    begin(kind) {
      const key = `${id}:${kind}`
      if (mutations.has(key)) return null
      const token = { id, generation, key }
      mutations.set(key, token)
      if (kind === 'segments') revision++
      return token
    },
    end(token) {
      if (token && mutations.get(token.key) === token) {
        mutations.delete(token.key)
        if (token.key.endsWith(':segments')) revision++
      }
    },
  }
}
