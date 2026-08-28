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
import { kvSet, store } from './_store.js'

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
  return res.status(200).json({ ok: true, calls: snapshot.calls.length })
}
