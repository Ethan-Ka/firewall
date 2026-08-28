/* /api/radio -- what was said, for a stretch of time that is over.
 *
 * The live tape crosses in /api/current and is the last ten minutes of it,
 * whole, every two seconds. This is the other question: a call from Tuesday is
 * on the screen and somebody wants to read it. Asked by time rather than by
 * call id, because that is how the tape is true -- a transmission is stored
 * because it was heard, and which call a sentence belonged to is a reading of
 * it that the parser can get wrong. The caller asks for the span the call ran
 * and gets what was said in it, which is the same grouping the live display
 * makes and no more confident than that.
 *
 * Cached hard at the edge. A span in the past cannot acquire new transmissions,
 * so the only rows this can be wrong about are the ones in a call that is still
 * running -- and those are already on the screen, live, from /api/current.
 */
import { ARCHIVES, ARCHIVE_SECONDS, history, nothing, store } from './_store.js'

const CACHE = 'public, max-age=60, s-maxage=3600, stale-while-revalidate=86400'

/** Most rows one span may return. A call that ran four hours in a thunderstorm
 *  is a long read and a fair one; a request for the whole month is not a
 *  transcript, it is a database dump, and this is where that stops. */
const MAX_ROWS = 1500

export default async function handler(req, res) {
  const now = Date.now() / 1000
  const from = Number(req.query?.from)
  const to = Number(req.query?.to)

  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) {
    res.setHeader('Cache-Control', 'no-store')
    return res.status(400).json({ error: 'from and to are unix seconds, from first' })
  }
  if (!store()) {
    res.setHeader('Cache-Control', 'no-store')
    return res.status(200).json({ feed: [], error: nothing() })
  }

  let feed = []
  try {
    /* Clamped to what this deployment keeps, so a request for last year is an
       empty answer about last year rather than an index walk that finds
       nothing after reading everything. */
    feed = await history(ARCHIVES.RADIO,
                         Math.max(from, now - ARCHIVE_SECONDS),
                         Math.min(to, now), MAX_ROWS)
  } catch (e) {
    res.setHeader('Cache-Control', 'no-store')
    return res.status(200).json({ feed: [], error: String(e.message || e) })
  }

  res.setHeader('Cache-Control', CACHE)
  /* Oldest first: this is a conversation, and history() sorts newest first
     because that is which end its cap cuts from, not because that is how
     anybody reads a transcript. */
  feed.sort((a, b) => a.ts - b.ts)
  return res.status(200).json({ feed, now })
}
