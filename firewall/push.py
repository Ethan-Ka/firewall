"""Send what this machine knows to a hosted tracker, every few seconds.

The tracker in web/ can be deployed somewhere public, and there are two ways to
connect it to the radio. It can read this server directly, which needs this
machine to be reachable from the internet -- a tunnel, kept up, with a
certificate on it. Or this machine can push, which needs nothing of the sort:
outbound HTTPS works from behind any router, there is no port to forward and no
inbound anything to secure. This is the second.

What crosses is a snapshot, whole, every `push_seconds`: the last day of calls,
the tape as it stands, and whether the source is healthy. The far end keeps one
of them at a time under a day's expiry. So the hosted page is always a few
seconds behind and always says by how much, which is the honest version of what
it is -- a copy -- rather than the pretence that a static site in a datacentre
is listening to a scanner.

The far end also keeps an archive that outlives the snapshot -- the calls
themselves, so the hosted page can say which hour this department actually runs
rather than which hour it ran today. That is built from the `archive` list on
each push: the calls whose contents have changed since the last one, which in
the steady state is nothing at all. Every `push_full_seconds` the whole window
goes up instead, flagged `full`, which re-states everything the far end should
be holding and is what makes a lost write or a replaced database heal itself.

Corrections cross too, and they are the one thing here that travels backwards.
The tape is pushed forwards -- a transmission is archived once, under its id,
because what was said was said -- but a truth typed into the review UI is a
better version of something already written down, often days later, long after
the clip itself has been evicted from memory. So it goes as a patch: the row's
id and the words, addressed to a row the far end already holds. See
_corrections below.

Two things deliberately do not cross:

Audio. A day of trunked radio is gigabytes and the clips only exist in this
process's memory. Rows go out with their url rewritten to public_url, if this
machine has a public origin, and nulled if it does not -- which the tracker
already draws as a disabled play button, because a row with no audio is a case
it has always had.

The words, when they are gated. If FIREWALL_USERS is set, somebody decided the
transcripts are not for everybody, and a public URL is the last place to quietly
undo that. Pushed redacted unless push_speech says otherwise, and the hosted
page then reads as locked in exactly the way this server's own pages do.
"""
import hashlib, json, time, urllib.error, urllib.request

from . import auth, core, corpus as _corpus, incidents as _incidents

# One connection's worth of patience. The push is a background nicety and the
# radio does not wait for it: a socket hung on a datacentre that has stopped
# answering must not still be hung when the next tick comes round.
TIMEOUT = 10


def _fingerprints(calls):
    """One short digest per call, keyed by id.

    Compared rather than the calls themselves so that what is held between
    pushes is a few kilobytes of hashes and not a second copy of the log. Sorted
    keys because a dict that serialises in a different order is not a call that
    changed, and would have this re-archive the whole window every ten seconds
    -- which is precisely the cost the delta exists to avoid.
    """
    out = {}
    for c in calls:
        blob = json.dumps(c, sort_keys=True, default=str).encode()
        out[c.get("id")] = hashlib.blake2b(blob, digest_size=8).hexdigest()
    return out


def _payload(cfg, seen=None, heard=None, full=True, fixes=None, sent=None):
    """One snapshot, as the far end wants it. Nothing here touches the network.

    `seen` is the fingerprints of the calls in the last push and `heard` the ids
    of the transmissions in it; together they are what turn the archive into a
    delta. Passing None for either -- which is what `firewall --check` and the
    first tick of the loop do -- sends the lot.

    `fixes` is every hand-typed correction the log knows about and `sent` the
    ones the far end has already been given, and they work the same way. Passing
    None for `fixes` reads them off disk here, which is what --check does;
    the loop reads them itself, so it can do it only when somebody has actually
    typed something.

    Returns (payload, call fingerprints, transmission ids, corrections told),
    the last three being what the caller records once the push has landed.
    """
    hours = float(cfg.get("push_hours") or 24)
    log = core.roster(cfg, since=time.time() - hours * 3600)
    live = core.snapshot()

    payload = {
        "calls": log["calls"],
        "feed": live.get("feed") or [],
        "logged": log.get("logged", False),
        "ok": live.get("ok", True),
        "error": live.get("error"),
        "hold_seconds": cfg.get("hold_seconds", 600),
        "speech": True,
        # Where the hosted page sends somebody who wants the words. Only useful
        # when this server has a public origin at all; without one there is
        # nowhere to send them and the page says "locked" without offering a
        # door that does not open.
        "login_url": (cfg.get("public_url").rstrip("/") + "/login"
                      if cfg.get("public_url") else None),
    }

    if auth.required(cfg) and not cfg.get("push_speech"):
        # Both, because the two lists carry the words in different places: the
        # feed rows and the per-call status lines. Reusing the server's own
        # redaction rather than writing a second one is the point -- a copy
        # would be the thing that quietly forgets a field the day one is added.
        auth.strip_current(payload)
        auth.strip_log(payload)

    base = (cfg.get("public_url") or "").rstrip("/")
    for row in payload["feed"]:
        # Absolute or nothing. A relative /api/clip?id= would resolve against
        # the hosted origin, which has no such route and no such audio, and the
        # player would fail on a click rather than be visibly unavailable.
        row["url"] = base + row["url"] if base and row.get("url") else None

    # What the far end should write down, decided AFTER the redactions above,
    # so a gated transcript is not archived for ever by the one code path that
    # was allowed to skip the gate.
    #
    # The calls go by fingerprint because a call changes -- it gains units, a
    # status, a closing time -- and every version of it has to land on top of
    # the last. The tape goes by id because a transmission does not: what was
    # said was said, so a row the far end has already been told about is one it
    # never needs to hear again, and re-sending the ten-minute window every ten
    # seconds would be the same sentence written down sixty times.
    marks = _fingerprints(payload["calls"])
    # A tape with the words taken out is not worth keeping. Redacted rows carry
    # ids, timings and a dispatch flag and nothing anybody would read, and the
    # archive is written once per id -- so storing them now would not merely
    # waste the space, it would be the reason turning the gate off next month
    # left a month of empty rows behind it. Nothing is archived instead, and the
    # day speech is allowed through is the day the tape starts.
    rows = [] if payload.get("speech") is False else payload["feed"]
    if full or seen is None or heard is None:
        payload["archive"] = payload["calls"]
        payload["archive_feed"] = rows
        payload["full"] = True
    else:
        payload["archive"] = [c for c in payload["calls"]
                              if seen.get(c.get("id")) != marks.get(c.get("id"))]
        payload["archive_feed"] = [r for r in rows if r.get("id") not in heard]
        payload["full"] = False

    # Corrections, and they go the other way to everything above: not a row the
    # far end has never seen, but better words for one it has been holding for
    # days. A truth is typed long after the clip has left the tape, so there is
    # no version of this that rides along on the feed -- it is addressed by the
    # transmission's id and merged into whatever the archive already has under
    # it. See incidents.corrections and _store.js's amend().
    #
    # Gated with the rest of the words, and it has to be said out loud because
    # this one arrives by a different door: a correction is a transcript, so a
    # deployment that is not being told what was said must not be told what was
    # really said either.
    told = {} if payload.get("speech") is False else (
        _incidents.corrections(cfg) if fixes is None else fixes)
    # Re-stated in full on a full push, exactly like the archive above and for
    # the same reason: a write that was lost, or a database that was replaced,
    # heals on the next one rather than leaving the machine's guess up for ever.
    payload["corrections"] = [
        {"id": k, "text": v} for k, v in sorted(told.items())
        if full or sent is None or sent.get(k) != v]
    return payload, marks, {r.get("id") for r in rows}, told


def _post(cfg, payload):
    """Send it. Raises on anything that is not a 2xx; returns the answer.

    The answer is worth reading because the far end has one failure it reports
    inside a success: the live copy was written and the archive behind it was
    not. That is deliberately not an HTTP error -- the tracker is current and
    the push did its main job -- so nothing would ever mention it if this threw
    the body away.
    """
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        cfg["push_url"], data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + str(cfg.get("push_token") or "")})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        try:
            return json.loads(r.read().decode() or "{}")
        except Exception:
            return {}


def push_once(cfg):
    """One push, whole, for `firewall --check`."""
    payload, _, _, _ = _payload(cfg)
    return _post(cfg, payload)


def poll(cfg):
    """Push forever, on the configured interval.

    Failures are reported on the transition rather than on every tick. A hosted
    tracker going unreachable for an hour is one fact, and printing it three
    hundred and sixty times is how a console stops being read -- the same reason
    source health is reported the way it is. Nothing here can stop the radio:
    every failure is caught, because a datacentre having a bad afternoon is not
    a reason for this process to stop listening to a scanner.
    """
    every = max(2, int(cfg.get("push_seconds") or 10))
    full_every = max(every, int(cfg.get("push_full_seconds") or 300))
    # Fingerprints of what the far end has been told, and when it was last told
    # everything. Both live here rather than in module state so that a restart
    # is a full push, which is the safe direction: the far end is written to
    # twice rather than never.
    seen, heard, last_full = None, None, 0.0
    # The corrections the log holds, the ones the far end has been given, and
    # the corpus's stamp at the time the first of those was read. Kept apart
    # because reading them is a walk of every incident.json on disk, and the
    # answer only ever changes when somebody types into the review UI -- so it
    # is read once, and then only when the corpus file itself has moved.
    fixes, told, stamped = None, None, ()
    failing, archiving = None, None
    while True:
        try:
            full = time.time() - last_full >= full_every
            at = _corpus.stamp(cfg)
            if fixes is None or at != stamped:
                fixes, stamped = _incidents.corrections(cfg), at
            payload, marks, ids, now_told = _payload(cfg, seen, heard, full,
                                                     fixes, told)
            # Before the post, because `told` is about to become this, and a
            # push that re-states everything on its five-minute tick is not
            # news. Only a correction the far end has not been given the current
            # words for is worth a line.
            fresh = [c for c in payload["corrections"]
                     if (told or {}).get(c["id"]) != c["text"]]
            answer = _post(cfg, payload)
            # Only after it landed. Recording what was sent by a push that
            # failed would have the next one report no changes and the far end
            # would never hear about those calls again.
            seen, heard, told = marks, ids, now_told
            if fresh:
                print(f"  ·  {len(fresh)} correction(s) published to the "
                      f"hosted tracker")
            if full:
                last_full = time.time()
            if failing:
                print(f"  ·  hosted tracker reachable again "
                      f"({time.strftime('%H:%M:%S')})")
                failing = None
            # Reported on the transition like everything else here, and kept
            # apart from `failing`: a tracker that is live and not archiving is
            # a different sentence from one that is not answering, and the fix
            # for each is somewhere else.
            why = answer.get("archive_error") if isinstance(answer, dict) else None
            if why != archiving:
                if why:
                    print(f"  !  hosted tracker is not keeping history: {why}")
                else:
                    print(f"  ·  hosted tracker is keeping history again "
                          f"({time.strftime('%H:%M:%S')})")
                archiving = why
        except urllib.error.HTTPError as e:
            # The body carries the reason -- a bad token, no store connected --
            # and the status alone would send somebody to the wrong problem.
            try:
                why = e.read().decode()[:200]
            except Exception:
                why = ""
            now = f"HTTP {e.code} {why}".strip()
            if now != failing:
                print(f"  !  hosted tracker refused the push: {now}")
                failing = now
        except Exception as e:                  # network, DNS, timeout, JSON
            now = f"{type(e).__name__}: {e}"
            if now != failing:
                print(f"  !  hosted tracker unreachable: {now}")
                failing = now
        time.sleep(every)


def enabled(cfg):
    """Both halves or neither. A url with no token is a push that will be
    refused every ten seconds for ever, which is worth saying at startup rather
    than discovering in the log."""
    return bool(cfg.get("push_url")) and bool(cfg.get("push_token"))


def line(cfg):
    """One line at startup, or None when no hosted tracker is configured."""
    if not cfg.get("push_url"):
        return None
    if not cfg.get("push_token"):
        return ("  hosted    FIREWALL_PUSH_URL is set but FIREWALL_PUSH_TOKEN "
                "is not — nothing will be pushed")
    hours = int(float(cfg.get("push_hours") or 24))
    audio = cfg.get("public_url") or "no audio (FIREWALL_PUBLIC_URL unset)"
    full = int(cfg.get("push_full_seconds") or 300)
    return (f"  hosted    pushing {hours}h of calls to {cfg['push_url']} "
            f"every {int(cfg.get('push_seconds') or 10)}s\n"
            f"            calls and transcripts kept there; whole window "
            f"re-sent every {full}s\n"
            f"            {audio}"
            + ("" if not auth.required(cfg) or cfg.get("push_speech")
               else " · transcripts stripped before they leave"))
