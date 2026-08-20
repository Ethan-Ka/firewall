"""Local place names, in one list, used twice.

The recogniser gets them as whisper `hotwords` so it stops hearing "Purdue" as
"pretty", and the parser gets them as a gazetteer so a building name still
resolves when the dispatcher's "respond to" was chewed up by the radio path.
Keeping one list means adding a hall you keep hearing fixes both at once.
"""

DEPARTMENTS = (
    "Purdue Fire", "West Lafayette Fire", "Tippecanoe County", "Lafayette",
)

# Distinctive enough to name a location on their own.
CAMPUS_BUILDINGS = (
    "Earhart", "Cary Quadrangle", "Hillenbrand", "Tarkington", "McCutcheon",
    "Wetherill", "Krannert", "Hovde", "Beering", "Slayter", "Mackey Arena",
    "Ross-Ade", "Purdue Memorial Union", "Purdue Village", "Honors College",
    "First Street Towers", "Third Street Suites", "Discovery Park",
    "Stewart Center", "Elliott Hall", "Corec",
)

# The halls above that are really two or more buildings, under the names the
# dispatcher uses for the halves. A run recorded on 2026-08-20 was called in as
# "Honors North" three times in one breath and filed at Beering, a road a mile
# away, because "Honors North" was not a candidate at all -- the list had the
# parent "Honors College" and nothing else. The wing is not a nicety: Honors
# North and Honors South are 110 m and two different front doors apart, and Cary
# East and Cary West are further than that.
#
# These cost no hotwords, which is why they are a separate tuple that VOCAB
# below leaves out. Whisper wrote "Honors North" correctly three times unaided,
# and it would: every stem here is already biased by its parent above -- Honors
# by "Honors College", Cary by "Cary Quadrangle", Meredith by itself -- and
# north, south, east and west are ordinary English. The tokens they would have
# cost are four street names that whisper does need.
CAMPUS_WINGS = ("Honors North", "Honors South", "Cary East", "Cary West",
                "Meredith South")

# One name for the two, because everything downstream -- the parser's gazetteer,
# its sound-alike table, and the incident log's judgement of how specific an
# address is -- wants the buildings and their wings as a single list of places.
CAMPUS = CAMPUS_BUILDINGS + CAMPUS_WINGS

# Also ordinary surnames ("Owen", "Harrison"), so a name from this half only
# counts as a place when the sentence around it says one is expected --
# otherwise "Griffin, you copy?" becomes a dispatch to Griffin Hall.
CAMPUS_AMBIGUOUS = (
    "Wiley", "Meredith", "Shreve", "Owen", "Windsor", "Harrison", "Hawkins",
    "Griffin", "Lilly", "Armstrong",
)
BUILDING_WORDS = (r"hall|quad|quadrangle|residence|dining|court|tower|towers|"
                  r"suites|center|centre|laboratory|lab|complex|apartments")

# Where an EMS call ENDS, which is not where it happened. "Dispatch, Medic 16 is
# going to be en route to IU" is the transport tail of a run whose location was
# somewhere else and which was already over by then, and it opened an incident
# on this display whose address read "IU". The parser needs the names before it
# can tell a destination from a location.
#
# Deliberately not in CAMPUS or STREETS: those two are swept for anywhere in a
# transcript, and a hospital is only the address when a dispatcher sent someone
# to it -- "Engine 11, respond to IU Health for a fire alarm" -- which is
# something the sentence knows and a gazetteer cannot. The dispatchers here say
# the bare "IU" for IU Health Arnett, across the river in Lafayette.
HOSPITALS = ("IU Health", "St Elizabeth", "Franciscan")

# The other spellings of those same three, in the form BUILDING_WORDS above uses.
# None need a hotword: the bare "IU" rides on the "IU" of "IU Health" above,
# which is the same token to the decoder.
HOSPITAL_ALIASES = r"IU|St\.\s+Elizabeth|Saint\s+Elizabeth"

# And the generic nouns. These are kept apart from the two lists above rather
# than merged into them, because a name can be narrowed to -- "a fire at IU
# Health" is a fire at IU Health -- and a noun cannot: "a fall at the hospital"
# narrowed to "hospital" would be a dispatch to no address at all. They also get
# no hotword; "hospital" is ordinary English the decoder gets right. "ER" is not
# here on its own because whisper writes a bare "er" for the pause before a word.
HOSPITAL_WORDS = r"hospital|emergency\s+room|the\s+ER"

# Ordered most-dispatched first, and the order is load-bearing here in a way it
# is nowhere else in this file: the list before these additions already spent 218
# of whisper's 223 hotword tokens, so only the fifteen streets above the marker
# below reach the recogniser at all. Everything after it still reaches the
# parser's gazetteer, where it costs nothing and occasionally saves a call.
#
# HOSPITALS cost ten of those tokens when it was added, and the three streets it
# pushed below the marker -- Second Street, Vine Street, Tower Drive -- are what
# paid for them. Those three are ordinary English the decoder gets right
# unaided; "IU" is two letters that it loses.
#
# Campus and the Village lead because Purdue FD is one of the two talkgroups this
# watches and its runs rarely leave that square mile, and because those are the
# names whisper cannot guess -- "Jischke" comes back as "Juskey" without help,
# while "Airport Road" is ordinary English the decoder gets right unaided. The
# outlying county roads therefore trail, rare dispatches or not.
STREETS = (
    "State Street", "Northwestern Avenue", "Grant Street", "Stadium Avenue",
    "Martin Jischke Drive", "University Street", "Third Street", "Wood Street",
    "Marsteller Street", "Russell Street", "Salisbury Street", "Waldron Street",
    "Harrison Street", "Sagamore Parkway", "Cumberland Avenue",
    # ---- past here whisper never sees them; the parser still does ----
    "Second Street", "Vine Street", "Tower Drive", "Nimitz Drive",
    "Navajo Street", "North Street", "Fowler Avenue",
    "Chauncey Avenue", "Robinson Street",
    "Happy Hollow Road", "Lindberg Road", "Oval Drive", "Littleton Street",
    "Steely Street", "Cherry Lane", "McCormick Road", "Yeager Road",
    "Kalberer Road", "Klondike Road", "Soldiers Home Road", "Airport Road",
)

# The local fleet, by the designators the dispatcher actually says. Numbers in
# here bias the recogniser toward those numbers, which is why this is the
# station roster and not a generic 1-99: "Medic 16" is worth biasing toward
# because it is real, and it keeps coming back as "Sixteens", "816" and
# "makes 16".
UNITS = ("Medic 16", "Medic 17", "Engine 16", "Unit 100", "Purdue Police",
         "PUPD")

# Radio words worth biasing toward that are not places. Call types are
# deliberately absent: "structure fire" and "chest pain" are ordinary English
# that whisper already gets right, and the hotword prompt is capped at 223
# tokens, so every token spent on them is a place name pushed off the end.
JARGON = ("dispatch", "respond to", "en route", "on scene", "in service",
          "stand by", "code 4", "residence hall")

# A line of dispatch in the house style, handed to whisper as `initial_prompt`.
# It teaches format rather than vocabulary -- digits over words, the comma after
# each unit, the "respond to X for Y" shape -- and measured on its own it took
# word error from 17.9% to 11.4%.
DISPATCH_STYLE = (
    "Purdue Fire, Medic 16, respond to Earhart Residence Hall for a medical. "
    "West Lafayette Fire, Engine 2, Medic 2, respond to 340 Sagamore Parkway "
    "West for a reported structure fire.")

# Ordered most-important-first: faster-whisper truncates the hotword prompt at
# 223 tokens and keeps the head, so anything past that is silently dropped.
VOCAB = (", ".join(DEPARTMENTS + UNITS + JARGON) + ". "
         + ", ".join(CAMPUS_BUILDINGS + CAMPUS_AMBIGUOUS) + ". "
         + ", ".join(HOSPITALS) + ". "
         + ", ".join(STREETS) + ".")
