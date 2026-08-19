# firewall

An ambient display that tells you what call the fire truck driving past your house
is running. Same idea as a "plane overhead" display, pointed at the fire department
instead of the sky.

One process, one command: listen, transcribe, parse, display.

```
  audio in  ->  whisper  ->  parse  ->  current call  ->  display
  (mock | broadcastify | trunk-recorder)
```

---

## Why this works here

Tippecanoe County, Indiana runs a **P25 Phase I** trunked radio system with nine
channels and three control channels (851.050, 853.8375 and 857.7375 MHz). Every
fire and EMS dispatch talkgroup on it is unencrypted. Out of the entire system,
exactly two talkgroups carry an encryption flag, and both are county event
channels rather than dispatch.

There is no public CAD feed for the county, so voice is the only source. That is
the one real difference from a plane display: ADS-B hands you structured data,
because the aircraft transmits its own callsign. Fire dispatch hands you a human
talking. Everything downstream of that is off the shelf; the speech-to-text and
parsing stage is the actual project.

A free empirical check beats any database entry. Broadcastify streams a live
"Tippecanoe County Fire" feed covering Lafayette, West Lafayette, Purdue and
county fire. If those talkgroups were encrypted, that feed would be silence.

### Timing

Dispatch goes out roughly 30 to 90 seconds before the truck reaches you, and
transcription adds a few seconds on top. The display is therefore already showing
the call when the rig passes the window. That is a useful accident rather than
something the design had to solve.

---

## Quick start

```bash
python3 -m firewall --open
```

That runs the whole system with synthetic dispatches every 45 seconds. No
credentials, no audio, no hardware, and no dependencies: mock mode is pure
standard library, because `requests` and `faster-whisper` are imported lazily
inside the sources that need them.

To get the bare `firewall` command instead of `python3 -m firewall`:

```bash
pip install -e .
firewall --open
```

---

## Working on the display

The server re-reads `firewall/display.html` from disk on every request. Edit it and
refresh. There is no restart and no build step.

You can also open that file directly with nothing running. It tries the API, fails,
and falls into demo mode:

- **SPACE** cycles all seven states: fire, medical, crash, alarm, hazmat, idle, error
- Visual knobs are CSS custom properties in `:root`
- `CATEGORIES` maps a call type to its label and accent
- `HOME_UNITS` is a regex of the rigs from stations that route past your house.
  Those chips get a marker, heavier weight, and the accent colour.

Nothing about the design is load-bearing. The only contract with the rest of the
system is one object:

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

Rewrite `display.html` from scratch if you want. Nothing else changes.

---

## Sources

| `--source` | Needs | Latency | Use it for |
|---|---|---|---|
| `mock` *(default)* | nothing | n/a | design and pipeline work |
| `broadcastify` | API key, about $5 credit | 10 to 30s, unmeasured | no hardware at all |
| `trunk` | Airspy plus trunk-recorder | 1 to 3s | the full local build |

```bash
firewall --source broadcastify
firewall --source trunk
```

### broadcastify

Register at **bcfy.io/dev/apply**, then copy `config.example.json` to `config.json`
and fill it in. Billing is metered and prepaid: a $5 minimum credit, charged per
record read, with a spending cap you set. Four talkgroups in one county is very
little volume.

Broadcastify Premium ($15 for six months, $30 for a year) is separate and buys 365
days of both live-audio and Calls archives. That is the underrated part. Pull a
month of historical Tippecanoe fire dispatches and tune the parser against real
phrasing offline, instead of waiting around for something to catch fire.

> **Unverified.** The authoritative API schema lives at **bcfy.io/dev/docs**, behind
> registration. The endpoint path and response field names in this repo were
> inferred from the public documentation summary. Check them before trusting them.
> Everything Broadcastify-specific is confined to `_bcfy_fetch()` and
> `_bcfy_normalize()` in `sources.py`, and that is the only place that should need
> editing.

The open question is latency. Broadcastify adds ingest and upload lag on top of
dispatch. Given the 30 to 90 second head start, it may still beat the truck to your
door, but it is close enough to be worth measuring before spending money on radio
hardware.

### trunk

Point `trunk_dir` at a trunk-recorder output directory. The source watches for new
call WAVs alongside their `.json` sidecars and filters on talkgroup.

---

## Hardware, if you go local

| Part | Pick | Cost |
|---|---|---|
| Antenna | Any 800 MHz mag-mount or discone, window or attic | $25 |
| SDR | Airspy R2 | $170 |
| Computer | Raspberry Pi 5 8GB, or any spare mini PC | $80, or nothing |
| Display | An old tablet in kiosk mode, or a 7" Pi touchscreen | nothing, or $60 |

About $275 new. Closer to $115 if you already own a spare machine and a tablet.

**Why the Airspy and not RTL-SDRs.** The system spans 851.050 to 857.7375 MHz,
which is 6.7 MHz. A single RTL-SDR usably covers about 2.4 MHz, so covering the
control channel plus whichever voice channel the system assigns takes three
dongles. Three dongles run about $120, barely cheaper, and cost you a powered hub,
three sources in the config, and gain matching. One Airspy R2 at 10 MSPS covers the
whole system in one device and is the best-trodden path in trunk-recorder.

**The gotcha to expect.** This is a simulcast system, and simulcast P25 can produce
garbled audio at particular locations regardless of signal strength. Antenna
placement or deliberate attenuation usually fixes it. It is the thing most likely
to cost you an evening.

---

## Suggested build order

Each step stands on its own, and the first one costs nothing.

1. **Run `--source mock` and build the display.** No accounts, no hardware.
2. **Point the pipeline at Broadcastify.** You find out the real call volume near
   your house, the exact phrasing your dispatchers use, and whether transcription
   and parsing hold up, before buying anything. If the answer is no, you have spent
   nothing.
3. **Buy the Airspy and antenna, and switch to `--source trunk`.** This buys three
   things and only three: lower latency, independence from a volunteer's uploader
   node staying online, and no per-record cost.
4. **Hang a tablet by the door** in kiosk mode pointed at the local URL.

---

## Talkgroups

Defaults are the Tippecanoe County Government P25 system (RadioReference sid 9099).

| Talkgroup | Department |
|---|---|
| 2105 | Purdue FD |
| 2021 | West Lafayette FD |
| 1901 | Lafayette FD |
| 1827 | Tippecanoe County Fire |
| 1833 | Tippecanoe EMS |

Override `talkgroups` in `config.json` for anywhere else.

---

## Configuration

Copy `config.example.json` to `config.json`. It is git-ignored.

| Key | Default | What it does |
|---|---|---|
| `bcfy_api_key` | none | Also readable from `BCFY_API_KEY` |
| `bcfy_system_id` | none | Broadcastify system id, not the RadioReference sid |
| `bcfy_api_base` | `https://api.bcfy.io/v1` | Verify against the live docs |
| `talkgroups` | the five above | Decimal id to display name |
| `poll_seconds` | 5 | Broadcastify poll interval |
| `whisper_model` | `base.en` | `tiny.en`, `base.en` or `small.en` |
| `trunk_dir` | `./trunk-out` | Where trunk-recorder writes |
| `use_llm_parser` | `false` | Needs `ANTHROPIC_API_KEY` |
| `hold_seconds` | 600 | How long a call stays on screen |
| `port` | 842 | HTTP port |

---

## API

`GET /api/current`

```json
{
  "call": { "dept": "...", "type": "...", "address": "...",
            "city": "...", "units": ["E2"], "ts": 1787115224.0 },
  "ok": true,
  "error": null,
  "last_ok": 1787115224.0,
  "hold_seconds": 600
}
```

`call` is `null` when nothing is running. When `ok` is `false`, `error` carries the
source's last failure verbatim, and the display renders its error state with that
text. `GET /` serves the display.

---

## Parsing

`parse.by_regex()` handles the standard dispatch cadence, for example "Engine 2,
Ladder 1, respond to 340 Sagamore Parkway West for a structure fire." It pulls
`['E2', 'L1']`, `Structure Fire`, and `340 Sagamore Parkway West` out of that
sentence, and it returns nothing for radio chatter that is not a dispatch, so
status checks never reach the screen.

Extend `TYPE_HINTS` in `parse.py` as you hear how your dispatchers actually talk.

For garbled transcripts, set `"use_llm_parser": true` and export
`ANTHROPIC_API_KEY` (about $0.0002 per call, `pip install -e ".[llm]"`). It falls
back to regex automatically if the call fails.

---

## Design

The display is built against `standardized-design-system` and was run through
`docs/audit-checklist.md`. The first version failed twelve checks. Two decisions
are deliberate rather than defaulted, and both are stated in the comment header of
`display.html`:

**Dark theme**, set explicitly with `data-theme="dark"` on `<html>`. This is an
always-on screen in a hallway, and a light surface at 3am is unusable. Switching to
light is one attribute.

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

All clear WCAG AA for body and large text. Every pair already appears in the
`contrastVerified` block of `tokens/tokens.json`, so no new pair was introduced.

Category colour is always paired with the category spelled out in text, and
home-station units carry a marker plus heavier weight plus colour. Nothing depends
on colour alone.

### Audit result

Checked by tool: contrast ratios (computed), horizontal overflow at 320, 768, 1440
and 2560 (Playwright), DOM rebuild count (MutationObserver).

Passing: no em dashes anywhere in the repo; no banned vocabulary; accents are clay
and pine rather than indigo; no gradient; no glow; no glassmorphism; dark mode
declared rather than assumed; body face is IBM Plex Sans; no all-caps eyebrow above
the heading; hierarchy uses position, weight and colour as well as size;
composition is bottom-left weighted and deliberately asymmetric; grouping uses
uneven spacing; one radius (4px) held throughout; motion capped at 220ms with
`prefers-reduced-motion` respected; one `h1`; empty, loading and error states all
exist, and the error state names the failure and the fix; focus-visible styling
present.

Not applicable: imagery, testimonials, feature cards, stat banners, numbered
sequences. This interface has none of those components.

Known gap: hover, active and disabled states exist only on the demo cycle button,
because the display has no other interactive elements by design. Stated rather than
implied, per `AGENTS.md`.

### A bug worth remembering

The first version rebuilt the whole DOM through `innerHTML` on every one-second
clock tick, so the entrance animation replayed once a second and the screen
appeared to jump. The fix splits the two jobs: `paint*()` rebuilds and is keyed on
call identity, `tick()` touches only text content and widths. Measured afterwards
at zero rebuilds across six seconds on a single call.

---

## Legal

Receiving unencrypted public-safety radio is legal federally. ECPA (18 U.S.C.
§2511) exempts transmissions readily accessible to the general public.

Indiana has **IC 35-44.1-2-7, "Unlawful Use of a Police Radio."** Read the statute
yourself. It appears to target scanner use in the commission of a crime rather than
stationary home listening, but that reading is not legal advice and the text is
worth ten minutes of your time.

Two rules regardless. Do not rebroadcast or republish the audio or the transcripts.
Do not put the screen where it is readable from the street, because dispatch
occasionally reads patient details aloud.

---

## Risks

**Future encryption.** Fire is in the clear today and tends to stay that way longer
than police, because of mutual aid and volunteer pagers. It is not permanent.
Seattle Fire announced encryption in 2026 citing patient health information, and
that reasoning applies anywhere. This is the strongest argument for prototyping
against Broadcastify before buying an Airspy.

**Node reliability.** The Broadcastify path depends on a volunteer's uploader node
staying online. The local SDR path does not.

**Call volume.** It may be lower near you than you expect. Step 2 of the build order
tells you before you spend.

---

## Not built yet

**Geo-filter.** Geocode `address`, then surface only the calls whose
station-to-incident path actually passes near you. Roughly a day of work and a real
improvement in signal.

**Siren detection.** A USB mic and an FFT on the 600 to 1500 Hz wail sweep, to
confirm the actual pass-by rather than inferring it. This is the fun one and also
the one that will eat a weekend.

---

## Repository layout

```
firewall/
  __main__.py     entry point, HTTP server, argument parsing
  config.py       defaults and config.json loading
  sources.py      mock, broadcastify and trunk sources
  core.py         shared state, whisper, publish, source health
  parse.py        transcript to structured call
  display.html    the screen, standalone and editable live
```

## License

MIT
