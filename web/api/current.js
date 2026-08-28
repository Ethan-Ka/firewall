/* /api/current out of the store: the radio, as of the last push.
 *
 * The one thing this cannot do is be live, and the whole of the difference is
 * in `ok` and `error`. A snapshot from four seconds ago is the radio; one from
 * forty minutes ago is a machine that has stopped talking, and rendering the
 * second as though it were the first is how a screen comes to show a fire that
 * finished before lunch with a green light next to it.
 */
import { kvGet, nothing, STALE_SECONDS } from './_store.js'

const ago = (s) => {
  const n = Math.max(0, Math.round(s))
  if (n < 60) return `${n}s`
  const m = Math.round(n / 60)
  return m < 60 ? `${m}m` : `${Math.round(m / 60)}h`
}

export default async function handler(req, res) {
  const now = Date.now() / 1000
  res.setHeader('Cache-Control', 'no-store')

  let snap = null
  try {
    snap = await kvGet()
  } catch (e) {
    return res.status(200).json({ ok: false, error: String(e.message || e),
                                  calls: [], feed: [], now })
  }
  if (!snap) {
    return res.status(200).json({ ok: false, error: nothing(),
                                  calls: [], feed: [], now, pushed_at: null })
  }

  const age = now - (snap.pushed_at ?? 0)
  const stale = age > STALE_SECONDS
  return res.status(200).json({
    /* Both failures collapse into one flag because the screen has one place to
       put them, and the words say which happened. Source health survives the
       push, so a machine that is up and failing to read the scanner still
       reports its own reason rather than being overwritten by ours. */
    ok: snap.ok !== false && !stale,
    error: stale
      ? `no update from the radio in ${ago(age)}`
      : snap.error ?? null,
    /* The running ones, out of the same list /api/log serves. They are
       roster-shaped rather than the richer thing a live server publishes --
       no per-call `radio`, because the tape crosses once as `feed` and not a
       second time per call -- so `ts` is filled from `opened` to match the
       shape the tracker declares. It reads ids off these and draws the
       transcript from the feed, which is why the difference does not show. */
    calls: (snap.calls ?? [])
      .filter((c) => c.live)
      .map((c) => ({ ...c, ts: c.opened })),
    feed: snap.feed ?? [],
    speech: snap.speech !== false,
    login_url: snap.login_url ?? null,
    hold_seconds: snap.hold_seconds ?? 600,
    /* This deployment's own clock, not the sender's. The tracker corrects
       every age on the screen against it, so it has to be the clock that
       stamped `pushed_at` -- otherwise the correction and the staleness
       disagree by however far apart two machines' clocks are. */
    now,
    pushed_at: snap.pushed_at ?? null,
  })
}
