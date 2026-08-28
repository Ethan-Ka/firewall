/* The wire, and the few facts every panel on this screen needs to agree about.
 *
 * Everything here is shared by the chart, the tracker and the transcript, and
 * that is the point: the three of them are three readings of one radio, and a
 * call that is a fire in one panel and EMS in another is worse than either
 * panel being wrong on its own.
 */

/* --------------------------------------------------------------- the wire */

/* Where the firewall server is.
 *
 * Empty means "this origin", which is the answer whenever the page and the
 * server are the same thing: `firewall` serving /tracker, and `npm run dev`,
 * whose proxy makes /api local on purpose. A hosted tracker is the other case
 * -- the page is on Vercel and the radio is on a machine at home behind a
 * tunnel -- and then this is that tunnel's origin, set at BUILD time as
 * VITE_API_BASE, because a static site has no server to ask at runtime.
 *
 * Cross-origin, everything below also has to say `credentials: 'include'` or
 * the browser sends no cookie and the transcripts arrive redacted from a
 * server you are signed in to. Same-origin that flag is a no-op, so it is
 * unconditional: one code path, and no way for the hosted build to be the
 * only one that was never exercised.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')

/** A server path -- '/api/log', or the '/api/clip?id=…' a payload handed us --
 *  as a URL this page may actually fetch. Anything already absolute is left
 *  alone: it came from somewhere that had already decided. */
export const api = (path: string): string =>
  !API_BASE || /^[a-z]+:/i.test(path) ? path : API_BASE + path

/** One GET on the wire. Never cached, always credentialed. */
export const wire = (path: string): Promise<Response> =>
  fetch(api(path), { cache: 'no-store', credentials: 'include' })

/** Where "sign in" points, and it is the server's form, never this page's.
 *
 *  Hosted, those are two different origins: the form lives with the transcripts
 *  it unlocks, and a relative /login would ask whatever is serving these
 *  static files for a page it has never heard of. The whole URL goes in `next`
 *  for the same reason -- a path would send somebody to the server's own
 *  display rather than back to the tracker they were reading. The server only
 *  honours a return address on its allow_origins list, which is the same list
 *  that let this page make the read in the first place.
 */
export const signInHref = (server?: string | null): string =>
  /* `server` is the login_url a pushed payload carries, and it wins when there
     is one: on a deployment fed by push there is no firewall server on the
     other end of api() at all, and a same-origin /login would land on this
     page's own catch-all rewrite -- the tracker, again, with a query string
     on it, which reads as a sign-in button that does nothing. */
  (server || api('/login')) + '?next=' + encodeURIComponent(
    server || API_BASE ? location.href : location.pathname)

/** How long ago the radio machine last pushed, or null when the tracker is
 *  reading a firewall server directly and there is no copy in between.
 *  Measured by the far end against one clock, never across two. */
export const pushedAgo = (p: { pushed_at?: number | null } | null,
                          serverNow: number | undefined): number | null =>
  p?.pushed_at && Number.isFinite(serverNow)
    ? Math.max(0, (serverNow as number) - p.pushed_at)
    : null


export interface Status {
  state: string
  ts: number
  text?: string | null
}

export interface Eta {
  station?: string | null
  scene_at?: number | null
  scene_eta?: number | null
  passes_you?: boolean
  pass_at?: number | null
  pass_eta?: number | null
  closest_metres?: number | null
  estimated?: boolean
}

/** Where one unit on a call has got to.
 *
 * A call-level status is the honest answer for a headline and the wrong one
 * here: on a working fire the engine is still on scene while the medic has
 * already gone back in service, and a single word cannot say both. `state` uses
 * core's own vocabulary, so `stateWord` reads it. `ts` and `text` are the
 * transmission this was read off, and they are null when nothing was ever said
 * about the unit beyond the dispatch that named it.
 */
export interface UnitState {
  unit: string
  state: string
  ts: number | null
  text: string | null
}

/** One row of /api/log: a call, live or filed, however it reached us. */
export interface Call {
  id: string
  /** The incident directory this call was written to, when it was written. */
  incident: string | null
  dept: string
  opened: number
  closed: number | null
  /** Null means the parser did not recognise a call type. Never fill it in. */
  type: string | null
  address: string | null
  city: string | null
  units: string[]
  /** One entry per unit in `units`, same order. Empty when the transmissions
   *  behind this call are not available to read a per-unit state off. */
  unit_states: UnitState[]
  /** The call is over, but the radio was never heard closing it: the log has no
   *  stamp and the dispatch is more than a day old. Deliberately separate from
   *  `closed`, which is a time the radio actually said something. Nothing that
   *  happened yesterday is still happening, but we do not know when it ended. */
  assumed_closed?: boolean
  /** Transmissions filed. Null when nothing is being recorded to disk. */
  count: number | null
  live: boolean
  status: Status | null
  eta: Eta | null
}

export interface LogPayload {
  calls: Call[]
  pushed_at?: number | null
  login_url?: string | null
  /** Filed records the log opened on something that turned out not to be a
   *  dispatch, and which the server left out of `calls`. Counted rather than
   *  silently dropped: a list shorter than the directory behind it has to say
   *  so. Absent on a server that predates the check. */
  not_dispatches?: number
  /** See CurrentPayload.speech. Here it costs the status lines their quotes. */
  speech?: boolean
  /** Whether an incident_dir is configured and present. Not "are there calls". */
  logged: boolean
  now: number
}

/** One transmission on the tape.
 *
 * `url` is the whole record, with a media-fragment range on it when this row is
 * one keyup out of several. Two rows off one trunked grant carry the same clip
 * id and differ only in that range, which is the point: the bytes were paid for
 * and downloaded once.
 *
 * start/end are what was heard, as measured. play_start/play_end are the same
 * range padded for a listener, and they are the pair to time a player against.
 * A keyup's own length is not the record's, and timing the record is how a
 * 1.6-second reply inside a 4.8-second grant came to read 0:03 of 0:04 the
 * instant it started.
 */
export interface Row {
  id: string
  clip?: string | null
  ts: number
  /** Null when the server is withholding it: transcripts are behind a login on
   *  this installation and this browser is not signed in. Distinct from "",
   *  which means the row was heard and nothing was said in it. */
  text: string | null
  dispatch: boolean
  url: string | null
  start?: number | null
  end?: number | null
  play_start?: number | null
  play_end?: number | null
}

/** The stretch of a record one row is, or null when the row IS the record.
 *  Both numbers or neither: half a range is not a range, and inventing the
 *  missing half would seek to somewhere nobody asked for. */
export function rangeOf(r: Row | null | undefined): { start: number; end: number } | null {
  if (!r) return null
  const a = r.play_start, b = r.play_end
  return Number.isFinite(a) && Number.isFinite(b) && (b as number) > (a as number)
    ? { start: a as number, end: b as number }
    : null
}

/** Seconds as m:ss, for a player readout. */
export const clock = (s: number) => {
  const n = Math.max(0, Math.floor(Number.isFinite(s) ? s : 0))
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}

/** A live call as /api/current publishes it, which carries its own transcript. */
export interface LiveCall extends Omit<Call, 'incident' | 'count' | 'live' | 'opened'> {
  ts: number
  radio?: Row[]
  radio_start?: number | null
  radio_dispatch_ts?: number | null
  reopenings?: { closed: number | null; ts: number; text: string }[]
}

export interface CurrentPayload {
  ok: boolean
  error: string | null
  now?: number
  hold_seconds?: number
  calls: LiveCall[]
  feed: Row[]
  /** Whether this payload has the words in it. False means somebody has put
   *  transcripts behind a login and nobody here is signed in: the rows, the
   *  times and the audio all still arrive, and only the text is missing.
   *  Absent from a server too old to have a gate, which is the same as one
   *  with no gate configured. */
  speech?: boolean
  /** When the radio machine last pushed, on the clock that sent `now`. Only a
   *  hosted deployment fed by push has one; reading a firewall server directly,
   *  there is no copy in between and nothing to be stale. */
  pushed_at?: number | null
  /** Where that machine's sign-in form is, when it has a public address. Null
   *  means there is nowhere to send somebody, and the page says "locked"
   *  without offering a door that does not open. */
  login_url?: string | null
}

/* ------------------------------------------------------------- categories */

/** Which of the two accents a call belongs to. `none` is an absence, not a third. */
export type Family = 'hazard' | 'ems' | 'none'

/* Mirrored, patterns and order, from the CATEGORIES table in display.html.
 * Two screens hang on the same wall and a structure fire has to be the same
 * colour on both, so this list is copied deliberately rather than derived --
 * and when one of them learns a new phrasing, the other has to be taught it.
 * First match wins, so the order is load-bearing: "Water Flow Alarm" is an
 * alarm before it is water. */
const CATEGORIES: { label: string; family: Family; match: RegExp }[] = [
  { label: 'Fire',    family: 'hazard', match: /fire|smoke|burn|arcing|wires/i },
  { label: 'Hazmat',  family: 'hazard', match: /hazmat|gas|carbon monoxide|co alarm|spill|leak/i },
  { label: 'Alarm',   family: 'hazard', match: /alarm/i },
  { label: 'Crash',   family: 'hazard', match: /crash|collision|mva|accident|extric/i },
  { label: 'Medical', family: 'ems',    match: /medic|ems|chest|breath|cardiac|fall|unconscious|seizure|overdose|sick/i },
  { label: 'Rescue',  family: 'ems',    match: /rescue|entrap|water|elevator|lift assist/i },
]

/** What kind of call this is, in a word, or null when the type is unknown. */
export function categoryOf(type: string | null | undefined): { label: string; family: Family } {
  if (!type) return { label: 'Unclassified', family: 'none' }
  const hit = CATEGORIES.find((c) => c.match.test(type))
  return hit ? { label: hit.label, family: hit.family } : { label: 'Other', family: 'none' }
}

export const familyOf = (type: string | null | undefined): Family => categoryOf(type).family

/** The CSS custom property carrying each family's chart fill. */
export const MARK: Record<Family, string> = {
  hazard: 'var(--mark-hazard)',
  ems: 'var(--mark-ems)',
  none: 'var(--mark-none)',
}

/** The same three as text. Different steps: a fill and a word are different jobs. */
export const INK: Record<Family, string> = {
  hazard: 'var(--ink-hazard)',
  ems: 'var(--ink-ems)',
  none: 'var(--muted-foreground)',
}

export const FAMILY_LABEL: Record<Family, string> = {
  hazard: 'Fire and hazard',
  ems: 'EMS and rescue',
  none: 'Unclassified',
}

/* ----------------------------------------------------------------- status */

/* Mirrored from core, same as display.html mirrors it. Monotonic, clear last. */
export const STATUS_LABEL: Record<string, string> = {
  dispatched: 'dispatched',
  enroute: 'en route',
  on_scene: 'on scene',
  transporting: 'transporting',
  at_hospital: 'at hospital',
  clear: 'cleared',
}

/** The word for a state, including one this file has not been taught. */
export const stateWord = (st: string | null | undefined): string =>
  !st ? 'unknown' : STATUS_LABEL[st] ?? st.replace(/_/g, ' ')

/** The order core moves a call through, for drawing progress. Clear is the end. */
export const STATUS_ORDER = ['dispatched', 'enroute', 'on_scene', 'transporting',
                             'at_hospital', 'clear']

/* --------------------------------------------------------------- the clock */

/* Every stamp on the wire is the server's, and every age and countdown here is
 * one of them subtracted from now. A screen whose own clock is off renders all
 * of it wrong by the same amount, silently -- and this is exactly the machine
 * nobody checks the clock on. So the server sends its own `now` and this
 * follows it, the same correction display.html makes, for the same reason. */
let skew = 0
const SAMPLES: number[] = []
const KEEP = 9

export function noteSkew(serverNow: number | undefined, t0: number, t1: number) {
  if (!(Number.isFinite(serverNow) && serverNow! > 1e9 && serverNow! < 4e9)) return
  SAMPLES.push(serverNow! - (t0 + t1) / 2)
  if (SAMPLES.length > KEEP) SAMPLES.shift()
  /* Median rather than the latest: one poll that queued behind a whisper decode
     is seconds late and would yank every age on the screen at once. */
  const sorted = [...SAMPLES].sort((a, b) => a - b)
  skew = sorted[sorted.length >> 1]
}

export const now = () => Date.now() / 1000 + skew
export const skewSeconds = () => skew

/* ------------------------------------------------------------- formatting */

const two = (n: number) => String(n).padStart(2, '0')

/** 24-hour, fixed width, for log columns. No skew correction: an absolute
 *  instant the server already fixed comes out right whatever this box thinks. */
export const hhmmss = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}

export const hhmm = (ts: number) => {
  const d = new Date(ts * 1000)
  return `${two(d.getHours())}:${two(d.getMinutes())}`
}

/** "14 Aug", or "14 Aug 2025" once the year is not this one. */
export const dayOf = (ts: number) => {
  const d = new Date(ts * 1000)
  const opts: Intl.DateTimeFormatOptions =
    d.getFullYear() === new Date().getFullYear()
      ? { day: 'numeric', month: 'short' }
      : { day: 'numeric', month: 'short', year: 'numeric' }
  return d.toLocaleDateString([], opts)
}

/** Rounded, never truncated: truncating is how 119 seconds reads "1m". */
export function ago(seconds: number): string {
  const a = Math.max(0, Math.round(seconds))
  if (a < 60) return `${a}s ago`
  const m = Math.round(a / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  const r = m % 60
  if (h < 24) return r ? `${h}h ${r}m ago` : `${h}h ago`
  const d = Math.round(h / 24)
  return `${d}d ago`
}

/** A duration, for how long a call ran. */
export function lasted(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

/* ---------------------------------------------------------------- windows */

export type WindowKey = '24h' | '7d' | 'all'

const ALL_WINDOWS: { key: WindowKey; label: string; seconds: number | null }[] = [
  { key: '24h', label: '24 hours', seconds: 86400 },
  { key: '7d',  label: '7 days',   seconds: 7 * 86400 },
  { key: 'all', label: 'All',      seconds: null },
]

/* How far back this deployment keeps calls at all, in seconds, or null for as
 * far back as the server will go.
 *
 * The hosted tracker is set to a day (VITE_RETAIN_HOURS, in vercel.json).
 * The server holding the radio has the whole incident log on disk and there is
 * no reason to hide it from a screen in the same building; a copy of the
 * tracker on the public internet is a different proposition, and a day is
 * about what "what has this department been called to" actually needs.
 *
 * It is one number and it does three things, because a retention that only did
 * one of them would be a lie in the other two: it is what the log is ASKED for,
 * what the app KEEPS of the answer, and what the window chooser may OFFER. A
 * "7 days" chip over a day of data is the worst of those -- a person reads an
 * empty week rather than a full day and concludes the department had a quiet
 * week.
 */
const RETAIN_SECONDS = ((): number | null => {
  const raw = Number(import.meta.env.VITE_RETAIN_HOURS)
  return Number.isFinite(raw) && raw > 0 ? raw * 3600 : null
})()

/** The retention as the server wants to hear it: hours, not an instant.
 *
 *  Hours rather than a `since` timestamp on purpose. This box's clock is the
 *  one thing on the screen that is allowed to be wrong -- the whole skew
 *  correction below exists because it often is -- and a `since` computed here
 *  would silently ask for the wrong day. A duration means the same thing on
 *  both machines, and the server subtracts it from its own clock.
 */
export const LOG_PATH = RETAIN_SECONDS
  ? `/api/log?hours=${RETAIN_SECONDS / 3600}`
  : '/api/log'

/** The windows this deployment can honestly offer. Nothing longer than what it
 *  keeps, and never empty: retention shorter than a day leaves the day chip,
 *  which is then clamped by inScope like every other one. */
export const WINDOWS = ((): typeof ALL_WINDOWS => {
  if (RETAIN_SECONDS === null) return ALL_WINDOWS
  const fits = ALL_WINDOWS.filter((w) => w.seconds !== null && w.seconds <= RETAIN_SECONDS)
  return fits.length ? fits : [ALL_WINDOWS[0]]
})()

/** Whether the app is dropping calls the server would still have. False on an
 *  installation with no retention set, where "all" really is all. */
export const retained = (): number | null => RETAIN_SECONDS

/** Old enough that this deployment does not keep it at all. Applied where the
 *  log lands rather than only where it is drawn, so a call past retention is
 *  not sitting in memory being counted by something that forgot to filter. */
export const expired = (c: Call, at: number) =>
  RETAIN_SECONDS !== null && at - c.opened > RETAIN_SECONDS

/** One place decides what is in scope, so the chart and the table never
 *  disagree about which calls they are describing. */
export function inScope(c: Call, win: WindowKey, dept: string | null, at: number) {
  if (dept && c.dept !== dept) return false
  const w = WINDOWS.find((x) => x.key === win)
  /* The narrower of the two, and `all` is not an exemption: it means "all of
     what this deployment has", which is the retention. */
  const span = w?.seconds === null || w?.seconds === undefined
    ? RETAIN_SECONDS
    : RETAIN_SECONDS === null ? w.seconds : Math.min(w.seconds, RETAIN_SECONDS)
  return span === null || at - c.opened <= span
}

/* Motion is switched off wholesale rather than per animation, because the
 * setting is a person saying "do not move things", not a hint. */
export const reducedMotion = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
