# The hosted half, which is now the whole of it

The collector used to run on a machine at home and push what it heard here. It
does not any more: something calls `/api/collect`, which polls Broadcastify,
transcribes what it finds, parses it with the same parser the CLI uses, and
writes the result where the page reads it. Nothing outside this deployment has
to be running -- though on the Hobby plan something outside it has to be doing
the calling, for which see **Driving the collector** below.

| Route | What it does | Called every |
| --- | --- | --- |
| `/api/collect` | **the radio** -- poll, transcribe, parse, store | ideally 60s; see below |
| `/api/current` | the radio as of the last collect | 2s |
| `/api/log` | the last day of calls | 10s |
| `/api/history` | every call kept, back to `ARCHIVE_DAYS` | 5 min |
| `/api/radio` | what was said between two instants | on demand |
| `/api/push` | (write) the old topology, still accepted | — |

## What actually moved, and what could not

The collector is not reimplemented here. `api/_collector.py` loads state into
`firewall/core.py`'s own globals, runs the same `core.publish()` path the CLI
runs, and writes the state back. The parser, the gazetteer, the keyup splitter,
the call state machine and the status vocabulary are all the code that was
already there and already scored against the corpus. What changed is where the
variables live between one transmission and the next, which is Redis.

Two things could not come across, and each is replaced at the narrowest seam
available rather than worked around:

**The decode.** `faster-whisper`'s `small.en` is 480MB of weights and seconds of
CPU per clip -- over the bundle limit, and re-fetched on every cold start.
`api/_transcribe.py` gets whisper-shaped segments from an HTTP service instead,
and `core.spans_from()` takes it from there. `segments.split()` was already
documented to accept "anything iterable of objects carrying .start, .end and
.text, optionally .words", so the substitution lands one function earlier than
any judgement about the words is made.

**The audio.** There is nowhere to hold a rolling window of mp3s and nothing to
serve them from. `core._tape_put()` grew a `remote` argument: the row carries an
absolute URL at the source instead of bytes. Broadcastify's clip URLs need no
credential, which is the fact that makes this possible at all.

## Why the collector stays for most of a minute

A cron cannot fire more than once a minute anywhere, and this system publishes a call
seconds after the transmission ends. A function that polled once and returned
would be looking at the radio for one instant in sixty and would miss most of a
shift. So one invocation polls for `FIREWALL_COLLECT_SECONDS` on the same
interval the CLI uses, and the cron's job is only to make sure another one is
along behind it.

It ends by choosing to, not by being killed. A function stopped at its
`maxDuration` never writes its state back, and every record it transcribed on
the way is paid for and lost -- so the budget sits well under the limit.

Two overlapping runs are safe. They poll the same talkgroup twice, which costs a
few records; they cannot corrupt anything, because state is written whole at the
end of a run and the loser of the race is simply overwritten. Losing a minute of
cursor is cheaper than the locking that would prevent it.

## The snapshot and the archive

Two different things, deliberately kept apart.

The **snapshot** is one key holding one JSON blob, written whole at the end of
every collect and expiring a day after the last one. That is the right shape for
"what is happening": a copy of a moment, replaced rather than accumulated, and
gone when there is nothing behind it. `/api/current` and `/api/log` are read off
it, which is why they are cheap enough to poll.

The **archive** is the calls themselves and the transmissions behind them, kept
past the day they happened, because every question worth asking of a fire
department needs more than today -- which hour it actually runs, whether the
weekend is quiet, what a normal week looks like. It is a hash keyed by id with a
sorted set on the timestamp beside it, so a push writes only what changed and a
read asks for a span by time and pays for that span.

The audio is not kept and never crosses. The clips live in the memory of the
process that recorded them, so an archived clip url is a link that outlives what
it points at; rows are stored with a null url, which the transcript has always
drawn as a visibly unavailable play button. The words are kept. Unless they are
gated -- if the words are being withheld the tape is not archived at all, rather
than archived empty, so turning the gate off later starts a real transcript
instead of leaving a month of blank rows behind it.

## Driving the collector

There is no cron in `vercel.json`, and that is a choice about where the words
come from rather than an oversight. The collector here cannot decode audio by
itself -- `small.en` is 480MB against a bundle limit half that -- so every clip
it fetches has to be sent to a paid transcription service, which is what
`FIREWALL_STT_KEY` buys. A machine at home already has the weights, has already
paid nothing per clip for two years of `faster-whisper`, and can push what it
hears to this deployment over ordinary outbound HTTPS. So it does.

That is the push path, and it is the one this deployment runs on:
`FIREWALL_PUSH_URL` and `FIREWALL_PUSH_TOKEN` in the collector machine's `.env`,
the same token in this project's environment, and `/api/push` writes the
snapshot and the archive that every reader here was built against. See
`firewall/push.py`. The page is a copy, always a few seconds behind, and always
stamped with how far -- which is the honest version of what it is.

`/api/collect` is still here and still works. Set `FIREWALL_STT_KEY` and it
collects; it is an ordinary endpoint with no auth on it and does not care what
invoked it, so anything on a timer drives it:

    while true; do curl -s https://<deployment>/api/collect >/dev/null; sleep 55; done

Overlapping calls are safe, as its module docstring explains. What it will not
do is talk over a working push: a run that ends at the configuration checks
writes its reason onto the page only when nothing else is keeping the page
current, because a collector that cannot start has nothing to say about a radio
that is plainly running.

Restoring the cron is one block in `vercel.json` and a key in the environment.
Note what the schedule has to be if you do: Hobby rejects anything more frequent
than daily at build time, failing the whole deployment rather than quietly
downgrading the cron, so `0 12 * * *` is the only one that builds -- one
fifty-second window a day, which the page will draw and stamp and which is not a
live tracker. Pro takes `* * * * *` and works as designed.

## What to set

Two of these are project settings rather than environment variables, and they
are first because nothing else matters until they are right. Both are wrong by
default on a project that predates this layout, and both fail quietly.

| Setting | Value | Why |
| --- | --- | --- |
| **Root Directory** | *empty* -- the repository root | The deployment used to be the tracker alone, so this was `web`. It is not any more: `vercel.json`, `api/` and the `firewall` package the collector imports all sit at the root, and every one of them is invisible from inside `web/`. Left at `web`, the build finds no config, infers no build, and publishes a deployment with no static output and no functions -- a 404 on every path, reported as a successful build. |
| **Framework Preset** | Other | The generic Python preset matches on filename alone -- `requirements.txt`, `pyproject.toml` or `Pipfile` in the root -- and is then *saved to the project*, where it outlives the file that caused it. It carries `useRuntime: "@vercel/python"`, so it forces the Python builder whether or not a `.py` file is anywhere near, and that builder stops the build hunting for a WSGI `app` this repository does not have. Worse if it finds one: a Python preset takes precedence over file-based functions, and everything under `api/` stops being built at all. Deleting the file does not clear the setting. |

One habit to avoid, because it hides both of the settings above. **Redeploy**
rebuilds the commit belonging to the deployment it was invoked on, not the tip
of the branch -- and a redeploy of a redeploy keeps inheriting the same commit,
however many times it is repeated. A chain of them can sit at a commit from
before this directory existed while the deployment list says `main` against
every entry, because that commit is still an ancestor of `main`. What it builds
is the repository as it was: no `vercel.json` at the root, so no build command,
so an empty output published in about fifty milliseconds and a 404 on every
path, functions included -- reported as `Ready`.

The tell is in the build log's first line, which names the commit, and its
`Build Completed in /vercel/output` time. A build that did the work takes tens
of seconds and lists what it produced. To deploy the current branch, push to it
and let the git integration open a new deployment, or promote a deployment that
already names the commit you want.

Then the environment variables:

| Variable | What it is |
| --- | --- |
| `BCFY_API_KEY` / `BCFY_KEY_ID` / `BCFY_APP_ID` | The Broadcastify credentials. All three: auth is a short-lived HS256 JWT signed with the key, carrying the Key ID as `kid` and the App ID as `iss`. Without them the cron refuses to run. |
| `BCFY_USERNAME` / `BCFY_PASSWORD` | Live Calls needs an authenticated user in the JWT for anything but a public playlist. A free account is enough. Exchanged for a user token once an hour and cached in Redis, so this is not a login per poll. |
| `BCFY_SYSTEM_ID` / `BCFY_TALKGROUPS` | What to listen to. **This list is the bill** -- billing is per record read, and every id added downloads and transcribes another department's whole day, twice: once from Broadcastify, once from the transcription service. |
| `FIREWALL_STT_KEY` / `FIREWALL_STT_URL` / `FIREWALL_STT_MODEL` | Where the words come from. Any OpenAI-compatible `/audio/transcriptions` endpoint. Without a key the collector refuses to run rather than filing silent calls. |
| `FIREWALL_COLLECT_SECONDS` | How long one invocation listens. Must stay under the function's `maxDuration`; see above for why. |
| `FIREWALL_PUSH_TOKEN` | Only for the old push topology. Leave unset and `/api/push` refuses every write, which is the right answer when nothing should be writing. |
| `KV_REST_API_URL` / `KV_REST_API_TOKEN` | Set for you by Vercel's Redis integration (Storage → Create → Upstash for Redis → connect to this project). `UPSTASH_REDIS_REST_*` is read as well, for a database created directly with Upstash. |
| `RETAIN_HOURS` | How long a pushed snapshot lives. Defaults to 24. Also the ceiling on `/api/log?hours=`. |
| `ARCHIVE_DAYS` | How far back the calls and transcripts are kept. Defaults to 30, and the ceiling on `/api/history?days=`. Unlike the snapshot this is enforced by pruning rather than by expiry: an archive that vanishes because nobody pushed for a month is not an archive. |
| `VITE_RETAIN_HOURS` | The tracker's side of the same number, in hours -- 720 for a 30-day archive. Read at BUILD time, so changing it takes a redeploy. It decides which window chips the page offers, and a "30 days" chip over a week of data is worse than no chip at all: somebody reads an empty month rather than a full week and concludes the department was quiet. |
| `VITE_API_BASE` | Leave unset for a deployment fed by push -- empty means "this origin", and the page reads the functions sitting next to it. Set it only when this tracker reads a firewall server directly over a tunnel. |

All of these live in the repository's `.env.vercel`, which is git-ignored and is
imported whole: Project → Settings → Environment Variables → Import .env. Select
Production and Preview both, or a preview deployment answers every push with a
503 and the difference is invisible until somebody opens the preview URL and
finds it empty.

### Still want to run the collector at home?

The push topology still works and `/api/push` still accepts writes, which is
what makes it possible to run both for a while, or to fall back. Set
`FIREWALL_PUSH_TOKEN` here and on that machine, leave `BCFY_API_KEY` unset here
so the cron refuses to run, and it behaves exactly as it did before:

```
FIREWALL_PUSH_URL=https://your-project.vercel.app/api/push
FIREWALL_PUSH_TOKEN=<the same secret>
FIREWALL_PUBLIC_URL=            # optional; see below
FIREWALL_PUSH_FULL_SECONDS=300  # optional; see below
```

Running both at once is not harmful but is pointless: two writers, one snapshot
key, and you pay Broadcastify twice for the same records.

## What a push carries

`calls` and `feed` are the snapshot: the whole window, every time, because that
is what "replaced whole" means.

`archive` and `archive_feed` are what to write down, and they are deltas -- the
calls whose contents have changed since the last push, and the transmissions
that are new. In the steady state both are empty, so a push that changes nothing
writes nothing. Every `FIREWALL_PUSH_FULL_SECONDS` the sender sets `full` and
sends the whole window instead, which re-states everything the archive should be
holding and triggers a prune. That is what makes a lost write or a replaced
database heal itself rather than leaving a permanent hole.

A sender too old to send either field falls back to archiving the whole window
on every push. Correct, and merely wasteful, which is what makes it safe to
deploy this before updating the machine with the radio on it.

`corrections` is the one thing that travels backwards. Everything above is a
record on its way to being written down for the first time; a correction is
better words for a transmission this archive has been holding for days --
somebody listened to the clip in the review UI and typed what was actually said.
By then the audio is long gone from the sender's memory, so it goes as a patch,
`{id, text}`, and is merged into whatever the archive already has under that id:
the timing, the department and the dispatch flag are kept, `text` becomes the
human's version and `machine` keeps the recogniser's, so a transcript that has
been corrected can still be told from one that never needed it. An id the
archive does not hold is skipped rather than created -- a row with no timestamp
does not belong in an index that sorts on timestamps. The count comes back as
`corrected`.

If the archive write fails the push still returns 200, with `archive_error` in
the body. The live copy landed and the page is current; only the history behind
it did not, and calling that a failed push would have the sender print an outage
over a working tracker. `firewall --check` prints that line, and so does the
push loop, on the transition.

## Audio

The clips live in the memory of the process that recorded them, and no push
carries them: a day of trunked radio is gigabytes, and Redis is not where it
would go. So a transmission's `url` is rewritten on the way out.

Set `FIREWALL_PUBLIC_URL` to the public origin of the firewall server -- if it
has one -- and the play buttons point back at it. Leave it unset, which is the
normal case for a machine behind a router, and every row is pushed with a null
url: the transcript still reads, and the play buttons are visibly disabled
rather than silently broken.

## Transcripts

The tape is archived along with the calls, and a call's detail row reads its own
stretch of it back out of `/api/radio` when you open it. Asked for by time
rather than by call id, because that is how the tape is true: a transmission is
stored because it was heard, not because a parser decided which call it belonged
to. On a department with two calls running at once you get both, which is what
the radio did.

If the server has `FIREWALL_USERS` set, the words are stripped before they are
pushed. That is not a limitation working around the lack of a session here; it
is the same decision the server already made, honoured at the one point where
it would otherwise be undone -- a public URL is the last place to quietly
un-gate something somebody chose to gate. `FIREWALL_PUSH_SPEECH=1` overrides it
for a deployment that is not public.
