"""Words out of audio, over HTTP, because the local way cannot come here.

The CLI loads faster-whisper and runs `small.en` on the CPU: 480MB of weights, a
couple of seconds a clip, cached under ~/.cache after a one-time download. None
of that survives the move to a serverless function -- the weights are twice the
bundle limit, and a cold start would fetch them again every time. So the one
piece of the chain that cannot be lifted is replaced rather than ported, and it
is replaced at the narrowest seam available: something that takes an mp3 and
gives back whisper-shaped segments.

Whisper-shaped is the whole trick. segments.split() is documented to take
"anything iterable of objects carrying .start, .end and .text, optionally
.words", so what comes back from here feeds the existing keyup splitter, the
existing parser and the existing gazetteer untouched. The transcription is the
only thing that changed; every judgement made about the words afterwards is the
same code the corpus was scored against.

Any OpenAI-compatible /audio/transcriptions endpoint works, which is deliberate:
the same two settings point this at OpenAI or at one of the several hosts
serving whisper-large-v3 for a fraction of the price. See .env.vercel.
"""
import json
import os
import urllib.error
import urllib.request
import uuid

from firewall import places as _places

TIMEOUT = 120

DEFAULT_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-1"


class TranscribeError(RuntimeError):
    """The transcription failed. Raised so the caller can leave the record
    unseen and try it again on the next tick rather than losing it."""


# What the decoder is told before it hears anything.
#
# The local path has two slots for this and the API has one, so they are spent
# in order of what each was measured to be worth. DISPATCH_STYLE is two
# sentences and took word error from 17.9% to 11.4% by teaching the shape of a
# dispatch -- digits over words, the comma after each unit. VOCAB is the local
# names, and biasing toward them took scored place names from 13/56 to 37/56;
# it is ordered most-important-first because it was already written to be
# truncated. So: the style first because it is short and general, then as much
# of the vocabulary as the budget allows.
#
# ~224 tokens is the documented ceiling and roughly four characters to the
# token, which is where the figure below comes from. Going over is not an error
# -- the far end simply keeps the head and drops the rest, which is the same
# thing faster-whisper does and the reason VOCAB is ordered the way it is.
_PROMPT_CHARS = 880
PROMPT = (_places.DISPATCH_STYLE + " " + _places.VOCAB)[:_PROMPT_CHARS]


class _Word:
    __slots__ = ("start", "end", "word")

    def __init__(self, start, end, word):
        self.start, self.end, self.word = float(start), float(end), word


class _Segment:
    """One whisper segment, in the shape segments.split() reads."""
    __slots__ = ("start", "end", "text", "words")

    def __init__(self, start, end, text, words=None):
        self.start, self.end, self.text = float(start), float(end), text
        self.words = words or None


def _multipart(fields, filename, blob):
    """One multipart/form-data body. `fields` may repeat a key, which is how
    timestamp_granularities[] asks for two granularities at once."""
    boundary = "----firewall" + uuid.uuid4().hex
    out = bytearray()
    for key, value in fields:
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n").encode()
    out += (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n").encode()
    out += blob
    out += f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _attach(segments, words):
    """Hand each top-level word to the segment whose span contains it.

    OpenAI returns segments and words as two flat lists rather than nesting one
    inside the other, and _atoms() reads words off the segment. Matched on the
    midpoint rather than the start so a word straddling a segment boundary lands
    on the side it mostly belongs to; a word matching nothing is dropped, which
    costs that word its own timing and leaves the segment's, which is what a
    response with no word timings at all would have given anyway.
    """
    if not words:
        return
    for w in words:
        mid = (w.start + w.end) / 2
        for s in segments:
            if s.start <= mid <= s.end:
                if s.words is None:
                    s.words = []
                s.words.append(w)
                break


def configured(cfg=None):
    """Whether this deployment can transcribe at all."""
    return bool((cfg or {}).get("stt_key") or os.environ.get("FIREWALL_STT_KEY"))


def transcribe(blob, cfg, filename="clip.mp3"):
    """One record's audio -> whisper-shaped segments, oldest first.

    Returns [] when nothing was said, which is a real answer and not a failure:
    a keyup of tones has no speech in it, and split() is written to hand that
    back as a single empty span rather than as nothing.
    """
    key = cfg.get("stt_key") or os.environ.get("FIREWALL_STT_KEY")
    if not key:
        raise TranscribeError(
            "FIREWALL_STT_KEY is not set, so there is nothing to transcribe with")
    url = cfg.get("stt_url") or DEFAULT_URL
    model = cfg.get("stt_model") or DEFAULT_MODEL

    fields = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("language", "en"),
        # Zero, not the default. This is a transcript somebody will act on, and
        # sampling is how a decoder that is unsure invents a plausible street.
        ("temperature", "0"),
        ("prompt", cfg.get("stt_prompt") or PROMPT),
        ("timestamp_granularities[]", "segment"),
        ("timestamp_granularities[]", "word"),
    ]
    body, content_type = _multipart(fields, filename, blob)
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            why = e.read().decode()[:300]
        except Exception:
            why = ""
        raise TranscribeError(f"HTTP {e.code} {why}".strip()) from None
    except Exception as e:
        raise TranscribeError(f"{type(e).__name__}: {e}") from None

    return _read(payload)


def _read(payload):
    """A verbose_json answer as segments, however much of it arrived.

    Written to degrade rather than to insist. Not every host that speaks this
    API returns word timings, and one that returns only `text` still produces a
    usable transmission -- it just cannot be split into keyups as finely, which
    split() already handles by falling back to segment boundaries.
    """
    raw = payload.get("segments")
    if not raw:
        text = (payload.get("text") or "").strip()
        if not text:
            return []
        # One segment covering whatever the response says the clip was. `0` is
        # honest when it says nothing: split() clamps against `duration` at the
        # call site, and a span of zero length is left alone by cues().
        return [_Segment(0.0, float(payload.get("duration") or 0.0), text)]

    segments = [_Segment(s.get("start") or 0.0, s.get("end") or 0.0,
                         s.get("text") or "")
                for s in raw]
    # Words nested inside the segment, for a host that does it that way.
    for seg, s in zip(segments, raw):
        nested = s.get("words")
        if nested:
            seg.words = [_Word(w.get("start") or 0.0, w.get("end") or 0.0,
                               w.get("word") or w.get("text") or "")
                         for w in nested]
    _attach(segments, [_Word(w.get("start") or 0.0, w.get("end") or 0.0,
                             w.get("word") or w.get("text") or "")
                       for w in (payload.get("words") or [])])
    return segments
