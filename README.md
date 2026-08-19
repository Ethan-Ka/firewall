# firewall

An ambient display that tells you what call the fire truck driving past your house
is running. Like a "plane overhead" display, but for the fire department.

One process. One command. Listen → transcribe → parse → screen.

```
   audio in  ──▶  whisper  ──▶  parse  ──▶  in-memory call  ──▶  display
  (mock | broadcastify | trunk-recorder)
```

## Quick start

```bash
pip install -e .
firewall --open
```

That runs the whole system with synthetic dispatches every 45 seconds — no
credentials, no audio, no hardware. The display opens in your browser.

## Working on the design

The display re-reads `firewall/display.html` from disk on every request, so edit
it and hit refresh. No restart, no build step.

You can also open `firewall/display.html` directly as a file with nothing running
at all — it tries the API, fails, and drops into demo mode:

- **SPACE** cycles all six states (fire / medical / crash / alarm / hazmat / idle)
- Every visual knob is a CSS custom property in `:root`
- Per-category accent colors are the `CATEGORY_STYLES` array
- `HOME_UNITS` is a regex of the rigs from stations that route past your house —
  those chips get the accent color

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
| `mock` *(default)* | nothing | — | design + pipeline work |
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
them against the docs — everything Broadcastify-specific is confined to
`_bcfy_fetch()` and `_bcfy_normalize()` in `sources.py`, and that is the only
place you should need to edit.

The open question is **latency**. Dispatch goes out 30–90 seconds before the truck
reaches you; Broadcastify adds ingest + upload lag. Measure it before deciding you
need the SDR.

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
(~$0.0002/call, `pip install -e ".[llm]"`). It falls back to regex automatically if
the call fails.

## Legal

Receiving unencrypted public-safety radio is legal federally — ECPA (18 U.S.C.
§2511) exempts transmissions readily accessible to the general public. Indiana has
**IC 35-44.1-2-7**; read it yourself. Two rules regardless: don't rebroadcast or
republish the audio or transcripts, and don't put the screen where it's readable
from the street — dispatch occasionally reads patient details.

## Not built yet

- **Geo-filter** — geocode `address`, only surface calls whose station→incident
  path actually passes near you
- **Siren detect** — USB mic + FFT on the 600–1500 Hz wail sweep to confirm the
  real pass-by

## License

MIT
