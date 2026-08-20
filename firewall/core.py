"""Shared state and the audio-to-screen path. The calls currently running."""
import copy, re, sys, threading, time
from collections import deque
from pathlib import Path
from . import (geo as _geo, incidents as _incidents, parse as _parse,
               places as _places, segments as _segments)

_lock = threading.Lock()
_state = {"purdue": None}
_health = {"ok": True, "error": None, "last_ok": None}
_whisper = None

# ------------------------------------------------------------- live calls
# Every call still running, most recent dispatch first. This was one slot, and
# one slot was wrong: West Lafayette and Purdue work at the same time most
# evenings, so a Purdue medical toned out thirty seconds after a WLFD structure
# fire took the fire off the hallway wall while it was still burning.
_calls = []

# Four is what a glanceable screen holds -- one headline and a short stack
# beside it -- and it bounds the payload: /api/current is polled every two
# seconds, and four calls with full _TAPE_ROWS conversations measures 21KB of
# JSON against 4KB for one. More cards than this is a log, not a display.
_MAX_CALLS = 4

# A cleared call stays up a minute after the radio closes it. Retiring it on the
# "code 4" is technically correct and reads as a glitch: someone glancing up
# sees the call vanish rather than sees it close.
_CLEAR_GRACE = 60

# Calls the radio has closed and _prune has taken off the wall, kept aside in
# case they come back. A crew that clears and is immediately recalled is on the
# same call, and without this the transmission that says so arrives with nothing
# live on the department and is dropped -- the call's units, type, address and
# whole conversation stranded in a closed record while the screen shows nothing.
#
# The live dict is kept rather than the incident on disk, and that is the point:
# a resurrected call has to come back as ITSELF, same id, same "ts", and it is
# "ts" that selects its own group off the tape in _rows_for, so its transcript
# comes back with it. Rebuilding one out of incident.json would be a lossy
# translation -- the log has no status, no eta and a different id -- of a file
# that may not exist at all, since incident_dir can be unset and the display
# still has to work.
#
# Bounded by the same window the log uses (incidents.REOPEN_WINDOW) and by
# _MAX_CALLS entries, so this is a short tail and never a second call list.
_cleared = []

# snapshot() retires stale calls itself -- a quiet call goes stale whether or not
# anything is transmitting -- but __main__ calls it with no arguments, so the
# silence window is remembered from the last publish() instead of passed in. It
# cannot change without a restart anyway.
_hold_seconds = [600]

# The contract /api/current publishes for each call. Built explicitly rather
# than by copying the live dict so internal bookkeeping (last_ts) stays internal.
_PUBLIC_KEYS = ("id", "dept", "ts", "transcript", "type", "address", "city",
                "units", "eta", "status", "reopenings")

# --------------------------------------------------------------- radio tape
# The audio behind each transcript, held in memory so the display can play it
# back. It is captured on the way past rather than fetched again: the source
# already paid for these bytes once (Broadcastify bills per record read), and
# audio_dir can be unset, in which case the clip exists nowhere else by the
# time the browser asks for it.
#
# Bounded twice, by rows and by bytes, because a busy afternoon on two
# talkgroups would otherwise grow this without limit in a process that is
# meant to run for months.
#
# Forty clips was sized for one call on screen. With _MAX_CALLS of them the
# oldest live call's own conversation was being evicted while the call was still
# on the wall, so the cap is now _MAX_CALLS * _TAPE_ROWS -- exactly what it
# takes to hold every row every live call can show. The byte cap is still the
# real ceiling: 96 trunked-radio clips run well under 48MB.
#
# Rows and bytes stopped being the same thing when one record started producing
# one row per keyup (see segments.py). The audio is stored once per RECORD in
# _clips and referenced by however many rows came out of it, so a two-keyup
# grant costs two rows and one blob: charging its bytes twice against the 48MB
# ceiling would evict a live call's own conversation to make room for a copy of
# something already held.
_TAPE_CLIPS = 96
_TAPE_BYTES = 48 * 1024 * 1024
_TAPE_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav",
              ".m4a": "audio/mp4", ".ogg": "audio/ogg"}
_tape = deque()                 # rows, oldest first; several may share one clip
_clips = {}                     # clip id -> {ident, mime, data, rows}
_tape_seq = 0


def _clip_ident(p):
    """What makes two paths the same recording, for the blob store.

    The path on its own is not an identity here. Broadcastify's download goes
    to a NamedTemporaryFile whose name the OS is free to hand out again once it
    is unlinked, and audio_dir names a clip by the second it started, so two
    records landing in the same second on one talkgroup write over each other.
    The inode answers both, and size plus mtime is the belt to that braces.
    """
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _held(ident):
    """The id this record is already stored under, or None. Caller holds the lock."""
    return next((k for k, c in _clips.items() if c["ident"] == ident), None)


def _evict():
    """Drop the oldest rows until the tape is inside both caps. Caller holds the lock.

    A clip's bytes are freed only when the last row referencing them goes, which
    is why this recounts rather than subtracting as it pops: popping the first
    of a grant's two rows frees nothing, and the loop has to keep going.
    """
    def bytes_held():
        return sum(len(c["data"]) for c in _clips.values())

    while len(_tape) > _TAPE_CLIPS or (bytes_held() > _TAPE_BYTES
                                       and len(_tape) > 1):
        _tape.popleft()
        live = {r["clip"] for r in _tape}
        for k in [k for k in _clips if k not in live]:
            del _clips[k]


def _tape_put(dept, text, ts, audio, dispatch, span=None):
    """Keep one transmission's audio. Returns its row id, or None if there is none.

    `span` is the keyup this row is, as a reference into the record: see
    segments.py. Its play range is carried on the row so the display can play
    just that stretch of a shared clip. It is None -- meaning the row is the
    whole record -- both for a source that does not split (mock has no audio at
    all) and for a record that turned out to hold exactly one keyup, which is
    the overwhelmingly common case and is left byte-for-byte as it was: no
    fragment on the URL, nothing for a player to get wrong.
    """
    global _tape_seq
    p = Path(audio) if audio else None
    if not p or not p.is_file():
        return None
    mime = _TAPE_MIME.get(p.suffix.lower())
    if not mime:
        return None
    ident = _clip_ident(p)
    # Read the bytes only for a record we are not already holding, and outside
    # the lock, because the second keyup of a grant arrives here microseconds
    # after the first and re-reading the mp3 for it would be pure waste.
    with _lock:
        fresh = _held(ident) is None
    data = None
    if fresh:
        try:
            data = p.read_bytes()
        except OSError as e:
            print(f"  !  clip not kept for replay ({type(e).__name__}: {e})",
                  file=sys.stderr)
            return None
    with _lock:
        cid = _held(ident)
        if cid is None:
            if data is None:
                # Held when we looked, evicted before we got the lock. Rare
                # enough to be worth reading under the lock rather than adding
                # a retry loop, and losing the row instead would be worse.
                try:
                    data = p.read_bytes()
                except OSError as e:
                    print(f"  !  clip not kept for replay "
                          f"({type(e).__name__}: {e})", file=sys.stderr)
                    return None
            _tape_seq += 1
            cid = str(_tape_seq)
            _clips[cid] = {"ident": ident, "mime": mime, "data": data, "rows": 0}
        c = _clips[cid]
        c["rows"] += 1
        # Row ids stay unique for the life of the process: the count only ever
        # goes up, and a clip that is evicted and later arrives again is a new
        # clip id. The display keys its list on this, so two rows off one grant
        # must not collide.
        rid = f"{cid}-{c['rows']}"
        _tape.append({"id": rid, "clip": cid, "dept": dept, "ts": float(ts),
                      "text": text, "dispatch": bool(dispatch),
                      "start": float(span["start"]) if span else None,
                      "end": float(span["end"]) if span else None,
                      "play_start": (span or {}).get("play_start"),
                      "play_end": (span or {}).get("play_end")})
        _evict()
    return rid


def clip(cid):
    """One kept clip as (bytes, mime), or None. Read by /api/clip.

    Still whole records, and deliberately: a media fragment is resolved by the
    browser, not here, so several rows asking for different stretches of one
    grant all fetch the same blob and seek in it. _send_media's Range support is
    what makes that one download rather than one per row.
    """
    with _lock:
        c = _clips.get(cid)
        return (c["data"], c["mime"]) if c else None


# A conversation ends when the department goes quiet, or when a dispatch
# arrives after long enough that it is plainly a new call. Traffic on a live
# call does not leave _TAPE_GAP of silence in the middle of itself, and a
# dispatch inside _TAPE_MERGE of the last word is the same one being restated.
# These bound the transcript on screen and nothing else: which call a
# transmission belongs to is decided by _live_call, over a wider window.
_TAPE_GAP = 180
_TAPE_MERGE = 150


# Twelve rows was a guess and it was too few: the Medic 16 call ran seventeen
# transmissions, so the display was showing the tail of a conversation whose
# start it had already dropped. Twenty-four covers a normal call end to end and
# still bounds the payload.
_TAPE_ROWS = 24


def _groups(rows):
    """`rows` split at every break in the radio's rhythm, oldest group first."""
    out, start = [], 0
    for i in range(1, len(rows)):
        gap = rows[i]["ts"] - rows[i - 1]["ts"]
        if gap > _TAPE_GAP or (rows[i]["dispatch"] and gap > _TAPE_MERGE):
            out.append(rows[start:i])
            start = i
    if rows:
        out.append(rows[start:])
    return out


def _conversation(rows):
    """The tail of `rows` since the last break in the radio's rhythm."""
    groups = _groups(rows)
    return groups[-1] if groups else []


def _rows_for(call):
    """The transmissions belonging to one call, oldest first.

    Scoped by conversation rather than by the call's timestamp. The obvious
    version -- everything on this department since call["ts"] -- looks right
    and is not: chatter that mentions a fire re-parses as a dispatch and
    replaces the call, which would silently drop the transcript of everything
    said before it. Grouping on the radio's own rhythm survives that.

    With more than one call live on a department the tail is no longer the
    answer, so the group that opened closest to this call's dispatch is. Two
    calls whose traffic genuinely interleaves on one talkgroup land in the same
    group and are shown the same transcript, which is the honest outcome: the
    radio does not separate them either.
    """
    return min(_groups([c for c in _tape if c["dept"] == call["dept"]]),
               key=lambda g: abs(g[0]["ts"] - float(call["ts"])), default=[])


def _row(c):
    """One tape row in the shape /api/current publishes.

    "url" points at the whole record, with a media-fragment range on it when
    this row is one keyup out of several. Two rows off one trunked grant carry
    the same clip id and the same blob, and differ only in that range -- which
    is the point: the bytes were paid for and downloaded once, and "seconds
    3.42 to 4.52 of that file" is a complete description of the second voice.

    play_start/play_end are null, and the URL bare, when the row IS the whole
    record. That is not a special case for the display to handle so much as a
    promise that nothing changed for the single-keyup clips that are almost all
    of them: same URL, same behaviour, no range for a player to mis-seek.
    """
    url = "/api/clip?id=" + c["clip"]
    if c["play_end"] is not None:
        url = _segments.fragment(url, c)
    return {"id": c["id"], "clip": c["clip"], "ts": c["ts"], "text": c["text"],
            "dispatch": c["dispatch"], "url": url,
            # What was heard, exactly as measured, for anything that wants to
            # show the range rather than play it. Distinct from play_start /
            # play_end, which are padded for a listener.
            "start": c["start"], "end": c["end"],
            "play_start": c["play_start"], "play_end": c["play_end"]}


def _public(call):
    """One call in the shape /api/current publishes.

    A fresh dict every time, because handler threads read this while source
    threads are still writing to the live call.

    The audio is left out of the rows. It goes over the wire once, when
    something asks to play it -- several live calls must not turn a two-second
    poll into a stream of megabytes.
    """
    out = {k: copy.deepcopy(call.get(k)) for k in _PUBLIC_KEYS}
    rows = _rows_for(call)
    out["radio"] = [_row(c) for c in rows][-_TAPE_ROWS:]
    # The conversation's true start and its dispatch, carried separately
    # because "radio" is the last _TAPE_ROWS rows and the display times each
    # row against the call, not against whatever survived the slice. Reading
    # radio[0] instead silently re-bases every "+Xs" the moment the window
    # slides, which is what made the offsets on a long call wrong.
    out["radio_start"] = rows[0]["ts"] if rows else None
    out["radio_dispatch_ts"] = next((r["ts"] for r in rows if r["dispatch"]), None)
    return out


def _closed_at(call):
    """When the radio closed this call, or None if it has not."""
    st = call.get("status") or {}
    return (float(st.get("ts") or call["last_ts"])
            if st.get("state") == "clear" else None)


def _prune():
    """Retire the calls that are over. The caller holds the lock."""
    now = time.time()
    live = []
    for c in _calls:
        closed = _closed_at(c)
        if closed is not None and now - closed > _CLEAR_GRACE:
            # Off the wall, but not gone: a closed call is set aside for the
            # rest of incidents.REOPEN_WINDOW in case it comes back.
            _cleared.append(c)
            continue
        if now - c["last_ts"] > _hold_seconds[0]:
            continue
        live.append(c)
    _cleared[:] = [c for c in _cleared
                   if now - (_closed_at(c) or 0) <= _incidents.REOPEN_WINDOW
                   ][-_MAX_CALLS:]
    # Over the cap the stalest goes rather than the oldest: a fire dispatched an
    # hour ago that the radio is still working outranks a quiet call toned out
    # after it.
    while len(live) > _MAX_CALLS:
        live.remove(min(live, key=lambda c: c["last_ts"]))
    _calls[:] = live


def snapshot():
    """Everything /api/current serves. Keys only ever get added to this."""
    with _lock:
        _prune()
        calls = [_public(c) for c in _calls]
        purdue = _state["purdue"]
    head = calls[0] if calls else None
    # The single-call keys are the whole payload as far as any display built
    # before this change is concerned, and they still mean exactly what they
    # meant: the newest call and its radio. Adding "calls" beside them is what
    # lets an old display keep rendering while a new one shows the rest.
    return {**_health, "purdue": purdue, "calls": calls, "call": head,
            "radio": head["radio"] if head else [],
            "radio_start": head["radio_start"] if head else None,
            "radio_dispatch_ts": head["radio_dispatch_ts"] if head else None}


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


# Whisper decodes 8kHz-ish trunked radio badly out of the box: it has never
# heard "Purdue", "Earhart" or "Tippecanoe" in this acoustic context and will
# happily emit "Pretty", "your heart" and "tip a canoe" instead. Passing the
# local vocabulary as `hotwords` biases the decoder toward these tokens, which
# is the single cheapest accuracy win available here: on 20 degraded clips it
# took the scored place names from 13/56 to 37/56.
SCANNER_VOCAB = _places.VOCAB


# Whisper trained on captioned video, and on a transmission that is tones or
# squelch noise it falls back to what captions say when nobody is talking. It
# is not mishearing the radio: there is no speech there to mishear.
#
# Matched clause by clause, because the filler arrives in strings of it
# ("See you next time, okay?"), and only when the whole segment is filler --
# "Medic 16 responding. Thank you." keeps its dispatch.
_FILLER = re.compile(
    r"^(?:"
    r"thanks?(?:\s+(?:you|so\s+much|again|for\s+(?:watching|having\s+us|"
    r"listening|joining\s+us)))?|"
    r"thank\s+you(?:\s+(?:very|so)\s+much)?|"
    r"you'?re\s+welcome|bye(?:\s*bye)?|goodbye|good\s+night|"
    r"see\s+you(?:\s+(?:next\s+time|later|soon|again))?|"
    r"i\s+love\s+you|please\s+subscribe|subscribe\s+to\s+my\s+channel|"
    r"don'?t\s+forget\s+to\s+subscribe|"
    r"you|okay|ok|oh|uh|um|hmm+|mm+|yeah|yes|no|so|and|"
    r"\[[^\]]*\]|\([^)]*\)|"                       # [Music], (upbeat music)
    r"(?:beep|boop|ding|tone|buzz)(?:[\s-]+(?:beep|boop|ding|tone|buzz))*"
    r")$", re.I)


def _is_hallucination(text):
    """Whisper's caption-filler, which real radio traffic never consists of."""
    clauses = [c.strip(" .,!?;-") for c in re.split(r"[,.!?;]", text)]
    clauses = [c for c in clauses if c]
    return bool(clauses) and all(_FILLER.match(c) for c in clauses)


def transcribe(path, cfg, alternate=False):
    """One clip as one string, the way it has always read. See transcribe_spans().

    Kept because a clip's whole transcript is still the right answer for
    --transcribe and for scoring against a hand-typed truth, both of which are
    about the recording rather than about who was speaking in it.
    """
    return " ".join(s["text"] for s in transcribe_spans(path, cfg, alternate)
                    if s["text"]).strip()


def transcribe_spans(path, cfg, alternate=False):
    """One clip as one span per keyup inside it. `alternate` is the second opinion.

    A downloaded record is a channel grant, not a transmission -- the talkgroup
    stays granted through the hang time, so a reply seconds later arrives in the
    same mp3 under the same timestamp. segments.split() recovers the keyups;
    this returns them with a play range attached. See segments.py for the
    measurements behind the thresholds.

    Always at least one span, and that is deliberate rather than defensive: a
    keyup of nothing but tones is a real transmission and the one a listener
    most wants to hear, so it comes back as a single empty-text span covering
    the record instead of an empty list the callers would each have to
    special-case.
    """
    global _whisper
    model_name = cfg["whisper_model"]
    if _whisper is None:
        from faster_whisper import WhisperModel
        # Fetched once and cached under ~/.cache/huggingface (base.en ~140MB,
        # small.en ~480MB); after that this is a ~2s load, so say so rather
        # than implying a download every time.
        print(f"  .  loading whisper '{model_name}' "
              f"(cached after a one-time download)")
        _whisper = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = _whisper.transcribe(
        str(path), language="en",
        # Required, not an optimisation. Without word timings whisper's segment
        # ends are token-window bookkeeping rather than observations of the
        # audio, and on the clip this whole mechanism exists for -- Medic 16's
        # "that is a negative." answered by the dispatcher's "Clear, thank you."
        # -- it dated the two segments [0.00,3.04] and [3.04,5.04], measuring
        # the gap at the real keyup as 0.00s. Asked for words, the same clip
        # resolves: last word out at 2.48, next in at 3.42. Measured here over
        # the twelve archived clips, 13.2s -> 16.3s, about 23% -- which is what
        # getting the speaker turn right costs, and it is worth it.
        word_timestamps=True,
        # VAD off by default, which is not the obvious choice. Silero decides a
        # noisy transmission stops being speech partway through and truncates
        # the rest: measured over 20 clips it cost 11 of 56 scored words, and
        # bought nothing -- noise-only keyups still come back empty, because
        # whisper's own no_speech_threshold already drops them. Turn it back on
        # with FIREWALL_WHISPER_VAD=true for long recordings with real silence
        # in them, where the trim is worth more than the tail.
        vad_filter=bool(cfg.get("whisper_vad")),
        # 8 rather than the usual 5: measured, it is worth a name per twenty
        # clips for 6% more time, and a name is the whole point.
        beam_size=int(cfg.get("whisper_beam", 8)) if not alternate else 10,
        # A dispatch is a handful of seconds of unrelated speech. Carrying the
        # previous window's text forward only lets one bad guess poison the
        # rest of the clip.
        condition_on_previous_text=False,
        # The second opinion drops the vocabulary bias on purpose. If the two
        # passes disagree, an unbiased hypothesis is the useful kind: a place
        # name it produces without being pushed toward one is real evidence.
        hotwords=None if alternate else (cfg.get("whisper_vocab") or SCANNER_VOCAB),
        # Separate from the hotwords, and doing a different job: the hotwords
        # say which names exist, this says what a dispatch sounds like.
        initial_prompt=None if alternate else _places.DISPATCH_STYLE,
        temperature=0.2 if alternate else 0.0,
    )
    dur = float(info.duration)
    # split() takes a `drop` predicate so caption filler can be removed BEFORE
    # the gaps are measured, and _is_hallucination is deliberately NOT passed
    # to it. Under the settings this program actually decodes with -- beam 8,
    # the scanner vocabulary, the dispatch-style prompt -- the second keyup of
    # 1787205431 comes back as " Thank you." at [3.42,4.52], which is filler by
    # every test we have and is in truth the dispatcher saying "Clear, thank
    # you." Dropping it first deletes the exact transmission this mechanism was
    # built to recover. (segments.py's own figures were taken with a plainer
    # decode, which hears the words; ours does not, and ours is what runs.)
    #
    # So the timings are measured over everything that was said, and the filler
    # test is applied afterwards, per keyup: a span that is nothing but caption
    # boilerplate keeps its place on the tape and loses its words, which is the
    # same empty-keyup row a squelch-only transmission has always produced. It
    # is playable, and playing it is how you find out what was really said.
    spans = _segments.split(list(segments), duration=dur)
    for s in spans:
        if _is_hallucination(s["text"]):
            s["text"] = ""
    if not spans:
        spans = [{"start": 0.0, "end": dur, "text": "", "gap": None}]
    if len(spans) > 1:
        for s, cue in zip(spans, _segments.cues(spans, duration=dur)):
            s["play_start"] = round(cue["play_start"], 2)
            s["play_end"] = round(cue["play_end"], 2)
    else:
        # One keyup means the record IS the transmission, so it is played whole,
        # exactly as before. Trimming it to whisper's idea of where the speech
        # starts would put a range on every clip in the archive to gain nothing
        # and risk clipping a first syllable.
        spans[0]["play_start"] = spans[0]["play_end"] = None
    # Two decimals because that is the resolution whisper reports and the
    # resolution a media fragment is written at. Left raw, a span start reads
    # 0.5800000000000005 in the JSON the display polls twice a second.
    for s in spans:
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    return spans


# ------------------------------------------------------------- call status
# The display used to call a truck "on scene" the moment geo's estimate ran out,
# which is an estimate rendered as an observation. On the Medic 16 call the
# estimate said on scene at +140s and at +152s the radio was still "Medic 16 en
# route". The radio knows; this program already hears all of it. So status is
# read off the traffic and nothing else moves it.
#
# Ordered, and only ever moves forward: a second unit calling en route after the
# first has arrived does not un-arrive the call.
#
# The two middle states are the transport leg, and they exist because an EMS
# call does not end at the scene: the medic loads the patient, drives to the
# hospital, and is only free once it clears there. The archive has all three
# transmissions -- "Medic 16 is going to be en route to IU", "Medic 16 has
# arrived at IU", then clearing IU -- and without these states the second one
# read as "on scene", which is a confident lie: the unit was five miles from
# the incident and the scene had been empty for minutes.
#
# The known cost of putting them above on_scene: on a fire where a medic
# transports one patient while the engine still works, the headline follows the
# medic and says "at IU" while the fire burns. That is the same one-talkgroup
# ambiguity _live_call documents, it is rarer than a plain EMS run, and the
# display shows the transmission that moved the status underneath it.
_STATUS_RANK = {"dispatched": 0, "enroute": 1, "on_scene": 2,
                "transporting": 3, "at_hospital": 4, "clear": 5}

# "16 is out" is out of the house, not out at the address, so the lookahead
# keeps it away from the arrival phrasing below.
_ENROUTE_RE = re.compile(
    r"\b(?:en\s*-?\s*route|enroute|on\s+route|responding|responds|"
    r"respond\b(?!\s+to)|on\s+the\s+way|rolling|(?:is|are)\s+out\b(?!\s+at))",
    re.I)

# "10-97" is arrival on this system. "out at" is the other way units announce it
# ("Engine 2 out at 300 North Street"), and needs something after it so a bare
# "we're out" does not count.
_ARRIVED_RE = re.compile(
    r"\b(?:on\s+(?:the\s+)?scene|on\s+(?:the\s+)?location|arrived|"
    r"out\s+at\s+(?=\w)|10[-\s]?97)\b", re.I)

# A dispatcher sending someone to help at a scene says "respond to X for an
# on-scene assist", and "we'll advise on scene" is a promise, not an arrival.
# Neither is a truck reporting where it is, so an arrival phrase alongside any
# of these is not counted.
_NOT_ARRIVED_RE = re.compile(
    r"\brespond\b|\bstand\s*by\b|\badvise\b|\bon[\s-]?scene\s+assist\b|"
    r"\bstart\s+(?:me|us|another)\b|\bwhen\s+(?:you|they)\b", re.I)

# A crew reporting itself names itself, one way or the other. Requiring that is
# what keeps a dispatcher's stock phrasing from marking the call arrived: this
# is the mistake the whole mechanism exists to stop making, so it fails closed
# and leaves the call "dispatched" rather than guessing.
#
# The bare number is in there because whisper drops the unit word about as often
# as it keeps it -- "Medic 16 is out" comes back as "16 is out" -- and a number
# in front of a status verb is still a truck naming itself.
_SELF_RE = re.compile(r"\b(?:we|we'?re|we\s+are|i'?m|i\s+am)\b|"
                      r"\b\d{1,3}\s+(?:is|are|will\s+be)\b", re.I)

# Reused rather than restated so the display and the incident log agree on when
# a call is over -- there is no version of this where the log has filed the
# incident closed and the screen still shows a truck working. Both of these are
# maintained next to each other in incidents.py: CLOSERS is the readable list of
# what "the call is finished" sounds like, and _HOSPITAL_RE is where a medic
# goes in between.
_CLEAR_RE = _incidents.CLOSERS
_HOSPITAL_RE = _incidents.HOSPITAL

# Kept out of _ENROUTE_RE deliberately: this word carries the destination with
# it, so "Medic 16 transporting" is the hospital leg even when whisper lost the
# name of the hospital.
_TRANSPORT_RE = re.compile(r"\btransport(?:ing|ed)?\b", re.I)


def _self_reported(text):
    """Does this sound like a unit talking about itself, not about a call?"""
    return bool(_parse.UNIT_RE.search(_parse.spoken_numbers(text))
                or _SELF_RE.search(text))


def read_status(text):
    """The status a transmission asserts, or None if it asserts none.

    Clearing is tested first because the phrasings overlap on purpose: a crew
    clearing the hospital says the hospital's name, and that transmission ends
    the call rather than reporting an arrival at it.

    Where a unit says it is going or has got to is then what separates the scene
    from the transport: the words are identical -- "en route", "arrived" -- and
    only the destination says which leg of the call this is.
    """
    if _CLEAR_RE.search(text):
        return "clear"
    if not _self_reported(text):
        return None
    # "Transporting" needs no destination to be one: a unit only ever transports
    # a patient, and only ever to a hospital.
    hospital = bool(_HOSPITAL_RE.search(text) or _TRANSPORT_RE.search(text))
    if (_ARRIVED_RE.search(text) and not _NOT_ARRIVED_RE.search(text)):
        return "at_hospital" if hospital else "on_scene"
    if _ENROUTE_RE.search(text) or _TRANSPORT_RE.search(text):
        return "transporting" if hospital else "enroute"
    # A crew that names the hospital and nothing else is standing in it. This is
    # the shape the arrival phrasings miss -- "Medic 17 at the hospital", the
    # transmission that used to hand the address slot the word "hospital" -- and
    # reading it as an arrival somewhere the call is not is the point.
    return "at_hospital" if hospital else None


def _live_call(dept, ts, cfg):
    """The live call a transmission on `dept` at `ts` most likely belongs to.

    Routed to the most recent live call on the department, and that is a
    documented best guess rather than a certainty: the radio gives no reliable
    way to tell two simultaneous calls on one talkgroup apart -- both are
    interleaved in the same audio and nothing in "Engine 2 is on scene" says
    which of them it closes. The newest wins because a crew just toned out is
    the one talking, and a wrong guess moves one status, never a whole call.

    The window is the incident log's, and it is measured from the call's last
    traffic rather than from its dispatch. Measured from the dispatch it was a
    limit on how long a call may RUN, which is not a thing the radio has: the
    Medic 16 run of 2026-08-20 was toned out at 01:46 and cleared IU some forty
    minutes later, so the call's own "clearing IU" fell outside its own window
    and could not move it off the wall. A call that is still being talked about
    is still live; one nobody has mentioned in this long belongs to a dispatch
    we never heard, and a stale "code 4" must still not clear a fire that is
    still burning.

    Silence is what ends a call here, exactly as it is in _prune, and the two
    read the same last_ts. They do not disagree: hold_seconds (600) is the
    shorter of the two by default, so a call that has gone quiet is off _calls
    before this window could ever hand it a transmission.
    """
    gap = int(cfg.get("incident_gap_seconds", 900))
    return next((c for c in _calls if c["dept"] == dept
                 and 0 <= ts - float(c["last_ts"]) <= gap), None)


def _bring_back(call, state, text, ts):
    """A closed call, running again. Caller holds the lock.

    THE exception to the monotonic status machine, and the only one. The rank
    guard exists to stop a second unit's "en route" un-arriving a call that a
    first unit has already reached -- two reports about the same stretch of one
    call, where the later one is stale. This is not that: the radio closed the
    call and has now said a unit is working it again, so the two reports are
    about different stretches of the call and the later one is the true one.
    Which is why it is written here, as a named move with its own evidence test
    (incidents.reopens), rather than as a hole in the guard below: nothing that
    fails that test can ever go backwards.

    It lands in the state the reopening transmission asserts, and never in a
    state nobody reported. That is free rather than lucky: reporting a position
    IS the price of admission -- a call cannot be reopened by traffic that does
    not say where a unit is -- so "Medic 16 back on scene" comes back on_scene,
    and there is never a moment where the display has to show a guess.
    """
    call.setdefault("reopenings", []).append(
        {"closed": _closed_at(call), "ts": float(ts), "text": text.strip()[:160]})
    call["status"] = {"state": state, "ts": float(ts), "text": text.strip()[:160]}
    call["last_ts"] = max(call["last_ts"], float(ts))
    if call not in _calls:
        _calls.append(call)
        _calls.sort(key=lambda c: c["ts"], reverse=True)


def _reopens(call, text, ts, state):
    """Does this transmission bring `call` back? Shared with the incident log."""
    return _incidents.reopens(call, text, ts, state, closed=_closed_at(call))


def _note_status(dept, text, ts, cfg, state=None):
    """Move the call this transmission belongs to along, and keep it alive.

    Returns the new status dict, or None when nothing changed.
    """
    state = read_status(text) if state is None else state
    with _lock:
        call = _live_call(dept, ts, cfg)
        if not call:
            # Nothing running on the department. The one thing this can still
            # be is a call that closed minutes ago coming back -- a crew
            # recalled to the address it just cleared -- so the short tail of
            # closed calls gets the same evidence test, newest first.
            back = next((c for c in reversed(_cleared) if c["dept"] == dept
                         and _reopens(c, text, ts, state)), None)
            if back is None:
                return None
            _cleared.remove(back)
            _bring_back(back, state, text, ts)
            _prune()                    # the returning call may breach the cap
            return back["status"]
        # Any traffic on a call is evidence it is still running, so it holds off
        # the silence timer whether or not it says where a unit is.
        call["last_ts"] = max(call["last_ts"], float(ts))
        now = (call.get("status") or {}).get("state", "dispatched")
        # Still on the wall for its grace minute, and back on the air inside it:
        # the same reopening, caught before _prune ever set the call aside.
        if now == "clear":
            if not _reopens(call, text, ts, state):
                return None
            _bring_back(call, state, text, ts)
            return call["status"]
        if not state:
            return None
        if _STATUS_RANK[state] <= _STATUS_RANK.get(now, 0):
            return None
        # The text rides along so the display can show what it heard rather than
        # asking the viewer to take "ON SCENE" on faith. Trimmed because whisper
        # occasionally returns a paragraph.
        call["status"] = {"state": state, "ts": float(ts), "text": text.strip()[:160]}
    # Only en route is worth re-assessing: it replaces the turnout guess with a
    # heard fact. Re-running it on arrival would hand back the turnout guess it
    # just displaced, and by then nobody is counting down anyway. "transporting"
    # is en route too, but to a hospital rather than to the call, so an ETA to
    # the scene must not be re-based on it.
    if state == "enroute":
        try:
            eta = _geo.assess(call, cfg)
        except Exception as e:
            # Same rule as publish(): a geocoder outage costs an ETA, never the
            # status the radio just gave us.
            print(f"  !  eta not revised ({type(e).__name__}: {e})", file=sys.stderr)
        else:
            with _lock:
                call["eta"] = eta
    return call["status"]


def _worth_retry(text):
    """Does this read like a dispatch that lost its address?

    Chatter is short and unit-less; re-decoding all of it would triple the work
    for nothing. A unit designator or a dispatch verb is the cheap tell.
    """
    if _parse.UNIT_RE.search(text):
        return True
    return (len(text.split()) >= 8
            and re.search(r"\brespond|dispatch|en\s?route|alarm|report",
                          text, re.I) is not None)


def _second_opinion(audio, cfg, span):
    """Re-decode `audio` unbiased and return what it heard over `span`.

    The retry is per keyup now that publish() is, and it has to be: the whole
    point is to hand _parse a dispatch, and a dispatch glued to the "Medic 16
    responding" that answered it is what this program has spent its life
    getting wrong.

    The unbiased pass may draw the keyup boundaries somewhere else, so its
    spans are matched by overlap rather than by index, and only a span that
    lies mostly inside ours counts. That fails in the safe direction: if the
    second pass hears the whole grant as one run of speech, nothing is mostly
    inside anything and the retry contributes nothing, which leaves the first
    decode standing rather than re-gluing the two voices we just separated.
    """
    spans = transcribe_spans(audio, cfg, alternate=True)
    if span is None:
        return " ".join(s["text"] for s in spans if s["text"]).strip()
    got = []
    for s in spans:
        overlap = min(span["end"], s["end"]) - max(span["start"], s["start"])
        if s["text"] and overlap > 0 and overlap * 2 >= s["end"] - s["start"]:
            got.append(s["text"])
    return " ".join(got).strip()


def _refine(call, f, text, ts):
    """Fold a restated dispatch into the call it restates. Caller holds the lock.

    The fields used to follow the newest parse, which is how a call ended up
    wearing a headline nobody was dispatched to: whatever the last transmission
    happened to resolve to won, including nothing. What may change and what may
    not is _incidents.improve(), shared with the log so the two cannot drift.
    """
    better = _incidents.improve(call, f)
    for u in f.get("units") or []:
        if u not in call["units"]:
            call["units"].append(u)
            better = True
    # The headline follows the better parse and only the better one: a follow-up
    # that added nothing must not replace a full dispatch line with "stand by".
    if better:
        call["transcript"] = text
    call["last_ts"] = max(call["last_ts"], ts)


def _is_dispatch(f, text):
    """Is this parse enough to be a call at all?

    It used to be "anything with a type or an address", and the archive shows
    what that let through: "Dispatch, Medic 16 is arrived at you." parses to no
    type and the location "you", and that was a call -- an untitled card at a
    place that does not exist, which also stole the wall from the call it was
    actually about.

    So a call needs either a recognised call type, or a location the parser can
    stand behind: somewhere the gazetteer knows, a corner, a road, a street
    address. A one-word fragment counts only when the dispatcher's own phrasing
    is around it, which is what keeps a real dispatch to a place we have never
    heard of ("respond to Walmart") while dropping the chatter that merely ran
    into a preposition. With neither, there is nothing to put on a screen: no
    headline, no address, nothing a person walking past could act on.
    """
    rank = _incidents.address_rank(f.get("address"))
    if f.get("type"):
        return True
    return rank >= 2 or (rank >= 1 and _incidents.TONE_OUT.search(text) is not None)


def publish(dept, text, ts, cfg, audio=None, span=None):
    """Parse a transcript and, if it looks like a dispatch, put it on screen.

    Every transmission is filed against an incident either way: the chatter
    after a dispatch is what says how the call actually went, and it is only
    worth anything if it was kept.

    One call per KEYUP, not per downloaded record: `span` says which stretch of
    `audio` this transmission is, and `ts` is that keyup's own absolute time,
    not the grant's. That is what makes the rest of this file work on the right
    unit -- "Dispatch, Medic 16 is clearing IU." closes the call and the "Medic
    16." that answered it a second and a quarter later is a separate row, where
    before they were one string and the closer fired on both at once. `span` is
    None for a source with no audio to split, and everything below is written to
    read the same either way.
    """
    # A keyup that came back with no words -- tones, a stepped-on reply, a unit
    # whisper's no_speech_threshold or the filler filter emptied out -- used to
    # return here, before the tape and before the incident log. That threw away
    # audio the source had already paid for and left the display playing a
    # "go ahead" with nothing in front of it, which is unreadable. It is a real
    # transmission and the one a listener most wants to hear, so it is kept and
    # filed like any other; it simply parses to nothing, so it can never be a
    # dispatch and can never assert a status.
    #
    # Its text is the empty string on purpose rather than a placeholder: the
    # transcript is what was heard, and nothing was. The display is what says
    # so, rendering an empty row as "(no speech)".
    text = text.strip()
    f = _parse.parse(text, cfg) if text else {}
    # A dispatch with no location is the one outcome worth spending time on:
    # it is unusable on screen, and the address is usually there in the audio.
    # So anything that reads like a dispatch but parsed without one gets a
    # second decode, and the second opinion is kept only if it finds a place.
    if (audio and text and not f.get("address") and cfg.get("whisper_retry", True)
            and _worth_retry(text)):
        try:
            alt = _second_opinion(audio, cfg, span)
        except Exception as e:
            alt = None
            print(f"  !  retry failed ({type(e).__name__}: {e})", file=sys.stderr)
        if alt and alt.strip() and alt != text:
            g = _parse.parse(alt, cfg)
            if g.get("address"):
                print(f"  .  second pass recovered a location: {alt!r}")
                text, f = alt, g
    # A hospital is where the patient goes, never where the call is. Dropped
    # here, once, so neither the screen nor the log can be handed one as a
    # location: "Medic 16 is going to be en route to IU" is a medic leaving a
    # scene it has already been at, and the incident on disk at 1787204769 --
    # address "IU" -- is what happens when nothing takes it away.
    if _incidents.is_hospital(f.get("address")):
        f = {**f, "address": None}
    is_dispatch = bool(f) and _is_dispatch(f, text)
    # Keep the audio alongside its transcript so the display can play the call
    # back rather than only report it. Chatter is kept as well: what actually
    # happened on the call is said on the radio afterwards, not in the parse.
    _tape_put(dept, text, ts, audio, is_dispatch, span)
    # Read once and handed to both, because a call coming back turns on what
    # this transmission reports (see incidents.reopens) and the log and the
    # screen must not answer that question differently.
    state = read_status(text) if text and not is_dispatch else None
    try:
        _incidents.record(cfg, dept, text, ts, audio,
                          f if is_dispatch else None, span=span, state=state)
    except Exception as e:
        print(f"  !  incident log failed ({type(e).__name__}: {e})", file=sys.stderr)
    if not is_dispatch:
        # Chatter is not nothing: this is where a crew says it is rolling or
        # standing in the driveway, which is the only honest source for what the
        # display shows as the call's status.
        st = _note_status(dept, text, ts, cfg, state)
        if st:
            print(f"  .  {dept} now {st['state']}: {text!r}")
        elif not text:
            print(f"  .  kept, nothing said on it ({dept})")
        else:
            print(f"  .  ignored (not a dispatch): {text!r}")
        return None
    with _lock:
        _hold_seconds[0] = int(cfg.get("hold_seconds", 600))
        _prune()
        # Which call this belongs to is decided exactly the way a status
        # transmission's is, by _live_call, and that is the fix: the same
        # transmission must not be able to move one call's status and fork a
        # second copy of it in the same breath. It used to be a hundred and
        # fifty seconds from the last word, and past that boundary the chatter
        # that _refine had been folding in correctly all along opened a whole
        # new call instead -- which is how a structure fire came to be sitting
        # on the wall under a headline nobody was dispatched to.
        #
        # So while a department's conversation is alive, a re-parse refines the
        # call it is about. A department can still be given a second call in the
        # middle of the first, and that is not rare enough to break -- it just
        # has to be shown rather than assumed, which is new_dispatch().
        call = _live_call(dept, float(ts), cfg)
        if call and ((call.get("status") or {}).get("state") == "clear"
                     or _incidents.new_dispatch(call, f, text)):
            # A cleared call is over even though it is still on the wall for its
            # grace minute. Nothing toned out after "code 4" belongs to it.
            call = None
        if call:
            _refine(call, f, text, float(ts))
        else:
            call = {
                # Stable for the call's whole life, restatements included, so
                # the display can follow one card between polls rather than
                # guessing from an index that shifts every time a call retires.
                "id": f"{dept}|{int(ts)}",
                "dept": dept,
                "ts": ts,
                "last_ts": float(ts),
                "transcript": text,
                # None when the parser did not recognise the call type, and no
                # word is put in its place. A placeholder here reads on the wall
                # as a call type the dispatcher said, and downstream it is
                # indistinguishable from one: the display promotes the address
                # to the headline instead, which is true.
                "type": f.get("type"),
                "address": f.get("address"),
                "city": f.get("city"),
                "units": f.get("units") or [],
                "eta": None,
                # Dispatched is all we know at the instant the tones drop, and
                # saying so in the payload is the point: the display has to
                # distinguish what was heard from what was modelled, and there is
                # no third value meaning "unknown" for it to misread.
                "status": {"state": "dispatched", "ts": float(ts),
                           "text": text.strip()[:160]},
                # Every close this call has already come back from, oldest
                # first, so a reopened call can never be mistaken on screen for
                # one that never ended. Empty rather than absent: the display
                # reads a list, always.
                "reopenings": [],
            }
            # The department has been given something new, and that ends the
            # last call's chance to come back -- the same rule the incident log
            # applies when a dispatch arrives. Its traffic belongs to this call
            # now.
            _cleared[:] = [c for c in _cleared if c["dept"] != dept]
            _calls.append(call)
            _calls.sort(key=lambda c: c["ts"], reverse=True)
            _prune()                    # the new call may have breached the cap
    # Best-effort: a geocoder outage or an unparseable address must never stop
    # a dispatch reaching the screen, which is the whole point of the display.
    try:
        eta = _geo.assess(call, cfg)
    except Exception as e:
        print(f"  !  eta unavailable ({type(e).__name__}: {e})", file=sys.stderr)
        eta = None
    with _lock:
        # A restatement that finally carried an address gets its ETA here. A
        # restatement that carried nothing new must not blank the estimate the
        # first transmission already earned.
        if eta or not call.get("eta"):
            call["eta"] = eta
    eta = call.get("eta") or {}
    note = ""
    if eta.get("passes_you"):
        note = f"  passes you in ~{eta['pass_eta']}s ({eta['closest_metres']}m)"
    elif eta.get("scene_eta") is not None:
        note = f"  on scene in ~{eta['scene_eta']}s from {eta['station']}"
    print(f"  ok {dept}: {call['type'] or '(no call type)'} @ {call['address']} "
          f"{call['units']}{note}")
    return call
