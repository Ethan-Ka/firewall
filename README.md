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

Or use the runner scripts, which create the venv, install `faster-whisper` on
first run, and work from any directory:

```
./scripts/run-broadcastify.sh      # verifies the key first, then polls
./scripts/run-trunk.sh             # watches the trunk-recorder output dir
```

`run-broadcastify.sh` refuses to start the poll loop if `--check` fails, so a bad
key or wrong endpoint cannot quietly burn metered requests. Both pass extra flags
straight through (`--open`, `--port 8421`).

### broadcastify

Register at **bcfy.io/dev/apply**, then put `BCFY_API_KEY` in `.env` (copy
`.env.example`), or copy `config.example.json` to `config.json` and fill it in.
Verify the credential without starting the server:

```
firewall --check --source broadcastify
```

That prints which `.env` was loaded, masks the key, makes exactly one request, and
distinguishes a rejected key (401/403) from a wrong endpoint (404).

Billing is metered and prepaid: a $5 minimum credit, charged per **record read**,
with a spending cap you set. Measured off the portal usage page on 2026-08-19 —
1,032 records for $0.62 — the rate is **about $0.0006 per record**, so $5 is
roughly 8,300 records.

The unit that costs money is records per fetch, not fetches per hour. A poll that
returns no new calls reads zero records and is free, so lowering
`FIREWALL_POLL_SECONDS` saves nothing:

| | Records | Cost |
|---|---|---|
| One `init=1` fetch | 25 | $0.015 |
| One poll, no new calls | 0 | free |
| Steady state, whole system (~16 records/hour) | 16/hr | ~$0.01/hour |
| Stuck re-sending `init=1` every 5s | 18,000/hr | **$10.80/hour** |

That last row is the one to design against: `init=1` returns the last 25 calls
regardless of age, so anything that keeps the `pos` cursor from advancing turns a
$7/month display into a drained credit in under half an hour. Four talkgroups in
one county is otherwise very little volume.

Broadcastify Premium ($15 for six months, $30 for a year) is separate and buys 365
days of both live-audio and Calls archives. That is the underrated part. Pull a
month of historical Tippecanoe fire dispatches and tune the parser against real
phrasing offline, instead of waiting around for something to catch fire.

**Verified against the live docs, 2026-08-19.** The earlier guess in this repo was
wrong in two ways, both now fixed:

- **URL layout.** Endpoints are `/{endpoint}/v1`, not `/v1/{endpoint}`. Live Calls
  is `GET https://api.bcfy.io/calls/v1/live/`. The old guess returned a hard 404.
- **Auth.** The raw API key is *not* a bearer token. It is the HMAC-SHA256 signing
  secret for a short-lived JWT whose header carries the **API Key ID** (`kid`) and
  whose payload carries the **Application ID** (`iss`). Minting lives in
  `bcfy_auth.py`, implemented with stdlib `hmac` only, and is unit-tested against
  the sample JWT published in the docs.

Live Calls also needs an **authenticated Broadcastify user** embedded in the JWT
(`sub`/`utk`), obtained by posting your username and password to
`/common/v1/auth`; the exception is public playlists. A free account suffices.

Query params are mutually exclusive: `playlist_uuid`, `sid`, `nodeId`, or `groups`.

**`groups` takes exactly one group, not the documented five.** The docs say
"comma delimited ... (Max 5)". Measured against sid 9099 on 2026-08-19, with
`init` dropped and a 6-hour `pos` window:

```
groups=9099-2021,9099-2105   ->  0 records
groups=9099-2021             ->  6 records
groups=9099-2105             -> 10 records
```

Empty set, no error, a plausible-looking `lastPos`. Encoded comma, pipe,
semicolon and a repeated `groups=` all fail the same way. So `broadcastify()`
**round-robins one group per tick**, each with its own `pos` cursor. The 5-second
floor applies to the endpoint rather than to each group, so two talkgroups means
each is polled every 10 seconds — well inside Broadcastify's own 10-30s ingest
lag, and it keeps every record you pay for one you actually wanted.

`init=1` is never sent. It reads 25 records regardless of age, and the loop drops
anything older than `hold_seconds` anyway. Each response's `lastPos` feeds the
next `pos`; on an empty result it comes back `0`, so the cursor holds its previous
value rather than resetting to the server's rolling 5-minute default.

> Per-call response field names are not listed on the docs pages that were
> captured, so `_bcfy_normalize()` accepts several spellings for the audio URL.
> `firewall --check --source broadcastify` dumps one raw record so it can be
> pinned down against a real response.
> Everything Broadcastify-specific is confined to `_bcfy_fetch()` and
> `_bcfy_normalize()` in `sources.py`, and that is the only place that should need
> editing.

The open question is latency. Broadcastify adds ingest and upload lag on top of
dispatch. Given the 30 to 90 second head start, it may still beat the truck to your
door, but it is close enough to be worth measuring before spending money on radio
hardware.

### eta and proximity

The display answers two questions about a dispatch: when the apparatus reaches
the scene, and — the one you actually care about — whether its route passes close
enough that you will hear the siren, and when. Set `FIREWALL_HOME` in `.env` to
`"lat,lon"` or a street address; leave it blank and you get the scene ETA only.

#### how to write the address

`FIREWALL_HOME` accepts any of these, all verified against the live geocoders:

| Value | Notes |
|---|---|
| `531 W Navajo St, West Lafayette, IN 47906` | full address; the ZIP is optional |
| `531 W Navajo St, West Lafayette, IN` | city and state |
| `531 W Navajo St` | street alone — West Lafayette, IN is assumed |
| `Cary Quadrangle` | a landmark or building name |
| `40.451520,-86.915309` | exact coords, no lookup at all |

Don't quote it, and leave off any apartment or unit number — neither geocoder
handles a unit and both do better without one. Abbreviations don't matter:
`St` / `Street` / `St.` and `W` / `West` all resolve identically.

Anything containing a comma is split into street, city and state, so naming a
city means the lookup happens **there**. Get the city wrong and you get nothing
back rather than a wrong answer — `500 W Navajo St, Lafayette, IN` returns no
match, because that street is in West Lafayette. This matters more here than in
most places: Lafayette and West Lafayette are different cities four miles and one
river apart, and they share street names.

The resolved coordinates are printed at startup and by `firewall --check`, so
check them against a map once:

```
home      40.451520, -86.915309  (531 W Navajo St, ...)  siren radius 600m
```

They're cached in `.geocache.json` (git-ignored); delete it to force a re-lookup.

#### stations

`firewall/geo.py` holds the station coordinates, taken from OpenStreetMap's named
building POIs and confirmed against westlafayette.in.gov and purdue.edu:

| Station | Address | Coords |
|---|---|---|
| WLFD 1 | 300 North St | 40.426102, -86.908674 |
| WLFD 2 | 531 W. Navajo St | 40.451520, -86.915309 |
| WLFD 3 | 1100 W. Kalberer Rd | 40.468799, -86.920082 |
| Purdue FD | 1250 3rd St | 40.427700, -86.924003 |

Which station rolls is inferred rather than routed for: Purdue has one house, so
its talkgroup settles it, and WLFD numbers apparatus by station, so `E2` means
Station 2. Those unit ids already come out of `parse.py`, which makes them a
better signal than any router could offer.

"Passes you" is a corridor test — the closest approach of the straight
station-to-incident path to your home, clamped to the segment so a call in the
opposite direction cannot claim to pass you — against `FIREWALL_SIREN_METRES`
(default 600m, which covers the surrounding streets and not just your own).

**Accuracy is about ± a minute, and no method does better.** The dominant term is
turnout — dispatch tones to wheels rolling — which runs 60–90 seconds and varies
call to call; NFPA 1710 sets an 80-second target. Lights, traffic, and which units
actually roll add more. Real road routing would tighten the travel leg and leave
turnout untouched, so it buys far less than it looks like it should. Tune
`TURNOUT_SECONDS` in `geo.py` against runs you watch. If you later want street
distances, a self-hosted OSRM is a drop-in for `travel_seconds()`.

Incident addresses are geocoded free — no key, no bill — via Nominatim with the
US Census geocoder as fallback, cached forever in `.geocache.json`.

Both results are validated, and that is not defensive padding. Two separate traps
sit on this exact county:

- **Census relabels cities and cannot be trusted to report one.** Ask it for
  `500 W Navajo St, Lafayette, IN` and it answers `500 W NAVAJO ST, LAFAYETTE,
  IN, 47906` — while handing back West Lafayette's coordinates and West
  Lafayette's ZIP. It normalises to the USPS postal city and echoes whatever you
  asked for, so no name check can catch it. Its results are instead verified by
  reverse-geocoding the returned point against OSM.
- **`Lafayette` is a substring of `West Lafayette`.** A city guard written with
  `in` rather than equality passes a Lafayette address straight through — the
  precise confusion the guard exists to prevent. `_same_city()` compares
  normalised strings for equality for this reason.

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

Copy `config.example.json` to `config.json`, or `.env.example` to `.env`. Both are
git-ignored. Precedence, lowest to highest:

```
DEFAULTS in config.py  <  config.json  <  .env  <  real environment
```

`.env.example` documents every variable. The loader is thirty lines of standard
library in `config.py`, so mock mode still has no dependencies.

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

**Street addresses and campus buildings are handled separately.** `ADDR_RE`
requires a house number and a street suffix, which is right for West Lafayette FD
and useless for Purdue FD — campus dispatches name a building, not a street, so
almost none of them carry an address at all. Rather than keeping a list of every
building on campus, `LANDMARK_RE` captures whatever sits between the dispatcher's
"respond to" and the reason clause that follows, and `_NOT_A_PLACE` throws out the
captures that ran into the call type instead of a place name. Street match wins
when both are present, since a landmark capture would happily swallow an address.

Shorthand is resolved by `LANDMARK_ALIASES` in `geo.py` — `PMU`, `the Union`,
`Cary Quad`, `CoRec`, `Ross-Ade`, `Mackey`, `Wetherill`. Two entries exist because
OSM has no matching name at all (`Purdue Memorial Union` and `Krannert` map to
their street addresses); the rest are dispatch abbreviations. Extend both this and
`TYPE_HINTS` as you hear how your dispatchers actually talk.

Geocoding campus buildings needs a second pass: Nominatim's structured query is
the only form that reliably honours the city, but it indexes streets rather than
points of interest, so `Ross-Ade Stadium` and `Wetherill Laboratory of Chemistry`
miss it entirely. `_nominatim()` therefore tries structured first and free-text
second.

Current coverage across the standard phrasings, all the way through to an ETA:

```
340 Sagamore Parkway West   Structure Fire          scene ~134s
1820 Cumberland Avenue      Vehicle Crash           scene ~186s
500 W Navajo Street         Automatic Fire Alarm    scene ~325s
415 North River Road        Carbon Monoxide Alarm   scene ~480s
Cary Quadrangle             Medical: Chest Pain     scene ~137s
CoRec                       Water Rescue            scene  ~92s
Ross-Ade Stadium            Fall / Lift Assist      scene ~155s
Wetherill                   Automatic Fire Alarm    scene ~158s
PMU                         Medical: Unresponsive   scene ~181s
Hillenbrand Hall            Elevator Rescue         scene ~101s  PASSES YOU
```

That is on clean text. **Real whisper output off a radio is not clean**, and the
regex is brittle in exactly the way regexes are. For garbled transcripts set
`FIREWALL_USE_LLM_PARSER=true` and fill in `ANTHROPIC_API_KEY` (about $0.0002 per
call, `pip install -e ".[llm]"`). It falls back to regex automatically if the call
fails, so turning it on costs nothing but the key.

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
