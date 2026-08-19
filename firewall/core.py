"""Shared state and the audio-to-screen path. One in-memory current call."""
import threading, time
from . import parse as _parse

_lock = threading.Lock()
_state = {"call": None}
_health = {"ok": True, "error": None, "last_ok": None}
_whisper = None


def snapshot():
    with _lock:
        return {**_state, **_health}


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
        print(f"  .  loading whisper '{model_name}' (first run downloads it)")
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
    with _lock:
        _state["call"] = call
    print(f"  ok {dept}: {call['type']} @ {call['address']} {call['units']}")
    return call
