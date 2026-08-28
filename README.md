# firewall

An ambient display that tells you what call the fire truck driving past your house
is running. Same idea as a "plane overhead" display, pointed at the fire department
instead of the sky.

One process, one command: listen, transcribe, parse, display.

```
  audio in  ->  whisper  ->  parse  ->  current call  ->  display
  (broadcastify | trunk-recorder)
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

That watches Broadcastify, which needs credentials in `.env` and is billed per
record read. `firewall --check` verifies the key without starting the poll
loop, and `--source trunk` reads a local trunk-recorder directory instead,
which costs nothing.

There is no mock source and no demo mode. There was one of each, generating a
dispatch every 45 seconds so the screens could be worked on without
credentials, and both were removed: a fabricated structure fire at a real
address is not a harmless placeholder on a display whose only job is saying
what is actually on fire. A screen that invents a call when it cannot reach its
server is worse than a blank one, because a blank one is obviously broken and a
burning building is not. Unreachable now says it is unreachable.

To get the bare `firewall` command instead of `python3 -m firewall`:

```bash
pip install -e .
firewall --open
```

There are two screens. `/` is the wall display: one call, read from across a
hallway. `/tracker` is the data view: what the department actually gets called
to, and every call in the log with its own state.

```bash
./scripts/run-tracker.sh              # opens /tracker
./scripts/run-tracker.sh --source trunk
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

### The transcript and the audio

A bar along the bottom carries the call's radio traffic: one line per transmission,
timed from the dispatch, with the dispatch itself set brighter than the chatter
around it. Click a line to hear it. The bar has play/pause, a scrub, and a status
that says what is loaded or what is wrong with it.

New transmissions play themselves as they arrive, one at a time, in the order they
were said. Two rules keep that from turning into a nuisance:

- The first payload after a page load is adopted silently. Opening the screen in
  the middle of a working fire should not replay the whole call at you.
- Nothing older than three minutes is ever played, so reconnecting after an outage
  does not empty an hour of backlog into the room.

Both halves are switches, and they are two switches rather than one because they
fail in opposite directions. The transcript is silent and can sit on a hallway
screen all day. The audio cannot: there are rooms where a radio playing itself at
3am is the reason the whole thing gets unplugged.

| Key | Chip | Does |
|---|---|---|
| **T** | `transcript` | The bar itself: transcript, scrub, replay |
| **A** | `audio` | Whether new transmissions play on arrival |
| **SPACE** | — | Play/pause. Also the gesture browsers demand before a page is allowed to make noise |

Both settings live in `localStorage`, per screen rather than in the config, because
which room the display is in is what decides them. A screen that reboots comes back
the way it was left.

The clips are the ones the poller already downloaded, held in memory and served from
`/api/clip`. Replaying a call never re-fetches it from the source — on Broadcastify
that would be paying for the same record twice. The tape holds the last 40 clips or
48MB, whichever comes first; past that, a line stays in the transcript and says the
clip is no longer held.

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

## The tracker

The display answers "what is happening right now" for somebody walking past.
The tracker answers a different question, and it needs a different screen:
what does this department get called to, and where has each of those calls got
to. It is at `/tracker`.

Four panels. Three of them are readings of the same radio, and the fourth is
who is on the other end of it:

- **Call types.** A horizontal bar per call type over the chosen window, sorted
  by count. Fills carry the family (fire and hazard, EMS and rescue,
  unclassified) and the type's own name carries its identity, so the chart is
  legible with no colour at all. Every count is printed at the bar tip rather
  than left in a tooltip, and a table view of the same numbers is one button
  away.
- **Calls.** One row per call, newest first, live ones marked with a dot and
  the word `live`. Opening a row shows that call's units, how far along it got,
  when it closed and how long it ran, and the incident id `firewall --replay`
  takes.
- **Radio.** Every transmission still held, in the order it was said, whether
  or not a call claimed it. The same `feed` the display draws from.
- **The department.** Purdue University Fire Department itself: what it turns
  out for, and all eleven rigs it owns with a photograph of each. Every unit
  says what it has done in the same window the chart above is drawing, and a
  unit that is out right now says so in the same words the call table uses.
  Open one for its chassis, pump, tank and crew.

  The photographs are Chris Allen's, out of the Purdue and Purdue Airport
  galleries at IndianaFireTrucks.com, and they are all rights reserved. So
  they are loaded from his server rather than copied into this one: no
  photograph he owns is in this repository or in the build Vercel serves, the
  watermark arrives in the frame he cropped, and every tile carries his name
  and links back to that photograph's own page.

  The cost of that is a panel that needs a route to the internet -- a tile
  with no route says `photo offline` rather than showing a broken image. If
  that matters more, or you have your own photographs, drop files named for
  the unit into `web/src/assets/units/` and they win, at build time, offline.

A filter row sits above all of them: department, and a 24 hour / 7 day / all
window. One filter row rather than four, so the chart, the table and the
roster's own counts can never end up describing different sets of calls. The screen opens on the narrowest
window that actually has calls in it, because a tracker left running for a week
and then glanced at should not greet you with an empty chart that looks broken.

### Building it

Unlike the display, this one has a build step. It is React, Tailwind v4 and
shadcn/ui, with d3 for the chart's scales and motion for its transitions.

```bash
cd web
npm install
npm run build        # writes web/dist/
```

The build is not committed. `./scripts/run-tracker.sh` makes one when it is
missing and rebuilds when something under `web/src` is newer than the last one,
saying so when it does; `firewall` serves whatever is in `web/dist` at
`/tracker`, and answers with the command above when there is nothing there.

`npm run dev` gives the usual Vite dev server with `/api` proxied to
`localhost:842`, so run `firewall` alongside it and the wire is live, cookies
and all. `FIREWALL_ORIGIN` moves the proxy if the server is on another port.

### Hosting it

The tracker is a static site and does not have to live on the machine holding
the radio. `web/` deploys to Vercel as it stands -- Vite preset, root directory
`web`, no build command to type -- and `vercel.json` carries the two things a
single-page app wants from a static host: every path answers with `index.html`,
and the hashed bundles are cached for a year.

The question is how the hosted page gets the calls, and there are two answers.

#### Push, which is the default

The machine running `firewall` posts a snapshot outward every ten seconds. The
hosted half -- three functions in `web/api/` and one key in a Redis -- keeps the
last day of it and serves it back to the page as `/api/log` and `/api/current`,
the same shapes the server itself serves.

Nothing reaches into a home network. There is no port to forward, no tunnel to
keep up and no certificate to renew, and the page still renders when the radio
machine is off, stamped with how long ago it last said anything. The page is a
copy a few seconds behind, and it says so: `pushed 4s ago` sits in the status
line and turns amber when the seconds become minutes.

On Vercel: add a Redis (Storage → Upstash for Redis → connect), which sets
`KV_REST_API_URL` and `KV_REST_API_TOKEN` for you, and set
`FIREWALL_PUSH_TOKEN` to a secret. In `.env` here:

```
FIREWALL_PUSH_URL=https://your-project.vercel.app/api/push
FIREWALL_PUSH_TOKEN=<the same secret>
```

`firewall --check` sends one and tells you what came back, which is worth doing
once: a wrong token and a project with no store connected both look like a
tracker that is simply empty, and empty reads as a quiet department.

Two things do not travel. The audio stays in the memory of the process that
recorded it -- a day of trunked radio is gigabytes, and Redis is not where it
would go -- so rows are pushed with their url made absolute against
`FIREWALL_PUBLIC_URL` if this machine has a public address, and nulled if it
does not, which the tracker has always drawn as a disabled play button. And the
words, if `FIREWALL_USERS` is set: they are stripped before they leave. That is
not a limitation of the hosted half but the same decision the server already
made, honoured at the one point where it would otherwise be quietly undone.

#### Direct, which is live

The hosted page fetches this server itself. Live to the second and the audio
plays, at the price of the server being reachable from the internet over HTTPS.

- **`VITE_API_BASE`**, in the Vercel project, is the public origin of the
  machine running `firewall` -- the tunnel in front of it. Read at build time,
  because a static site has nothing to ask at runtime, so changing it means
  redeploying. Empty -- the default, and what the push deployment uses -- means
  the page reads its own origin.
- **`FIREWALL_ALLOW_ORIGINS`**, on the server, names the deployment's origin.
  Exact origins, comma-separated; a browser will not accept a wildcard on a
  credentialed read and one is not offered. Vercel gives each deployment its own
  hostname, so a preview URL you intend to open needs naming next to the
  production one.

Setting the second also moves the session cookie to `SameSite=None; Secure`,
which browsers store only over HTTPS. That is not an extra requirement so much
as the same one twice: a page hosted elsewhere can only reach this server if
something is already terminating TLS in front of it. On a plain-HTTP server the
login stops working, and it stops working locally too.

Everything a directly-connected page can read is everything a signed-in browser
can read: the calls, the addresses, the ETAs, the audio, and the transcripts if
that browser is signed in at the tunnel origin. There is no version of this
where the allowlist is a formality.

#### What is kept, and for how long

A day, and the number is enforced in four places on purpose:

| Where | What it does |
| --- | --- |
| `FIREWALL_PUSH_HOURS` | how much of the log leaves this machine |
| `RETAIN_HOURS` on Vercel | the expiry on the stored snapshot, and the ceiling on `/api/log?hours=` |
| `VITE_RETAIN_HOURS` | what the page asks for, keeps of the answer, and offers as a window |
| the server's own `/api/log?hours=` | the same window when a page reads it directly |

Three of those are code that could be wrong and one is a database refusing to
hold anything older, which is why they are not collapsed into one. The visible
consequence is that the hosted tracker has no "7 days" chip: a window chooser
offering a week over a day of data is worse than not offering it, because an
empty week reads as a quiet department rather than as a window that was never
filled.

The `firewall` server itself is unchanged -- it has the whole incident log on
disk and a screen in the same building can still ask for all of it.

### Why it looks the way it does

The palette is shadcn's `stone` rather than its default `neutral`, and that is
not a taste call: the display has been running a warm neutral ramp on `#1A1917`
since it was built, stone's dark card token resolves to `#1c1917`, and the two
screens hang two feet apart. An achromatic grey next to a warm one reads as two
programs.

The chart's two fills are the display's own clay and pine hues stepped darker
and more saturated. The display's `#E8A791` and `#7ECBBF` were tried first and
fail as fills: at OKLCH lightness 0.785 they sit above the band a mark needs in
dark mode and their chroma reads as grey. That is not a fault in them. They
were chosen as text on a dark wall, which is a different job, and they are
still used for exactly that here. The fills measure:

```
mark hazard  #cb6440   4.53:1 on the card surface
mark ems     #00a08f   5.35:1
unclassified #78716c   3.65:1
worst adjacent pair    dE 11.0 deutan, 24.1 normal vision
```

Unclassified is deliberately a grey and not a third hue. "The parser did not
recognise this" is an absence, and giving it an identity colour would state
one.

## Sharing it, and the login

Everything about this is public radio until the moment a transcript is
involved. So the transcript is the only thing that can be locked, and it locks
on its own:

```bash
firewall --invite alex
```

That prints a generated password and the exact `FIREWALL_USERS` line to paste
into `.env`. Restart, and from then on the screens still show every call --
type, address, units, status, ETA, the chart, the log -- and still play every
clip to anyone who opens them. Only the words come out of the payload, and
`/login` puts them back.

```
FIREWALL_USERS=alex:reed-tumbler-42,sam:kiln-oxbow-9
```

With that line empty, which is the default, there is no login and nothing is
gated. One screen on one wall on one network does not need a lock on its own
door, and adding the first account is what turns the whole thing on.

### Why the line is drawn there

"Structure fire at 340 Sagamore" is a fact about a building. "60 year old male,
chest pain, conscious and breathing" is a fact about somebody's grandfather,
and it is the transcript that carries it. Gating the text while still serving
the clip it was made from does not hide what was said from someone determined
to listen -- it is not meant to. It stops the transcript being a searchable,
screenshottable, indexable record of a medical call, which is the part that
actually travels.

The review page at `/review` is the exception that proves it: it is a
transcript editor end to end, there is no version of it with the words gone,
so it is behind the login rather than merely quieter behind it. Same for
`/api/clips`, `/api/transcribe` and `/api/label`.

### What a session is

A signed cookie and no server-side state. `auth.issue` writes a name and an
expiry and signs both; `auth.verify` checks the signature and the clock. There
is no session table to keep, nothing to clean up, and a restart does not sign
anyone out.

The signing key is derived from `FIREWALL_USERS` unless you set
`FIREWALL_SECRET`. That is the property worth having: **deleting somebody's
line revokes them immediately**, because every cookie signed under the old
credential list stops verifying the moment the list changes. The cost is that
adding a friend signs out the friends you already have. Set `FIREWALL_SECRET`
to a random string if you would rather have it the other way.

Ten wrong passwords from one address and that address waits five minutes. A
generated password is three words and two digits, which is plenty against a
person typing and nothing at all against a script left running for a weekend;
the throttle is what makes the difference.

### Reaching it from outside the house

`--invite` prints this machine's LAN address, which is what a friend on the
same wifi needs. From anywhere else, put it behind something that terminates
TLS -- a Tailscale tunnel, a Cloudflare tunnel, an nginx with a certificate --
and do not port-forward 842 to the internet as it is. The session cookie is
always `HttpOnly`, and on a plain-HTTP server it is `SameSite=Lax` and not
`Secure`, because a `Secure` cookie is simply never stored over plain HTTP and
the login would fail on the wifi with no error a person could read. Over a
tunnel that terminates TLS in front of it, the browser is talking HTTPS and the
cookie is protected in transit regardless.

Setting `allow_origins` for a hosted tracker flips both: that tracker is a
different site, `Lax` means the browser never sends the cookie with its
fetches, and signing in would appear to work while the words stayed missing for
ever. So the cookie becomes `SameSite=None; Secure`, which is only stored over
HTTPS -- already true of anything a hosted page can reach. Setting
`allow_origins` on a plain-HTTP server therefore breaks its own login, and that
is the honest failure: the configuration says the browser is somewhere else.
The tracker's own "sign in" link points at the server's `/login`, not its own
origin, and hands it the page to come back to.

The passwords sit in `.env` in the clear. That is a deliberate trade for a
program with no database, and it is why `--invite` generates them rather than
inviting anyone to choose one: they must be worth nothing if the file leaks.

---

## Sources

| `--source` | Needs | Latency | Use it for |
|---|---|---|---|
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

1. **Run `--source trunk` against a recorded directory and build the display.** No accounts, no metered billing.
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
library in `config.py`, so the server starts without optional dependencies.

| Key | Default | What it does |
|---|---|---|
| `bcfy_api_key` | none | Also readable from `BCFY_API_KEY` |
| `bcfy_system_id` | none | Broadcastify system id, not the RadioReference sid |
| `bcfy_api_base` | `https://api.bcfy.io/v1` | Verify against the live docs |
| `talkgroups` | the five above | Decimal id to display name |
| `poll_seconds` | 5 | Broadcastify poll interval |
| `whisper_model` | `small.en` | `tiny.en`, `base.en`, `small.en`, `medium.en` |
| `whisper_vocab` | `places.py` | Names to bias the recogniser toward |
| `whisper_vad` | `false` | Silero VAD; on, it truncates noisy transmissions |
| `audio_dir` | `~/Documents/firewall-data/clips` | Archive call audio here for replaying |
| `incident_dir` | `~/Documents/firewall-data/incidents` | Per-incident audio and transcript log |
| `corpus_path` | `~/Documents/firewall-data/corpus.jsonl` | Hand-typed truth for `--score` |
| `incident_gap_seconds` | 900 | Quiet time that closes an incident |
| `trunk_dir` | `./trunk-out` | Where trunk-recorder writes |
| `use_llm_parser` | `false` | Needs `ANTHROPIC_API_KEY` |
| `hold_seconds` | 600 | How long a call stays on screen |
| `port` | 842 | HTTP port |
| `users` | none | `name:password` pairs; empty means no login and no gate |
| `session_secret` | derived | Cookie signing key; unset derives it from `users` |
| `session_days` | 30 | How long a sign-in lasts |

---

## API

`GET /api/current`

```json
{
  "call": { "dept": "...", "type": "...", "address": "...",
            "city": "...", "units": ["E2"], "ts": 1787115224.0 },
  "radio": [
    { "id": "41-1", "ts": 1787115224.0, "dispatch": true,
      "text": "Engine 2, Ladder 1, respond to 340 Sagamore Parkway West...",
      "url": "/api/clip?id=41" }
  ],
  "feed": [
    { "id": "41-1", "ts": 1787115224.0, "dispatch": true, "text": "...",
      "url": "/api/clip?id=41" }
  ],
  "ok": true,
  "error": null,
  "last_ok": 1787115224.0,
  "hold_seconds": 600
}
```

`GET /api/log`

```json
{
  "calls": [
    { "id": "West Lafayette FD|1787883326",
      "incident": "1787883326-west-lafayette-fd-structure-fire",
      "dept": "West Lafayette FD", "opened": 1787883326, "closed": null,
      "type": "Structure Fire", "address": "340 Sagamore Parkway West",
      "city": null, "units": ["E2", "L1"], "count": 12, "live": true,
      "status": { "state": "on_scene", "ts": 1787883501.0, "text": "..." },
      "eta": null }
  ],
  "logged": true,
  "now": 1787883342.359
}
```

Every call this installation knows about, live and filed, as one list, newest
first. `/api/current` publishes the four calls that fit on a wall, which is the
right answer for a wall and useless for counting anything: a chart of call types
over a shift needs the calls that have already scrolled off it. Those are on
disk and the running ones are in memory, and `core.roster` is the only place the
two are put together. Deliberately: a live call and its own incident are the
same call, and a client merging the two lists itself would draw every running
call twice. The key is `(dept, opened)`, which is the pair both ids were built
out of. Where both exist the live call wins on everything the radio is still
changing (type, address, units, status, eta) and the incident contributes the
two things memory does not have, how many transmissions were filed and when the
log stamped it closed.

`logged` is whether anything is being written to disk at all, which is not the
same question as whether there are any calls. `incident_dir` can be unset, and
an empty roster is often the expected state rather than a fault.
The tracker needs to tell those apart to say the right thing.

`status` and `eta` are `null` on a filed call and that is not an omission. The
log records what was said, not the state machine core ran over it, and
rebuilding them from the transmissions would be a second implementation of
`read_status` that could disagree with the first.

`GET /tracker` serves the tracker out of `web/dist`, or a 404 naming the command
that builds it, and `GET /assets/...` serves its bundles. A tracker hosted
elsewhere never asks for either — it fetches only the `/api` routes, from
whatever origin `FIREWALL_ALLOW_ORIGINS` lets in, and `OPTIONS` answers the
preflight the browser sends first.

`call` is `null` when nothing is running. When `ok` is `false`, `error` carries the
source's last failure verbatim, and the display renders its error state with that
text. `GET /` serves the display.

`radio` is the current call's transmissions, oldest first: the dispatch, then the
chatter after it. It is grouped by the radio's own rhythm rather than by the call's
timestamp — a transmission mentioning a fire re-parses as a dispatch and replaces
the call, and keying on that would silently drop everything said before it.

`feed` is every transmission still held, in the order it was said, whether or not a
call claimed it — same row shape, same ids, same clip urls. It exists because the
tape used to reach the browser only through a call: traffic that did not parse as a
dispatch, and everything said before the first dispatch of the evening, was
downloaded, transcribed, kept and written to the incident log while the display
showed nothing at all. A transmission is on the screen because it was heard; the
calls are what organise it afterwards. The display draws the focused call's `radio`
when there is one and `feed` when there is not, and it is bounded by `hold_seconds`,
so the bar goes quiet at the same moment the wall says nothing is running.

Publishing runs in that order too. A row goes on the tape before the parse, before
the second whisper decode a location-less dispatch triggers, and before the
geocoder: none of that decides whether something was heard, only how it is filed.

`GET /api/clip?id=41` returns that transmission's audio from memory, with byte-range
support so the display's scrub bar can seek. `404` once it has aged out of the tape.

### When transcripts are gated

`/api/current` and `/api/log` both carry `"speech": true`. On an installation
with `FIREWALL_USERS` set, a request with no valid session cookie gets
`"speech": false` and every word nulled out — `call.transcript`,
`status.text`, `reopenings[].text`, and `text` on every `radio` and `feed` row.
Nothing else changes: same rows, same ids, same clip urls, same timings, same
`hold_seconds`. A client that ignores `speech` still renders and still plays;
one that reads it can say "locked" rather than "(no speech)", which is the
difference between a row nobody may read and a transmission where nothing was
said.

`null`, not `""`, and the distinction is the point: `""` already means the
radio keyed up and no words came out of it.

`/api/clips`, `/api/transcribe` and `/api/label` answer `401` instead of
redacting — they are transcripts end to end, and with the words gone there is
nothing left of them. `GET /review` redirects to `/login?next=/review`, because
a page can be sent somewhere a `fetch` cannot.

```
POST /api/login    {"username": "alex", "password": "..."}
                   -> 200 + Set-Cookie, or 401, or 429 after ten wrong tries
POST /api/logout   -> 200, cookie cleared
GET  /logout       -> 302 to /, cookie cleared
GET  /login        -> the form; 302 to / when no accounts are configured
GET  /api/session  {"required": true, "user": "alex", "speech": true,
                    "origins": ["https://tracker.example.com"]}
```

`/api/session` is what a front end reads to render the state honestly: whether
there is a gate at all, who is through it, and therefore what an empty
transcript means. `required: false` is an installation with no accounts, where
`user` is `null` for everybody and everything is readable anyway. `origins` is
the `allow_origins` list, published so the sign-in form can tell a return
address it should honour from one somebody put in a link: `?next=` takes a path
on this server always, and a whole URL only when its origin is on that list.

The audio is never gated. See
[Sharing it, and the login](#sharing-it-and-the-login) for why that is the line.

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

**A crash is five call types, not one.** Purdue runs thousands of rental
e-scooters and bicycles, and a scooter laid down at Third and Russell is not a
two-car wreck on Sagamore: different trucks, different scene, different thing to
see on a wall. Both used to read `Vehicle Crash`, because the generic pattern
matched the word "crash" and threw away the vehicle it happened on. There are now
`Scooter Crash`, `Motorcycle Crash`, `Bicycle Crash`, `Pedestrian Struck` and
`Vehicle Crash`, in that order — first match wins, so the specific ones sit above
the generic one, and above `Fall / Lift Assist` as well, because somebody who came
off a scooter did fall and the fall is the least useful true thing to say about it.

Each of the four specific ones needs **two** words said, in either order: the
vehicle and something crash-shaped about it. That is a safety rule rather than a
nicety — a call type on its own is enough to open a call, so a bare noun would put
a card on the wall for "there's a scooter blocking the bay door". `scooter versus
vehicle`, `PI accident involving a scooter` and `fell off a scooter` all qualify;
`bike` in front of `path`, `lane`, `rack` or `trail` is a place and never a
crash. The generic line admits a bare `accident` only with `vehicle`, `traffic`,
`auto`, `motor vehicle` or `PI` in front of it, for the same reason. Unlike the
keyup thresholds in `segments.py`, none of this is measured against the archive —
there is no crash in it — so it is written to fail toward the old behaviour: a
phrase these do not recognise still lands on `Vehicle Crash`.

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
Third and Russell           Scooter Crash           scene ~157s
Stadium and Martin Jischke  Bicycle Crash           scene ~184s
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
fails, so turning it on costs nothing but the key. The prompt carries the local
place names, so it can undo the mishearings below rather than just parse around
them.

---

## Transcription

Trunked radio is 8kHz, compressed twice, and clipped at both ends of every
transmission. Whisper's errors on it are phonetic, not random, and the words it
invents are the local ones it has never heard:

```
Purdue      -> "pretty" / "Padufa"      Earhart  -> "your heart" / "IHUT"
Medic 16    -> "firemenic 16"           Sagamore -> "Sagamo"
```

Measured on 20 clips -- five dispatches spoken by four voices, band-limited to
300-3400Hz and mixed with noise at clean/15dB/8dB/3dB SNR -- scoring the place
and unit names above, whether the call reaches the screen at all, and whether
the address it lands on is the right one:

| configuration | names heard | reaches screen | right address | s/clip |
|---|---|---|---|---|
| `base.en`, VAD on, no vocabulary | 10/56 (18%) | 14/20 | 2/20 | 0.7 |
| `base.en` + vocabulary | 29/56 (52%) | | | 0.5 |
| `small.en`, no vocabulary | 13/56 (23%) | | | 1.4 |
| `small.en` + vocabulary | 37/56 (66%) | | | 1.3 |
| **`small.en` + vocabulary, VAD off** | **45/56 (80%)** | **20/20** | **14/20** | 1.2 |
| `medium.en` + vocabulary | 31/56 (55%) | | | 3.5 |

Chatter was promoted to a dispatch 0 times out of 8 in every configuration, so
none of this made the display trigger-happy. Four things earn their place:

1. **`small.en`, not `base.en`** (`FIREWALL_WHISPER_MODEL`). `medium.en` is
   *worse* here at 3x the cost -- it returned an empty transcript on one clip --
   so `small.en` is both the floor and the ceiling worth paying for on CPU.
2. **The vocabulary bias.** Every clip is decoded with the names in
   `places.py` passed as whisper `hotwords`. On its own that is the single
   largest win, 13/56 -> 37/56. Extend it with `FIREWALL_WHISPER_VOCAB`. Only
   the first ~220 tokens survive the prompt cap, so length is the constraint
   that matters -- reordering the same list moved the score by one clip, adding
   a hall you keep hearing is what helps.
3. **The VAD is off.** This is not the obvious setting. Silero decides a noisy
   transmission stops being speech partway through and truncates the rest, which
   is how a dispatch comes out as `"...being around the Earhart residen"`.
   Turning it off recovered 8 more names at identical latency and cost nothing:
   noise-only keyups still transcribe to nothing, because whisper's own
   `no_speech_threshold` already drops them. `FIREWALL_WHISPER_VAD=true` puts it
   back for long recordings that contain real silence.
4. **`FIREWALL_USE_LLM_PARSER=true`.** The remaining gap is not the recogniser.
   In 9 of the 11 clips that still land on the wrong address, the building name
   *is* in the transcript -- the sentence around it is too mangled for a regex.
   The parser prompt names the same places, so it recovers those.

**Replay, do not guess.** Audio and incidents land in
`~/Documents/firewall-data/`, outside the repo, because the recordings are not
yours to redistribute. Let it collect real calls, then tune against them:

```
firewall --transcribe clips/1787186926-2105.mp3
FIREWALL_WHISPER_MODEL=base.en firewall --transcribe clips/1787186926-2105.mp3
```

It prints the transcript, the parse, and how long the model took. The numbers
above come from synthetic speech pushed through a radio-shaped filter, not from
P25 vocoder audio: trust the ranking, re-measure the percentages on your own
clips.

Whichever model you run, unit numbers come back both ways -- "Engine 2" on one
call and "engine two" on the next -- so the parser folds spoken numbers to
digits before it looks for units. It also tolerates the comma whisper inserts
where the dispatcher paused ("respond to 340, Sagamore Parkway West"), and falls
back to matching any known local place named anywhere in the transcript when the
sentence structure is gone entirely.

## Ground truth

Everything above was measured on synthetic speech pushed through a radio-shaped
filter, because there was no labelled real audio to measure against. The fix is
not clever: listen to the clips and type what was actually said.

```
firewall --label clips/          # plays each clip, you type what you heard
firewall --score                 # word error rate under the current settings
```

The comfortable way to do it is the review page the server already serves at
**`/review`**, alongside the display:

```
firewall --source broadcastify --open        # display on /, review on /review
```

Every saved clip is listed newest first, dispatches marked, labelled ones
ticked. Selecting one plays it immediately, shows what the machine heard with
the words your truth does not contain called out in clay, and puts the cursor
in the box. `enter` saves and moves to the next clip, `shift enter` is a
newline, `esc` leaves the box and then `space` replays, `j`/`k` move, `n` marks
no speech. There is a 0.75x button and a loop button, which is what garbled
transmissions actually need, and a re-transcribe button that runs the current
settings against that one clip. You should never need the mouse.

`firewall --label PATH` is the same job from the terminal if you prefer it:
plays each unlabelled clip through `afplay` and reads what you type (`r`
replays, `s` skips, `q` quits). Either way, type what was *said*, including
the parts the radio ate; blank means no speech, which is a useful label in its
own right.

`--score` then transcribes every labelled clip with whatever settings are
currently in force and prints the word error rate, so a change to the model,
the vocabulary or the VAD can be judged on your audio rather than mine:

```
firewall --score
FIREWALL_WHISPER_MODEL=base.en firewall --score
FIREWALL_WHISPER_VAD=true firewall --score
```

The corpus is JSONL, one `{"audio": ..., "text": ...}` per line, so it outlives
any change to this program and can be edited in any text editor.

---

## Incidents

A dispatch is not one transmission, it is a conversation, and the display only
ever shows the first line of it. Everything after -- the units acknowledging,
the arrival, what they found -- was being transcribed and dropped.

`FIREWALL_INCIDENT_DIR` (default `~/Documents/firewall-data/incidents`) keeps it. An incident opens on a
dispatch, collects every transmission on that department afterwards with its
audio, and closes when the radio says it is over. The closers are `code 4`,
`in service`, `clear the scene`, `cancel`, `disregard` and `returning to
quarters` -- plus `and service`, which is not a typo but how whisper hears
"in service" off a radio. Failing all of those it closes after
`FIREWALL_INCIDENT_GAP` seconds of quiet.

```
firewall --incidents                  # newest first
firewall --replay                     # the most recent, as a timeline
firewall --replay <id> --play         # and play each transmission
```

```
  1787195476-purdue-fd-medical-alcohol
  2026-08-19 23:11:16  Purdue FD  Medical: Alcohol @ Honors College  ['M16']
  closed after 424s

  >> +   0s  Purdue Fire, Medic 16, stand by for possible alcohol poisoning ...
  >> +  31s  Purdue Fire, Medic 16, Unit 100, possible alcohol poisoning at Honors College.
     + 187s  Sixteens, en route, on herself.
     + 237s  Dispatch, medic 17 en route.
     + 424s  Medic 16 and service returning.
```

A pre-alert and the dispatch proper are one incident, not two. Any dispatch
arriving while the department's conversation is still live refines the running
call rather than forking a new one, which is why the address above is the
Honors College one and not the pre-alert's. Forking needs actual evidence of a
second call -- dispatcher phrasing *plus* a different call type or a different
place -- because without that test, ordinary chatter that happened to parse as
a dispatch could retitle a working fire under the reader. Refinement is
one-way: a type is written once, and an address is only replaced by the same
place said more precisely.

---

**Broadcastify's own transcripts are not an option here.** Broadcastify Calls
transcription exists, but it is a technology preview scoped to fire dispatch in
the Dallas-Fort Worth metro, and the Live Calls records for system 9099 carry no
transcript field -- just `groupId`, `ts`, `url`, `duration`, `descr`, `freq` and
friends. Nothing to read even if you paid for it.

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

Sharing it with friends is where the second rule gets tested, which is what
`FIREWALL_USERS` is for: see [Sharing it, and the login](#sharing-it-and-the-login).
A login is not a legal position. It is the difference between a handful of
people you know reading a transcript and it sitting on an open port for
anything that crawls one.

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
  sources.py      broadcastify and trunk sources
  core.py         shared state, whisper, publish, source health
  parse.py        transcript to structured call
  places.py       local names, for the recogniser and the parser
  incidents.py    grouping, recording and replaying whole incidents
  push.py         posting a snapshot out to a hosted tracker
  auth.py         who may read the transcripts, and the redaction itself
  login.html      the sign-in form, served at /login
  review.html     the labelling UI, served at /review
  corpus.py       hand-typed truth, and the score against it
  display.html    the screen, standalone and editable live
web/              the tracker: a static site, hosted or served at /tracker
  api/            the hosted half: push in, log and current out, one key
  vercel.json     the SPA rewrite, and the 24h retention a deployment gets
  .env.example    VITE_API_BASE and VITE_RETAIN_HOURS
  dist/           the build. Not committed; run-tracker.sh makes one
  src/App.tsx     layout, polling, filter row
  src/lib/        the wire contract the panels share, and purdue.ts: the
                  department and its roster, with the sources for both
  src/components/ TypeChart, CallTracker, Transcript, Roster, and shadcn/ui
  src/assets/units/  empty, and read the README in it before filling it:
                  a photograph named for a unit overrides the credited one
```

## License

MIT
