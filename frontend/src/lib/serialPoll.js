// Schedule from completion, so a slow backend cannot accumulate requests.
export function startSerialPoll(task, delay, { schedule = setTimeout, cancel = clearTimeout, onError = () => {} } = {}) {
  let stopped = false, timer
  async function tick() {
    try { await task() } catch (error) { if (!stopped) onError(error) }
    if (!stopped) timer = schedule(tick, delay)
  }
  void tick()
  return () => { stopped = true; if (timer !== undefined) cancel(timer) }
}
