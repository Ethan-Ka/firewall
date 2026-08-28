/* Counts of call types, as bars and as a table.
 *
 * The bars are the fast read and the table is the true one: the chart folds
 * everything past the twelfth type into a single bar because thirteen hairlines
 * of 1 are noise, but the table always lists every type, so nothing that got
 * folded is only reachable by mouse.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as d3 from 'd3'
import { animate } from 'motion'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  FAMILY_LABEL,
  MARK,
  familyOf,
  reducedMotion,
  type Call,
  type Family,
} from '@/lib/firewall'

const TOP_N = 12
/* The slot a row owns. The bar takes 24 of it and the remaining 8 is air, which
   is what keeps a dozen bars from reading as one hatched block. */
const ROW = 32
const BAR = 24
/* Gutter labels stop short of the baseline so the longest one never touches a
   bar it does not belong to. */
const GAP = 12
const TIP_GAP = 6
const CORNER = 4
const EASE: [number, number, number, number] = [0.2, 0, 0, 1]
const STAGGER = 0.018

/* Three durations for the whole panel, and each one means something. DURATION
   is a value changing and the chart settling back to rest, QUICK is the pointer
   being answered, FADE is something arriving or leaving. A fourth would only be
   a shade of one of these and nobody would be able to say which. */
const DURATION = 0.22
const QUICK = 0.14
const FADE = 0.12

/* Rows rest just under full opacity so the hovered one has somewhere to go.
   At 0.88 the fills still clear 3:1 on the card and the labels stay above 5:1,
   so resting is a step down in emphasis and not in legibility. */
const REST = 0.88
const DIM = 0.4

/* Every animation here is a tween on the same curve. Spelling that out once
   keeps a stray spring, which would overshoot an opacity past 1, out of reach. */
const tween = (duration: number) => ({ type: 'tween' as const, duration, ease: EASE })

/* Sentinels rather than empty strings, so a department that really does dispatch
   a call type named "" cannot collide with the unrecognised bucket. */
const NO_TYPE = '\u0000no-type'
const TAIL = '\u0000tail'

interface Datum {
  key: string
  label: string
  count: number
  family: Family
  /** True for the folded row, which stands for several types and has no family. */
  tail: boolean
}

/* ------------------------------------------------------------- measurement */

let ctx: CanvasRenderingContext2D | null = null
let widths = new Map<string, number>()

/** The rendered width of a string, at the font the svg will actually use. */
function textWidth(text: string, font: string): number {
  const key = `${font}\u0000${text}`
  const hit = widths.get(key)
  if (hit !== undefined) return hit
  if (!ctx) ctx = document.createElement('canvas').getContext('2d')
  /* No 2d context is a headless or memory-starved browser, not a normal one.
     Falling back to a per-character guess keeps the chart drawn and slightly
     wrong rather than undrawn. */
  if (!ctx) return text.length * 7
  ctx.font = font
  const w = ctx.measureText(text).width
  widths.set(key, w)
  return w
}

/** `text` if it fits, otherwise elided to fit. Colons mean the head names a
 *  group ("Medical: Chest Pain") and both ends carry meaning, so those elide in
 *  the middle; everything else loses its tail. */
function fit(text: string, max: number, font: string): string {
  if (max <= 0) return ''
  if (textWidth(text, font) <= max) return text
  const middle = text.includes(':')
  for (let keep = text.length - 1; keep > 0; keep--) {
    const head = middle ? Math.ceil(keep / 2) : keep
    const tail = keep - head
    const s = text.slice(0, head) + '…' + (tail > 0 ? text.slice(text.length - tail) : '')
    if (textWidth(s, font) <= max) return s
  }
  return '…'
}

const round = (n: number) => Math.round(n * 100) / 100

/** A bar with a rounded right end and a square left one. `rect rx` rounds all
 *  four corners, which puts a curve on the baseline and makes small values look
 *  like they start left of zero. */
function barPath(x0: number, y: number, w: number, h: number, r: number): string {
  const width = Math.max(0, w)
  const rr = Math.max(0, Math.min(r, width, h / 2))
  const [X, Y, W, H, R] = [round(x0), round(y), round(width), round(h), round(rr)]
  if (R === 0) return `M${X},${Y}h${W}v${H}h${-W}Z`
  return (
    `M${X},${Y}h${round(W - R)}a${R},${R} 0 0 1 ${R},${R}` +
    `v${round(H - R * 2)}a${R},${R} 0 0 1 ${-R},${R}h${-round(W - R)}Z`
  )
}

/* Neither ink clears 4.5:1 on all three fills, so the label inside a bar takes
   the one that clears its own: #1c1917 measures 4.53 on hazard and 5.35 on ems,
   white measures 4.75 on the unclassified grey where the dark ink is only 3.65. */
const INSIDE_INK: Record<Family, string> = {
  hazard: '#1c1917',
  ems: '#1c1917',
  none: '#ffffff',
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** Rounded share, except that a type with calls in it never reads as zero. */
function share(count: number, total: number) {
  const p = total > 0 ? (count / total) * 100 : 0
  const small = p > 0 && p < 1
  return {
    text: small ? '<1' : String(Math.round(p)),
    spoken: small ? 'under 1 percent' : `${Math.round(p)} percent`,
  }
}

/* ------------------------------------------------------------------ chart */

export function TypeChart({ calls, logged }: { calls: Call[]; logged: boolean }) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const [expanded, setExpanded] = useState(false)
  const [active, setActive] = useState<string | null>(null)
  const [width, setWidth] = useState(0)
  const [fontEpoch, setFontEpoch] = useState(0)
  const [fonts, setFonts] = useState({ label: '12px sans-serif', value: '12px monospace' })

  const sansProbe = useRef<HTMLSpanElement>(null)
  const monoProbe = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const paths = useRef(new Map<string, SVGPathElement>())
  const groups = useRef(new Map<string, SVGGElement>())
  const drawn = useRef(new Map<string, number>())
  const lastSig = useRef<string | null>(null)
  const firstPaint = useRef(true)
  const observer = useRef<ResizeObserver | null>(null)
  const rowAnims = useRef(new Map<string, { stop: () => void }>())
  const rowTargets = useRef(new Map<string, number>())
  const tipAnim = useRef<{ stop: () => void } | null>(null)
  const tipAt = useRef<{ key: string; x: number; y: number } | null>(null)
  const tipUp = useRef(false)
  const lastHot = useRef<Datum | null>(null)

  const total = calls.length
  const empty = total === 0

  const rows = useMemo(() => {
    const bucket = new Map<string, Datum>()
    for (const c of calls) {
      const key = c.type ?? NO_TYPE
      const at = bucket.get(key)
      if (at) {
        at.count++
        continue
      }
      bucket.set(key, {
        key,
        /* The parser did not name this one. Nothing here may name it either. */
        label: c.type ?? 'No type recognised',
        count: 1,
        family: familyOf(c.type),
        tail: false,
      })
    }
    /* Ties break on the label so two types stuck on the same count do not swap
       places every poll and make the chart look like it is updating. */
    return [...bucket.values()].sort(
      (a, b) => b.count - a.count || a.label.localeCompare(b.label),
    )
  }, [calls])

  const totals = useMemo(() => {
    const t: Record<Family, number> = { hazard: 0, ems: 0, none: 0 }
    for (const r of rows) t[r.family] += r.count
    return t
  }, [rows])

  const folded = rows.length > TOP_N && !expanded
  const shown = useMemo(() => {
    if (!folded) return rows
    const rest = rows.slice(TOP_N)
    const tail: Datum = {
      key: TAIL,
      label: `${rest.length} more types`,
      count: rest.reduce((s, r) => s + r.count, 0),
      family: 'none',
      tail: true,
    }
    return [...rows.slice(0, TOP_N), tail]
  }, [rows, folded])

  /* Measure the container rather than the window: this is one of three panels
     and it gets narrow long before the window does. */
  const wrap = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    observer.current = null
    if (!el) return
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width))
    ro.observe(el)
    observer.current = ro
    setWidth(el.clientWidth)
  }, [])

  /* Only on unmount. Stopping these per effect run is exactly the bug this
     component is trying not to have: a poll re-runs the effects, and a cleanup
     that stopped animations would cut the dim off mid fade. */
  useEffect(
    () => () => {
      observer.current?.disconnect()
      for (const a of rowAnims.current.values()) a.stop()
      tipAnim.current?.stop()
    },
    [],
  )

  /* Measure at the font the browser resolved, not at a font this file guessed:
     IBM Plex Mono and a fallback monospace disagree by enough to clip a label. */
  useLayoutEffect(() => {
    const a = sansProbe.current
    const b = monoProbe.current
    if (!a || !b) return
    const read = (el: HTMLElement) => {
      const s = getComputedStyle(el)
      return `${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`
    }
    const next = { label: read(a), value: read(b) }
    setFonts((f) => (f.label === next.label && f.value === next.value ? f : next))
  }, [fontEpoch, view, empty])

  /* A webfont that arrives after first paint invalidates every measurement made
     before it, and the labels stay elided at the old width until something else
     re-renders. Re-measure once when the fonts settle. */
  useEffect(() => {
    let live = true
    document.fonts?.ready.then(() => {
      if (!live) return
      widths = new Map()
      setFontEpoch((e) => e + 1)
    })
    return () => {
      live = false
    }
  }, [])

  const height = shown.length * ROW
  const gutter = Math.max(96, Math.min(200, width * 0.4))
  const valueW = Math.max(0, ...shown.map((d) => textWidth(String(d.count), fonts.value)))
  const plot = Math.max(24, width - gutter - valueW - TIP_GAP - 4)
  const max = d3.max(shown, (d) => d.count) ?? 0

  const x = useMemo(
    () => d3.scaleLinear().domain([0, max || 1]).range([0, plot]),
    [max, plot],
  )
  const band = useMemo(
    () => d3.scaleBand<string>().domain(shown.map((d) => d.key)).range([0, height]),
    [shown, height],
  )
  const thickness = Math.min(BAR, band.bandwidth())

  const labels = useMemo(() => {
    const room = Math.max(0, gutter - GAP)
    const out = new Map<string, string>()
    for (const d of shown) out.set(d.key, fit(d.label, room, fonts.label))
    return out
  }, [shown, gutter, fonts.label, fontEpoch])

  const sig = shown.map((d) => `${d.key}:${d.count}`).join('|')
  const geom = `${plot}|${thickness}|${gutter}|${height}`

  /* React never writes `d`. The bars are driven imperatively so a poll that does
     not move a number does not re-run an animation, and so a resize snaps to the
     new width instead of sliding to it. */
  useLayoutEffect(() => {
    if (view !== 'chart' || width <= 0) return
    const still = reducedMotion()
    const changed = sig !== lastSig.current
    const first = firstPaint.current
    lastSig.current = sig
    firstPaint.current = false

    const running: { stop: () => void }[] = []
    shown.forEach((d, i) => {
      const el = paths.current.get(d.key)
      if (!el) return
      const y = (band(d.key) ?? 0) + (band.bandwidth() - thickness) / 2
      const to = x(d.count)
      const put = (w: number) => {
        drawn.current.set(d.key, w)
        el.setAttribute('d', barPath(gutter, y, w, thickness, CORNER))
      }
      const from = drawn.current.get(d.key) ?? 0
      if (!changed || still || from === to) {
        put(to)
        return
      }
      put(from)
      running.push(
        animate(from, to, {
          ...tween(DURATION),
          delay: first ? i * STAGGER : 0,
          onUpdate: put,
        }),
      )
    })
    return () => {
      for (const a of running) a.stop()
    }
  }, [sig, geom, view, width])

  const focusRow = (i: number) => {
    const next = shown[i]
    if (!next) return false
    groups.current.get(next.key)?.focus()
    return true
  }

  const legend = (['hazard', 'ems', 'none'] as Family[]).filter((f) => totals[f] > 0)
  const hot = active ? shown.find((d) => d.key === active) : undefined
  /* The tooltip outlives the hover by one fade, so it needs something to say on
     the way out. Idempotent, and read only while nothing is hovered. */
  if (hot) lastHot.current = hot
  const tipShown = hot ?? lastHot.current

  /* Which row is active stays in React, because it changes once per row and not
     once per mousemove. The opacity does not: React would reassert whatever
     value it owned on the next poll, which lands every ten seconds and would
     snap a fade back to its start. So the render pass writes the resting
     opacity once and every move after that is written to the DOM. */
  useLayoutEffect(() => {
    if (view !== 'chart' || width <= 0) {
      /* The rows these targets describe are gone. Keeping them would tell the
         next run that a freshly mounted row is already dimmed. */
      for (const a of rowAnims.current.values()) a.stop()
      rowAnims.current.clear()
      rowTargets.current.clear()
      return
    }
    const still = reducedMotion()
    /* Coming back to rest is the one move allowed to take its time, so leaving
       the chart settles instead of flicking back. Every row uses the same
       duration and no delay, which is what makes it one motion and not twelve. */
    const duration = active === null ? DURATION : QUICK
    for (const d of shown) {
      const el = groups.current.get(d.key)
      if (!el) continue
      const to = active === null ? REST : active === d.key ? 1 : DIM
      /* The same target means the row is already there or already on its way.
         Skipping is what keeps a poll from restarting a dim under the pointer. */
      if (rowTargets.current.get(d.key) === to) continue
      rowTargets.current.set(d.key, to)
      rowAnims.current.get(d.key)?.stop()
      rowAnims.current.delete(d.key)
      if (still) {
        el.style.opacity = String(to)
        continue
      }
      rowAnims.current.set(
        d.key,
        animate(el, { opacity: to }, tween(duration)),
      )
    }
    for (const key of [...rowTargets.current.keys()]) {
      if (groups.current.has(key)) continue
      rowAnims.current.get(key)?.stop()
      rowAnims.current.delete(key)
      rowTargets.current.delete(key)
    }
  }, [active, shown, view, width])

  /* Motion owns the tooltip's transform and opacity outright. The size is read
     here rather than kept in state because a measure-then-reposition round trip
     would start the slide from the pre-measurement guess. */
  useLayoutEffect(() => {
    const el = tipRef.current
    if (!el) {
      tipUp.current = false
      tipAt.current = null
      return
    }
    const still = reducedMotion()
    if (!hot) {
      if (!tipUp.current) return
      tipUp.current = false
      tipAt.current = null
      tipAnim.current?.stop()
      if (still) el.style.opacity = '0'
      else tipAnim.current = animate(el, { opacity: 0 }, tween(FADE))
      return
    }
    const box = el.getBoundingClientRect()
    const slot = band(hot.key) ?? 0
    const anchor = gutter + x(hot.count)
    /* Flip to the other side of the bar tip rather than sliding along the edge,
       so a bar that fills the plot does not get its own tooltip laid over it. */
    let left = anchor + 8
    if (left + box.width > width - 4) left = anchor - 8 - box.width
    const px = Math.max(4, Math.min(left, Math.max(4, width - box.width - 4)))
    const py = Math.max(
      0,
      Math.min(slot + ROW / 2 - box.height / 2, Math.max(0, height - box.height)),
    )
    const at = tipAt.current
    const sameRow = at !== null && at.key === hot.key
    if (sameRow && Math.abs(at.x - px) < 0.5 && Math.abs(at.y - py) < 0.5) return
    const wasUp = tipUp.current
    tipAt.current = { key: hot.key, x: px, y: py }
    tipUp.current = true
    tipAnim.current?.stop()
    if (still) {
      el.style.transform = `translate(${px}px, ${py}px)`
      el.style.opacity = '1'
      return
    }
    if (wasUp) {
      tipAnim.current = animate(el, { x: px, y: py }, tween(QUICK))
      return
    }
    /* Arriving: land it before it is visible, so it fades in where it belongs
       instead of flying in from the last row that was hovered. Duration 0 is a
       write through motion, which stays the single owner of the transform. */
    animate(el, { x: px, y: py }, { duration: 0 })
    tipAnim.current = animate(el, { opacity: 1 }, tween(FADE))
  }, [hot, band, x, gutter, width, height, view])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Call types</CardTitle>
        {!empty && (
          <CardDescription>
            {plural(rows.length, 'type', 'types')}, {plural(total, 'call', 'calls')}
          </CardDescription>
        )}
        {!empty && (
          <CardAction>
            <Button
              variant="outline"
              size="sm"
              aria-pressed={view === 'table'}
              onClick={() => {
                /* Swapping the chart out is a pointer leaving it, and the rows
                   have to be told so, since no pointerleave fires on unmount. */
                setActive(null)
                setView((v) => (v === 'table' ? 'chart' : 'table'))
              }}
            >
              Table
            </Button>
          </CardAction>
        )}
      </CardHeader>

      <CardContent className="flex flex-col gap-3">
        {empty ? (
          <p className="text-sm text-muted-foreground">
            {logged ? (
              'No calls opened in this window.'
            ) : (
              'Past calls are not being kept, so there is nothing yet to count.'
            )}
          </p>
        ) : view === 'table' ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Type</TableHead>
                <TableHead scope="col">Family</TableHead>
                <TableHead scope="col" className="text-right">
                  Calls
                </TableHead>
                <TableHead scope="col" className="text-right">
                  Share
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((d) => (
                <TableRow key={d.key}>
                  <TableCell className="whitespace-normal">{d.label}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {FAMILY_LABEL[d.family]}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {d.count}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {share(d.count, total).text}%
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div ref={wrap} className="relative w-full">
            <span
              ref={sansProbe}
              aria-hidden="true"
              className="pointer-events-none absolute top-0 left-0 text-xs opacity-0"
            >
              0
            </span>
            <span
              ref={monoProbe}
              aria-hidden="true"
              className="pointer-events-none absolute top-0 left-0 font-mono text-xs tabular-nums opacity-0"
            >
              0
            </span>

            <svg
              width={width}
              height={height}
              role="group"
              aria-label="Calls by type"
              className="block"
            >
              {width > 0 && (
                <>
                  {/* One hairline at zero. Every bar starts here, so it is the
                      only rule the eye needs. */}
                  <line
                    x1={gutter}
                    x2={gutter}
                    y1={0}
                    y2={height}
                    stroke="var(--border)"
                    strokeWidth={1}
                    shapeRendering="crispEdges"
                  />
                  {shown.map((d, i) => {
                    const slot = band(d.key) ?? 0
                    const mid = slot + band.bandwidth() / 2
                    const end = gutter + x(d.count)
                    const value = String(d.count)
                    const outside = end + TIP_GAP + textWidth(value, fonts.value) <= width - 2
                    const label = labels.get(d.key) ?? d.label
                    const { spoken } = share(d.count, total)
                    return (
                      <g
                        key={d.key}
                        ref={(el) => {
                          if (el) groups.current.set(d.key, el)
                          else groups.current.delete(d.key)
                        }}
                        className="group/row outline-none"
                        tabIndex={0}
                        role="img"
                        aria-label={`${d.label}, ${plural(d.count, 'call', 'calls')}, ${spoken}, ${
                          d.tail ? 'folded rows' : FAMILY_LABEL[d.family]
                        }`}
                        style={{ opacity: REST }}
                        onPointerEnter={() => setActive(d.key)}
                        onPointerLeave={() => setActive((a) => (a === d.key ? null : a))}
                        onFocus={() => setActive(d.key)}
                        onBlur={() => setActive((a) => (a === d.key ? null : a))}
                        onKeyDown={(e) => {
                          if (e.key === 'ArrowDown' && focusRow(i + 1)) e.preventDefault()
                          else if (e.key === 'ArrowUp' && focusRow(i - 1)) e.preventDefault()
                          else if (e.key === 'Escape') setActive(null)
                        }}
                      >
                        <rect
                          x={0}
                          y={slot}
                          width={width}
                          height={band.bandwidth()}
                          fill="transparent"
                        />
                        <rect
                          x={0.5}
                          y={slot + 0.5}
                          width={Math.max(0, width - 1)}
                          height={Math.max(0, band.bandwidth() - 1)}
                          rx={3}
                          fill="none"
                          stroke="var(--ring)"
                          strokeWidth={2}
                          className="opacity-0 group-focus-visible/row:opacity-100"
                        />
                        <text
                          x={gutter - GAP}
                          y={mid}
                          textAnchor="end"
                          dominantBaseline="middle"
                          className="fill-foreground text-xs"
                        >
                          {label}
                          {label !== d.label && <title>{d.label}</title>}
                        </text>
                        <path
                          ref={(el) => {
                            if (el) paths.current.set(d.key, el)
                            else paths.current.delete(d.key)
                          }}
                          fill={MARK[d.family]}
                        />
                        {/* The ink steps rather than tweens: the tokens are
                            var() colours, which no tweener can interpolate, and
                            it lands inside the row's own 0.14s lift. A label
                            sitting inside its bar keeps its contrast ink, which
                            is a legibility choice and not an emphasis one. */}
                        <text
                          x={outside ? end + TIP_GAP : Math.max(gutter, end - TIP_GAP)}
                          y={mid}
                          textAnchor={outside ? 'start' : 'end'}
                          dominantBaseline="middle"
                          className={
                            !outside
                              ? 'font-mono text-xs tabular-nums'
                              : active === d.key
                                ? 'fill-foreground font-mono text-xs tabular-nums'
                                : 'fill-muted-foreground font-mono text-xs tabular-nums'
                          }
                          fill={outside ? undefined : INSIDE_INK[d.family]}
                        >
                          {value}
                        </text>
                      </g>
                    )
                  })}
                </>
              )}
            </svg>

            {/* Mounted whether or not anything is hovered, because a tooltip
                that unmounts cannot fade out, and one that remounts jumps.
                Duplicates the row's own aria-label, so it is hidden rather than
                read a second time. */}
            <div
              ref={tipRef}
              aria-hidden="true"
              style={{ opacity: 0 }}
              className="pointer-events-none absolute top-0 left-0 z-10 max-w-56 rounded-md border bg-popover px-2 py-1.5 text-xs shadow-md"
            >
              {tipShown && (
                <>
                  <div className="font-medium text-popover-foreground">{tipShown.label}</div>
                  <div className="mt-0.5 flex items-center gap-2 text-muted-foreground">
                    <span className="font-mono tabular-nums">
                      {plural(tipShown.count, 'call', 'calls')}
                    </span>
                    <span className="font-mono tabular-nums">
                      {share(tipShown.count, total).text}%
                    </span>
                    <span>
                      {tipShown.tail ? 'folded rows' : FAMILY_LABEL[tipShown.family]}
                    </span>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {legend.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {legend.map((f) => (
                <li key={f} className="flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className="size-2 shrink-0 rounded-sm"
                    style={{ background: MARK[f] }}
                  />
                  <span>{FAMILY_LABEL[f]}</span>
                  <span className="font-mono tabular-nums">{totals[f]}</span>
                </li>
              ))}
            </ul>
            {view === 'chart' && rows.length > TOP_N && (
              <Button variant="ghost" size="sm" onClick={() => setExpanded((v) => !v)}>
                {expanded ? `Show top ${TOP_N}` : `Show all ${rows.length} types`}
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
