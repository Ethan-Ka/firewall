/* The tape, as a column of lines you can hear.
 *
 * Every transmission the server still holds, in the order it was said, whether
 * or not a call claimed it. That is the whole point of the feed: a transmission
 * is on this screen because it was heard, not because something downstream
 * managed to classify it. So nothing here filters, and nothing here is dropped
 * for being unclaimed, short, or wordless.
 *
 * The audio engine below is a port of display.html's, decision for decision,
 * not a fresh attempt at the same problem. That one is the product of real
 * failures on a wall screen -- a keyup timed against the record it was cut
 * from, a browser that never let the page make a sound, an hour of backlog
 * dumped into a room after a reconnect -- and the two screens hang two feet
 * apart, so they must behave the same way when the radio does the same thing.
 *
 * Nothing here re-fetches audio from the source. The clip is the one the poller
 * already downloaded, handed over from the server's memory. On Broadcastify
 * that matters: reads are billed, and replaying a call by asking for it again
 * would pay for it twice.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Pause, Play } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { api, clock, dayOf, hhmmss, now, rangeOf, reducedMotion, signInHref, type Row } from '@/lib/firewall'

/* Pixels of slack in the at-the-bottom test. Never an equality: a fractional
 * scrollHeight, which is what a 13px line box and a device pixel ratio produce
 * between them, leaves a permanent sub-pixel gap and would pin Newest on for
 * the rest of the night. */
const BOTTOM_SLACK = 24

/* Following, the queue is a hand-off between one clip and the next and must
 * never become a backlog to dump on the room. Replaying or paused, the queue IS
 * the backlog, and throwing it away is exactly the bug: "do not stack up an
 * hour of radio" is a rule about a screen left alone, not about one where
 * somebody just deliberately pressed pause and is standing there listening. */
const QUEUE_FOLLOW = 4
const QUEUE_HELD = 24

/* Only what was said in the last few minutes goes in the queue. Reconnecting
 * after an outage must not replay an hour of radio at whoever is in the room. */
const RECENT = 180

/* mousemove is in the gesture list and no browser counts it as user
 * activation, so without a throttle walking a mouse across the screen would
 * fire a rejected play() sixty times a second forever. */
const RETRY_MS = 1500

/* Any gesture at all is permission, not just the space bar: this is a screen in
 * a hallway, and the person who walks up and touches it should get sound
 * without first being told there is a keyboard shortcut. */
const GESTURES = ['pointerdown', 'click', 'keydown', 'touchstart', 'mousemove'] as const

/* Shared with display.html on purpose. Two screens on one wall, one origin, and
 * a person who silenced one of them meant the radio, not that pane. */
const AUDIO_PREF = 'firewall.audio'

const readPref = (key: string, fallback: boolean) => {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch {
    /* Private mode, or storage turned off. A preference that cannot be read is
       not a reason to render nothing. */
    return fallback
  }
}

/* State the engine has to read back, kept as a ref beside its state. The media
 * listeners are attached once and would otherwise be reading whatever render
 * last closed over them, which is how a pause from four seconds ago comes to
 * outrank the clip playing now. */
function useFlag<T>(init: T | (() => T)) {
  const [v, setV] = useState<T>(init)
  const ref = useRef<T>(v)
  const set = useCallback((next: T) => {
    ref.current = next
    setV(next)
  }, [])
  return { v, ref, set }
}

type Item =
  | { kind: 'day'; key: string; label: string }
  | { kind: 'row'; key: string; row: Row }

export function Transcript({ feed, ok, error, live, speech = true }: {
  feed: Row[]
  ok: boolean
  error: string | null
  live: boolean
  /* False when transcripts are behind a login and this browser is not signed
     in. The rows still arrive and still play; only the words are missing, and
     a locked row must not be dressed up as a silent one -- "(no speech)" over
     a transmission that had plenty of it is the one wrong thing this pane
     could say. */
  speech?: boolean
}) {
  const still = reducedMotion()

  const items = useMemo<Item[]>(() => {
    /* Headings only once the tape actually spans a boundary. A single date
       repeated over one block states nothing the clock on every row does not
       already say, and costs a line of a rail this narrow. */
    const split = new Set(feed.map((r) => dayOf(r.ts))).size > 1
    const out: Item[] = []
    let day: string | null = null
    for (const row of feed) {
      const d = dayOf(row.ts)
      if (split && d !== day) out.push({ kind: 'day', key: `day:${d}`, label: d })
      day = d
      out.push({ kind: 'row', key: row.id, row })
    }
    return out
  }, [feed])

  /* Ids already painted. The poll hands back the same rows every couple of
     seconds with a new array identity, and without this the arrival animation
     replays down the entire column each time, which reads as the radio saying
     everything again. Audio ticks four times a second on top of that, so this
     is also what keeps the readout from re-animating the list. */
  const seen = useRef<Set<string>>(new Set())
  const fresh = (id: string) => !seen.current.has(id)
  useEffect(() => {
    /* Rebuilt, not grown: the tape retires rows by age, and a set that only
       ever gains ids is a leak on a display that runs for weeks. */
    seen.current = new Set(feed.map((r) => r.id))
  }, [feed])

  /* ------------------------------------------------------------ scrolling */

  /* Radix owns the element that actually scrolls, and the shadcn wrapper does
     not hand it out, so it is found from the root once on mount. */
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null)
  const mount = useCallback((node: HTMLDivElement | null) => {
    setViewport(node?.querySelector<HTMLDivElement>('[data-slot="scroll-area-viewport"]') ?? null)
  }, [])

  const [atBottom, setAtBottom] = useState(true)
  /* The same fact as a ref, because the effect that pins on arrival must read
     it at arrival time and not through whatever closure last rendered. */
  const stick = useRef(true)

  useEffect(() => {
    if (!viewport) return
    const read = () => {
      const near =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= BOTTOM_SLACK
      stick.current = near
      setAtBottom(near)
    }
    read()
    viewport.addEventListener('scroll', read, { passive: true })
    return () => viewport.removeEventListener('scroll', read)
  }, [viewport])

  const newest = feed.length ? feed[feed.length - 1].id : ''
  useLayoutEffect(() => {
    /* Follow the tape only for a reader who is already at the end of it.
       Anyone who has scrolled up is reading a line, and dragging them off it
       to show a row they did not ask for is the failure this exists to avoid;
       they get Newest instead. The playing row never moves the list either,
       for the same reason: audio follows the radio, reading does not. */
    if (viewport && stick.current) viewport.scrollTop = viewport.scrollHeight
  }, [viewport, newest, feed.length])

  const toNewest = () => {
    if (!viewport) return
    stick.current = true
    setAtBottom(true)
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: still ? 'auto' : 'smooth' })
  }

  /* ---------------------------------------------------------------- audio */

  /* One element for the life of the panel, made here rather than rendered.
     App.tsx repaints this panel every second and repolls every two, and an
     <audio> that lives in the tree is an element some future conditional can
     unmount mid-word. Nothing in the tree owns this one, so nothing can take
     it away. */
  const auRef = useRef<HTMLAudioElement | null>(null)
  const audio = () => {
    if (!auRef.current) {
      const a = new Audio()
      /* Nothing is fetched until something is played: a rail holding an hour
         of rows must not turn a scroll into a stream of megabytes. */
      a.preload = 'none'
      auRef.current = a
    }
    return auRef.current
  }

  /* Two states, and this panel used to have one. FOLLOWING, arrivals play as
     they come in. REPLAYING, you clicked an earlier line, arrivals stop
     playing themselves and stack up counted in the bar, and the clip you are
     on gets to finish before the radio takes the room back. */
  const follow = useFlag(true)
  const paused = useFlag(false)          /* explicit, and it outranks arrivals */
  const blocked = useFlag(false)         /* the browser has let out no sound yet */
  const gone = useFlag(false)            /* the clip rolled off the server */
  const sel = useFlag<string | null>(null)
  const audioOn = useFlag(() => readPref(AUDIO_PREF, true))

  /* Not named `live`: that is the poller's word for "the API answered", and
     the two have nothing to do with each other. */
  const [sounding, setSounding] = useState(false)
  const [waiting, setWaiting] = useState(0)
  const [readout, setReadout] = useState({ pos: 0, len: NaN })

  const queue = useRef<string[]>([])
  const known = useRef<Set<string>>(new Set())
  const primed = useRef(false)
  const pending = useRef<string | null>(null)   /* the clip the policy refused */
  const retrying = useRef(false)
  const lastTry = useRef(0)
  const armed = useRef(false)
  const disarm = useRef<() => void>(() => {})
  const feedRef = useRef<Row[]>(feed)

  /* Which part of the loaded record the row being played actually is.
     A Broadcastify record is one trunked channel grant and a grant can hold
     more than one keyup, so the server splits them: one row per keyup, every
     row pointing at the same untouched record with its own range on the url.
     Null means the row IS the record, the common case, and every readout below
     then falls back to the whole element. */
  const cue = useRef<{ start: number; end: number } | null>(null)
  const cueStart = () => (cue.current ? cue.current.start : 0)
  const cueEnd = () => (cue.current ? cue.current.end : auRef.current?.duration ?? NaN)
  /* The keyup's own length, not the record's. "Clear, thank you" is 1.6
     seconds of a 4.8-second grant: timing the recording made it read 0:03 of
     0:04 the instant it started. On a fragment both numbers came off the wire,
     so this is right before any metadata has loaded. */
  const cueLen = () => cueEnd() - cueStart()
  /* Elapsed within the keyup. A fragment row's currentTime starts at
     play_start rather than at zero, and every position was offset by it. */
  const cuePos = () => {
    const t = auRef.current?.currentTime ?? 0
    return Math.max(0, Math.min(cueLen(), t - cueStart()))
  }
  /* Whether any of THIS transmission is left. Deliberately not currentTime <
     duration: on a fragment that stays true for the rest of the record, and
     resuming on it plays the next speaker's keyup. */
  const cueLeft = () => cueEnd() - (auRef.current?.currentTime ?? 0) > 0.05

  const findRow = (id: string | null) =>
    (id ? feedRef.current.find((r) => r.id === id) ?? null : null)

  const tick = () => setReadout({ pos: cuePos(), len: cueLen() })

  /* The one place a clip is handed to the element, so the range and the source
     can never drift apart. Two rows off one grant are two different strings,
     same query and a different #t=, so the element reloads and seeks and the
     browser answers the second out of the blob it already holds: it strips the
     fragment, the server sees the same /api/clip?id= and replies 206. */
  function loadRow(r: Row) {
    cue.current = rangeOf(r)
    /* The payload names the clip as a path on the server, which is this page's
       own origin only when the two are served together. api() puts it back on
       whichever machine is holding the audio. */
    audio().src = api(r.url as string)
  }

  /* `replaying` is what separates a click on an old line from the bar playing
     the next arrival itself. Only the first one drops you into the past. */
  function play(id: string, replaying: boolean) {
    const r = findRow(id)
    if (!r?.url) return
    if (replaying) follow.set(false)
    sel.set(id)
    gone.set(false)
    paused.set(false)
    pending.current = id
    loadRow(r)
    audio().play()
      .then(() => { blocked.set(false); pending.current = null; tick() })
      .catch(nope)
    tick()
  }

  /* Two different failures wearing the same rejected promise. NotAllowedError
     is the autoplay policy, which is not the clip's fault and not permanent:
     the clip goes back on the front of the queue so the next gesture, or the
     next transmission, plays it rather than losing it. Anything else means the
     audio itself did not load, which no amount of retrying fixes. */
  function nope(err: unknown) {
    if ((err as { name?: string } | null)?.name === 'NotAllowedError') {
      blocked.set(true)
      const p = pending.current
      if (p && queue.current[0] !== p) {
        queue.current.unshift(p)
        setWaiting(queue.current.length)
      }
      armGesture()
    } else gone.set(true)
  }

  /* One-shot listeners, re-armed by nope() for as long as the policy refuses. */
  function armGesture() {
    if (armed.current) return
    armed.current = true
    const take = () => {
      armed.current = false
      for (const ev of GESTURES) window.removeEventListener(ev, take, true)
      if (blocked.ref.current) retryBlocked()
    }
    disarm.current = take
    for (const ev of GESTURES) window.addEventListener(ev, take, { capture: true, once: true })
  }

  /* Retry the clip the policy refused. Guarded twice. Once because a single tap
     arrives as both pointerdown and click, and calling play() twice on one
     element rejects the first promise with AbortError, which nope() would file
     as a dead clip and say so, which is a lie. Once on time, for mousemove. */
  function retryBlocked() {
    if (!blocked.ref.current || retrying.current) return
    if (Date.now() - lastTry.current < RETRY_MS) { armGesture(); return }
    lastTry.current = Date.now()
    const id = pending.current ?? queue.current[0]
      ?? [...feedRef.current].reverse().find((r) => r.url)?.id ?? null
    const r = findRow(id)
    if (!r?.url) return
    retrying.current = true
    sel.set(r.id)
    loadRow(r)
    audio().play()
      .then(() => {
        blocked.set(false)
        pending.current = null
        retrying.current = false
        if (queue.current[0] === r.id) {
          queue.current.shift()
          setWaiting(queue.current.length)
        }
        tick()
      })
      .catch((err) => { retrying.current = false; nope(err) })
  }

  /* One transmission at a time, in the order it was said: a call is a
     conversation, and two of it at once is noise. */
  function pump() {
    /* The two things that outrank an arrival: somebody is listening to the
       past, and somebody pressed pause. Neither is a state the radio may talk
       over. */
    if (!follow.ref.current || paused.ref.current) return
    const a = auRef.current
    if (a && !a.paused && !a.ended && a.currentSrc) return
    /* Skip rather than stall: a queued clip can have rolled off the server
       while it waited, and the arrivals behind it are still worth hearing. */
    while (queue.current.length) {
      const id = queue.current.shift() as string
      setWaiting(queue.current.length)
      const r = findRow(id)
      if (r?.url) return play(id, false)
    }
  }

  /* A transmission ending, however it ended: the element ran out of record, or
     we stopped it at the end of its keyup. A replay ends by rejoining the
     radio, because nobody wants to be left in the past by a clip finishing,
     and this is what makes listening as they come in resume on its own. */
  function finish() {
    follow.set(true)
    setSounding(false)
    tick()
    pump()
  }

  function playPause() {
    /* Pressing play on a blocked page means "yes, make noise", which is the
       same request as any other gesture and goes down the same path. */
    if (blocked.ref.current) { retryBlocked(); return }
    const a = audio()
    if (!a.paused && !a.ended && a.currentSrc) {
      /* Deliberate, and it outranks everything arriving afterwards: pump()
         must never take the room back from somebody who asked for quiet. */
      paused.set(true)
      a.pause()
      return
    }
    /* Is there more of this transmission, not is there more of this file: a
       fragment row stopped at the end of its keyup still has most of the
       record ahead of it, and carrying on into that is playing the next
       speaker on demand. Spent, it falls through to the newest clip. */
    if (a.currentSrc && cueLeft()) {
      paused.set(false)
      a.play().then(() => { blocked.set(false); tick() }).catch(nope)
      return
    }
    const r = [...feedRef.current].reverse().find((x) => x.url)
    if (r) play(r.id, false)
  }

  /* Back to the present, from either way of having left it: replaying an
     earlier line, and a deliberate pause. Both are states the radio is not
     allowed to talk over, so both need the one control that hands the room
     back. Without that, pause is a dead end -- the only way out of it is to
     play whatever you stopped on and wait for it to end, which is not a thing
     to ask of somebody who paused because they wanted the room quiet.
     Whatever stacked up meanwhile plays in the order it was said, oldest
     first, because that is the conversation. */
  function goLive() {
    if (follow.ref.current && !paused.ref.current) return
    follow.set(true)
    paused.set(false)
    audio().pause()   /* the point of the control is to leave the past now */
    if (blocked.ref.current) retryBlocked(); else pump()
  }

  function toggleAudio(on: boolean) {
    audioOn.set(on)
    try { localStorage.setItem(AUDIO_PREF, on ? '1' : '0') } catch { /* storage off */ }
    /* Off means off now, not after this transmission. Only arrivals are
       governed: a row somebody clicks still plays, because that click is a
       person asking for this one clip rather than for the radio. */
    if (!on) {
      queue.current = []
      setWaiting(0)
      pending.current = null
      audio().pause()
    }
    /* Turning it back on is itself the gesture the policy has been waiting
       for, so take it rather than making them go and find another. */
    if (on && blocked.ref.current) retryBlocked()
  }

  function seek(v: number) {
    const len = cueLen()
    if (!Number.isFinite(len) || !len) return
    /* Within the keyup, not within the record: halfway along this control is
       halfway through the thing you can hear, not halfway through a recording
       most of which belongs to somebody else. */
    audio().currentTime = cueStart() + Math.min(len, Math.max(0, v))
    tick()
  }

  /* Attached once, and every one of these handlers reads its state from refs
     rather than from the render that installed them. */
  useEffect(() => {
    const a = audio()
    const onTime = () => {
      /* The end of a keyup is enforced here rather than left to the media
         fragment. Browsers honour the start of a #t= range and cannot be
         relied on to stop at its end, and an overrun is not cosmetic: it is
         the next speaker played on top of this one, which is precisely the
         "two transmissions grouped as one" this mechanism exists to undo.
         Only while actually playing, because a seek fires timeupdate too. */
      const c = cue.current
      if (c && !a.paused && a.currentTime >= c.end) {
        a.pause()
        /* Park it on the boundary so the readout reads the keyup's length
           rather than a fifth of a second more than it. */
        if (a.currentTime > c.end) a.currentTime = c.end
        finish()
        return
      }
      tick()
    }
    const onMeta = () => {
      /* The url carries #t=play_start,play_end and the browser should already
         have seeked there, but doing it again costs one seek inside a file it
         has just cached and means the row plays its own keyup even on an
         engine that ignored the fragment: the difference between hearing the
         answer and hearing the question a second time. */
      const c = cue.current
      if (c && Math.abs(a.currentTime - c.start) > 0.05) a.currentTime = c.start
      tick()
    }
    const onPlay = () => { setSounding(true); tick() }
    const onPause = () => { setSounding(false); tick() }
    const onErr = () => {
      /* A clip can age out of the server's memory while it is still listed.
         Say so rather than sitting there apparently playing nothing, and while
         replaying stop there: rolling straight on to the queued arrivals would
         wipe the message off the bar before it had been read. */
      gone.set(true)
      setSounding(false)
      if (follow.ref.current) pump()
    }
    a.addEventListener('timeupdate', onTime)
    a.addEventListener('loadedmetadata', onMeta)
    a.addEventListener('play', onPlay)
    a.addEventListener('pause', onPause)
    a.addEventListener('ended', finish)
    a.addEventListener('error', onErr)
    return () => {
      a.pause()
      a.removeEventListener('timeupdate', onTime)
      a.removeEventListener('loadedmetadata', onMeta)
      a.removeEventListener('play', onPlay)
      a.removeEventListener('pause', onPause)
      a.removeEventListener('ended', finish)
      a.removeEventListener('error', onErr)
      disarm.current()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* Arrivals. The feed is in the order it was said, so anything new in it is
     in that order too, which is the order it goes into the queue. */
  useEffect(() => {
    feedRef.current = feed
    const rows = feed.filter((r) => r.url)
    const arrived = rows.filter((r) => !known.current.has(r.id))
    for (const r of rows) known.current.add(r.id)
    /* Ids only ever move forward, so a row off the tape can never come back.
       Pruned rather than remembering every transmission of the month. */
    if (known.current.size > 400) known.current = new Set(rows.map((r) => r.id))
    /* The first payload is history, not news: a browser opened mid-shift must
       not announce itself by playing the backlog into the room. */
    if (!primed.current) { primed.current = true; return }
    if (!audioOn.ref.current) return
    const at = now()
    for (const r of arrived) if (at - r.ts < RECENT) queue.current.push(r.id)
    const cap = follow.ref.current && !paused.ref.current ? QUEUE_FOLLOW : QUEUE_HELD
    queue.current = queue.current.slice(-cap)
    setWaiting(queue.current.length)
    /* An arrival while blocked is another chance at the policy rather than a
       reason to stop trying: a kiosk profile that played sound yesterday
       starts today already allowed, and giving up on the first refusal is how
       a screen ends up silent for the rest of the day. */
    if (blocked.ref.current) retryBlocked(); else pump()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed])

  /* ---------------------------------------------------------------- paint */

  const held = feed.length === 1 ? '1 transmission held' : `${feed.length} transmissions held`
  const playable = feed.some((r) => r.url)
  const current = sel.v ? feed.find((r) => r.id === sel.v) ?? null : null

  /* Two rows off one grant are two transmissions and are never drawn as one:
     that is the entire point of the split. But while one is playing it is
     worth saying where in the recording you are, because clicking the second
     of two lines a second apart is exactly when a listener wonders whether
     they are hearing the same clip twice. Said in the bar, which is about the
     clip, never in the list, which is about the transmissions. */
  const part = (() => {
    if (!current || !rangeOf(current) || current.clip == null) return ''
    const kin = feed.filter((r) => r.clip === current.clip)
    const at = kin.findIndex((r) => r.id === current.id)
    return kin.length > 1 && at >= 0 ? ` ${at + 1} of ${kin.length} in one recording.` : ''
  })()

  /* Every one of these says what to do about it rather than reporting that
     something is off. A screen that goes quiet and never explains itself reads
     as broken, and somebody unplugs it. */
  const note =
    blocked.v
      ? 'Sound is on, but this browser has not allowed it yet. Touch the screen or press any key and it starts. To keep it: padlock, then Site settings, then Sound, then Allow.'
    : gone.v ? 'That clip is no longer held.'
    : !playable
      ? 'No audio kept for these transmissions. A mock dispatch, and a source running without an archive, carry none.'
    : !follow.v
      ? (waiting ? `Replaying.${part} ${waiting} waiting.` : `Replaying an earlier transmission.${part}`)
    : paused.v && auRef.current?.currentSrc
      ? (waiting ? `Paused. ${waiting} waiting. Play carries on; Live rejoins the radio.`
                 : 'Paused. Play carries on; Live rejoins the radio.')
    : !audioOn.v ? 'Arrivals stay quiet. A row still plays when you click it.'
    : current ? (current.dispatch ? 'Dispatch.' : 'Radio traffic.') + part
    : 'Plays as it arrives.'

  const len = Number.isFinite(readout.len) && readout.len > 0 ? readout.len : 0
  const pos = Math.min(len, Number.isFinite(readout.pos) ? readout.pos : 0)

  return (
    <Card className="h-full min-h-0">
      <CardHeader>
        <CardTitle>Radio traffic</CardTitle>
        {feed.length > 0 && (
          <CardDescription>
            {held}
            {!speech && (
              <>
                {' · '}
                <a
                  href={signInHref()}
                  className="text-[var(--ink-ems)] underline-offset-2 hover:underline"
                >
                  sign in to read them
                </a>
              </>
            )}
          </CardDescription>
        )}
      </CardHeader>

      {!live && (
        /* The rows stay up, dimmed. The last thing heard is still true; it is
           only the following of it that stopped. */
        <div className="px-(--card-spacing) text-sm text-muted-foreground">
          Cannot reach the server.{feed.length > 0 ? ' These are the last rows it sent.' : ''}
        </div>
      )}

      {!ok && (
        <div className="mx-(--card-spacing) rounded-md bg-destructive/10 px-2.5 py-2 ring-1 ring-destructive/40">
          <div className="text-xs font-medium text-destructive">Source error</div>
          {/* Verbatim, line breaks and all. The server writes these to be acted
              on -- a key, a talkgroup, a quota -- and a tidied paraphrase costs
              the reader the one thing that would fix it. */}
          <div className="mt-1 font-mono text-[13px] leading-snug break-words whitespace-pre-wrap text-foreground">
            {error ?? '(no message)'}
          </div>
        </div>
      )}

      {feed.length > 0 ? (
        <>
          <CardContent className="relative min-h-0 flex-1 px-0">
            <ScrollArea
              ref={mount}
              /* The viewport's content box is display:table, which both kills
                 the sticky day heading and lets one long unbroken word widen a
                 22rem rail into a horizontal scrollbar. */
              className="h-full [&>[data-slot=scroll-area-viewport]>div]:block!"
            >
              <ol
                role="log"
                aria-label="Radio transcript"
                className={cn('flex flex-col pb-1', !live && 'opacity-60')}
              >
                {/* initial={false} so the first paint sets the backlog down as
                    it is, rather than flying in an hour of radio row by row. */}
                <AnimatePresence initial={false}>
                  {items.map((item) =>
                    item.kind === 'day' ? (
                      <li
                        key={item.key}
                        className="sticky top-0 z-10 bg-card px-(--card-spacing) py-1 text-xs font-medium text-muted-foreground"
                      >
                        {item.label}
                      </li>
                    ) : (
                      <motion.li
                        key={item.key}
                        aria-current={item.row.id === sel.v ? 'true' : undefined}
                        initial={still || !fresh(item.key) ? false : { opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: still ? 0 : 0.18, ease: [0.2, 0, 0, 1] }}
                      >
                        {/* A real button, so a keyboard and a screen reader get
                            the row for free. Disabled when the row has no clip:
                            a mock dispatch and a source with no archive keep no
                            audio, and the bar below says so in words. */}
                        <button
                          type="button"
                          disabled={!item.row.url}
                          onClick={() => play(item.row.id, true)}
                          title={item.row.url ? undefined : 'No audio kept for this transmission'}
                          className="flex w-full gap-2 rounded-sm px-(--card-spacing) py-0.5 text-left hover:bg-muted/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring disabled:hover:bg-transparent"
                        >
                          <span className="flex shrink-0 items-baseline font-mono text-[13px] leading-5 tabular-nums text-muted-foreground">
                            {/* Both glyph columns are held on every row, so the
                                timestamps do not step sideways the moment one
                                appears, and each distinction survives a
                                colourblind reader or a sun-washed wall. */}
                            <span aria-hidden className="w-2.5 text-center text-[var(--ink-ems)]">
                              {item.row.id === sel.v ? '♪' : ''}
                            </span>
                            <span aria-hidden className="w-2.5 text-center">
                              {item.row.dispatch ? '▸' : ''}
                            </span>
                            {hhmmss(item.row.ts)}
                          </span>
                          <span
                            className={cn(
                              'min-w-0 flex-1 text-sm leading-5 break-words',
                              item.row.id === sel.v ? 'text-[var(--ink-ems)]'
                                : item.row.dispatch ? 'text-foreground'
                                : 'text-muted-foreground',
                            )}
                          >
                            {item.row.dispatch && <span className="sr-only">Dispatch. </span>}
                            {/* A transmission that carried no words is still a
                                transmission, and the row is the evidence the
                                radio keyed up. Skipping it would leave a gap in
                                the timeline that nothing on screen explains. */}
                            {item.row.text?.trim() || (
                              <span className="italic text-muted-foreground">
                                {speech ? '(no speech)' : 'locked'}
                              </span>
                            )}
                            {/* Marked, quietly. These words were typed by
                                somebody who listened to the clip, which makes
                                them better than the rest of the transcript and
                                different in kind from it -- a line nobody can
                                tell apart from a machine's guess is one nobody
                                can weigh. The recogniser's version is on the
                                title so it is still there to be checked. */}
                            {item.row.corrected && (
                              <span
                                className="ml-1 align-super text-[10px] text-muted-foreground"
                                title={
                                  item.row.machine
                                    ? `heard as: ${item.row.machine}`
                                    : 'corrected by ear'
                                }
                              >
                                &#9998;
                              </span>
                            )}
                          </span>
                        </button>
                      </motion.li>
                    ),
                  )}
                </AnimatePresence>
              </ol>
            </ScrollArea>

            <AnimatePresence initial={false}>
              {!atBottom && (
                <motion.div
                  initial={still ? false : { opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={still ? { opacity: 1 } : { opacity: 0, y: 4 }}
                  transition={{ duration: still ? 0 : 0.18, ease: [0.2, 0, 0, 1] }}
                  className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center"
                >
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={toNewest}
                    className="pointer-events-auto shadow-sm ring-1 ring-foreground/10"
                  >
                    Newest
                  </Button>
                </motion.div>
              )}
            </AnimatePresence>
          </CardContent>

          <div className="border-t border-border px-(--card-spacing) pt-3">
            <div className="flex items-center gap-2">
              <Button
                size="icon-sm"
                variant="secondary"
                onClick={playPause}
                disabled={!playable}
                aria-label={sounding ? 'Pause' : 'Play'}
              >
                {/* Real icons rather than the play and pause glyphs. Those two
                    characters are one variation selector away from rendering as
                    colour emoji, and which side of that line a font falls on is
                    decided by the machine this is hung on rather than by us. */}
                {sounding ? <Pause aria-hidden /> : <Play aria-hidden />}
              </Button>
              <input
                type="range"
                aria-label="Seek within this transmission"
                min={0}
                max={len || 1}
                step={0.05}
                value={pos}
                disabled={!len}
                onChange={(e) => seek(Number(e.target.value))}
                className="min-w-0 flex-1 cursor-pointer [accent-color:var(--ink-ems)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-default disabled:opacity-40"
              />
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                {clock(pos)} / {clock(readout.len)}
              </span>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
              {/* Only while you are away from it -- replaying, or paused --
                  because a control that returns you to where you already are is
                  one that has to be read first to be dismissed. Paused counts:
                  the tape is still running past you, and this is the way back
                  that does not go through the clip you stopped. */}
              {(!follow.v || paused.v) && (
                <Button size="xs" variant="outline" onClick={goLive}>
                  Live{waiting ? ` · ${waiting} waiting` : ''}
                </Button>
              )}
              <Button
                size="xs"
                variant={audioOn.v ? 'secondary' : 'ghost'}
                aria-pressed={audioOn.v}
                onClick={() => toggleAudio(!audioOn.v)}
                className={cn(!audioOn.v && 'text-muted-foreground')}
              >
                Sound {audioOn.v ? 'on' : 'off'}
              </Button>
            </div>

            <p
              className={cn('mt-1.5 pb-1 text-xs leading-snug',
                blocked.v || gone.v ? 'text-[var(--ink-hazard)]' : 'text-muted-foreground')}
            >
              {note}
            </p>
          </div>
        </>
      ) : live ? (
        /* Empty and healthy. No spinner and no skeleton anywhere in here: the
           feed arrives on a poll, and a placeholder that blinks every couple of
           seconds turns a quiet radio into a screen that looks broken. */
        <CardContent className="min-h-0 flex-1 text-sm text-muted-foreground">
          Nothing heard yet. A transmission on a watched talkgroup puts a row here.
        </CardContent>
      ) : null}
    </Card>
  )
}
