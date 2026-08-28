/* The pictures.
 *
 * These are photographs, and they are not this repository's photographs. Every
 * one is Chris Allen's, out of the Purdue and Purdue Airport galleries at
 * IndianaFireTrucks.com, which reserves all rights to them.
 *
 * So they are LOADED FROM HIS SERVER rather than copied into this one. That is
 * not a technical detail, it is the whole arrangement: nothing copyrighted
 * ends up in this repository or in the bundle Vercel serves, the watermark
 * arrives in the frame the way he cropped it, and every picture carries his
 * name and links back to the photograph's own page. An app that shipped its
 * own copies would be republishing eleven photographs it has no licence to,
 * and it would do it silently.
 *
 * The cost is honest and worth stating: these load over the network from
 * somebody else's host, so a display with no route out sees the fallback
 * below, and a URL he rotates is a picture that stops arriving. If that
 * matters more than the above -- or if you have your own photographs, or his
 * permission -- drop files into src/assets/units/ named for the unit and they
 * win, locally and offline. See the README in that directory.
 */

import { useState } from 'react'

import type { Apparatus as Rig } from '@/lib/purdue'
import { cn } from '@/lib/utils'

/* Every photograph anyone has dropped in, keyed by lowercase unit id. Eager so
   the lookup below is a plain object read rather than a promise per tile, and
   `query: '?url'` so what lands in the map is a src rather than a module. */
const LOCAL = import.meta.glob('../assets/units/*.{jpg,jpeg,png,webp,avif}', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>

const localFor = (id: string): string | null => {
  const want = id.toLowerCase()
  for (const [path, url] of Object.entries(LOCAL)) {
    const file = path.slice(path.lastIndexOf('/') + 1)
    if (file.slice(0, file.lastIndexOf('.')).toLowerCase() === want) return url
  }
  return null
}

/** One rig's photograph.
 *
 *  `alt` describes the rig rather than naming the file, because that is what
 *  somebody who cannot see it is missing. The credit is NOT in the alt text:
 *  it is drawn on the tile and repeated in the detail panel, where it is
 *  reachable rather than buried in an attribute.
 */
export function Apparatus({
  rig,
  className,
  priority = false,
}: {
  rig: Rig
  className?: string
  /** The first few tiles are above the fold and should not wait for the
   *  scroller to reach them. Everything below stays lazy. */
  priority?: boolean
}) {
  const [failed, setFailed] = useState(false)
  const src = localFor(rig.id) ?? rig.photo.src

  /* A tile that says why it is empty. The alternative was a broken-image glyph
     from the browser, which tells somebody standing in a hallway nothing about
     whether the radio is still working. */
  if (failed) {
    return (
      <div
        className={cn(
          'flex aspect-[3/2] w-full items-center justify-center rounded-sm bg-muted/50 px-2 text-center',
          className,
        )}
      >
        <span className="font-mono text-[10px] leading-tight text-muted-foreground">
          photo
          <br />
          offline
        </span>
      </div>
    )
  }

  return (
    <img
      src={src}
      alt={`${rig.name}, a ${rig.rig}`}
      loading={priority ? 'eager' : 'lazy'}
      decoding="async"
      onError={() => setFailed(true)}
      /* 3:2 is the frame these were shot in, so the tile is that and nothing
         is cropped. Picking 240:100 to suit a drawing, as the first version
         did, would cut the aerial off the top of the ladder truck. */
      className={cn('aspect-[3/2] w-full rounded-sm bg-muted/40 object-cover', className)}
    />
  )
}
