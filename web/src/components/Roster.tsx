/* Who is on the other end of the radio, and what they send.
 *
 * The other three panels answer questions about the last day. This one answers
 * the question underneath them -- whose radio is this -- and it is the only
 * thing on the screen that is not a reading of the wire. That makes it
 * reference material sitting on an instrument, which is a thing to be careful
 * about, so it earns its place by being wired to the same day as everything
 * else: a rig that is out right now says so, and a rig that ran four calls in
 * this window says that. Left alone it is a roster; during a working fire it
 * is the part of the screen that tells you what just went past the window.
 *
 * The facts are in lib/purdue.ts with their sources. This file only draws.
 */

import { Fragment, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'

import { Apparatus } from '@/components/Apparatus'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { type Call, type UnitState, reducedMotion, stateWord } from '@/lib/firewall'
import {
  type Apparatus as Rig,
  DEPARTMENT,
  PHOTOGRAPHY,
  ROSTER,
  STATIONS,
  apparatusFor,
} from '@/lib/purdue'

/* The tracker's easing, not a second one. Two panels on one card that ease
   differently read as two pages. */
const EASE: [number, number, number, number] = [0.2, 0, 0, 1]

/** What this window knows about one rig. */
interface Activity {
  /** Calls in the window this rig was dispatched on. */
  runs: number
  /** The live call it is on, and where it had got to, when there is one. */
  out: { call: Call; state: UnitState | null } | null
  /** On the call the table currently has open. */
  shown: boolean
}

const NOTHING: Activity = { runs: 0, out: null, shown: false }

/* ------------------------------------------------------------------ panel */

export function Roster({
  calls,
  selected,
}: {
  calls: Call[]
  /** The call open in the table below, so the rigs on it can be marked. Two
   *  panels describing one call ought to point at each other. */
  selected: string | null
}) {
  const still = reducedMotion()
  const [open, setOpen] = useState<string | null>(null)

  /* One pass over the window, rather than a filter per rig inside the map
     below: seven rigs times a week of calls is a scan nobody needs to do
     seven times, and this runs again on every log poll. */
  const { activity, foreign } = useMemo(() => {
    const acc = new Map<string, Activity>()
    const others = new Set<string>()

    for (const call of calls) {
      for (let i = 0; i < call.units.length; i += 1) {
        const unit = call.units[i]
        const rig = apparatusFor(unit)
        if (!rig) {
          /* Mutual aid, and not a parse failure. Lafayette's engine on a
             Purdue box is a real unit that this department does not own, and
             quietly folding it into the roster would be the worse mistake. */
          others.add(unit)
          continue
        }
        const at = acc.get(rig.id) ?? { runs: 0, out: null, shown: false }
        at.runs += 1
        if (call.live && !at.out) {
          at.out = { call, state: call.unit_states[i] ?? null }
        }
        if (selected && call.id === selected) at.shown = true
        acc.set(rig.id, at)
      }
    }
    return { activity: acc, foreign: others.size }
  }, [calls, selected])

  const running = ROSTER.filter((r) => activity.get(r.id)?.out).length

  return (
    <Card>
      <CardHeader>
        <CardTitle>{DEPARTMENT.name}</CardTitle>
        <CardDescription>
          {DEPARTMENT.claim} Talkgroup{' '}
          <span className="font-mono text-foreground">{DEPARTMENT.talkgroup}</span>, which
          is the one this system listens to.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="flex flex-wrap gap-1.5">
          {DEPARTMENT.services.map((s) => (
            <Badge key={s} variant="outline" className="font-normal">
              {s}
            </Badge>
          ))}
        </div>

        <Separator />

        <div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h3 className="text-sm font-medium">Apparatus</h3>
            <p className="font-mono text-xs text-muted-foreground">
              {ROSTER.length} units
              {running ? ` · ${running} out` : ''}
            </p>
          </div>

          {STATIONS.map((st) => {
            const rigs = ROSTER.filter((r) => r.station === st.n)
            if (!rigs.length) return null
            return (
              <Fragment key={st.n}>
                <p className="mt-4 mb-1.5 text-xs text-muted-foreground">
                  {st.name}
                  <span className="mx-1.5 text-edge">&middot;</span>
                  {st.where}
                </p>
                <ul className="divide-y divide-border border-y border-border">
                  {rigs.map((rig, i) => (
                    <Unit
                      key={rig.id}
                      rig={rig}
                      at={activity.get(rig.id) ?? NOTHING}
                      open={open === rig.id}
                      onToggle={() => setOpen(open === rig.id ? null : rig.id)}
                      still={still}
                      /* Staggered across the station rather than the whole
                         roster, so the second list does not start four beats
                         late for having been drawn second. */
                      index={i}
                    />
                  ))}
                </ul>
              </Fragment>
            )
          })}

          <p className="mt-3 max-w-prose text-[11px] leading-snug text-muted-foreground">
            {/* The standing credit. Not a courtesy line at the bottom of a
                page nobody scrolls to: these are eleven photographs this
                project does not own, loaded from the photographer's own
                server, and his name belongs next to them. */}
            Photographs by{' '}
            <a
              href={PHOTOGRAPHY.href}
              target="_blank"
              rel="noreferrer"
              className="text-foreground underline underline-offset-2"
            >
              {PHOTOGRAPHY.by}, {PHOTOGRAPHY.site}
            </a>
            , used with credit and linked back. All rights reserved by the photographer.
            {foreign ? (
              <>
                {' '}
                {foreign} other {foreign === 1 ? 'unit' : 'units'} appeared on these calls
                and {foreign === 1 ? 'is' : 'are'} not on this roster &mdash; mutual aid,
                which the radio carries and this department does not own.
              </>
            ) : null}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------- unit */

function Unit({
  rig,
  at,
  open,
  onToggle,
  still,
  index,
}: {
  rig: Rig
  at: Activity
  open: boolean
  onToggle: () => void
  still: boolean
  index: number
}) {
  const detailId = `rig-${rig.id}`

  return (
    <motion.li
      /* The one entrance on this panel, and it runs once. Six rows arriving
         together is a flash; six rows arriving forty milliseconds apart is the
         list being dealt out, and the eye follows it down to where it stops. */
      initial={still ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: still ? 0 : 0.3, delay: still ? 0 : index * 0.045, ease: EASE }}
      className={cn(
        'transition-colors',
        open ? 'bg-muted/70' : 'hover:bg-muted/30',
        /* The call open in the table is on this rig. An outline rather than a
           fill, so it survives being open at the same time. */
        at.shown ? 'outline-2 -outline-offset-2 outline-ring/60' : null,
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={detailId}
        className="flex w-full cursor-pointer items-center gap-4 px-2 py-3 text-left outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      >
        <Apparatus rig={rig} priority={index < 3} className="w-32 shrink-0 sm:w-44" />

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-sm font-medium">{rig.name}</span>
            <span className="text-xs text-muted-foreground">{rig.role}</span>
            {/* Said only of the one rig it is true of. Marking everything
                that does not answer a box alarm put this on the chief's
                pickup and on three utility trucks, which is how a marker
                stops being one. */}
            {rig.standing ? (
              <span className="font-mono text-[10px] text-muted-foreground">
                {rig.standing}
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {rig.rig}
          </span>
        </span>

        <span className="shrink-0 text-right">
          {at.out ? (
            /* The word, not a light. Same vocabulary as the table, so a rig
               reading "on scene" here and in the row below is one claim. */
            <span className="flex items-center justify-end gap-1.5 font-mono text-[11px]">
              <span className="font-medium text-foreground">out</span>
              <span className="text-muted-foreground">
                {stateWord(at.out.state?.state ?? at.out.call.status?.state)}
              </span>
            </span>
          ) : (
            <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
              {at.runs
                ? `${at.runs} ${at.runs === 1 ? 'call' : 'calls'}`
                : /* Not "0 calls". A rig that has not run in this window is a
                     quiet shift, and a zero next to six other counts reads as
                     a rig out of service. */
                  'none this window'}
            </span>
          )}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            id={detailId}
            key="detail"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: still ? 0 : 0.22, ease: EASE }}
            className="overflow-hidden"
          >
            <div className="space-y-3 px-2 pb-4">
              <dl className="flex flex-wrap gap-x-8 gap-y-3">
                {rig.specs.map((s) => (
                  <div key={s.label}>
                    <dt className="text-[11px] text-muted-foreground">{s.label}</dt>
                    <dd className="mt-0.5 font-mono text-sm">{s.value}</dd>
                  </div>
                ))}
                {rig.crew ? (
                  <div>
                    <dt className="text-[11px] text-muted-foreground">Rides with</dt>
                    <dd className="mt-0.5 text-sm">{rig.crew}</dd>
                  </div>
                ) : null}
              </dl>

              {rig.note ? (
                <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
                  {rig.note}
                </p>
              ) : null}

              {/* This picture's own credit, with the year on it. The footer
                  below names the photographer once for the panel; this names
                  him for the photograph, and links to that frame rather than
                  to the gallery, so "where is this from" is one click and not
                  a search. */}
              <p className="text-[11px] text-muted-foreground">
                Photograph &copy; {rig.photo.year} {rig.photo.by} &middot;{' '}
                <a
                  href={rig.photo.href}
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  IndianaFireTrucks.com
                </a>
              </p>

              {at.out ? (
                <p className="text-xs text-foreground">
                  Out now on{' '}
                  {at.out.call.type ?? 'a call with no type recognised'}
                  {at.out.call.address ? ` at ${at.out.call.address}` : ''}.
                  {at.out.state?.text ? (
                    /* Quoted, because it is the radio and not this screen's
                       summary of it. Absent when transcripts are behind a
                       login nobody here is signed in to. */
                    <span className="text-muted-foreground"> &ldquo;{at.out.state.text}&rdquo;</span>
                  ) : null}
                </p>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.li>
  )
}
