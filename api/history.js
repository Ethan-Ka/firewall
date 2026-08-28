/* /api/history -- the calls this deployment has kept, past the day the
 * snapshot holds.
 *
 * Split from /api/log rather than folded into it, because the two are read at
 * completely different rates and folding them would make the cheap one as
 * expensive as the dear one. /api/log is the last day and it is polled every
 * ten seconds by every open tab; this is a month and it changes about as often
 * as the fire department gets a call. Asking for a month of calls six times a
 * minute to watch the last hour of it change is how a free database stops
 * being free.
 *
 * So this one is cached, at the edge, for five minutes. A call that opened
 * thirty seconds ago is in the snapshot and on the screen already; what a
 * viewer waits up to five minutes for is that same call being folded into the
 * history behind it, which nobody can see happen and which changes nothing
 * about what the page says.
 */
import { ARCHIVES, ARCHIVE_SECONDS, history, nothing, store } from './_store.js'

/* Long enough that the archive is not re-read on every poll, short enough that
 * a page left open all afternoon is not reading a morning-shaped week. The
 * stale window is much longer on purpose: serving a five-minute-old history
 * while a fresh one is fetched behind it beats making somebody wait, and this
 * data is not the part of the screen that has to be current. */
const CACHE = 'public, max-age=0, s-maxage=300, stale-while-revalidate=3600'

export default async function handler(req, res) {
  const now = Date.now() / 1000

  const asked = Number(req.query?.days)
  const span = Number.isFinite(asked) && asked > 0
    ? Math.min(asked * 86400, ARCHIVE_SECONDS)
    : ARCHIVE_SECONDS
  const from = now - span

  if (!store()) {
    res.setHeader('Cache-Control', 'no-store')
    return res.status(200).json({ calls: [], from, now, error: nothing() })
  }

  let calls = []
  try {
    calls = await history(ARCHIVES.CALLS, from, now)
  } catch (e) {
    /* The history failing is not the page failing. The tracker draws what it
       has from /api/log either way, and an empty archive with a reason on it
       is the difference between "this department was quiet" and "this endpoint
       is broken". */
    res.setHeader('Cache-Control', 'no-store')
    return res.status(200).json({ calls: [], from, now, error: String(e.message || e) })
  }

  res.setHeader('Cache-Control', CACHE)
  return res.status(200).json({
    calls,
    /* What the archive can answer, whatever was asked for. The tracker offers
       windows against this rather than against a number compiled into it, so a
       deployment that keeps a week does not show a chip for a month. */
    from,
    days: ARCHIVE_SECONDS / 86400,
    now,
  })
}
