# firewall

An ambient display that tells you what call the fire truck driving past your house
is running. Like a "plane overhead" display, but for the fire department.

One process, one command: listen, transcribe, parse, display.

```
  audio in  ->  whisper  ->  parse  ->  current call  ->  display
  (mock | broadcastify | trunk-recorder)
```

## Quick start

```bash
pip install -e .
firewall --open
```

That runs the whole system with synthetic dispatches every 45 seconds. No
credentials, no audio, no hardware. The display opens in your browser.

## Working on the design

The display re-reads `firewall/display.html` from disk on every request, so edit
it and hit refresh. No restart, no build step.

You can also open `firewall/display.html` directly as a file with nothing running
at all. It tries the API, fails, and drops into demo mode:

- **SPACE** cycles all seven states (fire, medical, crash, alarm, hazmat, idle, error)
- Every visual knob is a CSS custom property in `:root`
- Per-category accent colors are the `CATEGORY_STYLES` array
- `HOME_UNITS` is a regex of the rigs from stations that route past your house.
  Those chips get a marker, heavier weight, and the accent color

Nothing about the design is load-bearing. The only contract with the rest of the
system is this object:

```json
{
  "dept":    "West Lafayette FD",
  "type":    "Structure Fire",
  "address": "340 Sagamore Parkway West",
  "city":    "West Lafayette",
  "units":   ["E2", "L1", "BC1"],
  "ts":      1787115224.0
}
```

Served at `/api/current` as `{"call": <that or null>, "hold_seconds": 600}`.
Rewrite the display from scratch if you want; nothing else changes.

## Sources

| `--source` | Needs | Latency | Use it for |
|---|---|---|---|
| `mock` *(default)* | nothing | n/a | design and pipeline work |
| `broadcastify` | API key, ~$5 credit | 10–30s? | no hardware at all |
| `trunk` | Airspy + trunk-recorder | ~1–3s | the full local build |

```bash
firewall --source broadcastify
firewall --source trunk
```

### broadcastify

Register at **bcfy.io/dev/apply**, then copy `config.example.json` to
`config.json` and fill it in.

⚠️ The real API schema is at **bcfy.io/dev/docs**, behind registration. The
endpoint path and response field names here are inferred, not verified. Check
them against the docs. Everything Broadcastify-specific is confined to
`_bcfy_fetch()` and `_bcfy_normalize()` in `sources.py`, and that is the only
place you should need to edit.

The open question is **latency**. Dispatch goes out 30 to 90 seconds before the
truck reaches you; Broadcastify adds ingest and upload lag. Measure it before
deciding you need the SDR.

### trunk

Point `trunk_dir` at a trunk-recorder output directory. It watches for new call
WAVs plus their `.json` sidecars and filters on talkgroup.

## Talkgroups

Defaults are the Tippecanoe County Government P25 system (RadioReference sid 9099).
Every fire and EMS talkgroup on that system is unencrypted:

| TG | |
|---|---|
| 2105 | Purdue FD |
| 2021 | West Lafayette FD |
| 1901 | Lafayette FD |
| 1827 | Tippecanoe County Fire |
| 1833 | Tippecanoe EMS |

Override in `config.json` for anywhere else.

## Parsing

`parse.by_regex()` handles the standard dispatch cadence. Extend `TYPE_HINTS` in
`parse.py` as you hear how your dispatchers actually talk.

For garbled transcripts set `"use_llm_parser": true` and export `ANTHROPIC_API_KEY`
(about $0.0002 per call, `pip install -e ".[llm]"`). It falls back to regex
automatically if the call fails.

## Legal

Receiving unencrypted public-safety radio is legal federally. ECPA (18 U.S.C.
§2511) exempts transmissions readily accessible to the general public. Indiana has
**IC 35-44.1-2-7**; read it yourself. Two rules regardless: do not rebroadcast or
republish the audio or transcripts, and do not put the screen where it is readable
from the street, because dispatch occasionally reads patient details.

## Not built yet

- **Geo-filter**: geocode `address`, then surface only calls whose station-to-incident
  path actually passes near you
- **Siren detect**: USB mic plus an FFT on the 600 to 1500 Hz wail sweep, to confirm
  the real pass-by

## License

MIT

## Design

The display is built against `standardized-design-system` and was run through
`docs/audit-checklist.md`. Two decisions are deliberate rather than defaulted, and
both are stated in the comment header of `display.html`:

**Dark theme**, set explicitly with `data-theme="dark"` on `<html>`. This is an
always-on screen in a hallway, and a light surface at 3am is unusable. Switching
to light is one attribute.

**Two families.** IBM Plex Sans for anything readable, IBM Plex Mono for unit
designators and the clock only, because those are fixed-width codes that should
align in a column. Mono is never used for prose.

Contrast, measured against `--surface` (n-900, `#1A1917`):

| Pair | Ratio |
|---|---|
| n-0 on n-900 (headline) | 17.57:1 |
| n-100 on n-900 (address) | 15.02:1 |
| n-300 on n-900 (muted) | 9.67:1 |
| pine-300 on n-900 (EMS accent) | 9.35:1 |
| clay-300 on n-900 (hazard accent) | 8.67:1 |

All clear WCAG AA for body and large text. Every pair is already in the
`contrastVerified` block of `tokens/tokens.json`; no new pair was introduced.

Category colour is always paired with the category spelled out in text, and
home-station units carry a marker plus heavier weight plus colour, so nothing
depends on colour alone.

### Audit result

Checked by hand unless noted. Checked by tool: contrast ratios (computed),
horizontal overflow at 320, 768, 1440 and 2560 (Playwright), DOM rebuild count
(MutationObserver).

Passing: no em dashes anywhere in the repo; no banned vocabulary; accent is clay
and pine, not indigo; no gradient; no glow; no glassmorphism; dark mode declared
rather than assumed; body face is IBM Plex Sans; no all-caps eyebrow above the
heading; hierarchy uses position, weight and colour as well as size; composition
is bottom-left weighted and deliberately asymmetric; grouping uses uneven spacing;
one radius (4px) held throughout; motion capped at 220ms and
`prefers-reduced-motion` respected; one `h1`; empty, loading and error states all
exist, and the error state names the failure and the fix; focus-visible styling
present.

Not applicable: imagery, testimonials, feature cards, stat banners, numbered
sequences. This interface has no such components.

Known gap: hover, active and disabled states exist only on the demo cycle button,
because the display has no other interactive elements by design. Stated rather
than implied, per `AGENTS.md`.
