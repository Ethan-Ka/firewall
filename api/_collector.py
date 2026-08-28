"""The collector, as something that stops and starts.

Every other part of this program assumes a process that is always running. The
tape is a deque in memory, the calls are a list beside it, and the cursor into
Broadcastify is a local variable in a `while True`. That is the right shape for
a thing listening to a scanner and it is not available here: a function runs for
a few minutes when a cron tells it to and then it is gone, along with everything
it was holding.

So this file is the difference between those two, and it is deliberately only
that. It does not re-implement the collector -- it lends the collector a memory.
Load the state into core's globals, run the same publish path the CLI runs, put
the state back. The parser, the gazetteer, the keyup splitter, the call state
machine and the status vocabulary are all the code that was already there and
already scored against the corpus; what changed is where the variables live
between one transmission and the next.

Two things genuinely could not come across, and both are replaced at the
narrowest seam available:

  The decode. faster-whisper's `small.en` is 480MB of weights and seconds of CPU
  per clip. _transcribe.py gets whisper-shaped segments over HTTP instead, and
  core.spans_from() takes it from there, so every judgement made about the words
  is unchanged.

  The audio. There is nowhere to keep a rolling window of mp3s and nothing to
  serve them from. core._tape_put grew a `remote` argument: the row carries an
  absolute URL at the source instead of bytes, and the display cannot tell the
  difference. Broadcastify's clip URLs need no credential, which is what makes
  this possible at all.
"""
import json
import sys
import time

import _redis
import _transcribe
from firewall import bcfy_auth, config, core, segments as _segments, sources

# Where the collector's memory lives between invocations. Separate from the
# snapshot the readers poll: that one is a rendering, replaced whole and
# expendable, and this is the state that renders it.
STATE_KEY = "firewall:collector"

# Past this with nothing written, the state is not worth resuming -- every call
# in it has aged out of the display window and the cursor is far enough behind
# to be useless. Expiring it is how a deployment that was switched off for a
# fortnight comes back clean rather than replaying a stale afternoon.
STATE_TTL = 6 * 3600

# What the readers already expect, mirrored from api/_store.js rather than
# imported, because one is Python and the other is JavaScript. They have to
# agree; the tests in this repo are what says they do.
SNAPSHOT_KEY = "firewall:snapshot"


def settings():
    """cfg, from the environment and nothing else.

    config.load() reads .env and config.json when they exist and falls back to
    DEFAULTS and the real environment when they do not, which on a serverless
    function is always. So this is the same configuration the CLI computes, by
    the same code, minus the two files that are not there.
    """
    cfg = config.load()
    # Where the words come from, which is the one setting with no local
    # equivalent -- the CLI names a model it downloads, and this names a
    # service it calls.
    import os
    cfg["stt_url"] = os.environ.get("FIREWALL_STT_URL") or _transcribe.DEFAULT_URL
    cfg["stt_key"] = os.environ.get("FIREWALL_STT_KEY")
    cfg["stt_model"] = os.environ.get("FIREWALL_STT_MODEL") or _transcribe.DEFAULT_MODEL
    # The second decode is a local-only luxury: it re-runs whisper on the same
    # clip with different settings when a dispatch parsed without an address.
    # Here that is a second paid API call on every unlucky dispatch, and the
    # audio is not in this process to re-decode anyway.
    cfg["whisper_retry"] = False
    # Nothing has a disk. incidents.py already handles this being unset -- the
    # calls are kept in the archive api/_store.js writes, which is the thing
    # that outlives a serverless function.
    cfg["incident_dir"] = None
    return cfg


# ------------------------------------------------------------------- memory

def dump():
    """core's globals, as something that can be written down.

    Everything except the audio. `_clips` keeps its bookkeeping and drops the
    bytes, which for a remote clip were never here in the first place; a local
    one cannot occur in this process, and if it somehow did, storing the mp3 in
    Redis is not the answer to it.
    """
    with core._lock:
        return {
            "calls": core._calls,
            "cleared": core._cleared,
            "tape": list(core._tape),
            "clips": {k: {"ident": c["ident"], "mime": c["mime"],
                          "url": c.get("url"), "rows": c["rows"]}
                      for k, c in core._clips.items()},
            "seq": core._tape_seq,
            "health": core._health,
            "purdue": core._state.get("purdue"),
        }


def restore(state, cfg):
    """Put it back. Safe to call with nothing, which is a cold start."""
    state = state or {}
    with core._lock:
        core._calls[:] = state.get("calls") or []
        core._cleared[:] = state.get("cleared") or []
        core._tape.clear()
        core._tape.extend(state.get("tape") or [])
        core._clips.clear()
        for k, c in (state.get("clips") or {}).items():
            core._clips[k] = {"ident": c.get("ident"), "mime": c.get("mime"),
                              "url": c.get("url"), "data": None,
                              "rows": int(c.get("rows") or 0)}
        core._tape_seq = int(state.get("seq") or 0)
        core._health.update(state.get("health") or {})
        core._state["purdue"] = state.get("purdue")
        core._hold_seconds[0] = int(cfg.get("hold_seconds") or 600)
    # A cache keyed on rows that no longer exist is worse than an empty one.
    core._unit_state_cache.clear()


# ---------------------------------------------------------------- the radio
#
# Broadcastify over urllib rather than through sources.py's own fetch, which
# uses requests. The split is deliberate and it is drawn where the risk is: the
# parts that are subtle -- how the JWT is minted, which params bill for what,
# what a record's fields are called -- are imported from sources.py and shared,
# so there is one answer to each of those and the CLI and this file cannot drift
# on them. What is duplicated is the transport, which is a GET with a bearer
# token, and duplicating it is what keeps the deployment free of dependencies
# and keeps this file from editing the code path currently fetching real calls
# off a real scanner.

USER_KEY = "firewall:bcfy-user"


def _get(url, headers, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _user(cfg):
    """uid + token for the Broadcastify user, cached in Redis until it expires.

    Live Calls wants an authenticated user in the JWT for everything but public
    playlists. The CLI caches this in a module global for the life of the
    process; there is no such life here, so the cache is the database -- which
    is the same trade every other piece of state in this file makes, and it
    saves an auth round trip on every invocation for an hour at a time.
    """
    import urllib.parse
    import urllib.request
    if not (cfg.get("bcfy_username") and cfg.get("bcfy_password")):
        return None
    held = _redis.get_json(USER_KEY) or {}
    if held.get("exp", 0) > time.time() + 60:
        return held["uid"], held["token"]
    token = bcfy_auth.mint(cfg["bcfy_api_key"], cfg["bcfy_key_id"],
                           cfg["bcfy_app_id"], ttl=3600)
    body = urllib.parse.urlencode({"username": cfg["bcfy_username"],
                                   "password": cfg["bcfy_password"]}).encode()
    req = urllib.request.Request(
        f"{cfg['bcfy_api_base']}/common/v1/auth", data=body, method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        got = json.loads(r.read().decode())
    held = {"uid": int(got["uid"]), "token": got["token"],
            "exp": float(got.get("exp") or time.time() + 3600)}
    _redis.set_json(USER_KEY, held, ttl=3600)
    return held["uid"], held["token"]


def _jwt(cfg):
    return bcfy_auth.mint(cfg["bcfy_api_key"], cfg["bcfy_key_id"],
                          cfg["bcfy_app_id"], ttl=3600, user=_user(cfg))


def fetch(cfg, pos=None, group=None):
    """One poll. Returns (records, lastPos). Params and auth are sources.py's."""
    import urllib.parse
    params = sources._bcfy_params(cfg, pos, group)
    url = (f"{cfg['bcfy_api_base']}/calls/v1/live/?"
           + urllib.parse.urlencode(params))
    body = json.loads(_get(url, {"Authorization": "Bearer " + _jwt(cfg)}) or b"null")
    if isinstance(body, list):
        return body, None
    calls = body.get("calls") or body.get("data") or []
    return calls, body.get("lastPos")


# ------------------------------------------------------------------ one tick

def _handle(cfg, rec, seen):
    """One Broadcastify record, all the way to the screen.

    The shape of sources._bcfy_handle, with the two substitutions this file
    exists to make. Kept here rather than folded into that function because the
    two differ in every line that touches a disk, and a single function with a
    `hosted` flag through the middle of it would be harder to read than both.
    """
    tg, ts, url = sources._bcfy_normalize(rec)
    key = sources._bcfy_ident(rec, tg, ts)
    if key in seen or tg not in cfg["talkgroups"]:
        return False
    # Anything older than the display hold is invisible the moment it lands, so
    # paying to transcribe it is pure waste.
    if time.time() - ts > cfg["hold_seconds"]:
        seen.append(key)
        return False
    if not url:
        # Indexed before its mp3 was written. NOT marked seen: with a lag the
        # same record comes round again shortly, by which time it may have one.
        return False

    audio = _get(url, {}, timeout=30)
    # Named after the record it came from. Not cosmetic: several hosts serving
    # this API sniff the container from the filename's extension, and one that
    # is handed "clip" with no suffix rejects the upload rather than guessing.
    name = url.rsplit("/", 1)[-1].split("?")[0] or "clip.mp3"
    got = _transcribe.transcribe(audio, cfg, filename=name)
    # Whatever the record's real length is, the spans must not claim audio past
    # it. The decode reports it; without one, the last span's end is the best
    # available answer and split() has already clamped everything to it.
    dur = max([float(s.end) for s in got] or [0.0])
    spans = core.spans_from(got, dur)

    dept = cfg["talkgroups"][tg]
    for sp in spans:
        core.publish(dept, sp["text"], _segments.when(sp, ts), cfg,
                     audio=None, span=sp, remote=url)
    seen.append(key)
    return True


def tick(cfg, budget):
    """Poll every talkgroup in turn until the budget runs out.

    A cron cannot fire more than once a minute, and this system publishes a call
    seconds after it ends, so a function that polled once and exited would be
    looking at the radio for one instant in every sixty. Instead it stays for
    most of the minute, polling on the same interval the CLI uses, and the cron
    is what makes sure another one is along behind it.

    `budget` is seconds. It is checked before each sleep rather than after, so
    the function ends by choosing to rather than by being killed -- which is the
    only way the state below gets written.
    """
    started = time.time()
    interval = max(5, int(cfg["poll_seconds"]))
    groups = sorted(cfg["talkgroups"]) or [None]
    lag = max(0, int(cfg.get("bcfy_lag_seconds") or 0))

    memory = _redis.get_json(STATE_KEY) or {}
    restore(memory.get("core"), cfg)
    # The cursor and the seen-list, which in the CLI are locals in the poll
    # loop. Anchored to the display window on a cold start for the reason
    # sources.broadcastify() anchors them: left at None, every poll re-requests
    # and re-pays for the server's default last-five-minutes.
    pos = {str(g): v for g, v in (memory.get("pos") or {}).items()}
    for g in groups:
        pos.setdefault(str(g), time.time() - cfg["hold_seconds"])
    seen = list(memory.get("seen") or [])

    polls = published = 0
    error = None
    i = int(memory.get("i") or 0)
    while time.time() - started < budget:
        g = groups[i % len(groups)]
        i += 1
        t0 = time.time()
        try:
            calls, last = fetch(cfg, pos[str(g)], g)
            polls += 1
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            core.report_error(e)
            print(f"[bcfy] {error}", file=sys.stderr)
            break
        for rec in calls:
            # Per record: one malformed record must not abort the batch and
            # strand the cursor, which would have every survivor fetched -- and
            # billed -- a second time on the next run.
            try:
                if _handle(cfg, rec, seen):
                    published += 1
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                core.report_error(e)
                print(f"[bcfy] dropped one record ({error})", file=sys.stderr)
        # Forward only, so the cursor can never fall back to the server's
        # rolling default and pay for the same records again.
        pos[str(g)] = (max(pos[str(g)], t0 - lag) if lag
                       else (last or pos[str(g)]))
        if not error:
            core.report_ok()
        left = budget - (time.time() - started)
        if left <= interval:
            break
        time.sleep(interval)

    _redis.set_json(STATE_KEY, {
        "core": dump(),
        "pos": pos,
        # Bounded for the same reason the CLI's deque is: it only has to cover
        # the re-read window, and an unbounded list of every record ever seen
        # would be the one thing here that grows without limit.
        "seen": seen[-2000:],
        "i": i % max(1, len(groups)),
        "at": time.time(),
    }, ttl=STATE_TTL)
    return {"polls": polls, "published": published, "error": error}


# ---------------------------------------------------------------- the readers
#
# What follows writes exactly what api/push.js used to write, into exactly the
# keys api/_store.js reads. That is the contract the whole rearrangement rests
# on: /api/current, /api/log, /api/history and /api/radio were built against a
# machine at home pushing snapshots, and none of them has been told that the
# machine is gone. They cannot tell, because the bytes in Redis are the same.

CALLS_HASH, CALLS_INDEX = "firewall:calls", "firewall:calls:at"
RADIO_HASH, RADIO_INDEX = "firewall:radio", "firewall:radio:at"


def _retain_seconds():
    import os
    return int(round(float(os.environ.get("RETAIN_HOURS") or 24) * 3600))


def _archive_seconds():
    import os
    return int(round(float(os.environ.get("ARCHIVE_DAYS") or 30) * 86400))


def _keepable(record):
    """A record as the archive keeps it -- see the same function in _store.js.

    `live` is a claim about the present tense and a call stored as live stays
    live for ever. `url` is audio: here it is a Broadcastify link, and those do
    not stay good, so it is nulled rather than archived into a month of dead
    play buttons.
    """
    out = {k: v for k, v in record.items() if k != "live"}
    if "url" in record:
        out["url"] = None
    return out


def _archive(hash_key, index_key, stamp, records):
    """Write records into one of the two archives, by id, idempotently."""
    rows = [r for r in records
            if r.get("id") and isinstance(r.get(stamp), (int, float))]
    if not rows:
        return 0
    fields = []
    scores = []
    for r in rows:
        fields += [r["id"], json.dumps(_keepable(r), separators=(",", ":"),
                                       default=str)]
        scores += [str(float(r[stamp])), r["id"]]
    _redis.pipeline([["HSET", hash_key] + fields, ["ZADD", index_key] + scores])
    return len(rows)


def _prune(hash_key, index_key, cutoff):
    """Drop what is past retention. Index first, then the hash it pointed at:
    interrupted between the two, the archive holds a record nothing points at --
    invisible and overwritten next time -- rather than an index pointing at a
    record that is gone, which every read would have to defend against."""
    old = _redis.command("ZRANGEBYSCORE", index_key, "-inf", f"({cutoff}",
                         "LIMIT", 0, 5000) or []
    if not old:
        return 0
    _redis.pipeline([["ZREM", index_key] + list(old),
                     ["HDEL", hash_key] + list(old)])
    return len(old)


def render(cfg, error=None):
    """Write what the page reads: the snapshot, and then the archive behind it.

    In that order and never the other way round. The snapshot is what the screen
    is drawing right now and it is the write that must not be held up; the
    history behind it is a few seconds later either way, and a database having a
    bad moment should cost a deployment its month-old Tuesdays rather than its
    live radio.
    """
    live = core.snapshot()
    log = core.roster(cfg, since=time.time() - _retain_seconds())
    feed = live.get("feed") or []

    snapshot = {
        "calls": log["calls"],
        "feed": feed,
        "logged": log.get("logged", False),
        "speech": True,
        "hold_seconds": cfg.get("hold_seconds", 600),
        "ok": live.get("ok", True) and not error,
        "error": error or live.get("error"),
        # There is no firewall server behind this any more, so there is nowhere
        # to send somebody who wants to sign in. The page says "locked" without
        # offering a door that does not open -- which is what a null has always
        # meant here.
        "login_url": None,
        # Stamped with this deployment's own clock, which is also the clock
        # /api/current measures the age against. Two clocks is how "last heard
        # 4m ago" becomes the difference between two machines rather than an
        # age; there is only one machine now, and this keeps it that way.
        "pushed_at": time.time(),
    }
    _redis.set_json(SNAPSHOT_KEY, snapshot, ttl=_retain_seconds())

    archived = 0
    archive_error = None
    try:
        archived += _archive(CALLS_HASH, CALLS_INDEX, "opened", log["calls"])
        archived += _archive(RADIO_HASH, RADIO_INDEX, "ts", feed)
    except Exception as e:
        archive_error = f"{type(e).__name__}: {e}"
    return {"snapshot": len(log["calls"]), "archived": archived,
            "archive_error": archive_error}


def prune_all():
    """Both archives, past retention. Cheap, and not worth doing every minute."""
    cutoff = time.time() - _archive_seconds()
    return (_prune(CALLS_HASH, CALLS_INDEX, cutoff)
            + _prune(RADIO_HASH, RADIO_INDEX, cutoff))
