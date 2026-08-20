"""Dispatch transcript -> {type, address, city, units}."""
import difflib, json, re, sys
from collections import Counter

from . import places as _places

UNIT_RE = re.compile(
    r"\b((?:engine|medic|ladder|truck|squad|rescue|tanker|brush|battalion|ambulance|car|chief)"
    r"\s*\d{1,3}|[EMLTSRBA]\d{1,3})\b", re.I)

# Whisper renders spoken unit numbers either way -- "Engine 2" on one call and
# "engine two" on the next -- so numbers are folded to digits before UNIT_RE
# runs. Only after a unit word, so "two vehicles involved" is left alone.
ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
WORD_NUM = {w: i for i, w in enumerate(ONES)}
WORD_NUM.update(TENS)

UNIT_WORDS = ("engine|medic|ladder|truck|squad|rescue|tanker|brush|battalion|"
              "ambulance|car|chief")
_NUMWORD = "|".join(sorted(WORD_NUM, key=len, reverse=True))
SPOKEN_UNIT_RE = re.compile(
    rf"\b({UNIT_WORDS})\s+({_NUMWORD})(?:[\s-]+({_NUMWORD}))?\b", re.I)


def _digits(m):
    a = WORD_NUM[m.group(2).lower()]
    b = m.group(3)
    n = a + WORD_NUM[b.lower()] if b and a in TENS.values() else a
    return f"{m.group(1)} {n}" + (f" {b}" if b and n == a else "")


def spoken_numbers(text):
    """"engine two" -> "engine 2", so UNIT_RE sees one form, not two."""
    return SPOKEN_UNIT_RE.sub(_digits, text)


ABBR = {"engine": "E", "medic": "M", "ladder": "L", "truck": "T", "squad": "SQ",
        "rescue": "R", "tanker": "TK", "brush": "BR", "battalion": "BC",
        "ambulance": "A", "car": "C", "chief": "CH"}

# Extend this as you hear how your dispatchers actually phrase things.
TYPE_HINTS = [
    (r"structure fire|working fire|building fire",       "Structure Fire"),
    (r"vehicle fire|car fire",                           "Vehicle Fire"),
    (r"(automatic )?fire alarm|alarm sounding",          "Automatic Fire Alarm"),
    (r"chest pain",                                      "Medical: Chest Pain"),
    (r"difficulty breathing|shortness of breath",        "Medical: Breathing"),
    (r"cardiac arrest|cpr in progress",                  "Cardiac Arrest"),
    (r"unconscious|unresponsive",                        "Medical: Unresponsive"),
    # Heard on Purdue FD, and missing until a real dispatch was thrown away for
    # want of a call type: "stand by for possible alcohol poisoning".
    (r"alcohol poisoning|intoxicat|alcohol",             "Medical: Alcohol"),
    (r"allergic reaction|anaphyla",                      "Medical: Allergic Reaction"),
    (r"diabetic|hypoglyc",                               "Medical: Diabetic"),
    (r"laceration|bleeding|hemorrhag",                   "Medical: Bleeding"),
    (r"welfare check|check the welfare",                 "Welfare Check"),
    (r"psychiatric|mental health|suicid",                "Medical: Psychiatric"),
    (r"water flow|sprinkler",                            "Water Flow Alarm"),
    (r"smoke detector|detector activation",              "Detector Activation"),
    (r"seizure",                                         "Medical: Seizure"),
    (r"overdose|narcan",                                 "Medical: Overdose"),
    (r"fall(en)?|lift assist",                           "Fall / Lift Assist"),
    (r"personal injury|pi accident|crash|collision|mva", "Vehicle Crash"),
    (r"carbon monoxide|co alarm",                        "Carbon Monoxide Alarm"),
    (r"gas leak|odor of gas|natural gas",                "Gas Leak"),
    (r"water rescue",                                    "Water Rescue"),
    (r"elevator",                                        "Elevator Rescue"),
    (r"smoke (in|investigation)|odor of smoke",          "Smoke Investigation"),
    (r"wires? down|arcing",                              "Wires Down"),
]

STREET = (r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct|"
          r"way|place|pl|circle|cir|parkway|pkwy|highway|hwy|terrace|trail|mall|"
          r"plaza|square|sq)")
# The [,\s]+ after the house number is not cosmetic: whisper punctuates the
# dispatcher's pause, and "respond to 340, Sagamore Parkway West" used to parse
# as the address "340".
ADDR_RE = re.compile(
    rf"\b(\d{{1,5}}[,\s]\s*(?:(?:north|south|east|west|[NSEW])\.?\s+)?"
    rf"(?:[A-Z][\w'-]*\s+){{0,4}}{STREET}\b\.?"
    rf"(?:\s+(?:north|south|east|west))?)", re.I)


# Campus dispatches name a building, not a street: "respond to Cary Quadrangle",
# "respond to Wetherill Laboratory". ADDR_RE requires a house number and a street
# suffix, so it returns nothing for those -- which matters because Purdue FD is
# one of the two talkgroups this watches and almost none of its calls carry a
# street address.
#
# Rather than keep a list of every building on campus, this captures whatever
# sits between the dispatcher's "respond to" and the reason clause that follows.
# Dispatch phrasing is formulaic enough for that to hold, and the geocoder
# already resolves campus building names on its own.
#
# The introducer is split in two and captured rather than merely consumed,
# because which one fired says what kind of transmission this is: a dispatcher's
# "respond to X" names where the call IS, while a crew's "en route to X" names
# where that crew is going, and for a hospital those are opposite meanings. See
# _HOSPITAL below.
_SENT_TO = r"respond(?:ing)?\s+to|responding|stand(?:ing)?\s?by\s+for"
_HEADED_TO = r"en\s?route\s+to|at"
LANDMARK_RE = re.compile(
    r"\b(?P<intro>" + _SENT_TO + "|" + _HEADED_TO + r")[,\s]\s*"
    r"(?:the\s+)?"
    r"(?P<loc>[A-Z0-9][\w'\-]*(?:\s+(?!for\b|on\s+the\b)[A-Z0-9][\w'\-]*){0,4})"
    r"(?=\s*(?:,|\.|$|\bfor\b|\bon\s+the\b))", re.I)

# Radio words that sound enough like a building to be matched to one. "In
# service" resolving to Shreve Hall would put a phantom fire on the screen
# every time a unit went back in service, which is several times an hour.
#
# "will be" is here for the same reason and is worth naming: it scores 0.764
# against Wiley, over the 0.75 floor, and "Engine 2 will be out at this time"
# is a sentence a crew says several times a shift.
#
# "being" is the same trap and cost a real call: it scores 0.845 against
# Beering, so "Medic 16, Medic 16, being Ross, Honors North" filed a run to
# Honors North at Beering -- which geocodes to North Steven Beering Drive, a
# road a mile from the hall of that name and further still from the patient.
_NEVER = frozenset("""
service dispatch dispatcher returning arriving responding response officer
officers subject county information possible station scene en route enroute
clear copy medic engine ladder squad battalion ambulance rescue truck chief
unit units fire alarm female male year old assist advise advised will be being
""".split())

# Words that mean the capture ran into the call type instead of a place name.
_NOT_A_PLACE = re.compile(
    r"^(a|an|the|assist|standby|stand\s?by|report(ed)?|possible|unknown)$", re.I)

# The bare "at" in LANDMARK_RE is there for the two phrasings that carry a real
# location with no "respond to" in front of them -- "Engine 2 out at Cary
# Quadrangle" and "stand by for a medical run at Stadium and Martin Juskey" --
# but it fires just as happily on the chatter that surrounds them: "Medic 17 at
# the hospital" handed the address slot "hospital", and "Engine 2 will be out at
# this time" handed it "this time". core.publish() calls anything with an
# address a dispatch, so each of those threw a fabricated call onto the screen
# and reset the clock on the real one underneath it.
#
# What separates the two is that a landmark is a NAME. These words are the
# common nouns and function words a crew reaches for when it is referring to
# something rather than naming it, and a capture made of nothing else is not a
# place. One of them inside a real name is harmless -- "Medic 16 at the Purdue
# Memorial Union" survives -- because, like _NEVER above, the test is on the
# whole capture and not on any one word of it.
#
# The personal pronouns are on the strength of one real transmission: whisper
# heard "Medic 16 has arrived at IU" as "Medic 16 is arrived at you", and the
# display opened a call whose address read "you". They cost nothing next to a
# real name, which is why "respond to your heart residence hall" is still
# Earhart -- "heart" is not one of these.
#
# The hesitation noises are here because they are the only words that can turn
# up between "respond to" and the building, and losing a dispatch to one is the
# one failure this display cannot absorb: whisper writes the dispatcher's pause
# as "respond to er, Earhart", and the comma ends the capture at "er". Rejecting
# it is all that is needed -- by_regex() already falls through from _landmark to
# _gazetteer, which finds the Earhart the pause was standing in front of.
_NOT_A_NAME = _NEVER | frozenset("""
a an the another one
hospital hospitals time times location locations residence corner corners
intersection area house home headquarters quarters run runs call calls medical
this that these those it its there here their our
you your me my we us them him her
er uh um ah erm hmm mhm okay ok yeah
is are was am been going go out back over still now
""".split())

# "stand by for" is in LANDMARK_RE because the dispatcher does sometimes name
# the place in that clause -- "stand by for a run to Cary Quadrangle" -- but
# just as often the clause is the call type and nothing else, and "Purdue Fire,
# stand by for a possible structure fire" put "a possible structure fire" on the
# display as the location and then spent a Nominatim lookup trying to resolve
# it. TYPE_HINTS is already the list of what a call type sounds like, so it can
# say which of the two this is. The place test is what keeps a capture that
# carries both -- a type phrase AND a name we know -- on the place side.
_IS_TYPE = re.compile("|".join(pat for pat, _ in TYPE_HINTS), re.I)

# The dispatcher is a party on the radio, not a place and not a call type.
# "Dispatch, Medic 16 is going to be en route to IU" names two parties the way
# "Medic 16" names one, and "Dispatch 16, that is a negative" is the dispatcher
# answering unit 16 -- so the number after the word belongs to the unit being
# addressed and must never be read as a "D16" that no station owns.
#
# _NEVER already carried "dispatch" and "dispatcher", but it is only ever
# consulted a word at a time, and the tests it feeds pass a capture that is
# merely MOSTLY radio words: "en route to dispatch center" reached the display
# with the address "dispatch center". Matching the name itself, wherever it
# appears in a capture, is what closes that.
_DISPATCH = re.compile(r"\b(?:county\s+)?dispatch(?:er|ers|es|ed|ing)?\b", re.I)

# A hospital is where an EMS call ENDS, not where it happened. The transmission
# "Dispatch, Medic 16 is going to be en route to IU" opened an incident on this
# display whose address read "IU", when the call had been somewhere else and was
# already over -- the crew was reporting a transport, not a location.
#
# It cannot simply be struck out, though, because a hospital is a building like
# any other and does catch fire: "Engine 11, respond to IU Health for a fire
# alarm" is a real run to a real address. What separates the two is not the name
# but the phrasing around it, which is why _SENT_TO and _HEADED_TO above are
# kept apart.
# Split by whether the match is a NAME, because only a name can be narrowed to.
# "stand by for a fire at IU Health" arrives here as the whole clause and the
# address wanted out of it is "IU Health"; "stand by for a fall at the hospital"
# arrives the same way and there is no address in it to find.
_NAMED_ALT = ("|".join(re.escape(h).replace("\\ ", r"\s+").replace(" ", r"\s+")
                      for h in _places.HOSPITALS)
              + "|" + _places.HOSPITAL_ALIASES)
_HOSPITAL_ALT = _NAMED_ALT + "|" + _places.HOSPITAL_WORDS
_HOSPITAL_NAMED = re.compile(r"\b(?:" + _NAMED_ALT + r")\b", re.I)
_HOSPITAL = re.compile(r"\b(?:" + _HOSPITAL_ALT + r")\b", re.I)
_SENT_TO_RE = re.compile(_SENT_TO, re.I)

# How a crew narrates its own movement. "Clear" on its own is not one of them --
# "Clear, thank you" ends a conversation -- which is why the participle is
# required.
_STATUS_VERB = (r"en\s?route|enroute|transport(?:ing|ed)?|arriv(?:ed|ing|es)?|"
                r"clear(?:ing|ed)|headed|inbound|going|out|back")
# The same vocabulary asked of the words in front of a bare "at", which is how
# _landmark() below tells "arrived at IU" from "a fire alarm at IU Health".
_STATUS_AT = re.compile(r"\b(?:" + _STATUS_VERB + r")\s*$", re.I)
# What only a dispatcher says. The bare "responding" of _SENT_TO is left out on
# purpose: that one is the crew acknowledging, not the dispatcher sending.
_DISPATCH_CLAUSE = re.compile(
    r"\b(?:respond(?:ing)?\s+to|stand(?:ing)?\s?by\s+for)", re.I)

# The trip itself. This is deliberately not folded into LANDMARK_RE's
# introducers: every introducer added there is one more way for a stray capture
# to become an address, whereas this pattern can only ever match a hospital, so
# it is free to be as generous about the verb as the radio is.
_DESTINATION_RE = re.compile(
    r"\b(?:" + _STATUS_VERB + r"|at)"
    r"\s+(?:to|at|from)?\s*(?:the\s+)?"
    r"(?P<loc>" + _HOSPITAL_ALT + r")\b", re.I)


def _destination(text):
    """Where a crew said it was headed, when that is not where the call is.

    "Dispatch, Medic 16 is going to be en route to IU" says something the address
    slot cannot hold: the call happened elsewhere and is over, and the crew is on
    the way to the hospital with the patient. Reporting it as a field rather than
    leaving each reader to dig it out of the text again is what lets the display
    say "transporting to IU" instead of a stale "on scene".

    Every place this can name is a hospital, and that is not a shortcut but the
    whole of the category: anywhere else a crew says it is headed either IS the
    call, and belongs in the address ("Engine 2 en route to Cary Quadrangle"), or
    is not a name at all -- "quarters", "the station", "the scene" are common
    nouns _NOT_A_NAME already throws away.
    """
    m = _DESTINATION_RE.search(text)
    return " ".join(m.group("loc").split()).strip(" ,.") if m else None


def _landmark(text):
    """Building or landmark name from a dispatch with no street address.

    Every capture is tried, not just the first: a pre-alert names the call type
    before the place ("stand by for a possible structure fire at Cary
    Quadrangle"), so rejecting the type clause has to leave the place reachable.
    """
    for m in LANDMARK_RE.finditer(text):
        loc = " ".join(m.group("loc").split()).strip(" ,.")
        if not loc or _NOT_A_PLACE.match(loc):
            continue
        # "respond to, en route, average, on scene" -- whisper punctuated its
        # way into handing the address slot a pile of radio words.
        if all(t.lower() in _NOT_A_NAME for t in re.findall(r"[A-Za-z']+", loc)):
            continue
        # Whoever the crew was talking to is not where they are going.
        if _DISPATCH.search(loc):
            continue
        # A hospital counts as the address only when a dispatcher sent someone
        # to it. "Medic 16 has arrived at IU" is the tail of a call that
        # happened elsewhere; "respond to IU Health for a fire alarm" is a call
        # at the hospital, and the crew needs to be told which building.
        #
        # The bare "at" is both of those sentences, so read together with
        # _HEADED_TO above -- which calls a bare "at" a heading -- this looks
        # like a contradiction and is not. What separates them is whether the
        # transmission is a dispatch going out at all, and there are two tells
        # for that, either of which is enough: the dispatcher's own stock
        # phrasing somewhere in the line, and a call type. A dispatcher says WHY
        # someone is being sent; a crew reporting its own movement has no reason
        # to. Two tells rather than one because "stand by for a possible fire at
        # IU Health" carries no call type TYPE_HINTS recognises -- a bare "fire"
        # cannot be one, since every dispatch on this channel opens "Purdue
        # Fire" -- and it is still plainly a dispatch.
        #
        # The one crew that does state a complaint is the one restating it on
        # the way in, "Medic 16 has arrived at IU, chest pain patient", so a
        # status verb in front of the "at" overrules both tells rather than the
        # other way round.
        sent_to = bool(_SENT_TO_RE.match(m.group("intro"))) or bool(
            m.group("intro").lower() == "at"
            and (_DISPATCH_CLAUSE.search(text) or _IS_TYPE.search(text))
            and not _STATUS_AT.search(text[:m.start("intro")]))
        if _HOSPITAL.search(loc) and not sent_to:
            continue
        if _IS_TYPE.search(loc):
            # The capture ran into the call type, and is still a place if a name
            # we know is in there with it. The hospitals have to be asked for
            # by name rather than left to _fuzzy_place, which is the one table
            # they are deliberately absent from -- so "stand by for an elevator
            # rescue at Franciscan" had a call type, a real address, and nothing
            # that could see the second past the first.
            named = _HOSPITAL_NAMED.search(loc)
            if named:
                return named.group(0)
            if not _fuzzy_place(loc, force_context=True):
                continue
        return loc
    return None


# Last resort when both patterns miss. Whisper mangles the dispatcher's
# "respond to" often enough ("Purdue Fire, 19-15, dispatch, Wetherill Avenue")
# that the building name survives while the phrase introducing it does not.
_GAZ = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(
        _places.CAMPUS, key=len, reverse=True)) + r")\b"
    r"(?:\s+(?:" + _places.BUILDING_WORDS + r"))?", re.I)
# The ambiguous half of the list are also surnames, so they only count as a
# place when a building word follows: "Griffin, you copy?" is not a dispatch.
_GAZ_AMBIG = re.compile(
    r"\b((?:" + "|".join(re.escape(n) for n in _places.CAMPUS_AMBIGUOUS) + r")"
    r"\s+(?:" + _places.BUILDING_WORDS + r"))\b", re.I)
_GAZ_STREET = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(
        _places.STREETS, key=len, reverse=True)) + r")\b", re.I)


def _gazetteer(text):
    """A known local place named anywhere in the transcript."""
    for rx in (_GAZ, _GAZ_AMBIG, _GAZ_STREET):
        m = rx.search(text)
        if m:
            return " ".join(m.group(0).split()).strip(" ,.")
    return None


# --------------------------------------------------------------- near-misses
# A missed address is the one failure this display cannot absorb: a call with no
# location is a call you cannot act on. The recogniser rarely loses the name
# outright, though -- it writes something that SOUNDS like it. "Don Fairhart,
# residence hall" and "your heart residence hall" are both Earhart, and both are
# unreachable by exact matching.
#
# So place names are compared as sound rather than spelling: collapse the
# spelling to a consonant skeleton and score the edit similarity against the
# gazetteer. This returns the CANONICAL name, not the garbled one, because the
# geocoder needs the real building.
def _fold(s):
    """A crude consonant skeleton. Not a phoneme model, just enough of one."""
    s = re.sub(r"[^a-z]", "", str(s).lower())
    if not s:
        return ""
    for a, b in (("ph", "f"), ("ck", "k"), ("gh", ""), ("wr", "r"), ("kn", "n")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)                  # collapse doubles
    return s[0] + re.sub(r"[aeiouhwy]", "", s[1:])   # keep the first letter


# A dispatcher naming a corner drops the suffixes -- "at Stadium and Martin
# Juskey" -- and the suffix the gazetteer carries is dead weight in the scoring:
# "Stadium" lands exactly on the 0.75 floor against "Stadium Avenue", and "Martin
# Juskey" falls under it at 0.72 against "Martin Jischke Drive" while scoring
# 0.87 against the bare "Martin Jischke". So each street goes into the table
# twice, full name and bare base, and both map back to the canonical full name
# because that is the form the geocoder resolves.
_SUFFIX = re.compile(r"\s+(?:street|avenue|road|drive|lane|boulevard|way|"
                     r"parkway|court|circle|place|terrace|trail)$", re.I)


def _match_table(names, ambiguous=False):
    """(canonical, sound, spelling, is_bare, is_ambiguous) per name, plus base."""
    rows = []
    for n in names:
        base = _SUFFIX.sub("", n)
        for key in (n,) if base == n else (n, base):
            rows.append((n, _fold(key), re.sub(r"[^a-z]", "", key.lower()),
                         key != n, ambiguous))
    return rows


_FOLDED_STREETS = _match_table(_places.STREETS)
# Which half of the gazetteer a name came from has to survive into the scoring:
# an exact hit is normally proof enough on its own, and for a surname it is not.
_FOLDED = (_match_table(_places.CAMPUS)
           + _match_table(_places.CAMPUS_AMBIGUOUS, ambiguous=True)
           + _FOLDED_STREETS)


_BUILDING = re.compile(_places.BUILDING_WORDS, re.I)
_BUILDING_TOKENS = frozenset(_places.BUILDING_WORDS.replace("\\", "").split("|"))
# What has to sit next to a name before a sound-alike counts. Exact hits do not
# need this; a near-miss does, or every third word becomes a building.
_CUE = re.compile(r"(?:^|\b)(?:at|to|in|on|near|outside|inside|front\s+of|"
                  r"side\s+of|\d{1,5})$", re.I)
_FUZZY_MIN = 0.75
# A name with "residence hall" or "quadrangle" hard behind it is a building,
# whatever it sounds like, so the bar for which building drops. "your heart
# residence hall" only scores 0.63 against Earhart, and it is Earhart.
_FUZZY_MIN_BUILDING = 0.60
_FUZZY_MARGIN = 0.04


def _fuzzy_place(text, force_context=False, table=None):
    """The local place a garbled span was probably trying to say.

    force_context is for text already known to be a location -- whatever landed
    in the address slot -- where the surrounding cue words have been stripped
    away and demanding them again would throw the match away. table narrows the
    candidates, which is how an intersection side is kept from resolving to a
    residence hall.
    """
    table = table or _FOLDED
    tokens = re.findall(r"[A-Za-z0-9']+", text)
    # A dispatcher repeats the location on purpose -- "Honors North, Honors
    # North ... Honors North, room 4071" -- and saying it three times in one
    # breath does the same job for a name that the word "at" in front of it
    # does, which is to mark it as the place rather than a passing mention. It
    # is admitted as a context signal rather than as extra score because that is
    # what it is evidence of; a repeated name still has to beat the floor on
    # sound like any other. Repetition WITHIN one transmission is the whole of
    # the test, and transmissions are one breath long, so ordinary words do not
    # qualify by accident.
    said_twice = {p for p, c in Counter(
        " ".join(tokens[i:i + n]).lower()
        for n in (1, 2, 3) for i in range(len(tokens) - n + 1)).items() if c > 1}
    best, best_score = None, 0.0
    for i in range(len(tokens)):
        for n in (1, 2, 3):
            span = tokens[i:i + n]
            if len(span) < n:
                break
            low = [t.lower() for t in span]
            # One radio word anywhere in the span disqualifies it: "en route"
            # sounds enough like "Earhart" to invent a dispatch otherwise, and
            # the dispatcher is a party rather than a place in every form the
            # radio says his name.
            if any(t in _NEVER or _DISPATCH.fullmatch(t) for t in low):
                continue
            # The building suffix is not part of the name being matched -- it is
            # re-attached later -- and left in it competes with the name:
            # "residence" scores as well against Ross-Ade as "your heart" does
            # against Earhart.
            if all(t in _BUILDING_TOKENS for t in low):
                continue
            phrase = " ".join(span)
            folded = _fold(phrase)
            plain = re.sub(r"[^a-z]", "", phrase.lower())
            # A skeleton this short is noise -- unless the spelling behind it is
            # not. "Wood" folds all the way down to "wd" and was thrown out
            # before it could be scored, which lost the corner of Third and Wood.
            if len(folded) < 3 and len(plain) < 4:
                continue
            after = " ".join(tokens[i + n:i + n + 3])
            before = " ".join(tokens[max(0, i - 2):i])
            named_building = bool(_BUILDING.match(after.strip()))
            in_context = force_context or named_building or bool(
                _BUILDING.search(after) or _CUE.search(before)
                or phrase.lower() in said_twice)
            floor = _FUZZY_MIN_BUILDING if named_building else _FUZZY_MIN

            ranked = sorted(
                # Sound alone ties "Eatherill" between Wetherill and Elliott
                # Hall, and calls "service" a 0.86 match for Shreve. Spelling
                # alone misses "your heart". Half of each settles both.
                ((difflib.SequenceMatcher(None, folded, nf).ratio()
                  + difflib.SequenceMatcher(None, plain, np_).ratio()) / 2,
                 name, bare, amb)
                for name, nf, np_, bare, amb in table)
            score, name, bare, amb = ranked[-1]
            runner_up = next((sc for sc, nm, *_ in reversed(ranked[:-1])
                              if nm != name), 0.0)
            if score < floor or score <= best_score:
                continue
            # An exact-sounding match stands on its own; anything less has to be
            # somewhere a place name would actually appear.
            if score < 0.999 and not in_context:
                continue
            # Except where the name is also a surname, and an exact hit is
            # exactly the wrong thing to trust: "Griffin, you copy?" is one
            # firefighter calling another by name, and it landed in the address
            # slot as a dispatch to Griffin Hall. The gazetteer has always made
            # that half of the list earn a building word first; this path was
            # skipping the requirement whenever the spelling happened to be
            # perfect, which for a surname it always is.
            if amb and not in_context:
                continue
            # Suffix-less street names are ordinary English -- North, State,
            # Wood, Second -- so they are only offered where the text is already
            # known to be a location: the address slot and the two sides of an
            # intersection. Sweeping the whole transcript with them turned
            # "Engine 2 is north of the scene" into a dispatch to North Street.
            if bare and not force_context:
                continue
            # Two buildings this phrase fits equally well is not evidence for
            # either of them. Judged within the phrase: a different phrase
            # matching something else says nothing about this one.
            if score - runner_up < _FUZZY_MARGIN:
                continue
            best, best_score = name, score
    return best


# Everything a dispatcher or whisper puts between two street names: "Stadium and
# Martin Jischke", "Stadium at Martin Jischke", and the "&" and "/" whisper
# writes when the pause was short. "the corner of X and Y" and "X and Y
# intersection" need nothing extra -- the connector is still in the middle.
_CONNECTOR = frozenset(("and", "at", "&", "/"))
# "&" and "/" have to survive tokenising or those two phrasings never reach the
# loop below at all.
_XSECT_TOKEN = re.compile(r"[A-Za-z0-9']+|[&/]")


def _intersection(text):
    """Both streets of a corner dispatch, as "A and B", or None.

    Purdue FD is sent to corners as often as to addresses -- "stand by for a
    medical run at Stadium and Martin Juskey" is Stadium Avenue at Martin Jischke
    Drive -- and keeping only the first street sends the crew to a road a mile
    long. Each side is canonicalised on its own, because "A and B" is the form
    Nominatim resolves -- but only where OSM carries the crossing node, so a
    corner it has never heard of geocodes to nothing at all.

    The connector is what makes this safe to run on every transcript: "and" is
    everywhere in radio traffic, so both sides must independently land on a
    STREET, which "Medic 16 and Engine 11" and "code 4 and in service" cannot.
    """
    tokens = _XSECT_TOKEN.findall(text)
    for i, tok in enumerate(tokens):
        if tok.lower() not in _CONNECTOR:
            continue
        # Three tokens either side: enough for "Martin Jischke Drive", short
        # enough that the street named in the previous clause is out of reach.
        a = _fuzzy_place(" ".join(tokens[max(0, i - 3):i]),
                         force_context=True, table=_FOLDED_STREETS)
        b = _fuzzy_place(" ".join(tokens[i + 1:i + 4]),
                         force_context=True, table=_FOLDED_STREETS)
        if a and b and a != b:
            return f"{a} and {b}"
    return None


def _unit(raw):
    m = re.match(r"([a-z]+)\s*(\d+)", raw.strip(), re.I)
    if m and m.group(1).lower() in ABBR:
        return ABBR[m.group(1).lower()] + m.group(2)
    return raw.upper().replace(" ", "")


_TRAILING_DIR = re.compile(r"\b(north|south|east|west)\b\.?\s*$", re.I)
# The same hazard at the other end of the name, and the one the gazetteer
# reintroduced: once "Navajo Street" was a known street, the fuzzy matcher in
# _canonical() rewrote "W Navajo Street" to it and "531 W Navajo Street" -- WLFD
# Station 2's own address -- came back as "531 Navajo Street". N/S/E/W prefixes
# are how this county names both sides of a street, and the geocoder resolves
# whichever one it is handed without complaint.
_LEADING_DIR = re.compile(r"^(north|south|east|west|[NSEW])\b\.?\s+", re.I)
_TAIL = re.compile(r"\b(residence\s+hall|dining\s+court|quadrangle|quad|hall|"
                   r"towers?|suites)\b\.?\s*$", re.I)


def _canonical(where):
    """Rewrite a matched location to the name the geocoder will recognise.

    "Don Fairhart, residence hall" is not an address anywhere; Earhart is. The
    house number and any direction survive, because "340 Sagamore Parkway West"
    and "340 Sagamore Parkway East" are a mile and a half apart, and 531 W and
    531 E Navajo Street are two different houses on two different sides of town.

    A direction is put back in the form the dispatcher said it, "W" or "West":
    Nominatim resolves both to the same point (531 W and 531 West Navajo Street
    both come back at 40.45152, -86.91531), so expanding or contracting it would
    buy nothing and could only ever lose information.
    """
    if not where:
        return where
    # Every path that can name a place empties into here, which makes this the
    # one place worth restating the two rules above: the dispatcher is a party,
    # and a hospital name has nothing to gain from a gazetteer of campus halls --
    # "IU Health" is not a garbled Hovde. The name is returned on its own because
    # what arrives is often the clause around it, "a fire at IU Health". A
    # generic "hospital" is not narrowed to, so "1701 Hospital Drive" is left to
    # the street handling below where it belongs.
    if _DISPATCH.search(where):
        return None
    named = _HOSPITAL_NAMED.search(where)
    if named:
        return named.group(0)
    m = re.match(r"(\d{1,5})\s+(.*)", where)
    num, rest = (m.group(1), m.group(2)) if m else (None, where)
    # A trailing direction is normally no part of the name: "340 Sagamore
    # Parkway West" has to reach "Sagamore Parkway" in the table, and the West
    # goes back on afterwards. The residence-hall wings are where that breaks,
    # because there the direction IS the last word of the name -- stripping it
    # left "Honors", the table handed back "Honors South", and the call went out
    # to "Honors South North". So the whole name is offered first, and only one
    # the gazetteer cannot place entire falls through to being taken apart.
    whole = _fuzzy_place(rest, force_context=True)
    if whole and _TRAILING_DIR.search(whole):
        d, stripped, name = None, rest, whole
    else:
        d = _TRAILING_DIR.search(rest)
        stripped = _TRAILING_DIR.sub("", rest)
        name = _fuzzy_place(stripped, force_context=True)
    if not name:
        return where
    tail = _TAIL.search(stripped)
    if tail and not name.lower().endswith(tail.group(1).lower().split()[-1]):
        name = f"{name} {tail.group(1).title()}"
    # The match is tested rather than the text, because on North Street the
    # leading word IS the street: prepending it there would ask the geocoder for
    # "300 North North Street".
    lead = _LEADING_DIR.match(rest)
    if lead and not name.lower().startswith(lead.group(1).lower()):
        name = f"{lead.group(1).title()} {name}"
    out = f"{num} {name}" if num else name
    return f"{out} {d.group(1).title()}" if d else out


def by_regex(text):
    units, seen = [], set()
    for m in UNIT_RE.finditer(spoken_numbers(text)):
        u = _unit(m.group(1))
        if u not in seen:
            seen.add(u)
            units.append(u)
    # "Medic 17, respond to Wiley dining court" is not 17 Wiley Dining Court:
    # "court" is a street suffix, so the house-number pattern happily eats the
    # unit number in front of it.
    addr = next((m for m in ADDR_RE.finditer(text)
                 if not re.search(r"\b(?:engine|medic|ladder|truck|squad|rescue|"
                                  r"tanker|brush|battalion|ambulance|car|chief|"
                                  r"unit)\s*$", text[:m.start(1)], re.I)), None)
    # Street address first -- it is the more specific match, and a landmark
    # capture would happily swallow one. The gazetteer is last: it ignores
    # sentence structure entirely, so it is right less often than either.
    if addr:
        where = _canonical(addr.group(1).replace(",", "").title())
    else:
        # An intersection comes back with both halves already canonical and
        # skips _canonical, which would fuzzy-match the whole "A and B" string
        # back down to whichever single street it resembled most.
        where = _intersection(text) or _canonical(
            _landmark(text) or _gazetteer(text) or _fuzzy_place(text))
    # A hospital that became the address is the call, not a destination: the
    # dispatcher sent someone to it. Only the other case is worth reporting, and
    # a crew transporting away from a street address keeps both.
    dest = _destination(text)
    if dest and where and _HOSPITAL.search(where):
        dest = None
    return {
        "units": units[:6],
        "type": next((lab for pat, lab in TYPE_HINTS if re.search(pat, text, re.I)), None),
        "address": where,
        "destination": dest,
        "city": None,
    }


# Whisper's failure mode on this audio is phonetic, not random: the words it
# emits sound like what was said. Naming the local places lets the model undo
# that -- "Pretty fireman at 16 ... your heart residence hall" is recoverable as
# Purdue Fire, Medic 16, Earhart Residence Hall only if it knows those exist.
LOCAL_CONTEXT = (
    "Coverage area: West Lafayette and Purdue University, Tippecanoe County, "
    "Indiana. Departments: Purdue Fire, West Lafayette Fire, Lafayette Fire, "
    "Tippecanoe County Fire, Wabash Township. Campus buildings dispatched to "
    "include the residence halls Earhart, Cary Quadrangle, Wiley, Hillenbrand, "
    "Meredith, Shreve, Tarkington, Owen, Windsor, Harrison, McCutcheon, "
    "Hawkins, Griffin, First Street Towers, Third Street Suites, and academic "
    "buildings such as Wetherill, Lilly, Beering, Krannert, Armstrong, Hovde, "
    "Stewart Center, Elliott Hall, Mackey Arena, Ross-Ade, Purdue Memorial "
    "Union. Common streets: State, Northwestern, Grant, University, Stadium, "
    "Martin Jischke, Third, Second, Wood, Marsteller, Russell, Waldron, "
    "Harrison, Vine, Tower, Nimitz, Salisbury, Chauncey, Fowler, Robinson, "
    "North, Navajo, Happy Hollow, Sagamore Parkway, Cumberland, Yeager, "
    "Kalberer, Klondike, Soldiers Home, McCormick, Lindberg, Airport Road. "
    "Hospitals: IU Health (said on the radio as the bare \"IU\"), St Elizabeth, "
    "Franciscan. Dispatch is the dispatcher, a party on the radio like a unit."
)


def by_llm(text):
    """Much more robust on garbled transcripts. ~$0.0002 per call."""
    import anthropic
    prompt = (
        "This is a US fire/EMS radio dispatch transcript, produced by speech "
        "recognition on narrowband scanner audio. It is often garbled, and "
        "misheard words usually sound like the real ones (Purdue -> 'pretty', "
        "Earhart -> 'your heart', Medic 16 -> 'a matic 16').\n\n"
        f"{LOCAL_CONTEXT}\n\n"
        'Return ONLY JSON: {"type": <short call type, Title Case>, '
        '"address": <street address or landmark, corrected to the real local '
        'name, or null>, '
        '"destination": <the hospital a crew said it was taking the patient to, '
        'or null>, '
        '"city": <city or null>, "units": [<short ids like E2, M5, L1>]}\n'
        "Correct obvious mishearings against the places listed above, but do "
        "not invent an address the transcript gives no basis for.\n"
        '"Dispatch" names the dispatcher and is never a place or a call type: '
        '"Dispatch, Medic 16 is going to be en route to IU" is Medic 16 talking '
        "to dispatch, and \"Dispatch 16\" is dispatch answering unit 16, not a "
        "unit called D16.\n"
        "A hospital a crew is en route to, arriving at or clearing is where the "
        "patient is being taken, not where the call is: put it in "
        '"destination", never in "address". A hospital a dispatcher sends a '
        "unit TO is the opposite -- it is the address like any other, and then "
        '"destination" is null. Only a hospital ever goes in "destination"; a '
        "unit going back to quarters or on to another scene is neither.\n"
        "Dispatchers name corners as often as addresses. If the location is an "
        'intersection, return both streets as "A and B" -- "at Stadium and '
        'Martin Juskey" is "Stadium Avenue and Martin Jischke Drive" -- since '
        "one street on its own is a mile of road. A list of units, or the "
        '"and" in ordinary chatter, is not an intersection.\n'
        'If this is not a dispatch (chatter, status check), return {"type": null}.\n\n'
        f"Transcript: {text}")
    r = anthropic.Anthropic().messages.create(
        model="claude-haiku-4-5", max_tokens=300,
        messages=[{"role": "user", "content": prompt}])
    raw = re.sub(r"^```(?:json)?|```$", "", r.content[0].text.strip(), flags=re.M).strip()
    out = json.loads(raw)
    out.setdefault("units", [])
    out.setdefault("city", None)
    out.setdefault("address", None)
    out.setdefault("destination", None)
    return out


def parse(text, cfg):
    if cfg.get("use_llm_parser"):
        try:
            return by_llm(text)
        except Exception as e:
            print(f"  ! llm parser failed ({e}), using regex", file=sys.stderr)
    return by_regex(text)
