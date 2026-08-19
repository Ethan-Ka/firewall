"""Where calls come from. Each source runs in one thread and calls core.publish()."""
import json, os, sys, tempfile, time
from collections import deque
from pathlib import Path
from . import core

# --------------------------------------------------------------- mock
MOCK = [
    ("West Lafayette FD",
     "Engine 2, Ladder 1, Battalion 1, respond to 340 Sagamore Parkway West "
     "for a reported structure fire."),
    ("Purdue FD",
     "Medic 2, Engine 11, respond to Cary Quadrangle for a 60 year old male, chest pain."),
    ("West Lafayette FD",
     "Engine 2, Medic 2, Ladder 1, respond to 1820 Cumberland Avenue, "
     "personal injury accident."),
    ("Tippecanoe County Fire",
     "Engine 3, respond to 415 North River Road for a carbon monoxide alarm activation."),
]


def mock(cfg):
    print("[mock] synthetic dispatch every 45s. No credentials, no audio.")
    i = 0
    while True:
        dept, text = MOCK[i % len(MOCK)]
        core.publish(dept, text, time.time(), cfg)
        core.report_ok()
        i += 1
        time.sleep(45)


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
            core.publish(cfg["talkgroups"].get(tg, f"TG {tg}"),
                         core.transcribe(wav, cfg["whisper_model"]),
                         meta.get("start_time") or time.time(), cfg)
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


def _bcfy_fetch(cfg, pos=None, group=None):
    """Returns (calls, lastPos)."""
    import requests
    r = requests.get(
        f"{cfg['bcfy_api_base']}/calls/v1/live/",
        params=_bcfy_params(cfg, pos, group),
        headers={"Authorization": f"Bearer {_bcfy_jwt(cfg)}"},
        timeout=20)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, list):
        return body, None
    calls = body.get("calls") or body.get("data") or []
    return calls, body.get("lastPos")


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
        return 1, f"{type(e).__name__}: {e}"

    print(f"[check] OK, JWT accepted. {len(calls)} call(s) returned, lastPos={last}")
    if calls:
        # The docs pages do not list per-call field names; show one raw record
        # so _bcfy_normalize() can be confirmed against reality.
        print(f"[check] raw record: {json.dumps(calls[0], indent=2)[:600]}")
        print(f"[check] normalized: {_bcfy_normalize(calls[0])}")
    else:
        print("[check] no calls in the window — quiet system, not an error.")
    return 0, None


def broadcastify(cfg):
    """Poll Live Calls. Assumes credentials are already verified.

    Deliberately does NOT call bcfy_check(): scripts/run-broadcastify.sh and
    `firewall --check` already do, and running it here made every launch pay
    for the same verification twice.
    """
    import requests
    # The endpoint documents a 5s floor, and it applies to the endpoint, not to
    # each group -- so one group per tick keeps us compliant while still giving
    # every talkgroup a turn. Polling faster would not save or cost money
    # either way: billing is per record read, so an empty poll is free.
    interval = max(5, int(cfg["poll_seconds"]))
    groups = sorted(cfg["talkgroups"]) or [None]

    # One cursor per group, since each group's lastPos advances independently.
    # Anchored to the display window up front: left at None, every poll would
    # re-request (and re-pay for) the server's default last-5-minutes window.
    # Reaching back hold_seconds also absorbs Broadcastify's 10-30s ingest lag,
    # which a now() anchor would race and drop calls to.
    pos = {g: time.time() - cfg["hold_seconds"] for g in groups}
    seen = deque(maxlen=2000)
    cycle = interval * len(groups)
    print(f"[bcfy] system {cfg['bcfy_system_id']} tgs={groups} "
          f"one per {interval}s, so each every {cycle}s")

    i = 0
    while True:
        g = groups[i % len(groups)]
        i += 1
        try:
            calls, last = _bcfy_fetch(cfg, pos[g], g)
            for rec in calls:
                tg, ts, url = _bcfy_normalize(rec)
                key = f"{tg}-{ts}"
                if key in seen or tg not in cfg["talkgroups"]:
                    continue
                seen.append(key)
                # Anything older than the display hold is already invisible, so
                # transcribing it is pure waste.
                if time.time() - ts > cfg["hold_seconds"]:
                    continue
                if not url:
                    print(f"[bcfy] call tg={tg} has no audio url; "
                          f"fields={sorted(rec)}", file=sys.stderr)
                    continue
                print(f"[bcfy] call tg={tg} ts={int(ts)}")
                with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
                    f.write(requests.get(url, timeout=30).content)
                    tmp = f.name
                try:
                    core.publish(cfg["talkgroups"][tg],
                                 core.transcribe(tmp, cfg["whisper_model"]), ts, cfg)
                finally:
                    os.unlink(tmp)
            # lastPos advances this group's cursor so each poll only bills for
            # new calls. It comes back 0 (or absent) on an empty result set, so
            # hold the previous position rather than resetting -- a reset would
            # drop the cursor back to the server's rolling 5-minute default and
            # pay for the same records over and over.
            pos[g] = last or pos[g]
            core.report_ok()
        except Exception as e:
            core.report_error(e)
            print(f"[bcfy] error: {e}", file=sys.stderr)
        time.sleep(interval)


ALL = {"mock": mock, "trunk": trunk, "broadcastify": broadcastify}
