"""Group transmissions into incidents, keep their audio, and replay them later.

A dispatch is not one transmission, it is a conversation: the dispatch itself,
the units acknowledging, the arrival, the disposition. The display only ever
shows the first of those, so everything else was being transcribed and thrown
away -- including the transmissions that say what actually happened.

An incident opens on a dispatch, collects every transmission on that department
afterwards, and closes when the radio says it is over -- CLOSING_PHRASES below
is the list of what that sounds like -- or after `incident_gap_seconds` of
quiet. Each one is a directory of audio files plus an incident.json, which is
what `firewall --replay` reads.
"""
import json, os, re, shutil, sys, time
from pathlib import Path

from . import parse as _parse, places as _places

# ------------------------------------------------------------ call completion
# Where a medic takes a patient. This is a list because a call does not end at
# the scene: the unit transports, and the transport is a leg of the same call
# that only finishes at the hospital door. Naming the destinations is what lets
# the rest of this file tell "en route to IU" (leaving the scene with a patient)
# from "clearing IU" (the call is finally over), and it is what stops a hospital
# turning up as an incident's address.
#
# "IU" is IU Health Arnett in Lafayette, which is what dispatch and crews
# actually say. "St E" is how Saint Elizabeth is said out loud. The generic ones
# are here because whisper loses proper nouns first and a crew says "the ER" as
# often as it says the name.
HOSPITALS = (
    r"IU(?:\s+Health)?(?:\s+Arnett)?",
    r"(?:St\.?|Saint)\s+E(?:lizabeth)?",
    r"Franciscan(?:\s+Health)?",
    r"Home\s+Hospital",
    r"(?:the\s+)?hospital",
    r"(?:the\s+)?E\.?\s?R\.?",
    r"(?:the\s+)?emergency\s+(?:room|department)",
)
_HOSPITAL = r"(?:the\s+)?(?:" + "|".join(HOSPITALS) + r")"
HOSPITAL = re.compile(r"\b" + _HOSPITAL + r"\b", re.I)


def is_hospital(where):
    """Is this location nothing but a hospital -- a transport destination?

    Whole-string, so a genuine dispatch to a hospital's own street address is
    untouched: "IU" is a destination, "5165 McCarty Lane" is a place a truck is
    sent. The incident at 1787204769 is on disk with the address "IU" because
    nothing used to ask this question.
    """
    return bool(where) and bool(re.fullmatch(_HOSPITAL, str(where).strip(" .,"),
                                             re.I))


# How dispatchers and crews say a call is finished. This is the list to edit
# when you hear a phrasing that is missing: one entry per phrasing, each with a
# note on what it means on the radio, joined into CLOSERS below. Entries are
# regex fragments that must start and end on a word character -- the word
# boundaries are added once, at the join -- and they are matched against the
# whole transmission, so keep each one specific enough that ordinary traffic
# cannot trip it. A false closer takes a running call off the wall.
CLOSING_PHRASES = (
    # Scene secure, no further assistance needed. The common one on this system.
    r"code\s*(?:4|four)",
    # Back available. "and service" is not a typo: it is how whisper hears "in
    # service" off a narrowband radio, and it comes back that way often enough
    # to matter. "back in service" is covered by the same entry.
    r"(?:in|and)\s+service",
    # The scene itself is done with. Note that a bare "clear" is deliberately
    # NOT here: "Clear." on its own is how this dispatcher acknowledges routine
    # traffic, twice in the archive, on calls that were still running.
    r"clear(?:ing)?\s+(?:the\s+)?scene",
    # The call never happened, or someone else took it.
    r"cancel(?:led)?",
    r"disregard",
    # Heading home, which on this system is said as often as "in service".
    r"returning\s+to\s+(?:quarters|station)",
    r"(?:back\s+)?in\s+quarters",
    # The end of an EMS call, and the one this list was missing: the patient is
    # handed over and the unit is leaving the hospital. Everything before this
    # -- "en route to IU", "arrived at IU" -- is the transport leg of a call
    # that is still running, so the hospital name alone must never close it.
    rf"clear(?:ing|ed)?\s+(?:of\s+|from\s+)?{_HOSPITAL}",
    # Ten-codes for available. 10-8 is in service; 10-19 is returning.
    r"10[-\s]?8",
    r"10[-\s]?19",
    # A crew saying it can take the next run. Written as a self-report on
    # purpose: "no units available" is dispatcher traffic about somebody else,
    # and closing a call on it would be a lie about this one.
    r"(?:is|are|am|we'?re|will\s+be|back)\s+available",
)

CLOSERS = re.compile(r"\b(?:" + "|".join(CLOSING_PHRASES) + r")\b", re.I)

# How long after the closer the closing exchange is still this call's.
#
# A closer is the end of the CALL, not the end of the talking. Once a record
# started being split per keyup the incident on disk at 1787205904 stopped
# recording its own last words: "Dispatch, Medic 16 is clearing IU." closed the
# call and the "Medic 16." that answered it 1.25s later arrived with nothing
# open on the department and was thrown away, audio and all. The dispatcher's
# acknowledgements in this archive land 4s behind the traffic they answer
# (+152/+156 and +251/+255 on the two dispatched calls), and an exchange runs a
# couple of those deep, so a minute is slack rather than a guess -- and it is
# the same minute core keeps a cleared call on the wall for, so the screen and
# the log agree about which transmissions are part of the closing.
#
# Measured from the close and never from the last transmission, so a department
# that simply keeps talking cannot roll a finished call forward for ever. A
# dispatch is excluded outright, grace or no grace: a new call is a new
# incident even if it is toned out seconds after the last one cleared.
CLOSE_GRACE = 60

# How long after the close a call may still come back.
#
# A closer ends the call; it does not promise the call is over. A crew clears
# and is recalled to the same address, a second patient turns up, a unit reports
# back on scene a minute after going in service -- and until this existed that
# traffic either forked a stranger with none of the call's history or, being
# chatter, was thrown away with nothing open on the department.
#
# Three minutes, and the number is not free: it is core's _TAPE_GAP, the silence
# the display already treats as the end of one conversation and the start of the
# next. Measured from the close, and the closing exchange files inside
# CLOSE_GRACE, so a reopening is always inside the same tape group as the call
# it belongs to -- which is what lets a resurrected call pick its own transcript
# back up instead of coming back mute. Past it the radio has moved on: a unit
# that went in service three minutes ago has had time to be given something
# else, and its traffic is then about a call we never heard rather than this one.
REOPEN_WINDOW = 180

_open = {}          # dept -> incident dict, at most one live per department
_closed = {}        # dept -> the last closed incident, until REOPEN_WINDOW is out


# ------------------------------------------ what later traffic may change
# A call keeps the identity it was dispatched with, and later transmissions may
# only improve on it. This is not a preference. It is what the recording of
# 2026-08-20 forced: "Dispatch, Medic 16 is arrived at you." parses to no call
# type and to the location "you", and because anything with an address counted
# as a dispatch it took a titled, addressed call off the wall and put that in
# its place. A hallway screen retitling a fire under whoever is reading it is
# worse than a slightly stale title, so where these rules are unsure they keep
# what is already there.
#
# They live here, next to the log's own merge, rather than in core, because the
# screen and the log must never end up disagreeing about what a call was.

# How far a location narrows the world down: 4 is a doorway, 3 is a point, 2 is
# a mile of road, and 1 is a fragment the parser cannot stand behind -- "you",
# "IU", "this time", the things a landmark capture picks up out of ordinary
# chatter. 1 is deliberately not "a location" as far as anything below cares.
_HOUSE = re.compile(r"^\d{1,5}\s+\S")
# Anchored at the end, because the suffix has to BE the end of the name for the
# name to be a road: "Third Street Suites" is a building on First Street, not a
# street. The optional direction is how this county names both sides of one
# ("Sagamore Parkway West").
_ROAD_TAIL = re.compile(
    rf"\b{_parse.STREET}\b\.?(?:\s+(?:north|south|east|west))?\.?$", re.I)
_STREET_NAMES = frozenset(n.lower() for n in _places.STREETS)
_KNOWN_PLACE = tuple(sorted((*_places.CAMPUS, *_places.CAMPUS_AMBIGUOUS),
                            key=len, reverse=True))


def _road(where):
    """Is this a road rather than a point on one?"""
    s = where.strip().strip(".")
    return bool(_ROAD_TAIL.search(s)) or s.lower() in _STREET_NAMES


def address_rank(where):
    """How specific a location is. Higher is more specific; 0 is none at all."""
    where = (where or "").strip()
    if not where:
        return 0
    if _HOUSE.match(where):
        return 4
    # A corner outranks either street that makes it: "Stadium Avenue" is a mile
    # of road and "Stadium Avenue and Martin Jischke Drive" is a place a truck
    # can be sent to, which is the whole reason parse bothers to build one.
    sides = re.split(r"\s+and\s+", where, flags=re.I)
    if len(sides) == 2 and all(_road(s) for s in sides):
        return 3
    if _road(where):
        return 2
    if any(re.search(rf"\b{re.escape(n)}\b", where, re.I) for n in _KNOWN_PLACE):
        return 3
    return 1


_WORD = re.compile(r"[a-z0-9]+")
# "at" is in here because the address slot keeps whichever preposition the
# capture ran into; "and" is deliberately not, so a street still reads as part
# of the corner it belongs to.
_FILLER_WORD = frozenset(("the", "of", "a", "an", "at"))


def same_place(a, b):
    """Are these two locations the same place, one of them said more precisely?

    Word containment rather than string equality, because that is the shape
    every real improvement takes: "North Street" becoming "300 North Street",
    "Stadium Avenue" becoming "Stadium Avenue and Martin Jischke Drive". Two
    locations that merely mention a common word are not each other.
    """
    x = {w for w in _WORD.findall((a or "").lower()) if w not in _FILLER_WORD}
    y = {w for w in _WORD.findall((b or "").lower()) if w not in _FILLER_WORD}
    return bool(x and y and (x <= y or y <= x))


def improve(call, f):
    """Fold a later parse into a call, keeping only what is better. -> changed?

    The call type is written once. A real type fills an absent one -- the
    pre-alert names the call and the dispatch thirty seconds later names the
    address -- but nothing replaces a type the dispatcher already gave us,
    least of all the None that every piece of chatter parses to.

    The address may be filled, or sharpened, and that is all: a location has to
    be the same place said better before it may replace what is on the wall. A
    different place is either a different call (see new_dispatch) or a
    transmission mentioning somewhere else, and neither is a reason to move a
    running call to a new address.
    """
    changed = False
    if f.get("type") and not call.get("type"):
        call["type"] = f["type"]
        changed = True
    old, new = call.get("address"), f.get("address")
    if new and new != old and (
            not old
            # A rank-1 address is a fragment, and a fragment is what the fuzzy
            # matcher leaves behind when it half-heard a building name. Anything
            # the gazetteer can actually vouch for beats it outright.
            or (address_rank(new) > address_rank(old)
                and (address_rank(old) < 2 or same_place(old, new)))):
        call["address"] = new
        changed = True
    if f.get("city") and not call.get("city"):
        call["city"] = f["city"]
        changed = True
    return changed


# The dispatcher opening a call, as distinct from a crew talking on one. The
# bare imperative is the tell and the reason "responding" is excluded here
# while parse's landmark capture accepts it: "Medic 16 responding to Cary
# Quadrangle" is an acknowledgement of a call that already exists, and only the
# dispatcher says "respond to".
TONE_OUT = re.compile(
    r"\battention\b|\bstand(?:ing)?\s*by\s+for\b|\brespond\s+(?:to|with|code)\b|"
    r"\breport(?:ed|s)?\s+of\s+an?\b|\bwe\s+have\s+an?\s+(?:report|call|working)\b",
    re.I)


def new_dispatch(call, f, text):
    """Is this a SECOND call on a department that is already working one?

    A department can absolutely be given two calls inside one conversation, so
    this has to be possible -- but it now takes evidence rather than any parse
    that happened to yield an address, which is what was opening phantom calls.
    The evidence is the dispatcher's own phrasing plus something that cannot be
    the call already running: a location that is a different place, or a
    different call type. Both are judged against values the running call
    actually has, so a half-blank call is always refined and never forked --
    that is the pre-alert, and it is the same call.
    """
    if not TONE_OUT.search(text):
        return False
    if call.get("type") and f.get("type") and f["type"] != call["type"]:
        return True
    here, there = call.get("address"), f.get("address")
    return (address_rank(here) >= 2 and address_rank(there) >= 2
            and not same_place(here, there))


# What a unit reporting its own position sounds like, as core's status machine
# reads it. Named here because it is the price of admission for a reopening:
# see reopens(). "clear" is deliberately absent -- a closer cannot bring a call
# back -- and so is "dispatched", which nothing but a tone-out ever asserts.
_POSITION = ("enroute", "on_scene", "transporting", "at_hospital")


def reopens(inc, text, ts, state=None, closed=None, dispatch=False):
    """Is this transmission a closed call coming back on the air?

    Reopening the wrong call rewrites history, which is worse than a stranger
    turning up beside it, so this takes two independent pieces of evidence and
    refuses everything else:

    A unit has to report where it is. "Medic 16 back on scene" is a call that
    is running again; "Medic 16." -- the acknowledgement that lands 1.25s after
    "Dispatch, Medic 16 is clearing IU." and that CLOSE_GRACE exists to keep --
    asserts nothing, so it files against the closed call exactly as it does
    today without pretending the call restarted. `state` is core's reading of
    the traffic rather than a second one taken here, so the screen and the log
    cannot end up disagreeing about whether a call came back.

    And it has to be THIS call: a unit that was on it, or its address said
    again. Without that, "Medic 17 on scene" -- a different crew, on a call we
    never heard toned out -- would resurrect whatever cleared last.

    A dispatch never reopens, at any distance. The dispatcher toning out is how
    a new call begins, and the second alarm at an address that just cleared is
    a new call with its own tones, its own units and its own incident number.
    That is the same rule the CLOSE_GRACE pop has always applied, stated once
    more here so the two cannot drift apart.
    """
    closed = closed if closed is not None else inc.get("closed")
    if dispatch or not closed or state not in _POSITION:
        return False
    if not 0 <= ts - closed <= REOPEN_WINDOW:
        return False
    # The closing exchange repeats the closer back -- the crew clears, the
    # dispatcher says it again -- and a stale repeat must never restart a call.
    if CLOSERS.search(text):
        return False
    # The unit extraction on its own, not the whole parser. This used to ask
    # by_regex for a dict and throw every field of it away but one, which meant
    # a fuzzy match against every building on campus to find out whether the
    # word "16" was in the sentence.
    said = set(_parse.units(text))
    if said & set(inc.get("units") or []):
        return True
    # The address said again, and only when the address is one the parser can
    # stand behind: a rank-1 fragment ("you", "IU") appears in ordinary chatter
    # and is no evidence of anything. Containment one way only -- every word of
    # the address is in the transmission -- because the symmetric test would let
    # a transmission saying nothing but "Street" match "300 North Street".
    where = inc.get("address")
    if address_rank(where) >= 2:
        want = {w for w in _WORD.findall(where.lower()) if w not in _FILLER_WORD}
        heard = {w for w in _WORD.findall(text.lower()) if w not in _FILLER_WORD}
        if want and want <= heard:
            return True
    return False


def reopen(inc, text, ts):
    """Put a closed incident back on the air, keeping the close on the record.

    The closure is not erased. It happened, the radio said so, and someone
    reading the log later has to be able to see that this call ended and then
    started again -- so the close and the transmission that undid it are
    appended as a pair and `closed` goes back to None, meaning what it has
    always meant: this incident is not over right now.
    """
    inc.setdefault("reopenings", []).append(
        {"closed": int(inc["closed"]), "ts": int(ts), "text": text.strip()[:160]})
    inc["closed"] = None


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "call").lower()).strip("-")[:40] or "call"


def _root(cfg):
    d = cfg.get("incident_dir")
    return Path(d) if d else None


def _write(inc):
    """Rewrite incident.json. Cheap, and it means a kill -9 loses nothing."""
    p = Path(inc["dir"])
    # "dir" is where the file is being written and says nothing inside it, and
    # the underscore keys are this module's own bookkeeping -- _files is the
    # live map from source clip to stored name, which holds absolute paths on
    # this machine and would be stale the moment the directory moved.
    body = {k: v for k, v in inc.items()
            if k != "dir" and not k.startswith("_")}
    _keep_truths(p / "incident.json", body)
    try:
        (p / "incident.json").write_text(json.dumps(body, indent=1))
    except Exception as e:
        print(f"  !  incident not saved: {e}", file=sys.stderr)


def _keep_truths(f, body):
    """Carry any hand-typed truths on disk into the version about to replace it.

    This module writes incident.json from memory, whole, after every
    transmission. apply_truth() writes into the same file from somewhere else --
    the review UI, which is very often a SECOND process (`firewall --review`
    attaches no source precisely so you can label yesterday while today is being
    recorded). Nothing in the live process's memory knows a truth was typed, so
    the next transmission on that call would rewrite the file without it and the
    label would appear to save and then vanish.

    corpus.jsonl is the durable record and this is a projection of it, so the
    worst case was recoverable rather than lost -- but "recoverable by knowing
    to re-run something" is not a thing to leave in a program, and a read of a
    few kilobytes on a write that happens a few times a minute is not a cost.

    Matched on the tape row id, and on the (ts, audio) pair for rows filed
    before ids were written down. Never on position: a rewrite can add rows.

    `body` shares its rows with the caller's in-memory incident, so this puts
    the truth back into memory as well as onto disk. That is deliberate: the
    live process then carries it, and the read above costs something only on the
    first write after a label rather than on every transmission for ever.
    """
    try:
        was = json.loads(f.read_text()).get("transmissions") or []
    except Exception:
        return                      # no file yet, or nothing readable in it
    truths = {}
    for t in was:
        if t.get("truth") is None:
            continue
        if t.get("id"):
            truths[("id", t["id"])] = t["truth"]
        truths[("at", t.get("ts"), t.get("audio"))] = t["truth"]
    if not truths:
        return
    for t in body.get("transmissions") or []:
        if t.get("truth") is not None:
            continue
        got = truths.get(("id", t.get("id"))) if t.get("id") else None
        if got is None:
            got = truths.get(("at", t.get("ts"), t.get("audio")))
        if got is not None:
            t["truth"] = got


def _stash(src, dst):
    """Put one transmission's audio in the incident directory.

    Hard-linked where the filesystem allows it, because the source has usually
    already written this exact clip to audio_dir: copying meant every call sat
    on disk twice, under two names, and turned up twice in the review list. A
    link is the same bytes under a second name, so the incident directory is
    still self-contained -- a real file, playable and findable on its own, that
    outlives whatever happens to the archive copy.

    The copy stays as the fallback and always will: links cannot cross devices
    (a temp file under /var/folders, an incident dir on an external disk) and
    not every filesystem offers them. Failing to keep a clip is never an
    acceptable outcome here; the clip is the only thing that cannot be redone.
    """
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy(src, dst)


def record(cfg, dept, text, ts, audio=None, call=None, span=None, state=None,
           rid=None):
    """File one transmission. `call` is the parse result, or None for chatter.

    `span` is which stretch of `audio` this transmission is, when the clip held
    more than one keyup (see segments.py). The audio is stored once per clip and
    the span recorded against it, so a grant carrying an exchange leaves one
    file in the directory and two transmissions pointing into it at different
    seconds -- rather than the same recording hard-linked twice under two names,
    which is what naming by transmission number would have done.

    `state` is what core read off this transmission -- see reopens(), which is
    the only thing here that uses it.

    `rid` is the id this transmission already carries on core's tape, written
    down so the log and the tape agree about which transmission is which.
    Nothing in this module reads it; it is here because a correction is
    addressed by it. A truth typed against a clip a week from now has to name a
    row the hosted archive is already holding, the archive knows that row by its
    tape id, and this file is the only place the two could ever be joined up
    again. None for a source that keeps no audio, and a row with no id is simply
    one no correction can reach.
    """
    root = _root(cfg)
    if not root:
        return None
    gap = int(cfg.get("incident_gap_seconds", 900))
    inc = _open.get(dept)

    # A closed incident is kept open here rather than dropped on the spot, so
    # the acknowledgements that follow the closer still have somewhere to go
    # (see CLOSE_GRACE). It stops being open on the first thing that plainly is
    # not part of that closing: a dispatch, or anything at all once the grace is
    # spent. It is not thrown away at that point though -- it moves aside, where
    # REOPEN_WINDOW of traffic can still bring it back.
    if inc and inc["closed"] and (call or ts - inc["closed"] > CLOSE_GRACE):
        _closed[dept] = inc
        _open.pop(dept, None)
        inc = None

    back = _closed.get(dept)
    if back is not None:
        if call or not back["closed"] or ts - back["closed"] > REOPEN_WINDOW:
            # A dispatch is a new call and takes the department's attention with
            # it, so the old one loses its chance to come back the moment the
            # tones drop -- and so does one nobody has said anything about for
            # REOPEN_WINDOW.
            _closed.pop(dept, None)
        elif inc is None and reopens(back, text, ts, state):
            inc = _open[dept] = _closed.pop(dept)
    # Inside CLOSE_GRACE the closed call is still in _open, so the same test
    # runs against it there: a crew that comes back ninety seconds after
    # clearing has reopened the call whether or not the grace happens to have
    # run out yet.
    if inc is not None and inc["closed"] and reopens(inc, text, ts, state,
                                                     dispatch=call is not None):
        reopen(inc, text, ts)
        print(f"  .  incident reopened ({inc['id']}): {text!r}")

    # A dispatch normally starts a new incident -- but not while one is still
    # running, because most of what re-parses as a dispatch mid-call is the same
    # call being restated: the pre-alert and the address thirty seconds later
    # ("stand by for possible alcohol poisoning", then the building), or a crew
    # repeating where it was sent. This used to hold only for the first hundred
    # and fifty seconds, and outside that window the identical transmission
    # forked a second incident for a call that had only ever been dispatched
    # once. So the whole live window refines, and forking now takes evidence
    # that this is a different call -- the same test the screen applies, so the
    # log and the screen count the same calls.
    if call and inc and 0 <= ts - inc["last_ts"] <= gap \
            and not new_dispatch(inc, call, text):
        improve(inc, call)
    elif call or inc is None or ts - inc["last_ts"] > gap:
        if not call:
            return None                     # chatter with nothing open: not ours
        opened = int(ts)
        # The directory is named once and keeps that name for good, even when a
        # later transmission gives the call a better title than it opened with.
        # The name is the incident's id: it is already in incident.json, already
        # printed on the console, already the argument to `--replay`, and this
        # process is hard-linking audio into the directory while the review tool
        # may be reading it. Renaming it to gain a nicer word would break every
        # one of those to fix nothing -- the title everything actually displays
        # is read out of incident.json, and that one does improve. A dispatch
        # with no recognised call type slugs to "call", which is honest: the
        # parser did not know what it was, and no word here should pretend it
        # did.
        d = root / f"{opened}-{_slug(dept)}-{_slug(call.get('type'))}"
        d.mkdir(parents=True, exist_ok=True)
        inc = _open[dept] = {
            "id": d.name, "dir": str(d), "dept": dept, "opened": opened,
            "closed": None, "type": call.get("type"), "address": call.get("address"),
            "units": list(call.get("units") or []), "last_ts": ts,
            "transmissions": [],
            # Source clip -> the name it was stored under. Not written to
            # incident.json; see _write.
            "_files": {},
        }

    n = len(inc["transmissions"]) + 1
    name = None
    src = str(Path(audio).resolve()) if audio and Path(audio).exists() else None
    if src:
        files = inc.setdefault("_files", {})
        name = files.get(src)
        if not name:
            # Numbered by stored file rather than by transmission, so the
            # directory still reads 001, 002, 003 with no gaps when a grant
            # contributes two transmissions and one file.
            name = f"{len(files) + 1:03d}{Path(src).suffix or '.mp3'}"
            try:
                _stash(Path(src), Path(inc["dir"]) / name)
            except Exception as e:
                print(f"  !  incident audio not saved: {e}", file=sys.stderr)
                name = None
            else:
                files[src] = name
    row = {"ts": ts, "text": text, "audio": name, "dispatch": bool(call)}
    if rid:
        row["id"] = rid
    if name and span:
        # Where inside that file this transmission is, so --replay and anything
        # else reading the log can seek to the right voice instead of playing
        # the whole grant twice.
        row["start"] = round(float(span["start"]), 2)
        row["end"] = round(float(span["end"]), 2)
    inc["transmissions"].append(row)
    inc["last_ts"] = ts
    # Units mentioned later in the call are still this incident's units.
    for u in (call or {}).get("units") or []:
        if u not in inc["units"]:
            inc["units"].append(u)

    # Only the first closer stamps the call. A closing exchange is full of
    # them -- the crew clears, the dispatcher acknowledges by repeating it back
    # -- and the stamp has to stay on the moment the radio actually ended the
    # call, so that anything filed during the grace reads as what it is:
    # traffic after the close, not proof the close happened later.
    if CLOSERS.search(text) and not inc["closed"]:
        inc["closed"] = int(ts)
        print(f"  .  incident closed ({inc['id']}, {n} transmissions)")
    _write(inc)
    return inc


def load(cfg, which=None):
    """One incident by id (or the newest), as a dict. None if there is none."""
    root = _root(cfg)
    if not root or not root.exists():
        return None
    if which and which != "latest":
        p = root / which / "incident.json"
        return json.loads(p.read_text()) if p.exists() else None
    dirs = sorted((d for d in root.iterdir() if (d / "incident.json").exists()),
                  key=lambda d: d.name)
    return json.loads((dirs[-1] / "incident.json").read_text()) if dirs else None


def recording(cfg):
    """Is anything actually being written to disk on this run?

    Asked because "no calls yet" and "nothing is being kept" look identical from
    the outside -- both are an empty list -- and they mean opposite things to
    somebody looking at a screen. incident_dir can be unset, and mock mode sets
    it to None outright so fiction never lands in the log, so an empty roster is
    the expected state rather than a fault. The directory has to exist as well as
    be named: a path pointing at an external disk that is not plugged in is
    configured and is recording nothing.
    """
    root = _root(cfg)
    return bool(root and root.is_dir())


def catalogue(cfg, limit=200):
    """Newest incidents first, each as a dict of what was filed for it.

    listing() below is the console's view of the same directory and stops at six
    columns because a terminal line has nowhere to put a unit list. The call
    tracker in the browser wants the units and the close stamp too, and widening
    listing()'s tuple to carry them is the one thing that cannot be done here:
    `firewall --incidents` unpacks those six positionally, so a seventh field
    turns the oldest reader of this log into a ValueError. The dict is therefore
    the source and the tuple a slice of it, which is also what stops the two
    drifting about what "newest" or "count" mean.

    Nothing in here raises on a bad directory, and that is load-bearing rather
    than defensive: incident.json is rewritten after every transmission, so the
    newest file on disk is one a call is being written into right now, and a
    reader that dies on a half-written record is a reader that dies exactly when
    something is happening.
    """
    root = _root(cfg)
    if not root or not root.exists():
        return []
    try:
        dirs = sorted(root.iterdir(), reverse=True)
    except OSError:
        return []
    out = []
    for d in dirs:
        f = d / "incident.json"
        if not f.exists():
            continue
        try:
            i = json.loads(f.read_text())
            opened = int(i["opened"])
        except Exception:
            # Unreadable, truncated, or missing the one field that says when the
            # call was. Skipped rather than filled in, because `opened` is what
            # everything downstream sorts on and what core.roster matches a live
            # call against -- a guessed one would merge two unrelated calls.
            continue
        out.append({
            "id": i.get("id") or d.name,
            "opened": opened,
            # None means the radio has not ended this call, which is also what
            # it means after a reopening: see reopen(), which puts it back.
            "closed": i.get("closed"),
            "dept": i.get("dept"),
            "type": i.get("type"),
            "address": i.get("address"),
            "units": list(i.get("units") or []),
            "count": len(i.get("transmissions") or []),
        })
        if len(out) >= limit:
            break
    return out


def stamp(cfg, ident):
    """What makes an incident's file the same file it was last time, or None.

    The cheap half of a pair with transmissions() below. catalogue() is read
    every ten seconds and deliberately leaves the transcripts behind, because
    they are almost all of the bytes: the 48-transmission call in this archive
    is a 7KB incident.json of which the six fields the tracker charts are 300
    bytes. Anything that does want the transcripts therefore has to be able to
    ask "has this changed" without reading them, and that is this -- one stat
    per incident, against a file that is only rewritten while its call is
    running, so the answer is no for every call but the live one.

    Size as well as mtime because a rewrite that lands inside the filesystem's
    mtime resolution still changes the length: incident.json is written whole
    after every transmission, and a transmission always adds a row.
    """
    root = _root(cfg)
    if not root or not ident:
        return None
    try:
        st = (root / ident / "incident.json").stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def transmissions(cfg, ident):
    """Every transmission filed for one incident, oldest first, with its stamp.

    Returns (stamp, rows), or (None, None) when there is nothing on disk to
    read -- which is not the same answer as an incident that has no
    transmissions, and callers that draw "nothing was said" differently from
    "we cannot see what was said" need the difference.

    The stamp comes back from AFTER the read rather than from before it, so the
    pair is always describing one state of the file: a call being written into
    while this runs hands back rows that go with the stamp they were read at,
    and a caching caller re-reads on its next poll instead of remembering newer
    rows under an older file's name. Unreadable is not an error here for the
    reason catalogue() gives -- the newest file on disk is one a call is being
    written into right now.
    """
    root = _root(cfg)
    if not root or not ident:
        return None, None
    f = root / ident / "incident.json"
    try:
        body = f.read_text()
        st = f.stat()
    except OSError:
        return None, None
    try:
        rows = json.loads(body).get("transmissions") or []
    except Exception:
        return None, None
    # A hand-typed truth outranks what whisper heard, here and everywhere the
    # words are read for meaning rather than for scoring. This is the read path
    # core takes to work out which of a call's units have cleared, and a
    # correction is precisely the case where the machine's version was wrong
    # enough to be worth typing over -- reading "Engine 2 is and service" when
    # somebody has already written down "Engine 2 is in service" would be
    # keeping the mistake on purpose.
    #
    # The machine's own text stays on the row and is untouched: corpus.py scores
    # against it, and the review UI shows it next to the box you type into. Only
    # this reading of the file prefers the truth. `truth` may be the empty
    # string, which is a label meaning "nothing was said on this one", so the
    # test is against None and not against falseness.
    rows = [dict(r, text=r["truth"]) if r.get("truth") is not None else r
            for r in rows]
    return (st.st_mtime_ns, st.st_size), rows


def apply_truth(cfg, path, text):
    """Write one hand-typed truth onto the transmission it belongs to.

    Returns (row id, note). The id is the tape row this correction is addressed
    to, or None when there is nothing to address it to; `note` says why, and is
    None when the truth landed cleanly.

    A truth is keyed on the RECORDING and a transmission is a keyup inside it,
    and those are not always the same thing. A trunked grant holds the talkgroup
    through the hang time, so an exchange -- "Dispatch 16, that is a negative."
    answered four seconds later by "Clear, thank you." -- reaches disk as one
    mp3 with two transmissions pointing into it. One typed line covers both, and
    there is no honest way to divide it: nothing here knows which words were the
    first voice and which the second, and a guess would put the dispatcher's
    sentence in the medic's mouth on a log people read to find out who said
    what. So a clip holding more than one keyup keeps its truth in corpus.jsonl,
    where --score reads it, and its transmissions keep the machine's version.
    That is the uncommon case; a record that turned out to hold exactly one
    keyup is the overwhelmingly common one.

    A clip that is not inside an incident directory -- a loose recording in
    audio_dir -- has no transmission to write to and no id to correct by. The
    label is still saved; there is simply nowhere for it to be published to.

    Read and written without a lock, because the file this races against is one
    a live call is being appended to and the race heals itself in both
    directions: a transmission filed between this read and this write is put
    back by that call's next _write, which writes the whole incident from
    memory, and the truth written here survives that because _keep_truths
    carries it across. Locking a JSON file against another process to close a
    millisecond that repairs itself is not the trade.
    """
    f = Path(path).parent / "incident.json"
    if not f.exists():
        return None, "not part of a filed incident"
    try:
        inc = json.loads(f.read_text())
    except Exception as e:
        return None, f"incident.json is unreadable ({type(e).__name__})"
    name = Path(path).name
    rows = [t for t in inc.get("transmissions") or [] if t.get("audio") == name]
    if not rows:
        return None, "no transmission in this incident points at that clip"
    if len(rows) > 1:
        return None, (f"{len(rows)} transmissions share this recording; "
                      f"the truth is kept for --score but not published")
    row = rows[0]
    if row.get("truth") == text:
        return row.get("id"), None          # already there; do not rewrite
    row["truth"] = text
    try:
        f.write_text(json.dumps(inc, indent=1))
    except Exception as e:
        return None, f"incident.json could not be written ({e})"
    return row.get("id"), None


def corrections(cfg):
    """Every hand-typed truth in the log, as {tape row id: text}.

    What a push sends to a hosted tracker so the archived transcript there stops
    being the machine's guess. Keyed on the row id because that is what the far
    end stores a transmission under -- see push.py, which is the only caller and
    which is careful about how often it asks, since this reads every incident.json
    on disk rather than stat-ing them the way catalogue() does.

    Rows filed before ids were written down are skipped rather than guessed at.
    """
    root = _root(cfg)
    if not root or not root.exists():
        return {}
    out = {}
    for f in sorted(root.glob("*/incident.json")):
        try:
            inc = json.loads(f.read_text())
        except Exception:
            continue
        for t in inc.get("transmissions") or []:
            if t.get("id") and t.get("truth") is not None:
                out[t["id"]] = t["truth"]
    return out


def listing(cfg, limit=20):
    """Newest incidents first, as (id, opened, dept, type, address, count).

    Six wide and staying six wide -- `firewall --incidents` unpacks this
    positionally. Anything that wants more of the record calls catalogue().
    """
    return [(i["id"], i["opened"], i["dept"], i["type"], i["address"], i["count"])
            for i in catalogue(cfg, limit)]


def replay(cfg, which=None, play=False):
    """Print an incident as a timeline, optionally playing each transmission."""
    import subprocess
    inc = load(cfg, which)
    if not inc:
        print("  no incidents recorded yet "
              "(set FIREWALL_INCIDENT_DIR and let it run)")
        return 1
    opened = inc["opened"]
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(opened))
    print(f"  {inc['id']}")
    print(f"  {when}  {inc.get('dept')}  {inc.get('type')} @ {inc.get('address')}"
          f"  {inc.get('units')}")
    if inc.get("closed"):
        print(f"  closed after {inc['closed'] - opened}s")
    if inc.get("reopenings"):
        print(f"  closed and came back {len(inc['reopenings'])}x")
    print()
    # The closes and the comings-back are things that happened at a time, so
    # they are read out on the same timeline as the traffic rather than in a
    # footer -- a reader has to see the call end, and then see it start again,
    # in the order the radio did it. The middle number orders a mark against a
    # transmission with the same stamp: the closer's own words print before the
    # close they caused, and the reopening prints before the transmission that
    # brought the call back, which is that transmission's first row.
    marks = sorted([(r["closed"], 2, "--", "call closed") for r in inc.get("reopenings", ())]
                   + [(r["ts"], 0, "++", "call reopened") for r in inc.get("reopenings", ())]
                   + ([(inc["closed"], 2, "--", "call closed")] if inc.get("closed") else []))

    def _marks(until):
        while marks and marks[0][:2] <= until:
            when, _, sign, what = marks.pop(0)
            print(f"  {sign} +{int(when - opened):4d}s  ---- {what} ----")

    root = Path(inc.get("dir") or (_root(cfg) / inc["id"]))
    # A trunked grant that held an exchange leaves several transmissions
    # pointing into one file, and only those are worth showing a range for.
    shared = {t.get("audio") for t in inc["transmissions"]
              if t.get("audio") and sum(1 for u in inc["transmissions"]
                                        if u.get("audio") == t["audio"]) > 1}
    played = None
    for t in inc["transmissions"]:
        _marks((int(t["ts"]), 1))
        mark = ">>" if t["dispatch"] else "  "
        where = ""
        if t.get("audio") in shared and t.get("start") is not None:
            where = f"  [{t['audio']} {t['start']:.2f}-{t['end']:.2f}]"
        # A corrected transmission prints what was typed, marked, because a
        # timeline somebody is reading to find out what happened should show
        # the best version of the words and should not pretend the radio was
        # clearer than it was.
        said = t["truth"] if t.get("truth") is not None else t["text"]
        fixed = "*" if t.get("truth") is not None else " "
        print(f"  {mark} +{int(t['ts'] - opened):4d}s{where} {fixed} {said}")
        if play and t.get("audio"):
            f = root / t["audio"]
            # afplay on macOS takes -t (a duration) and no start offset -- there
            # is nothing in `afplay -h` to seek with, and no ffplay on this
            # machine -- so a shared clip is played once, whole, and the range
            # above says which part of it you are listening for. Playing it
            # again for the second transmission would just repeat the exchange.
            if f.exists() and t["audio"] != played:
                played = t["audio"]
                subprocess.run(["afplay", str(f)], check=False)
    _marks((float("inf"), 9))
    return 0
