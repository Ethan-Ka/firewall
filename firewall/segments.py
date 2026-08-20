"""One downloaded record is not one transmission. Recover the keyups inside it.

A Broadcastify Calls record is one trunked *channel grant*, not one person
talking. On a P25 system the talkgroup stays granted through the hang time after
a unit unkeys -- a second or two, so the reply does not have to re-request the
channel and clip its own first syllable -- and every keyup inside that window
comes down as ONE mp3 carrying ONE start timestamp. core.transcribe() then joins
whisper's segments into a single string, so a two-voice exchange arrives as one
transcript, one "+Xs" in the incident log, and one row on the radio bar.

The clip that forced this module is 1787205431-2105.mp3, filed as a single
transmission reading "Dispatch 16, that is a negative.  Clear, thank you."
The first sentence is Medic 16 answering the dispatcher's question from the
previous record; the second is the dispatcher closing it out. Two people, two
keyups, one record -- a reply glued onto the message it answers, with nothing on
screen to say a second voice ever spoke. It is not a one-off: 1787205904, eight
minutes later on the same call, is "Dispatch, Medic 16 has arrived at IU."
followed by the dispatcher's "Medic 16.", filed the same way.

Nothing above the audio can fix this, so it is fixed here: given whisper's
segments for one clip, split() returns one span per keyup, timed relative to the
clip start. The audio file is left alone. A span is a *reference* into it.


WHAT THE REAL CLIPS MEASURE
---------------------------
Every clip in ~/Documents/firewall-data/clips (eleven records off Purdue FD,
hand labelled in corpus.jsonl) was decoded with the settings core.transcribe()
uses. Two things came out of that, and both of them decide the design.

First: **segment boundaries alone cannot see this.** Whisper split 1787205431
into exactly the two segments a human would draw -- and dated them [0.00, 3.04]
and [3.04, 5.04]. The gap between the two keyups measured 0.00 seconds, because
without word timestamps whisper's segment ends are token-window bookkeeping, not
observations of the audio. A gap rule reading segment boundaries would have
scored the one case this module exists for as a single transmission.

Ask for word timestamps and the same clip resolves properly: the last word of
the first keyup ends at 2.46, the first word of the second starts at 3.70, and
the 1.24s between them measures -32.8dB against -21.3dB for the clip as a whole
-- the channel really was dead there. So core.transcribe() must pass
`word_timestamps=True`; measured over the whole archive it cost 10.5s -> 11.5s,
about 10%, which is nothing against getting the speaker turn right. split() still
falls back to segment boundaries when the words are absent, so it degrades to
today's behaviour rather than crashing -- but in that mode it will not catch the
Medic 16 exchange, and that is the whole point of it.

Second: **the two populations are far apart, and the band between them is
empty.** Every word gap in the archive, largest first:

    1.56s  1787205904  "at you."[sic]       | "Medic"       -40.3dB   KEYUP
    1.24s  1787205431  "negative."          | "Clear,"      -32.8dB   KEYUP
    0.28s  1787205904  "Dispatch,"          | "Medic"       -19.1dB   breath
    0.22s  1787205431  "16,"                | "that"        -16.4dB   breath
    0.20s  1787204044  "Fire,"              | "Medic"       -11.7dB   breath
    0.16s  1787204044  "16,"                | "stand"       -13.9dB   breath
    0.10s  1787205431  "Clear,"             | "thank"       -24.0dB   breath
    0.10s  1787204196  "route,"             | "information" -17.8dB   breath

Pauses inside one person's turn top out at 0.28s; the two genuine speaker
changes floor at 1.24s. Nothing lands between them, and the ceiling does not
move when a single real transmission is padded front and back with a second or
two of authentic dead air, or when two real clips are spliced together with it.

_GAP is 0.8, in the middle of that empty band: 4.4x the largest pause inside
anyone's sentence, and comfortably below the smallest real keyup boundary.

The dB column is corroboration, not a rule. A pause inside a transmission is
not quiet -- the carrier is still up, so the channel noise floor comes through
at roughly the clip's own level, and one of them is actually louder than the
clip average. Real dead air between grants is 11 to 18dB down, because the
receiver has nothing to decode. Energy would therefore work as a second signal
if the gap ever stops being enough; it is not implemented because on this
evidence the gap is enough, and reading the PCM back would mean decoding every
clip twice.


WHY THE GAP IS NOT ASKED TO DECIDE ON ITS OWN
---------------------------------------------
A false split is much worse than a missed split. A missed split leaves the
display exactly as it is today. A false split invents a speaker turn that never
happened and hands _parse.parse() half a dispatch -- and the half it keeps is
usually the front half, the one without the address.

The near-miss that proves it is in 1787204044, the asthma-attack dispatch. The
dispatcher pauses about a second in the middle of "...for a medical run at
[pause] Stadium and Martin Jischke...". Whisper absorbed that pause into the
duration of the word "at" (4.22 -> 5.22) instead of emitting it as a gap, so
gap-alone survived by luck. Had it drawn the boundary the other way, a 0.8s rule
would have cut the dispatch immediately before its address.

So an ordinary-length gap has to be corroborated by the transcript agreeing that
somebody finished talking: the word before the gap must end a sentence. Both
true boundaries in the archive do -- "...that is a negative." and "...arrived
at IU." -- as does every boundary in the spliced pairs, and "at" does not. This
is cheap, it is the tell a human uses, and it fails in the safe
direction: a keyup that ends mid-phrase stays glued to the next one, which is
today's behaviour, not a new kind of wrong.

_LONG_GAP is the one escape hatch, and it is the weaker half of this: past two
seconds the transcript is not consulted, because whisper demonstrably *hides*
in-speech pauses inside word durations rather than reporting them, so a gap it
did report at that length is not a breath. This threshold is reasoned, not
measured -- there is no two-second in-speech pause anywhere in the archive to
measure against. If a false split ever shows up, it will be this rule.

The overwhelmingly common case is a clip that is one transmission, and it is
protected structurally rather than by tuning: with fewer than two atoms, or with
no qualifying gap, split() returns exactly one span whose text is the same string
core.transcribe() builds today.
"""
import re
from collections import namedtuple

# Seconds of silence that, together with a finished sentence, mean somebody
# unkeyed. See the module docstring for the measured distribution this sits in
# the middle of: 0.28s is the largest in-speech pause in the archive, 1.24s the
# smallest real speaker change, and nothing at all was measured in between.
#
# Do not lower this below ~0.6 without reading display.html first. The player
# there stops a keyup at its play_end from a `timeupdate` event, which browsers
# fire about four times a second, so playback can overrun the boundary by up to
# a quarter second. That is harmless only while the gap it lands in is wider
# than the 0.25s padding either side plus that overrun -- at 0.8 the overrun
# dies in silence, and below ~0.6 the tail of one transmission starts bleeding
# into the voice of the next.
_GAP = 0.8

# Long enough that the transcript does not get a vote. Whisper reports pauses
# this long only when the channel really went quiet.
_LONG_GAP = 2.0

# Lead-in and tail given to a span when it is handed to a player. A quarter
# second is what makes a keyup sound like a keyup: it catches the squelch
# opening, and on a 8kHz-ish trunked clip the first syllable is half-eaten by
# the codec ramp anyway. It is clamped against the neighbouring spans in cues(),
# so padding can never play the previous speaker's last word.
_PAD = 0.25

# Whisper punctuates dispatch audio well enough to be worth trusting for this
# one narrow question -- did that voice finish? The trailing quote/bracket class
# is there because a decoded sentence occasionally comes back as `negative."`
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*\s*$")

# Words when whisper was asked for them, whole segments when it was not. `word`
# is carried because the two need different joining: whisper's word tokens
# already own their leading space (" negative.", but "-0." after "16"), so
# gluing them with a separator would put "16 -0." in the log.
_Atom = namedtuple("_Atom", "start end text word")


def _atoms(segments, drop):
    """The finest-grained timed pieces `segments` will give up, in order.

    Filler is dropped here rather than after splitting, and that ordering is
    deliberate. Whisper answers a keyup of tones or squelch noise with caption
    boilerplate ("Thank you.", "[Music]"), and such a segment sitting between
    two real ones is not a transmission -- it is the dead air *between* two
    transmissions wearing a transcript. Removing it first lets the gap it was
    hiding do its job; removing it afterwards would leave a phantom span and a
    boundary the audio does not have.
    """
    out = []
    for s in segments:
        text = getattr(s, "text", "") or ""
        if drop is not None and drop(text):
            continue
        words = getattr(s, "words", None)
        if words:
            for w in words:
                out.append(_Atom(float(w.start), float(w.end),
                                 getattr(w, "word", "") or "", True))
        elif text.strip():
            out.append(_Atom(float(s.start), float(s.end), text, False))
    return out


def _join(atoms):
    """One span's atoms as the string the parser and the display will see."""
    out = ""
    for a in atoms:
        if a.word:
            out += a.text
        else:
            out = (out + " " + a.text.strip()) if out else a.text.strip()
    return re.sub(r"\s+", " ", out).strip()


def _breaks(atoms, gap, long_gap):
    """Indices in `atoms` where a new keyup starts."""
    cuts = []
    for i in range(1, len(atoms)):
        quiet = atoms[i].start - atoms[i - 1].end
        if quiet >= long_gap or (quiet >= gap
                                 and _SENTENCE_END.search(atoms[i - 1].text)):
            cuts.append(i)
    return cuts


def split(segments, gap=_GAP, long_gap=_LONG_GAP, drop=None, duration=None):
    """One record's whisper segments -> one dict per transmission in it.

    `segments` is anything iterable of objects carrying `.start`, `.end` and
    `.text`, optionally `.words` -- faster_whisper's Segment, or a namedtuple in
    a test. Times are seconds from the start of the clip, which is what makes
    a span addressable inside the one mp3 that was downloaded.

    `drop` is an optional predicate on a segment's text, called to discard
    whisper's caption filler; core._is_hallucination is the intended argument,
    passed in rather than imported so this module stays below core.

    `duration` clamps the span ends. Worth passing when the result will be
    handed to a player: whisper cheerfully dates the end of a 1.69s clip at
    3.12s, and a media fragment asking for audio that does not exist is a
    silent bug rather than a loud one.

    Returns [{"start", "end", "text", "gap"}, ...] oldest first, where "gap" is
    the seconds of measured silence in front of that transmission and is None
    for the first. Always at least one span for a clip with any speech in it;
    [] only when there was nothing to transcribe.
    """
    atoms = _atoms(segments, drop)
    if not atoms:
        return []
    cuts = _breaks(atoms, gap, long_gap)
    spans, quiet = [], None
    for a, b in zip([0] + cuts, cuts + [len(atoms)]):
        group = atoms[a:b]
        end = group[-1].end
        spans.append({"start": max(0.0, group[0].start),
                      "end": min(end, duration) if duration else end,
                      "text": _join(group),
                      "gap": quiet})
        quiet = round(atoms[b].start - end, 2) if b < len(atoms) else None
    return spans


def when(span, clip_ts):
    """A span's absolute unix time: the clip's timestamp plus its own offset.

    The reason every transmission out of one record must not keep the record's
    timestamp: the incident log orders transmissions by `ts` and the display
    renders each row as "+Xs" from the call, so two keyups sharing one number
    are drawn as simultaneous. Broadcastify's `ts` is when the grant opened,
    which is when the *first* of them started, so this is a correction and not
    a guess.
    """
    return float(clip_ts) + float(span["start"])


def cues(spans, audio=None, clip_ts=None, duration=None, pad=_PAD):
    """Spans as playable references into the one audio file they came from.

    This is the whole reason the audio is not cut up: a Broadcastify record was
    paid for once and written to disk once, and "seconds 3.70 to 4.86 of that
    file" is a complete description of a transmission. Re-encoding would spend
    CPU to produce bytes that already exist, and would leave the archive holding
    two versions of the same audio under different names.

    `play_start`/`play_end` are padded for a listener and clamped against the
    neighbouring spans, so playing one transmission can never leak a word of
    the one before it. `start`/`end` stay exactly as measured, because the log
    should record what was heard, not what is comfortable to listen to.
    """
    out = []
    for i, s in enumerate(spans):
        floor = spans[i - 1]["end"] if i else 0.0
        ceil = spans[i + 1]["start"] if i + 1 < len(spans) else (
            duration if duration else s["end"] + pad)
        out.append({
            "audio": audio,
            "start": s["start"],
            "end": s["end"],
            "play_start": max(floor, s["start"] - pad),
            "play_end": min(ceil, s["end"] + pad),
            "ts": when(s, clip_ts) if clip_ts is not None else None,
            "text": s["text"],
        })
    return out


def fragment(url, cue):
    """`url` with a media-fragment range, e.g. "/api/clip?id=7#t=3.45,4.81".

    Browsers implement this on a plain <audio src>, which is what lets the radio
    bar give every transmission its own play button while every one of them
    points at the same untouched mp3.
    """
    return f"{url}#t={cue['play_start']:.2f},{cue['play_end']:.2f}"
