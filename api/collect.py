"""/api/collect -- the radio, run by a clock instead of by a `while True`.

Vercel's cron calls this. It stays for most of a minute, polling Broadcastify
on the same interval the CLI uses, transcribing what it finds and writing the
result where the page reads it. Then it exits. Between runs, everything it
knows is in Redis.

Why it stays rather than polling once and returning: this system publishes a
call seconds after the transmission ends, and a cron cannot fire more often
than once a minute even where minute crons are allowed. A function that looked
at the radio for one instant in every sixty would miss most of a shift. So the
budget below is nearly the whole minute, and the schedule's job is only to make
sure another one is along behind it.

That schedule is currently `0 12 * * *` -- once a day -- because Vercel's Hobby
plan rejects any cron that would run more than daily, and a deployment that
will not build collects nothing at all. One run a day is not a live tracker and
is not pretending to be: it is one fifty-second window, and the page will show
that window and the stamp on it. Anything that calls this endpoint drives it
just as well as the cron does, on whatever interval the caller likes, which is
how it gets run properly without the plan -- see api/README.md.

Overlap is safe and expected. Two of these running at once poll the same
talkgroup twice, which costs a few records; they cannot corrupt anything,
because the state is written whole at the end of a run and the loser of the race
is simply overwritten by the winner. Losing a minute of cursor is cheaper than
the locking that would prevent it.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import BaseHTTPRequestHandler

import _collector
import _redis
import _transcribe

# How long one invocation spends listening. Under the function's own maxDuration
# (60s in vercel.json) with room to spare, because the run has to END on its own
# terms -- a function that is killed at the limit never writes its state back,
# and every record it transcribed on the way is paid for and lost.
BUDGET = int(os.environ.get("FIREWALL_COLLECT_SECONDS") or 50)

# Pruning walks an index by score. Worth doing occasionally and not every
# minute, so it happens on the runs that land near the top of the hour.
PRUNE_EVERY = 3600


def run():
    """One invocation's worth of radio. Returns what to say about it."""
    started = time.time()
    if not _redis.configured():
        return 503, {"error": "no store is connected to this deployment"}

    # Both of these end the run before tick(), and both are written to the
    # snapshot on the way out rather than only returned here. A 503 is read by
    # the cron and by nobody else; the page is where somebody is waiting for
    # calls that are never coming, so the page is where the reason goes.
    cfg = _collector.settings()
    if not cfg.get("bcfy_api_key"):
        return 503, {"error": _collector.fault(
            "BCFY_API_KEY is not set, so there is no radio to listen to")}
    if not _transcribe.configured(cfg):
        return 503, {"error": _collector.fault(
            "FIREWALL_STT_KEY is not set, so audio can be fetched but not "
            "transcribed")}

    out = _collector.tick(cfg, BUDGET)
    # Rendered even when the poll failed. A run that could not reach
    # Broadcastify still knows what the calls looked like a minute ago, and the
    # page saying so with a stale stamp beats it saying nothing at all.
    out.update(_collector.render(cfg, error=out.get("error")))

    last = _redis.get_json("firewall:pruned") or {}
    if time.time() - float(last.get("at") or 0) > PRUNE_EVERY:
        try:
            out["pruned"] = _collector.prune_all()
            _redis.set_json("firewall:pruned", {"at": time.time()})
        except Exception as e:
            out["prune_error"] = f"{type(e).__name__}: {e}"

    out["seconds"] = round(time.time() - started, 1)
    return 200, out


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Vercel's cron signs its calls; a deployment can also be poked by hand
        # while it is being set up. Both are allowed, and neither is anonymous
        # write access to anything -- the worst a stranger can do by calling
        # this is make it poll the radio, which is what it does anyway.
        try:
            code, body = run()
        except Exception as e:
            traceback.print_exc()
            code, body = 500, {"error": f"{type(e).__name__}: {e}"}
        blob = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)
