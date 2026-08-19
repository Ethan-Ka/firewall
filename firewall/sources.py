"""Where calls come from. Each source runs in one thread and calls core.publish()."""
import json, os, sys, tempfile, time
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
# The authoritative schema lives at bcfy.io/dev/docs (behind registration).
# Everything Broadcastify-specific is confined to these two functions. If the
# real field names differ, this is the ONLY place that needs editing.
def _bcfy_fetch(cfg, since_ts):
    import requests
    r = requests.get(
        f"{cfg['bcfy_api_base']}/calls",
        params={"systemId": cfg["bcfy_system_id"],
                "talkgroups": ",".join(str(t) for t in cfg["talkgroups"]),
                "since": int(since_ts)},
        headers={"Authorization": f"Bearer {cfg['bcfy_api_key']}"},
        timeout=20)
    r.raise_for_status()
    body = r.json()
    return body.get("calls", body if isinstance(body, list) else [])


def _bcfy_normalize(c):
    """One API record to (talkgroup:int, start_ts:float, audio_url:str)."""
    tg = c.get("talkgroup") or c.get("tg") or c.get("call_tg")
    if isinstance(tg, str) and "-" in tg:        # "{sid}-{talkgroup}" form
        tg = tg.split("-", 1)[1]
    return (int(tg),
            float(c.get("ts") or c.get("start_time") or time.time()),
            c.get("audioUrl") or c.get("url") or c.get("filename"))


def broadcastify(cfg):
    import requests
    if not (cfg.get("bcfy_api_key") and cfg.get("bcfy_system_id")):
        sys.exit("Set bcfy_api_key and bcfy_system_id in config.json "
                 "(register at bcfy.io/dev/apply).")
    since, seen = time.time() - 300, set()
    print(f"[bcfy] system {cfg['bcfy_system_id']} "
          f"tgs={sorted(cfg['talkgroups'])} every {cfg['poll_seconds']}s")
    while True:
        try:
            for rec in _bcfy_fetch(cfg, since):
                tg, ts, url = _bcfy_normalize(rec)
                key = f"{tg}-{ts}"
                if key in seen or tg not in cfg["talkgroups"]:
                    continue
                seen.add(key)
                since = max(since, ts)
                print(f"[bcfy] call tg={tg} ts={int(ts)}")
                with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
                    f.write(requests.get(url, timeout=30).content)
                    tmp = f.name
                try:
                    core.publish(cfg["talkgroups"][tg],
                                 core.transcribe(tmp, cfg["whisper_model"]), ts, cfg)
                finally:
                    os.unlink(tmp)
            core.report_ok()
        except Exception as e:
            core.report_error(e)
            print(f"[bcfy] error: {e}", file=sys.stderr)
        time.sleep(cfg["poll_seconds"])


ALL = {"mock": mock, "trunk": trunk, "broadcastify": broadcastify}
