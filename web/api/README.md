# The hosted half

Three functions and one key in a Redis. The machine with the radio on it pushes
what it knows to `POST /api/push`; the tracker reads `/api/log` and
`/api/current` out of the store. Nothing here reaches back into a home network,
which is the point -- there is no tunnel to keep up and no port to forward, and
the page still renders when the radio machine is off, stamped with how long ago
it last said anything.

## What to set

On the Vercel project:

| Variable | What it is |
| --- | --- |
| `FIREWALL_PUSH_TOKEN` | A shared secret. `openssl rand -hex 32`. Same value goes in the firewall server's `FIREWALL_PUSH_TOKEN`. Without it `/api/push` refuses every write rather than accepting anonymous ones. |
| `KV_REST_API_URL` / `KV_REST_API_TOKEN` | Set for you by Vercel's Redis integration (Storage → Create → Upstash for Redis → connect to this project). `UPSTASH_REDIS_REST_*` is read as well, for a database created directly with Upstash. |
| `RETAIN_HOURS` | How long a pushed snapshot lives. Defaults to 24. Also the ceiling on `/api/log?hours=`. |

On the machine running `firewall`, in `.env`:

```
FIREWALL_PUSH_URL=https://your-project.vercel.app/api/push
FIREWALL_PUSH_TOKEN=<the same secret>
FIREWALL_PUBLIC_URL=            # optional; see below
```

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

If the server has `FIREWALL_USERS` set, the words are stripped before they are
pushed. That is not a limitation working around the lack of a session here; it
is the same decision the server already made, honoured at the one point where
it would otherwise be undone -- a public URL is the last place to quietly
un-gate something somebody chose to gate. `FIREWALL_PUSH_SPEECH=1` overrides it
for a deployment that is not public.
