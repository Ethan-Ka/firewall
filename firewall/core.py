"""Shared state and the audio-to-screen path. One in-memory current call."""
import sys, threading, time
from . import geo as _geo, parse as _parse

_lock = threading.Lock()
_state = {"call": None, "purdue": None}
_health = {"ok": True, "error": None, "last_ok": None}
_whisper = None


def snapshot():
    with _lock:
        return {**_state, **_health}


def set_purdue(info):
    """Latest Purdue campus status, or None when the watcher is off.

    Separate from the call slot and from source health on purpose: campus
    status is a second, independent feed that rides along on /api/current.
    """
    with _lock:
        _state["purdue"] = info


def report_ok():
    with _lock:
        _health.update(ok=True, error=None, last_ok=time.time())


def report_error(msg):
    """Surfaced verbatim on the display error state, so make it actionable."""
    with _lock:
        _health.update(ok=False, error=str(msg)[:200])


def transcribe(path, model_name):
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        # ~140MB is fetched once and cached under ~/.cache/huggingface; after
        # that this is a ~2s load, so say so rather than implying a download
        # every time.
        print(f"  .  loading whisper '{model_name}' "
              f"(cached after a ~140MB first-run download)")
        _whisper = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(str(path), language="en", vad_filter=True)
    return " ".join(s.text for s in segments).strip()


def publish(dept, text, ts, cfg):
    """Parse a transcript and, if it looks like a dispatch, put it on screen."""
    f = _parse.parse(text, cfg)
    if not f.get("type") and not f.get("address"):
        print(f"  .  ignored (not a dispatch): {text[:70]!r}")
        return None
    call = {
        "dept": dept,
        "ts": ts,
        "transcript": text,
        "type": f.get("type") or "Dispatch",
        "address": f.get("address"),
        "city": f.get("city"),
        "units": f.get("units") or [],
    }
    # Best-effort: a geocoder outage or an unparseable address must never stop
    # a dispatch reaching the screen, which is the whole point of the display.
    try:
        call["eta"] = _geo.assess(call, cfg)
    except Exception as e:
        print(f"  !  eta unavailable ({type(e).__name__}: {e})", file=sys.stderr)
        call["eta"] = None
    with _lock:
        _state["call"] = call
    eta = call.get("eta") or {}
    note = ""
    if eta.get("passes_you"):
        note = f"  passes you in ~{eta['pass_eta']}s ({eta['closest_metres']}m)"
    elif eta.get("scene_eta") is not None:
        note = f"  on scene in ~{eta['scene_eta']}s from {eta['station']}"
    print(f"  ok {dept}: {call['type']} @ {call['address']} {call['units']}{note}")
    return call
