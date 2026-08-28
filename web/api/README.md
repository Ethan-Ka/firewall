# The hosted half

Five functions over a Redis. The machine with the radio on it pushes what it
knows to `POST /api/push`; the tracker reads the rest. Nothing here reaches back
into a home network, which is the point -- there is no tunnel to keep up and no
port to forward, and the page still renders when the radio machine is off,
stamped with how long ago it last said anything.

| Route | What it answers | Read every |
| --- | --- | --- |
| `/api/current` | The radio, as of the last push | 2s |
| `/api/log` | The last day of calls | 10s |
| `/api/history` | Every call kept, back to `ARCHIVE_DAYS` | 5 min |
| `/api/radio` | What was said between two instants | on demand |
| `/api/push` | (write) | — |

## The snapshot and the archive

Two different things, deliberately kept apart.

The **snapshot** is one key holding one JSON blob, written whole every few
seconds and expiring a day after the last push. That is the right shape for
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

## What to set

On the Vercel project:

| Variable | What it is |
| --- | --- |
| `FIREWALL_PUSH_TOKEN` | A shared secret. `openssl rand -hex 32`. Same value goes in the firewall server's `FIREWALL_PUSH_TOKEN`. Without it `/api/push` refuses every write rather than accepting anonymous ones. |
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

On the machine running `firewall`, in `.env`:

```
FIREWALL_PUSH_URL=https://your-project.vercel.app/api/push
FIREWALL_PUSH_TOKEN=<the same secret>
FIREWALL_PUBLIC_URL=            # optional; see below
FIREWALL_PUSH_FULL_SECONDS=300  # optional; see below
```

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
