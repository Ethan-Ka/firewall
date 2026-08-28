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

/* ------------------------------------------------------------- the archive
 *
 * The snapshot above is a copy of right now, replaced whole every few seconds
 * and gone a day after the radio machine stops. That is the right shape for
 * "what is happening", and the wrong one for every question worth asking of a
 * fire department: which hour it actually runs, whether Tuesdays are quiet,
 * what a normal week looks like. Those need calls kept past the day they
 * happened, so they are kept here.
 *
 * Two keys rather than one blob, because the access patterns pull in opposite
 * directions. A hash lets a push write only the calls that CHANGED -- no read
 * of the archive, no rewrite of it, a few hundred bytes on the wire every ten
 * seconds instead of the whole history. A sorted set on `opened` lets a read
 * ask for a span by time and get back exactly the ids in it, so "the last
 * seven days" costs the last seven days and not the last month.
 *
 * The words are kept and the audio is not, which is the one line drawn through
 * all of this. A day of trunked radio is gigabytes and the clips only ever
 * exist in the memory of the process that recorded them, so an archived clip
 * url is a link that outlives what it points at. What is kept is everything
 * that is still true next month: when a call opened and closed, what it was,
 * where, who went, what each unit did, and every transmission that was heard,
 * with what was said in it.
 */

/** Every archived call, by id, and the same ids by `opened` so a span can be
 *  asked for by time. */
const CALLS = { hash: 'firewall:calls', index: 'firewall:calls:at', at: 'opened' }

/** Every archived transmission, the same way, by `ts`.
 *
 *  Kept beside the calls rather than inside them because that is how they
 *  arrive and how they are true: the tape is a record of what was said on a
 *  talkgroup, and which call a sentence belonged to is a reading of it that the
 *  parser can get wrong. A row is stored because it was heard. The calls are
 *  what organise it afterwards, on the way out, by time -- which is the same
 *  thing _feed() decided upstream and for the same reason. */
const RADIO = { hash: 'firewall:radio', index: 'firewall:radio:at', at: 'ts' }

export const ARCHIVES = { CALLS, RADIO }

/** How far back the archive goes. Unlike the snapshot's TTL this is enforced
 *  by pruning rather than by expiry: an archive that vanishes because nobody
 *  pushed for a month is not an archive. */
export const ARCHIVE_SECONDS =
  Math.round(Number(process.env.ARCHIVE_DAYS || 30) * 86400)

/** A ceiling that has nothing to do with time. Retention is the honest bound;
 *  this one is the one that holds when something upstream goes wrong and starts
 *  opening a call a second, and it is what keeps a free database free. */
const MAX_CALLS = 20000

/** The same for the tape, which runs an order of magnitude ahead of it: a call
 *  is a dozen transmissions on a quiet day and a hundred on a bad one. */
const MAX_ROWS = 200000

/** Most a single read will return. Well past a month of one department, and
 *  the difference between a slow page and a function that times out if this
 *  database is ever pointed at a county. */
const MAX_READ = 6000

/* Several commands, one round trip. Upstash counts them individually and bills
 * them individually; what this saves is latency, which is the thing a serverless
 * function is actually short of. */
async function pipeline(commands) {
  const s = store()
  if (!s) throw new Error('no store is configured for this deployment')
  if (!commands.length) return []
  const r = await fetch(s.url + '/pipeline', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${s.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(commands),
    cache: 'no-store',
  })
  const body = await r.json().catch(() => null)
  if (!r.ok) throw new Error(body?.error || `store returned HTTP ${r.status}`)
  const rows = Array.isArray(body) ? body : []
  const bad = rows.find((x) => x?.error)
  if (bad) throw new Error(bad.error)
  return rows.map((x) => x?.result ?? null)
}

/** A record as it is kept: itself, minus the fields that cannot survive being
 *  written down.
 *
 *  `live` is a claim about the present tense, and a call stored as live stays
 *  live for ever -- a month-old fire with a pulsing dot on it. The reader takes
 *  `live` from the snapshot, which is the only thing entitled to an opinion
 *  about it, and everything the archive holds reads as over.
 *
 *  `url` is audio. The clips live in the memory of the process that recorded
 *  them and are gone long before the words are, so an archived url is a link
 *  that resolves, returns nothing, and makes a play button that looks fine and
 *  does nothing. Null is the case the transcript has always drawn correctly:
 *  the row reads, and the play button is visibly unavailable. */
const keepable = (c) => {
  const { live, url, ...rest } = c   // eslint-disable-line no-unused-vars
  return 'url' in c ? { ...rest, url: null } : rest
}

/** Write these records into one of the archives above. Idempotent by id, so a
 *  call pushed again while it is still running lands on top of itself and the
 *  last version -- the one with the closing time and the full unit list -- is
 *  the one that is kept. */
export async function archive(which, records) {
  const rows = (records || []).filter(
    (c) => c && typeof c.id === 'string' && Number.isFinite(c[which.at]))
  if (!rows.length) return 0
  await pipeline([
    ['HSET', which.hash, ...rows.flatMap((c) => [c.id, JSON.stringify(keepable(c))])],
    ['ZADD', which.index, ...rows.flatMap((c) => [String(c[which.at]), c.id])],
  ])
  return rows.length
}

/** Put better words onto rows this archive is already holding.
 *
 * The one write here that is not a whole record. Everything else arrives
 * complete -- a call is pushed again with its closing time on it, a
 * transmission is pushed once and never changes -- and an HSET of the whole
 * value is right for both. A correction is neither: it is a person typing what
 * was actually said into a clip from last Tuesday, and by then the sender has
 * nothing left of that row but its id and the words. Writing it as a whole
 * record would replace the archived transmission with a two-field stub and
 * lose its timing, its department and its dispatch flag.
 *
 * So it is read, merged and written back. `machine` keeps whatever the
 * recogniser had said, because a transcript that quietly becomes a human's
 * version with no trace of the machine's is one nobody can audit afterwards.
 *
 * Ids the archive does not hold are skipped, not created. A correction names a
 * transmission that was pushed here at the time; if it is gone -- pruned past
 * retention, or never archived because the words were gated then -- there is no
 * row to improve, and inventing one out of an id and a sentence would put a
 * transmission with no timestamp into an index that sorts on timestamps.
 *
 * Returns how many rows were actually changed. */
export async function amend(which, patches) {
  const rows = (patches || []).filter(
    (c) => c && typeof c.id === 'string' && typeof c.text === 'string')
  if (!rows.length) return 0
  const ids = [...new Set(rows.map((c) => c.id))]
  const raw = (await command(['HMGET', which.hash, ...ids])) || []
  const held = new Map()
  ids.forEach((id, i) => {
    if (!raw[i]) return
    try {
      held.set(id, JSON.parse(raw[i]))
    } catch { /* not JSON, so not a transmission. */ }
  })
  const write = []
  for (const { id, text } of rows) {
    const row = held.get(id)
    /* Already says this. Not an error and not worth a write: the sender
       re-states every correction it knows about on its full push, which is what
       heals a lost one, and paying for that with a rewrite of the lot every few
       minutes is what the check avoids. */
    if (!row || row.text === text) continue
    held.set(id, { ...row, text, machine: row.machine ?? row.text, corrected: true })
    write.push(id)
  }
  if (!write.length) return 0
  await pipeline([
    ['HSET', which.hash, ...write.flatMap((id) => [id, JSON.stringify(held.get(id))])],
  ])
  return write.length
}

/** Drop what is past retention, and then whatever is over the ceiling.
 *
 * Both halves delete from the index first and the hash from what the index
 * said, which is the order that fails safe: interrupted between the two, the
 * archive holds a call nothing points at -- invisible, and overwritten the
 * next time that id is pushed -- rather than an index pointing at a call that
 * is not there, which every read would then have to defend against. */
export async function prune(which, cap, at = Date.now() / 1000) {
  const cutoff = at - ARCHIVE_SECONDS
  const drop = async (ids) => {
    if (ids.length) {
      await pipeline([['ZREM', which.index, ...ids], ['HDEL', which.hash, ...ids]])
    }
    return ids.length
  }
  let gone = await drop(
    (await command(['ZRANGEBYSCORE', which.index, '-inf', `(${cutoff}`,
                    'LIMIT', 0, MAX_READ])) || [])
  const over = (Number(await command(['ZCARD', which.index])) || 0) - cap
  if (over > 0) {
    gone += await drop(
      (await command(['ZRANGE', which.index, 0, Math.min(over, MAX_READ) - 1])) || [])
  }
  return gone
}

/** Both archives, past retention and over their ceilings. */
export const pruneAll = async (at = Date.now() / 1000) =>
  (await prune(CALLS, MAX_CALLS, at)) + (await prune(RADIO, MAX_ROWS, at))

/** The archived calls opened in [from, to], newest first.
 *
 *  Newest first is not a presentation choice: it is which end the cap cuts
 *  from. A span with more calls in it than MAX_READ should come back missing
 *  the oldest, not missing today. */
export async function history(which, from, to, limit = MAX_READ) {
  if (!store()) return []
  const ids = (await command(['ZREVRANGEBYSCORE', which.index, to, from,
                              'LIMIT', 0, Math.min(limit, MAX_READ)])) || []
  if (!ids.length) return []
  const raw = (await command(['HMGET', which.hash, ...ids])) || []
  const out = []
  for (const v of raw) {
    /* A miss is an id the index still points at and the hash no longer holds.
       Skipped rather than reported: it is a prune that was interrupted, it
       heals itself on the next one, and it is not the reader's problem. */
    if (!v) continue
    try {
      out.push(JSON.parse(v))
    } catch { /* not JSON, so not a call. */ }
  }
  return out
}
