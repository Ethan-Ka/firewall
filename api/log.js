/* /api/log, answered out of the store instead of off a disk with the incidents
 * on it. Same shape as the firewall server's own, because it is the same
 * payload -- this one has been through a push, a day's retention and a JSON
 * round trip, and the tracker should not be able to tell.
 */
import { kvGet, nothing, TTL_SECONDS } from './_store.js'

export default async function handler(req, res) {
  const now = Date.now() / 1000
  res.setHeader('Cache-Control', 'no-store')

  let snap = null
  try {
    snap = await kvGet()
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e), calls: [], logged: false, now })
  }
  if (!snap) {
    /* 200 with an empty list, not a 404. There is no call log here yet, which
       is a true and complete answer to the question asked; a 404 would put the
       tracker into "server unreachable" over a deployment that is working
       perfectly and has nothing in it. */
    return res.status(200).json({ calls: [], logged: false, speech: true, now,
                                  pushed_at: null, error: nothing() })
  }

  /* The narrower of what was asked for and what this deployment keeps. The
     tracker asks in hours for the reason the firewall server takes hours: a
     browser's clock is the one on the screen allowed to be wrong, and a
     duration means the same thing on both machines. */
  const asked = Number(req.query?.hours)
  const span = Number.isFinite(asked) && asked > 0
    ? Math.min(asked * 3600, TTL_SECONDS)
    : TTL_SECONDS

  return res.status(200).json({
    calls: (snap.calls || []).filter((c) => now - c.opened <= span),
    logged: snap.logged === true,
    speech: snap.speech !== false,
    login_url: snap.login_url ?? null,
    pushed_at: snap.pushed_at ?? null,
    now,
  })
}
