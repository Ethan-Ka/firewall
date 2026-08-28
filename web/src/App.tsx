import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { animate } from 'motion'
import { Button } from '@/components/ui/button'
import { TypeChart } from '@/components/TypeChart'
import { CallTracker } from '@/components/CallTracker'
import { Roster } from '@/components/Roster'
import { Transcript } from '@/components/Transcript'
import { cn } from '@/lib/utils'
import {
  type Call, type CurrentPayload, type LogPayload, type Row, type WindowKey,
  MARK, WINDOWS, ago, dayOf, familyOf, inScope, noteSkew, now, reducedMotion,
  signInHref, skewSeconds, spanWords, wire, expired, retained, pushedAgo,
  LOG_PATH, HISTORY_PATH, spanOf, wireJson,
  type Family,
} from '@/lib/firewall'

/* How often each half of the wire is asked.
 *
 * /api/current is the radio and is polled on the display's own two seconds.
 * /api/log walks the incident directory and reads a json file per call, so it
 * is not something to do twice a second; ten is plenty for a chart counting a
 * shift. The gap that matters is a NEW call, which would otherwise take up to
 * ten seconds to reach the tracker while the transcript already had its
 * dispatch on screen, so a change in the live id set pulls the log
 * immediately. Responsive where a person would notice, cheap where nobody
 * would. */
const CURRENT_MS = 2000
const LOG_MS = 10000

/* And the archive, which is neither of those. /api/history is a month of calls
 * and it is answered off a cache at the edge; what changes in it between two
 * reads is a call that /api/log has already put on the screen. Five minutes is
 * how long a page left open can go on drawing yesterday's boundary before the
 * word "yesterday" starts to mean the wrong day. */
const HISTORY_MS = 300000

/* Past this, a pushed copy is old enough to say so in the hazard colour. The
 * far end is what decides whether stale means `ok: false` -- it stamped the
 * push and measured the age on one clock, and this is only the colour. Kept
 * equal to STALE_SECONDS in web/api/_store.js so the word and the colour turn
 * over at the same moment. */
const PUSH_STALE = 120

export default function App() {
  const [log, setLog] = useState<LogPayload | null>(null)
  const [feed, setFeed] = useState<Row[]>([])
  const [health, setHealth] = useState<{ ok: boolean; error: string | null }>(
    { ok: true, error: null })
  /* Only a deployment fed by push has these: how old the copy is, and where
     the machine behind it takes a sign-in. Null everywhere else, which is the
     tracker reading a firewall server with nothing in between. */
  const [age, setAge] = useState<number | null>(null)
  const [loginUrl, setLoginUrl] = useState<string | null>(null)
  /* Everything older than the day /api/log carries, fetched apart from it and
     merged under it. Empty on a tracker reading a firewall server directly,
     which has the whole incident log on a disk and answers /api/log with it. */
  const [history, setHistory] = useState<Call[]>([])
  const [live, setLive] = useState(true)
  const [reached, setReached] = useState(false)
  /* Optimistic until a payload says otherwise, so the rows do not flash
     "locked" for one poll on an installation that has no gate at all. */
  const [speech, setSpeech] = useState(true)

  const [win, setWin] = useState<WindowKey>('24h')
  const [selected, setSelected] = useState<string | null>(null)

  /* A second hand for the ages and the clock. Kept separate from the polls so
     "4m ago" keeps counting between them rather than freezing for two seconds
     at a time. */
  const [, setTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])

  /* The set of live call ids as of the last /api/current, so a dispatch can
     pull the log without the log itself having to notice. A ref rather than
     state: it is read inside the poll and must never re-run the effect. */
  const liveIds = useRef('')
  const pullLog = useRef<() => void>(() => {})

  const fetchLog = useCallback(async () => {
    try {
      const r = await wire(LOG_PATH)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const j: LogPayload = await r.json()
      /* A server that predates unit_states sends the field not at all, and the
         panels below read a list. Normalised once, here, rather than guarded
         for at every use: the empty list is already the contract's way of
         saying "not known", which is exactly what an older server means by
         leaving it out, so the two collapse honestly onto one shape. */
      const at = now()
      setLog({
        ...j,
        /* Dropped here, at the door, and not only in the filters below. The
           server answers a retention window as of when IT read the clock, and
           a tab left open overnight would otherwise go on holding yesterday's
           calls between polls -- counted by anything that reads log.calls
           without filtering, and drawn the moment somebody picks a longer
           window. What this deployment does not keep, it does not keep. */
        calls: (j.calls ?? [])
          .map((c) => ({
            ...c,
            units: c.units ?? [],
            unit_states: c.unit_states ?? [],
          }))
          .filter((c) => !expired(c, at)),
      })
    } catch {
      /* Held rather than blanked. A log that failed to refetch is stale by a
         few seconds; an empty one is a lie about the department's whole day. */
    }
  }, [])
  pullLog.current = fetchLog

  useEffect(() => {
    let stop = false
    const poll = async () => {
      const t0 = Date.now() / 1000
      try {
        const r = await wire('/api/current')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const j: CurrentPayload = await r.json()
        if (stop) return
        noteSkew(j.now, t0, Date.now() / 1000)
        setLive(true)
        setReached(true)
        setFeed(Array.isArray(j.feed) ? j.feed : [])
        setSpeech(j.speech !== false)
        setAge(pushedAgo(j, j.now))
        setLoginUrl(j.login_url ?? null)
        setHealth({ ok: j.ok !== false, error: j.ok === false ? j.error : null })
        const ids = (j.calls ?? []).map((c) => c.id).sort().join(',')
        if (ids !== liveIds.current) {
          liveIds.current = ids
          pullLog.current()
        }
      } catch {
        if (!stop) setLive(false)
      }
    }
    poll()
    const t = setInterval(poll, CURRENT_MS)
    return () => { stop = true; clearInterval(t) }
  }, [])

  useEffect(() => {
    fetchLog()
    const t = setInterval(fetchLog, LOG_MS)
    return () => clearInterval(t)
  }, [fetchLog])

  /* The archive. Asked for once and then every few minutes, and abandoned for
     good on a 404: that is a deployment with no /api/history route at all --
     the firewall server itself, or a hosted half that predates the archive --
     and polling it for the life of the tab would be a request a minute that
     can only ever fail. Anything else is left alone to retry, because a
     datacentre having a bad moment is not a missing endpoint. */
  const noArchive = useRef(false)
  useEffect(() => {
    let stop = false
    const pull = async () => {
      if (noArchive.current) return
      try {
        /* Null is a deployment with no archive behind it, however it says so --
           a 404, or the firewall server's own habit of answering an unknown
           /api path with a page. Given up on for good either way: polling a
           route that is not there is a request a minute that can only fail. */
        const j = await wireJson<{ calls?: Call[] }>(HISTORY_PATH)
        if (!j) { noArchive.current = true; return }
        if (stop) return
        const at = now()
        setHistory((j.calls ?? [])
          .map((c) => ({ ...c, units: c.units ?? [], unit_states: c.unit_states ?? [] }))
          .filter((c) => !expired(c, at)))
      } catch {
        /* Held. A month of calls that failed to refetch is a month of calls. */
      }
    }
    pull()
    const t = setInterval(pull, HISTORY_MS)
    return () => { stop = true; clearInterval(t) }
  }, [])

  /* Which window the screen opens on, decided once off the first payload.
     Fixing it at 24h is what a default usually is and it is wrong here. This
     gets left running for days and then looked at, and a department that had a
     quiet night would be greeted by an empty chart that reads as broken rather
     than as accurate. So it opens on the narrowest window that actually
     contains calls, once, and the choice belongs to the room after that. */
  const picked = useRef(false)

  /* The two halves, as one list. The log's copy of a call wins wherever both
     have it: the archive is written from pushes and is a few minutes behind by
     design, and a call that is still running is in both -- once as it stood
     when it was last archived, once as it is now. Merged here rather than in
     either fetch so neither has to know the other exists, and so a tracker
     reading a firewall server directly (where history is empty) goes through
     exactly the same code path as a hosted one. */
  const all = useMemo(() => {
    const live = log?.calls ?? []
    if (!history.length) return live
    const seen = new Set(live.map((c) => c.id))
    return [...live, ...history.filter((c) => !seen.has(c.id))]
  }, [log, history])
  const at = now()

  /* Decided against the merged list and not against the log, and settled only
     once something is in it. The archive arrives on its own schedule, so the
     log alone can be empty on a deployment holding three weeks of calls -- and
     picking off it would fix the screen on an empty day and then never look
     again, because this only ever runs once. */
  useEffect(() => {
    if (picked.current || !all.length) return
    picked.current = true
    const on = now()
    const fit = WINDOWS.find((w) => all.some((c) => inScope(c, w.key, null, on)))
    if (fit) setWin(fit.key)
  }, [all])

  /* The one place scope is decided, so the chart and the table can never
     describe two different sets of calls. */
  /* Running calls first, then newest dispatch first inside each group.
     The server hands these back in time order, which is the right order for a
     log and the wrong one for a wall: a call that is happening now belongs at
     the top whether it was toned out four minutes ago or forty. Sorted here
     rather than in the table so the chart and the table keep reading the same
     array, and sorted with a stable comparator so rows do not swap places
     between polls when two calls share a second. */
  const scoped = useMemo<Call[]>(
    () => all
      .filter((c) => inScope(c, win, null, at))
      .sort((a, b) => Number(b.live) - Number(a.live) || b.opened - a.opened),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all, win, Math.floor(at / 60)])

  const oldest = scoped.length ? Math.min(...scoped.map((c) => c.opened)) : null
  const running = scoped.filter((c) => c.live).length

  /* The hour of the day this department actually runs, which is the one thing a
     week of calls knows and a live screen cannot.

     Counting calls per hour of the day and taking the biggest bin is the
     obvious version and it is wrong, because the bins are not the same size.
     A window is a stretch of wall clock with two ragged ends: opened at 14:20
     over seven days, the 14:00 bin has had eight goes at collecting a call and
     every other hour has had seven, and an archive that started on Tuesday
     afternoon gives the afternoon hours a whole extra day over the morning
     ones. Raw counts read that as "this department is busy at two", when what
     happened is that we watched two o'clock for longer.

     So each hour is divided by how long it was actually watched, measured by
     walking the window rather than derived from its length -- because the
     window's length says nothing about which hours of the day are in it.

     Ties go to the earlier hour, so the figure does not swap between two equal
     hours on every poll. */
  const busiest = useMemo(() => {
    if (!scoped.length) return null

    /* Bounded by the data at the near end as well as by the window. A window
       that reaches back further than the archive does is mostly a stretch of
       time nobody was listening to, and counting it as watched would divide
       every hour by a week of silence. */
    const span = spanOf(win)
    const from = Math.max(oldest as number, span === null ? -Infinity : at - span)
    const calls = new Array(24).fill(0)
    const watched = new Array(24).fill(0)

    for (const c of scoped) {
      if (c.opened >= from) calls[new Date(c.opened * 1000).getHours()] += 1
    }
    /* Walked in fixed steps and each step charged to the hour its middle falls
       in, rather than walked from one hour boundary to the next.

       The boundary version is the tidier loop and it hangs twice a year. On the
       night the clocks go back 01:00 happens twice, and rounding an instant in
       the second one down to "the start of its hour" lands in the first one --
       behind where the walk already is. A step that cannot go backwards has no
       opinion about any of that, and the hour it lands in is whatever the
       browser says it is, which is the same thing the calls were binned by.

       The step is five minutes until the window is long enough that five
       minutes is thousands of iterations for no gain: nothing here is measuring
       to the minute, and the figure it feeds is a call an hour. */
    const step = Math.max(300, (at - from) / 20000)
    for (let t = from; t < at; t += step) {
      const slice = Math.min(step, at - t)
      watched[new Date((t + slice / 2) * 1000).getHours()] += slice / 3600
    }

    let best = -1
    let rate = 0
    for (let h = 0; h < 24; h += 1) {
      /* An hour barely watched at all is not a candidate. One call in the six
         minutes since the archive began is ten an hour, and it would beat every
         real hour on the board for the rest of the day. */
      if (watched[h] < 0.25 || !calls[h]) continue
      const r = calls[h] / watched[h]
      if (r > rate) { best = h; rate = r }
    }
    if (best < 0) return null
    return { hour: best }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scoped, win, oldest, Math.floor(at / 60)])

  /* Whole percentages that add to a hundred. Rounding each share on its own
     leaves 67 + 29 + 3, and three numbers under a bar that visibly fills the
     width reads as a broken figure rather than as rounding. */
  const split = useMemo(() => {
    if (!scoped.length) return null
    const order: Family[] = ['ems', 'hazard', 'none']
    const raw = order.map((f) => ({
      family: f,
      n: scoped.filter((c) => familyOf(c.type) === f).length,
    }))
    const exact = raw.map((r) => (r.n / scoped.length) * 100)
    const pct = exact.map(Math.floor)
    let left = 100 - pct.reduce((a, b) => a + b, 0)
    for (const i of exact
      .map((v, k) => [v - Math.floor(v), k] as const)
      .sort((a, b) => b[0] - a[0])
      .map(([, k]) => k)) {
      if (left <= 0) break
      pct[i] += 1
      left -= 1
    }
    return raw.map((r, i) => ({ ...r, pct: pct[i] }))
  }, [scoped])
  const logged = log?.logged ?? false
  const skew = Math.round(skewSeconds())
  const retain = retained()

  /* A selection that scrolled out of scope is a highlight on nothing. */
  useEffect(() => {
    if (selected && !scoped.some((c) => c.id === selected)) setSelected(null)
  }, [scoped, selected])

  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground lg:h-dvh lg:overflow-hidden">
      <a href="#calls" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-sm focus:bg-card focus:px-3 focus:py-2 focus:ring-2 focus:ring-ring">
        Skip to the calls
      </a>

      {/* Asymmetric on purpose: the figure sits low and left against a meta
          column on the right, and nothing here is mirrored about the centre. */}
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-x-8 gap-y-4 px-4 pt-5 pb-4 sm:px-6">
        <div className="min-w-0">
          <h1 className="text-sm font-medium text-muted-foreground">Call tracker</h1>
          <p className="mt-1 flex items-baseline gap-3">
            {/* Said once, settled, for anything that reads rather than looks.
                A number counting up sixty times a second is not a thing to
                announce, and an aria-live region here would say the whole
                figure again on every frame. */}
            <span className="sr-only">
              {scoped.length} {scoped.length === 1 ? 'call' : 'calls'}
              {oldest ? ` since ${dayOf(oldest)}` : ''}
            </span>
            <Count value={scoped.length} />
            <span aria-hidden="true" className="text-sm text-muted-foreground">
              {scoped.length === 1 ? 'call' : 'calls'}
              {oldest ? <> since {dayOf(oldest)}</> : null}
            </span>
          </p>
        </div>

        <dl className="flex min-w-0 flex-wrap items-end gap-x-6 gap-y-3 sm:gap-x-10">
          <Stat label="Active calls">
            <span className="text-2xl leading-none font-semibold tabular-nums">
              {running}
            </span>
          </Stat>

          <Stat label="Busiest hour">
            {busiest ? (
              <span className="font-mono text-2xl leading-none font-semibold tabular-nums">
                {String(busiest.hour).padStart(2, '0')}:00
              </span>
            ) : scoped.length ? (
              /* Calls, but not enough of the clock watched to rank an hour
                 against the others -- a deployment that has been up for twenty
                 minutes. Distinct from having nothing at all, because the two
                 look identical on a fresh screen and only one of them is
                 fixed by waiting. */
              <span className="text-sm text-muted-foreground">too soon to say</span>
            ) : (
              <span className="text-sm text-muted-foreground">no calls yet</span>
            )}
          </Stat>

          <Stat
            label="EMS and fire"
            note={split
              ? split.filter((r) => r.n).map((r) => `${r.pct}% ${SHARE[r.family]}`).join(' \u00b7 ')
              : null}
          >
            {split ? <Split split={split} /> : (
              <span className="text-sm text-muted-foreground">no calls yet</span>
            )}
          </Stat>
        </dl>
      </header>

      {/* One filter row above everything it scopes, so both panels always
          re-read against the same slice. Per-panel filters are how two
          numbers on one screen come to disagree. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-3 border-y border-border px-4 py-2.5 sm:px-6">
        {/* Absent rather than disabled when this deployment keeps one window's
            worth of calls: a row of one chip that is always on is a control
            that cannot be operated, and the status line at the right says what
            the span is in words. */}
        {WINDOWS.length > 1 ? (
          <fieldset className="flex flex-wrap items-center gap-1.5">
            <legend className="sr-only">Time window</legend>
            {WINDOWS.map((w) => (
              <Chip key={w.key} on={win === w.key} onClick={() => setWin(w.key)}>
                {w.label}
              </Chip>
            ))}
          </fieldset>
        ) : null}

        {/* What a viewer needs to judge what they are looking at: whether it
            is current, how far back it goes, and whether anything is being
            held back from them. Deliberately not the operator's diagnostics --
            this is a public page, and "recording" or a count of records that
            did not parse as calls mean nothing to somebody watching the radio
            and cannot be acted on by them. */}
        <p className="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1
                      font-mono text-xs text-muted-foreground">
          {!live ? (
            <span className="text-[var(--ink-hazard)]">not connected</span>
          ) : !health.ok ? (
            <span className="text-[var(--ink-hazard)]">radio source is failing</span>
          ) : age != null && age > PUSH_STALE ? (
            /* Past the window the far end calls stale, so the word and the
               colour turn over on the same threshold it does. */
            <span className="text-[var(--ink-hazard)]">last update {ago(age)}</span>
          ) : age != null ? (
            <span>updated {ago(age)}</span>
          ) : null}

          {retain ? <span>{spanWords(retain)} of history</span> : null}

          {!speech ? (
            <a
              href={signInHref(loginUrl)}
              className="text-[var(--ink-ems)] underline-offset-2 hover:underline"
            >
              transcripts hidden, sign in
            </a>
          ) : null}

          {Math.abs(skew) > 90 ? (
            <span className="text-[var(--ink-hazard)]">
              this device is {ago(Math.abs(skew)).replace(' ago', '')} off; times are corrected
            </span>
          ) : null}
        </p>
      </div>

      <main className="grid min-h-0 flex-1 gap-4 p-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex min-h-0 min-w-0 flex-col gap-4 lg:overflow-y-auto">
          {/* Held at its natural height. As a plain flex child it shrinks when
              the column runs out of room, and a card that shrinks under an
              overflow-hidden does not scale, it clips: at 720px the chart lost
              its bars and then cut its own heading through the middle of the
              letters. The column scrolls instead, which is the honest way to
              be short of space. */}
          <div className="shrink-0">
            <TypeChart calls={scoped} logged={logged} />
          </div>
          <div id="calls" className="min-w-0 shrink-0">
            <CallTracker
              calls={scoped}
              logged={logged}
              selected={selected}
              onSelect={setSelected}
            />
          </div>
          {/* Last in the column on purpose. It is the only panel here that is
              not a reading of the wire, so it sits under the ones that are:
              somebody glancing at this screen wants the running calls, and
              somebody reading it wants to know whose engine that was. Scoped
              with everything else, because "four calls this window" has to
              mean the same window the chart above it drew. */}
          <div className="min-w-0 shrink-0">
            <Roster calls={scoped} selected={selected} />
          </div>
        </div>

        <div className="min-h-0 lg:h-full">
          <Transcript
            feed={feed}
            ok={health.ok}
            error={health.error}
            live={live || !reached}
            speech={speech}
          />
        </div>
      </main>
    </div>
  )
}

/* Short enough to sit under a bar without wrapping. "Unclassified" is the word
   everywhere else on this screen and it does not fit here, so the bar's own
   label says EMS and fire and this names the third slice honestly rather than
   dropping it. */
const SHARE: Record<Family, string> = { ems: 'EMS', hazard: 'fire', none: 'neither' }

/* One figure in the header row. The optional note carries what the number
   cannot say on its own. */
function Stat({ label, note, children }: {
  label: string
  note?: string | null
  children: React.ReactNode
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1.5">{children}</dd>
      {note ? <p className="mt-1 text-[11px] text-muted-foreground">{note}</p> : null}
    </div>
  )
}

/* Part to whole, at three segments, which is the most this form carries. The
   fills are the chart's, so the header and the bars can never disagree about
   what colour a fire is, and the percentages under it are set in text ink
   rather than in the fill colours: a mark beside a number carries identity, a
   coloured number is just hard to read.

   A zero share is dropped rather than drawn at zero width, or its 2px gap
   survives it and leaves a notch in the bar with nothing on either side. */
function Split({ split }: { split: { family: Family; n: number; pct: number }[] }) {
  const parts = split.filter((r) => r.pct > 0)
  return (
    <div
      className="flex h-2 w-24 gap-[2px] overflow-hidden sm:w-32"
      role="img"
      aria-label={split.filter((r) => r.n)
        .map((r) => `${r.pct} percent ${SHARE[r.family]}`).join(', ')}
    >
      {parts.map((r) => (
        <span
          key={r.family}
          className="rounded-[1px]"
          style={{ width: `${r.pct}%`, background: MARK[r.family] }}
        />
      ))}
    </div>
  )
}

/* The headline figure, counting to its value rather than appearing at it.
 *
 * Two durations, and the difference is the point. The first paint runs longer
 * because it is the only moment the number is genuinely arriving; every change
 * after that is a filter being answered or a call landing, and those are state
 * changes that should feel immediate rather than performed. A poll that moves
 * nothing animates nothing, which is the guard that stops this counting at the
 * viewer every two seconds for ever.
 *
 * Written straight to the node rather than through state: this settles over
 * dozens of frames, and putting each one through React would re-render the
 * chart and the whole call table underneath it to move one digit.
 *
 * `tabular-nums` on a figure this size is normally wrong -- equal-width digits
 * make a number look loose at display sizes -- and it is right here for the one
 * reason that outranks it: the digits are changing every frame, and
 * proportional figures make the whole line jitter sideways while they do.
 */
function Count({ value }: { value: number }) {
  const el = useRef<HTMLSpanElement>(null)
  const from = useRef(0)
  const first = useRef(true)

  useEffect(() => {
    const node = el.current
    if (!node) return
    const start = from.current
    from.current = value
    if (start === value) { node.textContent = String(value); return }
    if (reducedMotion()) {
      /* Someone who asked for no motion gets the answer, not a slower
         performance of it. */
      node.textContent = String(value)
      first.current = false
      return
    }
    const run = animate(start, value, {
      duration: first.current ? 0.5 : 0.22,
      ease: [0.2, 0, 0, 1],
      onUpdate: (v) => { node.textContent = String(Math.round(v)) },
    })
    first.current = false
    return () => run.stop()
  }, [value])

  return (
    <span
      ref={el}
      aria-hidden="true"
      className="text-5xl leading-none font-semibold tracking-tight tabular-nums sm:text-6xl"
    />
  )
}

/* A filter chip. A shadcn Button in two states rather than a hand-rolled control,
   so focus, hover and active come from the same place as every other button
   here. On is stated by aria-pressed and carried visually by fill AND weight,
   never by colour alone. */
function Chip({ on, onClick, children }: {
  on: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={on ? 'secondary' : 'ghost'}
      aria-pressed={on}
      onClick={onClick}
      className={cn('h-7 rounded-sm px-2.5 text-xs',
        on ? 'font-medium text-foreground' : 'font-normal text-muted-foreground')}
    >
      {children}
    </Button>
  )
}
