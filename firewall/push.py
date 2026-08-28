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
import json, threading, time, urllib.error, urllib.request

from . import auth, core

# One connection's worth of patience. The push is a background nicety and the
# radio does not wait for it: a socket hung on a datacentre that has stopped
# answering must not still be hung when the next tick comes round.
TIMEOUT = 10


def _payload(cfg):
    """One snapshot, as the far end wants it. Nothing here touches the network."""
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
    return payload


def _post(cfg, payload):
    """Send it. Raises on anything that is not a 2xx."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        cfg["push_url"], data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + str(cfg.get("push_token") or "")})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status


def push_once(cfg):
    """One push, for the loop below and for `firewall --check`."""
    return _post(cfg, _payload(cfg))


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
    failing = None
    while True:
        try:
            push_once(cfg)
            if failing:
                print(f"  ·  hosted tracker reachable again "
                      f"({time.strftime('%H:%M:%S')})")
                failing = None
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
    return (f"  hosted    pushing {hours}h of calls to {cfg['push_url']} "
            f"every {int(cfg.get('push_seconds') or 10)}s\n"
            f"            {audio}"
            + ("" if not auth.required(cfg) or cfg.get("push_speech")
               else " · transcripts stripped before they leave"))
