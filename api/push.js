/* Where the machine with the radio on it puts what it knows.
 *
 * The direction is the whole design. Nothing here reaches into a home network,
 * so there is no inbound access to arrange, no tunnel to keep up and no port
 * forwarded off a router; the firewall process pushes, and this deployment
 * holds the last day of it. When the radio machine goes off, the page keeps
 * working and says how long ago it last heard something -- which is a better
 * failure than a fetch that hangs.
 */
import { createHash, timingSafeEqual } from 'node:crypto'
import { amend, ARCHIVES, archive, kvSet, pruneAll, store } from './_store.js'

/* Compared as digests rather than as strings so the comparison is over two
 * equal-length buffers whatever was sent -- timingSafeEqual throws on a length
 * mismatch, and guarding that with a length check first would leak the length
 * of the real token through the shape of the answer. */
const sameToken = (a, b) => {
  const h = (s) => createHash('sha256').update(String(s)).digest()
  return timingSafeEqual(h(a), h(b))
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST')
    return res.status(405).json({ error: 'push is a POST' })
  }

  const want = process.env.FIREWALL_PUSH_TOKEN
  if (!want) {
    /* Refused rather than left open. An unauthenticated push endpoint is a
       stranger's call log on your tracker, and there is no reading of a
       missing token that means "let anybody write". */
    return res.status(503).json({
      error: 'FIREWALL_PUSH_TOKEN is not set on this deployment',
    })
  }
  const got = (req.headers.authorization || '').replace(/^Bearer\s+/i, '')
  if (!got || !sameToken(got, want)) {
    return res.status(401).json({ error: 'bad or missing push token' })
  }
  if (!store()) {
    return res.status(503).json({
      error: 'no store is connected to this deployment',
    })
  }

  let body = req.body
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body)
    } catch {
      return res.status(400).json({ error: 'body is not JSON' })
    }
  }
  if (!body || !Array.isArray(body.calls)) {
    return res.status(400).json({ error: 'expected {calls: [...]}' })
  }

  /* Stamped here, on the way in, with the clock this deployment will later
     measure the age against. Trusting the sender's stamp would make every
     "last heard 4m ago" the difference between two machines' clocks rather
     than an age, and the one number nobody can check is the one on the
     machine sitting in somebody's house. */
  const snapshot = {
    calls: body.calls,
    feed: Array.isArray(body.feed) ? body.feed : [],
    logged: body.logged === true,
    speech: body.speech !== false,
    hold_seconds: Number(body.hold_seconds) || 600,
    /* The source's own health, kept apart from whether the push arrived: a
       radio machine that is up and failing to reach Broadcastify is a
       different fact from one that has stopped pushing, and the reader
       reports both. */
    ok: body.ok !== false,
    error: body.error ?? null,
    login_url: typeof body.login_url === 'string' ? body.login_url : null,
    pushed_at: Date.now() / 1000,
  }

  try {
    await kvSet(JSON.stringify(snapshot))
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) })
  }

  /* The archive, after the snapshot and never instead of it. The snapshot is
     what the screen is drawing right now and it is the write that must not be
     held up; the history behind it is a few seconds later either way, and a
     database having a bad moment should cost a deployment its month-old
     Tuesdays rather than its live radio.

     `archive` is the calls the sender knows have changed since it last pushed
     -- usually none, sometimes one -- so the steady state is a write of a few
     hundred bytes every ten seconds rather than a rewrite of the month. A
     sender too old to send it falls back to the whole window, which is correct
     and merely wasteful, and the fallback is what makes this safe to deploy
     before the machine with the radio on it is updated. */
  let archived = 0
  let corrected = 0
  let archiveError = null
  try {
    const calls = Array.isArray(body.archive) ? body.archive : body.calls
    /* The fallback is for a sender too old to have sent a delta, and it is
       gated on `speech` for the same reason the sender is: rows with the words
       taken out of them are ids and timings, they are written once per id, and
       archiving them would leave a month of empty transcript behind whenever
       somebody later decided the words could be published after all. */
    const rows = Array.isArray(body.archive_feed) ? body.archive_feed
      : snapshot.speech ? snapshot.feed : []
    archived = await archive(ARCHIVES.CALLS, calls)
    archived += await archive(ARCHIVES.RADIO, rows)
    /* After the archive and never before it. A correction names a transmission
       that is expected to be here already, and on the very first push of a
       fresh database the row it names arrives in the same request -- so
       merging afterwards is what lets a correction land on a transmission this
       deployment has only just been told about, instead of being skipped for
       not existing yet and waiting for the sender's next full push.

       A sender too old to send this simply does not, and nothing here changes:
       the transcript stays the machine's, which is what it has always been. */
    if (Array.isArray(body.corrections)) {
      corrected = await amend(ARCHIVES.RADIO, body.corrections)
    }
    /* Pruning walks the indexes by score and is only worth doing when the
       sender says it has just re-sent everything, which it does every few
       minutes. Doing it on every push would be a scan for nothing 30 times a
       minute. */
    if (body.full === true) await pruneAll(Date.now() / 1000)
  } catch (e) {
    archiveError = String(e.message || e)
  }

  /* 200 with the reason in it, not a 502. The push succeeded -- the live copy
     is written and the page is current -- and telling the sender it failed
     would have it print an outage over a working tracker. The sender reports
     this line separately, in its own words. */
  return res.status(200).json({
    ok: true,
    calls: snapshot.calls.length,
    archived,
    corrected,
    archive_error: archiveError,
  })
}
