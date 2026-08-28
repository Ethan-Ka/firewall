/* The 24 hours of calls this deployment holds, and the two functions that
 * reach it.
 *
 * There is no database here in any real sense: one key, one JSON blob, written
 * whole by the machine with the radio on it and read whole by the tracker. That
 * is the right shape for what this is -- a copy of a snapshot that is replaced
 * every few seconds -- and it keeps the read path to a single round trip, which
 * matters when a page polls it every two seconds.
 *
 * Redis over its REST API rather than a client library, because the library
 * would be a dependency, a bundle and a version to keep up with in exchange for
 * wrapping two fetches. Vercel's own KV integration and a plain Upstash
 * database both speak this and both hand their credentials over under one of
 * the two names below, so either one works with nothing configured by hand.
 */

const URL_ENV = ['KV_REST_API_URL', 'UPSTASH_REDIS_REST_URL', 'REDIS_REST_URL']
const TOKEN_ENV = ['KV_REST_API_TOKEN', 'UPSTASH_REDIS_REST_TOKEN', 'REDIS_REST_TOKEN']

const first = (names) => {
  for (const n of names) {
    const v = process.env[n]
    if (v) return v.replace(/\/+$/, '')
  }
  return null
}

export const store = () => {
  const url = first(URL_ENV)
  const token = first(TOKEN_ENV)
  return url && token ? { url, token } : null
}

/** The one key. Namespaced so the database can be shared with something else. */
export const KEY = 'firewall:snapshot'

/** How long a pushed snapshot lives, in seconds.
 *
 * This is the retention, and it is the outermost of the three places a day is
 * enforced -- the pusher sends a day, the reader filters to a day, and this
 * expires the lot a day after the last push. Belt and braces on purpose: the
 * first two are code that could be wrong, and this one is the database refusing
 * to hold anything older whatever the code does.
 *
 * It also settles what happens when the radio machine goes off: the page keeps
 * showing the last day it knew about, clearly stamped as of when, and then
 * there is nothing. Which is the honest end state -- a tracker with no source
 * has nothing to say, and saying nothing beats a day-old chart with no date on
 * it.
 */
export const TTL_SECONDS = Math.round(Number(process.env.RETAIN_HOURS || 24) * 3600)

/** Past this with no push, the radio machine is not reporting and the page is
 *  told so rather than left to render stale rows as live ones. Generous next to
 *  a ten-second push interval: a slow poll or one dropped request is not an
 *  outage, and crying about it every two seconds would train people to ignore
 *  it. */
export const STALE_SECONDS = 120

/* One Redis command, as the REST API's own JSON form: the command and its
 * arguments as an array in the body. The path form (/set/key/value) is the
 * documented shortcut and the wrong shape here -- a snapshot is tens of
 * kilobytes of JSON with slashes and quotes all through it, and none of that
 * belongs in a URL. */
async function command(args) {
  const s = store()
  if (!s) throw new Error('no store is configured for this deployment')
  const r = await fetch(s.url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${s.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(args),
    cache: 'no-store',
  })
  const body = await r.json().catch(() => null)
  if (!r.ok || body?.error) {
    throw new Error(body?.error || `store returned HTTP ${r.status}`)
  }
  return body?.result ?? null
}

export const kvSet = (value) => command(['SET', KEY, value, 'EX', TTL_SECONDS])

export async function kvGet() {
  if (!store()) return null
  const raw = await command(['GET', KEY])
  /* A key that has expired reads as null, which is not an error: it is a
     deployment nothing has pushed to in a day, and the caller says so in its
     own words. */
  return raw ? JSON.parse(raw) : null
}

/** What the tracker gets when there is nothing to serve, and why.
 *
 * Distinguished on purpose. "Nobody has connected a store to this deployment"
 * is a setup that was never finished; "the store is empty" is a radio machine
 * that has not pushed for a day. Both leave the screen blank and the fix for
 * each is somewhere else entirely, so the screen says which. */
export const nothing = () =>
  store()
    ? 'no update from the radio in the last day'
    : 'this deployment has no store connected (see web/api/README.md)'
