"""Where calls come from. Each source runs in one thread and calls core.publish()."""
import json, os, sys, tempfile, time
from collections import deque
from pathlib import Path
from . import core, segments as _segments

# --------------------------------------------------------------- trunk-recorder
def trunk(cfg):
    """Watch a trunk-recorder output dir for new call WAVs + .json sidecars."""
    d = Path(cfg["trunk_dir"])
    d.mkdir(parents=True, exist_ok=True)
    seen = {p.name for p in d.rglob("*.wav")}
    print(f"[trunk] watching {d.resolve()} ({len(seen)} existing files ignored)")
    while True:
        for wav in sorted(d.rglob("*.wav")):
            if wav.name in seen:
                continue
            seen.add(wav.name)
            meta_p = wav.with_suffix(".json")
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            tg = int(meta.get("talkgroup") or 0)
            if cfg["talkgroups"] and tg not in cfg["talkgroups"]:
                continue
            print(f"[trunk] {wav.name} tg={tg}")
            # One publish per keyup, not per recorded call: trunk-recorder holds
            # the channel through the hang time exactly as Broadcastify's
            # recorder does, so a reply lands in the same wav. start_time is
            # when the grant opened, which is when the FIRST keyup started, so
            # adding the span's own offset is a correction and not a guess --
            # without it two voices share a timestamp and the display draws them
            # as simultaneous.
            dept = cfg["talkgroups"].get(tg, f"TG {tg}")
            ts = meta.get("start_time") or time.time()
            for sp in core.transcribe_spans(wav, cfg):
                core.publish(dept, sp["text"], _segments.when(sp, ts), cfg,
                             audio=wav, span=sp)
            core.report_ok()
        time.sleep(2)


# --------------------------------------------------------------- broadcastify
# Verified against the saved bcfy.io/dev/docs pages (2026-08-19):
#   GET https://api.bcfy.io/calls/v1/live/    poll no faster than every 5s
#   params (mutually exclusive): playlist_uuid | sid | nodeId | groups (max 5)
#   extra: init=1 (last 25 calls, "valid only when requesting calls for a
#          single group or a specific node") | pos=<unix ts> (calls newer than
#          pos). With neither, the server returns the last 5 minutes.
#   response carries lastPos, the ts of the newest call, to feed the next pos.
# Free (non-premium) accounts get 5 concurrent sessions on this endpoint.
# A group is "{sid}-{talkgroup}" for trunked systems, "c-{fid}" for conventional.
from . import bcfy_auth

_user_tok = {}      # cached {uid, token, exp} from /common/v1/auth


def _bcfy_jwt(cfg, as_user=True):
    """Mint a fresh short-lived JWT, embedding the user token when needed."""
    user = None
    if as_user:
        u = _bcfy_user(cfg)
        if u:
            user = (u["uid"], u["token"])
    return bcfy_auth.mint(cfg["bcfy_api_key"], cfg["bcfy_key_id"],
                          cfg["bcfy_app_id"], ttl=3600, user=user)


def _bcfy_user(cfg):
    """uid + token for the Broadcastify user, cached until it expires.

    Live Calls requires an authenticated user in the JWT for everything except
    public playlists, so this runs before the first fetch.
    """
    import requests
    if not (cfg.get("bcfy_username") and cfg.get("bcfy_password")):
        return None
    if _user_tok and _user_tok.get("exp", 0) > time.time() + 60:
        return _user_tok
    r = requests.post(
        f"{cfg['bcfy_api_base']}/common/v1/auth",
        headers={"Authorization": f"Bearer {_bcfy_jwt(cfg, as_user=False)}"},
        data={"username": cfg["bcfy_username"], "password": cfg["bcfy_password"]},
        timeout=20)
    r.raise_for_status()
    body = r.json()
    _user_tok.clear()
    _user_tok.update(uid=int(body["uid"]), token=body["token"],
                     exp=float(body.get("exp") or time.time() + 3600))
    return _user_tok


def _bcfy_params(cfg, pos=None, group=None):
    """Params for one request. `group` is a single talkgroup id, or None for sid.

    `groups` accepts exactly one group on this server, whatever the docs say.
    The docs describe it as "comma delimited ... (Max 5)", and separately note
    that `init` is "valid only when requesting calls for a single group or a
    specific node" -- which looked like the explanation for multi-group queries
    returning nothing. It is not. Re-measured 2026-08-19 against sid 9099 with
    `init` dropped entirely and a 6h `pos` window:

        groups=9099-2021,9099-2105   ->  0 records
        groups=9099-2021             ->  6 records
        groups=9099-2105             -> 10 records

    Empty set, no error, a plausible-looking lastPos. So one group per request
    it is; broadcastify() round-robins them. Earlier separator attempts (encoded
    comma, pipe, semicolon, repeated `groups=`) all failed the same way.

    This is worth the extra requests because billing is per record read
    (~$0.0006, measured), and requests themselves are free:

      groups=  bills only for the talkgroup asked for.
      sid=     bills for every record in the county. Sampled over 2h: 33
               records, 9 wanted -- 73% of the spend discarded locally.
      init=1   reads 25 records ($0.015) whatever their age, and the loop below
               drops everything older than hold_seconds anyway. Never sent.
    """
    p = {"groups": f"{cfg['bcfy_system_id']}-{group}"} if group \
        else {"sid": cfg["bcfy_system_id"]}
    if pos:
        p["pos"] = int(pos)
    return p


class BcfyBadResponse(Exception):
    """A Live Calls response that is not a batch of calls.

    Its own class because it is the one failure on this endpoint that does not
    arrive as an exception already: the socket was fine, the status was 200, and
    what came back was something else.
    """


def bcfy_reason(e):
    """One exception, as the sentence to put on the display.

    A class name earns its place in front of a URLError or a KeyError, which
    otherwise arrive as a bare "timed out" or a bare quoted field name. It is
    only noise in front of a BcfyBadResponse, which is already a sentence
    written to be read off a screen.
    """
    return str(e) if isinstance(e, BcfyBadResponse) else f"{type(e).__name__}: {e}"


def _bcfy_calls(body):
    """(calls, lastPos) out of one Live Calls response, or raise saying why.

    Shared with the hosted collector rather than written twice, because what the
    fields are called and what counts as an answer is exactly the kind of thing
    the two must not drift on.

    The endpoint does not report every failure in the status line. A request it
    rejects can come back 200 with an `errors` array where the calls should be,
    and a BCFY_API_BASE aimed at the wrong host can come back 200 with a web
    page. Both used to land on `body.get("calls") or body.get("data") or []` and
    become an empty batch, indistinguishable from a quiet talkgroup -- so the
    poll counted as a success, report_ok() cleared the health flag, and the
    display sat there with a green light and no calls for as long as the key
    stayed bad. A source that is failing looked exactly like a source that had
    nothing to say, which is the one thing the health flag exists to prevent.
    So anything not recognisable as a batch of calls raises instead, carrying
    the server's own words where there are any -- report_error() puts the string
    straight on the screen.

    An empty batch that really is one stays what it always was: a quiet system,
    not an error.
    """
    if isinstance(body, list):
        return body, None
    if not isinstance(body, dict):
        raise BcfyBadResponse(
            f"Live Calls answered with {type(body).__name__}, not calls -- "
            f"check BCFY_API_BASE is the bare host (https://api.bcfy.io)")
    errors = body.get("errors")
    if errors:
        # The shape bcfy_check() reads off a 401: [{code, title}, ...].
        try:
            said = "; ".join(f"{e.get('code')} {e.get('title')}".strip()
                             for e in errors) or str(errors)[:160]
        except Exception:
            said = str(errors)[:160]
        raise BcfyBadResponse(f"Live Calls refused the request: {said}")
    calls = body.get("calls")
    if calls is None:
        calls = body.get("data")
    if not isinstance(calls, list):
        raise BcfyBadResponse(
            "no calls in the Live Calls response (keys: "
            + (", ".join(sorted(map(str, body))) or "none") + ")")
    return calls, body.get("lastPos")


def _bcfy_fetch(cfg, pos=None, group=None):
    """Returns (calls, lastPos)."""
    import requests
    r = requests.get(
        f"{cfg['bcfy_api_base']}/calls/v1/live/",
        params=_bcfy_params(cfg, pos, group),
        headers={"Authorization": f"Bearer {_bcfy_jwt(cfg)}"},
        timeout=20)
    r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        # 200 and not JSON at all -- a proxy's error page, or a host that is
        # simply not the API. Worth naming, because a decoder's own complaint
        # about line 1 column 1 says nothing about which of those it was.
        raise BcfyBadResponse(
            f"Live Calls answered "
            f"{r.headers.get('Content-Type') or 'something'} rather than "
            f"JSON: {r.text[:120]!r}")
    return _bcfy_calls(body)


def _bcfy_normalize(c):
    """One API record to (talkgroup:int, start_ts:float, audio_url:str|None).

    Confirmed against a live response: groupId is "{sid}-{talkgroup}", ts is the
    call start as a unix int, and url is the MP3. Records also carry start_ts,
    end_ts, duration, descr, display, grouping, tag, sid, siteId, freq and src.
    """
    g = str(c.get("groupId") or "")
    tg = g.split("-", 1)[1] if "-" in g else g
    return (int(tg),
            float(c.get("ts") or c.get("start_ts") or time.time()),
            c.get("url"))


def _bcfy_ident(rec, tg, ts):
    """A stable identity for one record, for the dedupe ring.

    "{tg}-{ts}" is not one: ts is a whole second and two transmissions on the
    same talkgroup can share it (a 1s "Medic 16." landing in the same second as
    the tail of a simulcast copy). Whichever arrived second was silently
    dropped, which looks exactly like the fetch bug it hides inside. Prefer the
    record's own id, then the MP3 url -- unique per call, since it names the
    stored file -- and keep tg-ts only as a last resort.
    """
    for k in ("id", "callId", "call_id", "uuid", "filename"):
        v = rec.get(k)
        if v:
            return f"id:{v}"
    return f"url:{rec['url']}" if rec.get("url") else f"tgts:{tg}-{ts}"


def bcfy_check(cfg):
    """One request against the configured endpoint, reporting what actually failed.

    Separates "your key is wrong" from "the URL is wrong", which otherwise look
    identical from inside the poll loop.

    Scoped to the display window rather than `init=1` so that verifying a
    credential is close to free: `init` would read 25 records ($0.015) every
    time anyone ran --check, and this used to run twice per launch.
    """
    import requests
    missing = [n for n, k in (("BCFY_API_KEY", "bcfy_api_key"),
                              ("BCFY_KEY_ID", "bcfy_key_id"),
                              ("BCFY_APP_ID", "bcfy_app_id"),
                              ("BCFY_SYSTEM_ID", "bcfy_system_id"))
               if not cfg.get(k)]
    if missing:
        return 1, (f"Missing {', '.join(missing)} in .env. The API key alone is not "
                   f"enough: auth is a JWT signed with the key, and its header needs "
                   f"the API Key ID while its payload needs the Application ID. Both "
                   f"are on the developer portal (API Keys / Company & Apps).")

    key = cfg["bcfy_api_key"]
    print(f"[check] key  {key[:6]}...{key[-4:]} ({len(key)} chars)"
          f"  kid={cfg['bcfy_key_id']}  iss={cfg['bcfy_app_id']}")
    try:
        if cfg.get("bcfy_username"):
            u = _bcfy_user(cfg)
            print(f"[check] user {cfg['bcfy_username']} -> uid={u['uid']}")
        else:
            print("[check] user (none set; Live Calls needs BCFY_USERNAME/PASSWORD "
                  "unless you use a public playlist)")
        pos = time.time() - cfg["hold_seconds"]
        grp = sorted(cfg["talkgroups"])[0] if cfg["talkgroups"] else None
        print(f"[check] GET  {cfg['bcfy_api_base']}/calls/v1/live/ "
              f"{_bcfy_params(cfg, pos, grp)}")
        calls, last = _bcfy_fetch(cfg, pos, grp)
    except requests.HTTPError as e:
        code = e.response.status_code
        if code in (401, 403):
            # The API returns a structured error object; its code says which
            # part of the credential failed, which is worth spelling out.
            try:
                err = e.response.json()["errors"][0]
                api_code, title = str(err.get("code")), err.get("title")
            except Exception:
                api_code, title = "", e.response.text[:120]
            hint = {
                "103": "the JWT itself was rejected (bad signature, or exp "
                       "missing/expired). BCFY_API_KEY is the signing secret.",
                "108": "the JWT was well-formed but BCFY_KEY_ID does not name a "
                       "known API key. Copy the API Key ID from the portal's API "
                       "Keys page; it is not the key itself and not the App ID.",
            }.get(api_code, "check the key is active, funded, and carries the "
                            "bcfy_calls_api entitlement.")
            return 1, f"HTTP {code} ({api_code} {title}): {hint}"
        if code == 404:
            return 1, (f"HTTP 404 from {cfg['bcfy_api_base']}/calls/v1/live/ — "
                       f"check BCFY_API_BASE is the bare host (https://api.bcfy.io).")
        return 1, f"HTTP {code}: {e.response.text[:200]}"
    except Exception as e:
        # A BcfyBadResponse lands here, and it is the reason --check exists:
        # a 200 carrying an error object used to print "OK, JWT accepted.
        # 0 call(s) returned" and send somebody off to debug a quiet talkgroup.
        return 1, bcfy_reason(e)

    print(f"[check] OK, JWT accepted. {len(calls)} call(s) returned, lastPos={last}")
    if calls:
        # The docs pages do not list per-call field names; show one raw record
        # so _bcfy_normalize() can be confirmed against reality.
        print(f"[check] raw record: {json.dumps(calls[0], indent=2)[:600]}")
        print(f"[check] normalized: {_bcfy_normalize(calls[0])}")
    else:
        print("[check] no calls in the window — quiet system, not an error.")
    return 0, None


def _keep_audio(cfg, tg, ts, data):
    """Write the raw call audio to audio_dir, if one is configured."""
    d = cfg.get("audio_dir")
    if not d:
        return None
    try:
        out = Path(d)
        out.mkdir(parents=True, exist_ok=True)
        p = out / f"{int(ts)}-{tg}.mp3"
        p.write_bytes(data)
        print(f"  .  saved {p}")
        return p
    except Exception as e:
        print(f"[bcfy] could not save audio: {e}", file=sys.stderr)


def _bcfy_handle(cfg, rec, seen):
    """Fetch, transcribe and publish one record. Raising here loses only this one.

    `seen` is appended to only where the record is genuinely finished with, so
    that the re-read window (see broadcastify()) can retry the rest:
      published        -> seen, obviously.
      too old          -> seen; it can never become interesting again.
      no audio url     -> NOT seen. A record can be indexed before its MP3 is
                          written, and with lag > 0 the same record comes round
                          again a few seconds later, by which time it may have
                          one. Marking it seen made that unrecoverable.
      raised anything  -> NOT seen. A blip on the clip download used to cost the
                          transmission permanently; the re-read is already paid
                          for, so retrying inside the window is free.
    """
    import requests
    tg, ts, url = _bcfy_normalize(rec)
    key = _bcfy_ident(rec, tg, ts)
    if key in seen or tg not in cfg["talkgroups"]:
        return
    # Anything older than the display hold is already invisible, so
    # transcribing it is pure waste.
    if time.time() - ts > cfg["hold_seconds"]:
        seen.append(key)
        return
    if not url:
        print(f"[bcfy] call tg={tg} ts={int(ts)} has no audio url yet; "
              f"fields={sorted(rec)}", file=sys.stderr)
        return
    print(f"[bcfy] call tg={tg} ts={int(ts)}")
    audio = requests.get(url, timeout=30).content
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        tmp = f.name
    # Optional clip archive. Transcription accuracy can only be tuned against
    # the audio that produced a bad transcript, and by then the live call is
    # long gone.
    kept = _keep_audio(cfg, tg, ts, audio)
    try:
        # Everything downstream works off the archived clip when there is one,
        # so the incident log can link to those bytes instead of copying them
        # and leaving two files, two review entries, and two things to label
        # for one transmission. tmp is the fallback and still matters: with
        # audio_dir unset the archive does not exist, and the incident log
        # would otherwise have nothing to keep.
        clip = str(kept or tmp)
        # A record is a channel grant, and the grant holds through the hang
        # time, so two people keying up seconds apart arrive as one mp3 under
        # one timestamp -- "Dispatch 16, that is a negative." answered by
        # "Clear, thank you." was filed as a single transmission. One publish
        # per keyup fixes that; `ts` is when the grant opened, so the span's own
        # offset is what puts the second voice at the second it spoke. The
        # bytes are still downloaded and paid for exactly once, and every span
        # is a reference into them.
        dept = cfg["talkgroups"][tg]
        for sp in core.transcribe_spans(clip, cfg):
            core.publish(dept, sp["text"], _segments.when(sp, ts), cfg,
                         audio=clip, span=sp)
        seen.append(key)
    finally:
        os.unlink(tmp)


def broadcastify(cfg):
    """Poll Live Calls. Assumes credentials are already verified.

    Deliberately does NOT call bcfy_check(): scripts/run-broadcastify.sh and
    `firewall --check` already do, and running it here made every launch pay
    for the same verification twice.
    """
    # The endpoint documents a 5s floor, and it applies to the endpoint, not to
    # each group -- so one group per tick keeps us compliant while still giving
    # every talkgroup a turn.
    interval = max(5, int(cfg["poll_seconds"]))
    groups = sorted(cfg["talkgroups"]) or [None]
    cycle = interval * len(groups)
    lag = max(0, int(cfg.get("bcfy_lag_seconds") or 0))

    # --- the cursor, and what completeness costs ---------------------------
    # `pos` filters on ts, the call START, but a call cannot be published until
    # it ENDS and clears Broadcastify's 10-30s ingest. So the newest ts in a
    # batch is not a safe watermark: a long transmission starting at T publishes
    # after a short one starting at T+10, and once lastPos has moved to T+10 the
    # long one is never inside any later window. It is not returned again, ever.
    # Both halves of that gap are real here -- the saved clips include an 18s
    # call on tg 2105 at ts 1787204196 and a 3s one at 1787204200 -- and the
    # ingest lag alone is enough, since it varies per record. That is how a
    # reply reaches the screen with nothing called before it.
    #
    # bcfy_lag_seconds holds the cursor that many seconds behind the wall clock,
    # so a record is still inside the requested window when it finally shows up.
    # There is no cheaper way to be complete: /calls/v1/live/ has no upper-bound
    # parameter, so "newer than now-lag" also re-reads every record already
    # published in that window, and reads are the bill. A record whose audio
    # publishes P seconds after its start is read
    #     1 + (lag - P) / cycle   times,   cycle = poll_seconds * len(talkgroups)
    # because that is how many polls happen before the cursor passes its ts.
    # Simulated offline against a scripted server -- 16 records/hour (the
    # measured rate here), 8s calls, 20s ingest, so P = 28s, 10s cycle, at the
    # measured $0.0006/record:
    #
    #   lag=0    1.0x  $0.0006/call  $0.0096/h  $7/mo  and it drops calls
    #   lag=30   1.0x  $0.0006/call  $0.0096/h  $7/mo  covers ingest, not a
    #                                                   long transmission
    #   lag=60   4.0x  $0.0024/call  $0.0384/h  $28/mo default: 30s worst
    #                                                   ingest + the 18s
    #                                                   longest clip in the
    #                                                   archive + one cycle
    #   lag=120 10.0x  $0.0060/call  $0.0960/h  $69/mo pure overlap window,
    #                                                   no added coverage
    #
    # So completeness costs about $21 a month here at the default, and only
    # while there is traffic: an empty window is still free. The lever that
    # buys it back cheaply is FIREWALL_POLL_SECONDS, which divides the
    # multiplier -- lag=60 at poll_seconds=15 (a 30s cycle) is 2.1x -- for the
    # price of seeing a call up to 30s later. Note this reverses the old rule
    # that polling is free: with lag > 0 every poll re-reads the window's tail.
    #
    # What lag does NOT cost is display latency. A record enters the window the
    # moment it publishes, whatever the cursor is doing, so the screen stays as
    # live as the ingest allows; lag buys completeness with money, not delay.
    #
    # One cursor per group, since each advances independently. Anchored to the
    # display window up front: left at None, every poll would re-request (and
    # re-pay for) the server's default last-5-minutes window.
    pos = {g: time.time() - cfg["hold_seconds"] for g in groups}
    seen = deque(maxlen=2000)
    print(f"[bcfy] system {cfg['bcfy_system_id']} tgs={groups} "
          f"one per {interval}s, so each every {cycle}s")
    # 28s is the P above: an 8s call plus 20s of ingest, the middle of what
    # this system does. Worth printing -- a display quietly paying 10x, or
    # quietly dropping traffic, looks identical from the couch.
    mult = 1 + max(0, lag - 28) / cycle if lag else 1.0
    print(f"[bcfy] cursor {'trailing %ds behind now' % lag if lag else 'at lastPos'}"
          f" -- {'complete' if lag else 'late-published calls WILL be missed'},"
          f" ~{mult:.1f}x records read (~${mult * 0.0006:.4f}/call),"
          f" no added display latency; "
          + ("BCFY_LAG_SECONDS=0 is cheaper and lossy" if lag
             else "raise BCFY_LAG_SECONDS to stop losing them"))

    i = 0
    while True:
        g = groups[i % len(groups)]
        i += 1
        # Sampled before the fetch, not after the loop: transcription runs
        # inline in this thread, so a slow decode can put a minute between the
        # request and the cursor update. Advancing to (then - lag) would step
        # straight over everything that published while whisper was busy.
        t0 = time.time()
        try:
            calls, last = _bcfy_fetch(cfg, pos[g], g)
        except Exception as e:
            core.report_error(e)
            print(f"[bcfy] error: {e}", file=sys.stderr)
            time.sleep(interval)
            continue
        dropped = False
        for rec in calls:
            # Per record, because one malformed record used to abort the whole
            # batch from the outer try -- every sibling behind it in the list
            # went unprocessed, and the cursor stood still, so the survivors
            # were fetched (and billed) a second time on the next poll.
            try:
                _bcfy_handle(cfg, rec, seen)
            except Exception as e:
                dropped = True
                core.report_error(e)
                print(f"[bcfy] dropped one record ({type(e).__name__}: {e}); "
                      f"rest of batch continues", file=sys.stderr)
        # max() so the cursor can only ever move forward: it must not fall back
        # to the server's rolling 5-minute default and pay for the same records
        # over and over. With lag=0 it is lastPos again, which comes back 0 (or
        # absent) on an empty result set, hence the fallback to the old value.
        pos[g] = max(pos[g], t0 - lag) if lag else (last or pos[g])
        # Only when the pass had nothing wrong with it. report_ok() clears what
        # report_error() just set, so unconditionally, a record that failed lit
        # the display for the few microseconds between the two lines and never
        # again -- and a source failing on every record read as healthy.
        if not dropped:
            core.report_ok()
        time.sleep(interval)


# No mock source. There was one, generating a dispatch every 45 seconds so the
# display could be worked on without credentials, and it was removed because a
# fabricated structure fire is not a harmless placeholder on a screen whose
# whole purpose is telling somebody what is actually on fire. The cost of
# keeping it was a wall display that could not be trusted at a glance, and a
# fake call that reached the incident log the first time anything ran the
# server without going through __main__'s guard.
ALL = {"trunk": trunk, "broadcastify": broadcastify}
