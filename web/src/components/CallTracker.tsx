/* The call tracker: one row per call, newest first, each one openable into the
 * little that is actually known about it.
 *
 * The rule this file keeps, everywhere, is that nothing on screen may be more
 * certain than the wire. Two instants are real -- `opened` and `status.ts` --
 * and everything else is either drawn without a time or worded as an estimate.
 */

import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'

import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import {
  type Call,
  type Row,
  type UnitState,
  STATUS_ORDER,
  dayOf,
  hhmm,
  hhmmss,
  lasted,
  radioPath,
  reachedState,
  reducedMotion,
  stateWord,
  wire,
} from '@/lib/firewall'

const COLUMNS = 6

/* Typed as a tuple rather than left to widen: motion's `ease` takes a bezier of
   exactly four numbers, and a plain array literal is a number[] to the checker. */
const EASE: [number, number, number, number] = [0.2, 0, 0, 1]

/* --------------------------------------------------------------- fragments */

/** An absence. Not an em dash, and not a word that could be mistaken for data. */
function Nothing({ title }: { title: string }) {
  return (
    <span className="text-muted-foreground" title={title}>
      &middot;
    </span>
  )
}

/* "live" is the whole marker. There was a pulsing dot next to it, which said
   nothing the word did not already say, and an animation that repeats for ever
   on a screen left running for months is decoration wearing a signal's clothes.
   The row's own ink already steps up for a running call; this names it. */
function LiveMark() {
  return (
    <span className="font-mono text-[11px] font-medium text-foreground">live</span>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="space-y-1 text-sm">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------------- the clock */

/** Whether a stamp falls on the day this box thinks it is. */
function openedToday(ts: number) {
  const d = new Date(ts * 1000)
  const n = new Date()
  return (
    d.getFullYear() === n.getFullYear() &&
    d.getMonth() === n.getMonth() &&
    d.getDate() === n.getDate()
  )
}

/* ------------------------------------------------------------ progression */

interface Step {
  key: string
  word: string
  ts: number | null
  reached: boolean
}

/* The ladder is cut at the state the call is actually in. Drawing the rest of
   STATUS_ORDER as pending would tell an operator that a structure fire is on
   its way to a hospital, which nobody said and which is not going to happen.

   Two things can move it, and which one did is worth knowing. A live call has
   `status`: core's own state machine, run over the traffic as it arrived. A
   filed one has none and never will -- the log keeps what was said, not the
   machine that read it -- so the furthest state its units were HEARD to reach
   stands in, which is the same reading asked one question higher. See
   reachedState. The caption under the ladder says which of the two this was.

   `clear` is not a rung, and that is the one thing this cannot get wrong. The
   states below it are a progression a truck really does climb -- a unit at the
   hospital drove there and stood on the scene first -- so drawing them reached
   and blank is a fair reading of the state it ended on. Clearing implies none
   of them: "Medic 16 is in service refusal" is a crew that never transported
   and was never at a hospital, and filling the ladder under that closer drew
   both. So the ladder is cut at the furthest POSITION and the close is put on
   the end of it.

   Only two rungs can carry a time: `opened`, which is the dispatch, and the
   transmission the rung was read off. The ones between were passed through. */
const CLEAR = STATUS_ORDER.indexOf('clear')

function progression(call: Call): Step[] {
  const heard = call.status
  const read = heard ? null : reachedState(call)
  const state = heard?.state ?? read?.state ?? null
  const ts = heard?.ts ?? read?.ts ?? null

  /* Where the call got to before it ended. When the state above is already a
     position it IS that; when the call has cleared, the closer says nothing
     about how far it got and the units are the only ones who can. */
  const rank = state ? STATUS_ORDER.indexOf(state) : 0
  const done = rank >= CLEAR
  const pos = done ? reachedState(call, 'at_hospital') : null
  const upto = done ? Math.max(0, STATUS_ORDER.indexOf(pos?.state ?? 'dispatched'))
                    : Math.max(0, rank)
  const posTs = done ? (pos?.ts ?? null) : ts

  const steps: Step[] = []
  for (let i = 0; i <= upto; i++) {
    const key = STATUS_ORDER[i]
    const stamped = i === 0 ? call.opened : i === upto ? posTs : null
    steps.push({ key, word: stateWord(key), ts: stamped, reached: true })
  }

  /* A state core has learned and this file has not. It is still a fact on the
     wire, so it goes on the end rather than being dropped or renamed. */
  if (state && rank < 0) {
    steps.push({ key: state, word: stateWord(state), ts, reached: true })
  }

  if (done) {
    steps.push({ key: 'clear', word: stateWord('clear'),
                 ts: ts ?? call.closed ?? null, reached: true })
  } else if (call.live) {
    steps.push({ key: 'clear', word: stateWord('clear'), ts: null, reached: false })
  } else if (call.closed != null || call.assumed_closed) {
    /* Over, and that is the log's claim rather than a crew's -- the units above
       may have said nothing at all. `closed` is a time the radio was heard
       ending it and carries its stamp; assumed_closed is a dispatch old enough
       that nothing is still happening, and carries none, which is the whole
       reason the server flags it rather than inventing one. */
    steps.push({ key: 'clear', word: stateWord('clear'),
                 ts: call.closed ?? null, reached: true })
  }
  return steps
}

/* ---------------------------------------------------------------- the ETA */

/** The arrival lines, all of them worded as arithmetic rather than as report. */
function etaLines(call: Call): string[] {
  const eta = call.eta
  if (!eta) return []
  const out: string[] = []
  if (eta.station) out.push(`Running from ${eta.station}`)

  if (eta.passes_you) {
    const when =
      eta.pass_at != null
        ? `due past you about ${hhmm(eta.pass_at)}`
        : eta.pass_eta != null
          ? `due past you in about ${lasted(eta.pass_eta)}`
          : 'passes you on the way'
    const near =
      eta.closest_metres != null
        ? `, closest ${Math.round(eta.closest_metres)} m`
        : ''
    out.push(when.charAt(0).toUpperCase() + when.slice(1) + near)
  } else if (eta.scene_at != null) {
    out.push(`Due on scene about ${hhmm(eta.scene_at)}`)
  } else if (eta.scene_eta != null) {
    out.push(`Due on scene in about ${lasted(eta.scene_eta)}`)
  }
  return out
}

/* --------------------------------------------------------------- the crew */

/** One unit, its state in words, and the transmission that word was read off. */
function UnitLine({ u, cleared }: { u: UnitState; cleared: boolean }) {
  return (
    <li
      className={cleared ? 'text-muted-foreground' : 'text-foreground'}
      title={u.text ?? undefined}
    >
      <div className="flex items-baseline gap-2">
        <span className="font-mono">{u.unit}</span>
        <span>{stateWord(u.state)}</span>
        {u.ts == null ? (
          /* The call has a dispatch time and this unit does not. Printing the
             call's would say a crew was heard from when nobody heard it. */
          <span className="ml-auto pl-2 text-xs text-muted-foreground">nothing heard</span>
        ) : (
          <span className="ml-auto pl-2 font-mono text-xs tabular-nums text-muted-foreground">
            {hhmmss(u.ts)}
          </span>
        )}
      </div>
      {/* The transmission is the evidence for the word above it. A wall screen
          has no pointer, so the evidence for a unit still on the call has to be
          readable without hovering; a cleared unit keeps its in the title,
          because it is not going to change what anybody does next. */}
      {!cleared && u.text ? (
        <div className="truncate text-[11px] text-muted-foreground">{u.text}</div>
      ) : null}
    </li>
  )
}

function UnitGroup({ label, units, cleared }: {
  label: string
  units: UnitState[]
  cleared: boolean
}) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">
        {label} ({units.length})
      </div>
      <ol className="mt-1 space-y-1">
        {units.map((u, i) => (
          <UnitLine key={`${u.unit}-${i}`} u={u} cleared={cleared} />
        ))}
      </ol>
    </div>
  )
}

function UnitRoster({ call }: { call: Call }) {
  if (!call.units.length) {
    return <span className="text-muted-foreground">None named</span>
  }

  /* An empty list is the reader saying it could not read the transmissions, and
     the entries are one per unit or nothing. Drawing a short list as the whole
     crew would take a unit off the screen, and filling a missing one in as
     dispatched would put a truck somewhere on the strength of nothing said. So
     anything but a complete list falls back to the designators alone. */
  if (call.unit_states.length !== call.units.length) {
    return (
      <>
        <div className="flex flex-wrap gap-1">
          {call.units.map((u, i) => (
            <Badge key={`${u}-${i}`} variant="outline" className="font-mono">
              {u}
            </Badge>
          ))}
        </div>
        <div className="text-xs text-muted-foreground">
          Where these got to was not read back.
        </div>
      </>
    )
  }

  /* Cleared is the only state that takes a unit off the call, so it is the only
     one this file tests for. A state core has learned since this was written is
     still somebody working, which is the safe side to be wrong on. Order inside
     each group is the wire's, not one invented here. */
  const working = call.unit_states.filter((u) => u.state !== 'clear')
  const cleared = call.unit_states.filter((u) => u.state === 'clear')

  return (
    <div className="space-y-3">
      {working.length ? (
        <UnitGroup label="Still on the call" units={working} cleared={false} />
      ) : null}
      {cleared.length ? (
        <UnitGroup label="Cleared" units={cleared} cleared />
      ) : null}
    </div>
  )
}

/* ---------------------------------------------------------------- the tape */

/* How long after a call opens its traffic is still its traffic, when the radio
 * was never heard closing it. The same ten minutes core holds a call open for,
 * because it is the same question -- and generous rather than tight, since the
 * cost of the wrong answer here is a neighbouring call's sentence in the list
 * and not a missing one. */
const OPEN_TAIL = 600

/* Read either side of the call. Dispatch traffic runs a few seconds ahead of
 * the stamp on the call it opened, and the closing exchange runs past it. */
const LEAD = 60
const TRAIL = 120

/** What was said while this call ran.
 *
 * Asked for by time, which is the only thing the archive knows: a transmission
 * is stored because it was heard, not because a parser decided which call it
 * belonged to. So this is honestly a stretch of the tape rather than "this
 * call's transcript", and on a department with two calls running at once it
 * will show both -- which is what the radio did.
 *
 * Fetched when the row is opened and not before. Nobody expands two thousand
 * rows, and a month of transcripts is not something to pull down on the chance
 * that somebody might.
 */
function Tape({ call }: { call: Call }) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stop = false
    const to = call.closed ?? call.opened + OPEN_TAIL
    ;(async () => {
      try {
        const r = await wire(radioPath(call.opened - LEAD, to + TRAIL))
        /* No such route: this tracker is reading a firewall server directly, or
           a hosted half that predates the archive. Neither is an error worth
           printing -- the section simply is not there. */
        if (r.status === 404) { if (!stop) setRows([]) ; return }
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const j: { feed?: Row[]; error?: string } = await r.json()
        if (stop) return
        setError(j.error ?? null)
        setRows(j.feed ?? [])
      } catch (e) {
        if (!stop) { setError(String((e as Error).message || e)); setRows([]) }
      }
    })()
    return () => { stop = true }
  }, [call.id, call.opened, call.closed])

  if (rows === null) {
    return <p className="text-sm text-muted-foreground">Reading the tape…</p>
  }
  if (error) {
    return <p className="text-sm text-muted-foreground">{error}</p>
  }
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground">
        {call.live
          ? 'Still running — the transcript is in the panel on the right.'
          : 'Nothing from this call was kept.'}
      </p>
    )
  }

  return (
    <ol className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.id} className="flex gap-3 text-sm">
          <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
            {hhmmss(r.ts)}
          </span>
          {/* Null is withheld and "" is silence, and they are different facts
              about the same row. Neither is a sentence, so neither is printed
              as one. */}
          <span className={cn('min-w-0',
                              r.dispatch ? 'text-foreground' : 'text-muted-foreground')}>
            {r.text === null ? (
              <span className="italic">locked</span>
            ) : r.text === '' ? (
              <span className="italic">nothing said</span>
            ) : (
              r.text
            )}
          </span>
        </li>
      ))}
    </ol>
  )
}

/* ------------------------------------------------------------- the detail */

function Detail({ call }: { call: Call }) {
  const steps = progression(call)
  const lines = etaLines(call)
  /* The same call reachedState makes inside progression, made again here for
     the caption. The ladder alone cannot say where its rungs came from, and a
     state read off the units is a weaker claim than one the state machine ran:
     it is the furthest any single truck got, which on a working fire is the
     medic and not the call. Saying so is cheaper than being asked. */
  const read = call.status ? null : reachedState(call)

  return (
    <div className="px-2 pt-1 pb-4">
      <div className="grid gap-5 rounded-lg bg-muted/40 p-4 sm:grid-cols-[1.2fr_1fr_1fr]">
        <div className="space-y-4">
          <Field label="Where">
            <div className="text-foreground">
              {call.address ?? <Nothing title="No address on the wire" />}
            </div>
            {call.city ? (
              <div className="text-muted-foreground">{call.city}</div>
            ) : null}
          </Field>
          <Field label="Department">
            <div className="text-foreground">{call.dept}</div>
          </Field>
        </div>

        <Field label={`Units (${call.units.length})`}>
          <UnitRoster call={call} />
        </Field>

        <Field label="Status">
          <ol className="space-y-1">
            {steps.map((s) => (
              <li key={s.key} className="flex items-baseline gap-2">
                <span className={s.reached ? 'text-foreground' : 'text-muted-foreground'}>
                  {s.word}
                </span>
                {s.reached ? null : (
                  <span className="text-xs text-muted-foreground">not yet</span>
                )}
                {s.ts == null ? null : (
                  <span className="ml-auto pl-2 font-mono text-xs tabular-nums text-muted-foreground">
                    {hhmmss(s.ts)}
                  </span>
                )}
              </li>
            ))}
          </ol>
          {call.status ? null : read ? (
            <div className="text-xs text-muted-foreground">
              Read from {read.unit}. The log keeps what was said, not a status
              for the call.
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">
              No unit was heard reporting itself after the dispatch.
            </div>
          )}
        </Field>
      </div>

      <Separator className="my-4" />

      <div className="grid gap-5 sm:grid-cols-3">
        <Field label="Closed">
          {call.closed != null ? (
            <div>
              <span className="font-mono tabular-nums text-foreground">
                {hhmmss(call.closed)}
              </span>
              <span className="text-muted-foreground">
                {' '}
                after {lasted(call.closed - call.opened)}
              </span>
            </div>
          ) : call.assumed_closed ? (
            /* "Still open" here is the same lie the row's own status column
               refuses: the server flagged this one over -- a dispatch old
               enough that nothing about it is still happening -- and withheld a
               stamp on purpose rather than inventing one. Both halves of that
               have to be said, or the panel reads as a call running since
               Tuesday. */
            <span className="text-muted-foreground">
              Over. The radio was never heard closing this one.
            </span>
          ) : (
            <span className="text-muted-foreground">Still open</span>
          )}
        </Field>

        <Field label="Transmissions">
          {call.count == null ? (
            <span className="text-muted-foreground">Nothing recorded to disk</span>
          ) : (
            <div className="text-foreground">
              <span className="font-mono tabular-nums">{call.count}</span> filed
            </div>
          )}
          {call.incident ? (
            <div className="text-muted-foreground">
              Record{' '}
              <span className="font-mono text-foreground">{call.incident}</span>
            </div>
          ) : null}
        </Field>

        <Field label="Arrival">
          {lines.length ? (
            <>
              {lines.map((l) => (
                <div key={l} className="text-foreground">
                  {l}
                </div>
              ))}
              <div className="text-xs text-muted-foreground">
                Estimated from distance. Only the status is reported.
              </div>
            </>
          ) : (
            <span className="text-muted-foreground">No estimate</span>
          )}
        </Field>
      </div>

      <Separator className="my-4" />

      {/* Last, and deliberately: everything above is a reading of the radio and
          this is the radio. Somebody who doubts a status line scrolls down to
          the sentence it was read off. */}
      <Field label="What was said">
        <Tape call={call} />
      </Field>
    </div>
  )
}

/* ---------------------------------------------------------------- the row */

function CallRow({
  call,
  open,
  still,
  onToggle,
}: {
  call: Call
  open: boolean
  still: boolean
  onToggle: () => void
}) {
  const detailId = `call-detail-${call.id}`
  const extra = call.units.length - 3

  /* What a screen reader is told this row is, since the visible cell it hangs
     off is a bare timestamp. Built from the same fields the row draws, so the
     two can never describe different calls. */
  const name = [
    hhmm(call.opened),
    call.type ?? 'no type recognised',
    call.address ? `at ${call.address}` : null,
    call.dept,
    call.live ? 'live' : null,
  ].filter(Boolean).join(', ')

  return (
    <Fragment>
      <TableRow
        onClick={onToggle}
        className={cn(
          /* The row is the mouse target and the button inside it is the real
             control. The row was carrying role="button" and tabIndex, which
             works and costs the table its structure: a tr that is a button is
             no longer a row, so a screen reader loses the column headers for
             every cell in it and reads seven unlabelled strings. Native
             semantics stay, the button does the announcing, and the row still
             shows focus because the ring is drawn from :has(). */
          'cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:-outline-offset-2 has-[:focus-visible]:outline-ring',
          open
            ? 'bg-muted/90 hover:bg-muted active:bg-muted/60'
            : 'hover:bg-muted/40 active:bg-muted/70',
          /* Three steps back from the present, so the eye lands on what is
             happening without being told twice. A running call holds full ink;
             a filed one the radio never closed sits a step down, because it may
             still be going and nobody heard the end of it; a cleared call is
             finished and sits a step further.

             80% is the floor, not a taste: muted-foreground on the card
             measures 6.76:1 and this blend measures 4.79:1, which is the
             dimmest a row can go and still clear AA for body text. Going to
             70% reads better and lands at 4.00:1, which is a row nobody can
             read at the far end of the hall. */
          call.live ? 'text-foreground'
            : call.closed || call.assumed_closed ? 'text-muted-foreground/80'
            : 'text-muted-foreground',
        )}
      >
        <TableCell className="align-top font-mono tabular-nums">
          <button
            type="button"
            aria-label={name}
            aria-expanded={open}
            aria-controls={detailId}
            /* Stopped, or the row's own handler runs straight after this one
               and toggles the call shut again in the same click. */
            onClick={(e) => { e.stopPropagation(); onToggle() }}
            className="cursor-pointer text-left outline-none"
          >
            {hhmm(call.opened)}
          </button>
          {/* A date on every row of a screen showing the last hour is noise. */}
          {openedToday(call.opened) ? null : (
            <div className="text-[11px] text-muted-foreground">{dayOf(call.opened)}</div>
          )}
        </TableCell>

        <TableCell className="align-top">
          {call.type ? (
            call.type
          ) : (
            /* The parser did not name this one. Saying so is the only honest
               thing on offer: a stand-in word here becomes a call type on the
               next screen that reads this. */
            <span className="italic text-muted-foreground">No type recognised</span>
          )}
        </TableCell>

        <TableCell className="align-top">
          {call.address ? (
            <div className="max-w-[16rem] truncate" title={call.address}>
              {call.address}
            </div>
          ) : (
            <Nothing title="No address on the wire" />
          )}
          {call.city ? (
            <div className="text-[11px] text-muted-foreground">{call.city}</div>
          ) : null}
        </TableCell>

        <TableCell className="align-top">
          {call.units.length ? (
            <div className="flex flex-wrap items-center gap-1">
              {call.units.slice(0, 3).map((u, i) => (
                <Badge key={`${u}-${i}`} variant="outline" className="font-mono">
                  {u}
                </Badge>
              ))}
              {extra > 0 ? (
                <Badge
                  variant="outline"
                  className="font-mono"
                  title={call.units.slice(3).join(', ')}
                >
                  +{extra}
                </Badge>
              ) : null}
            </div>
          ) : null}
        </TableCell>

        <TableCell className="align-top">
          {call.live ? (
            <span className="flex items-center gap-1.5">
              <LiveMark />
              {stateWord(call.status?.state)}
            </span>
          ) : (
            /* A filed call carries no status on the wire, and the word for a
               state nobody sent is not a state. `closed` is a fact. So is a
               dispatch more than a day old, which the server flags rather than
               stamping, because it knows the call is over and not when: that
               reads "cleared" like any other finished call, and the title says
               the radio was never heard ending it, so nobody mistakes the two.
               "filed" is left for the genuinely uncertain middle. */
            <span
              className="text-muted-foreground"
              title={call.assumed_closed
                ? 'Over. The radio was never heard closing this one.'
                : undefined}
            >
              {call.closed != null || call.assumed_closed ? 'cleared' : 'filed'}
            </span>
          )}
        </TableCell>

        <TableCell className="align-top text-right font-mono tabular-nums">
          {call.count == null ? (
            <Nothing title="Nothing is being recorded to disk for this call" />
          ) : (
            call.count
          )}
        </TableCell>
      </TableRow>

      {/* The row outlives the panel inside it. Unmounting it with the selection
          would cut the close animation off at its first frame. */}
      <tr aria-hidden={!open}>
        <td id={detailId} colSpan={COLUMNS} className="border-0 p-0">
          <AnimatePresence initial={false}>
            {open ? (
              <motion.div
                key="detail"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: still ? 0 : 0.22, ease: EASE }}
                className="overflow-hidden"
              >
                <Detail call={call} />
              </motion.div>
            ) : null}
          </AnimatePresence>
        </td>
      </tr>
    </Fragment>
  )
}

/* -------------------------------------------------------------- the panel */

/* Most rows the table draws at once.
 *
 * A window used to be a day and is now a month, and a month is a couple of
 * thousand of these -- each one a table row with a unit list, a status and an
 * expandable detail behind it. React will build all of them, the browser will
 * lay all of them out, and the person reading has scrolled past forty. So the
 * table is the newest of them and says so, and the chart above it goes on
 * counting every call in the window, because that is the number that is being
 * asked for. Narrowing the window is what shows the rest, which is what the
 * window chooser is for.
 */
const MAX_ROWS = 200

export function CallTracker({
  calls,
  logged,
  selected,
  onSelect,
}: {
  calls: Call[]
  logged: boolean
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  /* Read once per render rather than per row: the setting is one person's
     answer, and asking matchMedia several hundred times cannot change it. */
  const still = reducedMotion()
  const running = calls.filter((c) => c.live).length
  const shown = calls.length > MAX_ROWS ? calls.slice(0, MAX_ROWS) : calls

  return (
    <Card>
      <CardHeader>
        <CardTitle>Calls</CardTitle>
        {calls.length ? (
          <CardDescription>
            {calls.length} {calls.length === 1 ? 'call' : 'calls'},{' '}
            {running === 0 ? 'none running' : `${running} running`}
            {shown.length < calls.length
              ? `, newest ${shown.length} listed`
              : null}
          </CardDescription>
        ) : null}
      </CardHeader>

      <CardContent>
        {calls.length === 0 ? (
          <p className="max-w-prose py-6 text-sm text-muted-foreground">
            {logged ? (
              'No calls opened in this window.'
            ) : (
              'Past calls are not being kept, so only calls still running appear here.'
            )}
          </p>
        ) : (
          /* The table takes the horizontal scroll itself. A page that slides
             sideways on a phone loses the Time column, which is the one thing
             every row is read by. */
          <Table className="min-w-[36rem]">
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Time</TableHead>
                <TableHead scope="col">Type</TableHead>
                <TableHead scope="col">Address</TableHead>
                <TableHead scope="col">Units</TableHead>
                <TableHead scope="col">Status</TableHead>
                <TableHead scope="col" className="text-right">
                  Transmissions
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((call) => (
                <CallRow
                  key={call.id}
                  call={call}
                  open={selected === call.id}
                  still={still}
                  onToggle={() => onSelect(selected === call.id ? null : call.id)}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
