"""Where the trucks are coming from, and whether they pass your door.

Two questions the display wants answered about a dispatch:

  1. When does the apparatus reach the scene?
  2. Does its route pass close enough to you to hear the siren, and when?

Both need a station, an incident location, and a travel-time estimate. All three
are approximations, and the honest error bar is +/- a minute or two -- see
ETA_NOTE below before trusting a number off this module.
"""
import json, math, os, re, time, urllib.parse, urllib.request
from pathlib import Path

# Station coordinates, from OpenStreetMap's named building POIs rather than
# street-range interpolation (Census puts Stn 2 and 3 tens of metres off, and
# cannot find Stn 1 or PUFD in West Lafayette at all -- see geocode()).
# Addresses confirmed against westlafayette.in.gov and purdue.edu/ehps/fire.
STATIONS = {
    "WL1":  ("WLFD Station 1",  40.426102, -86.908674),   # 300 North St
    "WL2":  ("WLFD Station 2",  40.451520, -86.915309),   # 531 W Navajo St
    "WL3":  ("WLFD Station 3",  40.468799, -86.920082),   # 1100 W Kalberer Rd
    "PU":   ("Purdue FD",       40.427700, -86.924003),   # 1250 3rd St
}

# Which station a dispatch rolls from. Purdue has one house, so its talkgroup
# alone settles it. WLFD numbers its apparatus by station -- E2 and L2 live at
# Station 2 -- so the unit ids parse.py already pulls out of the transcript are
# a better signal than anything a router could offer.
DEPT_STATION = {"Purdue FD": "PU"}
WL_BY_DIGIT = {"1": "WL1", "2": "WL2", "3": "WL3"}

# ETA_NOTE: seconds-level accuracy is not reachable, by this method or any other.
# The dominant term is turnout -- dispatch tones to wheels rolling -- which runs
# 60-90s and varies call to call (NFPA 1710 sets an 80s target for fire). Lights,
# traffic and which units actually roll add more. Real road routing would tighten
# the travel leg but not turnout, so it buys less than it looks like it should.
# Treat every number here as +/- a minute, and tune TURNOUT_SECONDS against calls
# you actually watch.
TURNOUT_SECONDS = 80        # dispatch -> rolling
RESPONSE_MPH = 35           # average over the run, not top speed
ROAD_FACTOR = 1.35          # straight-line -> street distance, urban grid
SIREN_METRES = 600          # "close enough to hear it", incl. surrounding streets

_EARTH_M = 6371000.0
_UA = "firewall-dispatch-display/0.1 (+https://github.com/ethankawley/firewall)"


def haversine(a, b):
    """Great-circle metres between two (lat, lon) pairs."""
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_M * math.asin(math.sqrt(h))


def _xy(pt, origin):
    """Local flat-earth metres, good to well under a metre over a county."""
    lat, lon = pt
    lat0, _ = origin
    return (math.radians(lon - origin[1]) * _EARTH_M * math.cos(math.radians(lat0)),
            math.radians(lat - lat0) * _EARTH_M)


def nearest_on_path(home, start, end):
    """Closest approach of the straight start->end path to home.

    Returns (metres_from_home, fraction_along_path). The fraction is clamped to
    the segment, so a home behind the station or past the incident reports the
    endpoint rather than a point on the infinite line -- otherwise a call in the
    opposite direction could claim to pass you.
    """
    hx, hy = _xy(home, start)
    ex, ey = _xy(end, start)
    seg2 = ex * ex + ey * ey
    if seg2 == 0:
        return haversine(home, start), 0.0
    t = max(0.0, min(1.0, (hx * ex + hy * ey) / seg2))
    return math.hypot(hx - ex * t, hy - ey * t), t


def travel_seconds(metres):
    """Street travel time for a straight-line distance, excluding turnout."""
    return (metres * ROAD_FACTOR) / (RESPONSE_MPH * 0.44704)


def station_for(dept, units):
    """Best guess at the responding house: talkgroup first, then unit numbers."""
    if dept in DEPT_STATION:
        return DEPT_STATION[dept]
    for u in units or ():
        # parse.py normalises to e.g. "E2", "L1", "BC1" -- the leading digit of
        # the number is the station.
        digits = "".join(c for c in u if c.isdigit())
        if digits and digits[0] in WL_BY_DIGIT:
            return WL_BY_DIGIT[digits[0]]
    return None


# Dispatch shorthand, and the handful of campus buildings OSM does not carry
# under the name a dispatcher uses. Each maps to something the geocoders do
# resolve, so the lookup path stays the same and the result still gets cached.
# Keys are matched case- and punctuation-insensitively; extend this as you hear
# what your dispatchers actually say.
LANDMARK_ALIASES = {
    # Not in OSM under any dispatch-recognisable name; use the street address.
    "purdue memorial union":   "101 N Grant St, West Lafayette, IN",
    "pmu":                     "101 N Grant St, West Lafayette, IN",
    "the union":               "101 N Grant St, West Lafayette, IN",
    "krannert":                "403 W State St, West Lafayette, IN",
    "krannert building":       "403 W State St, West Lafayette, IN",
    # Shorthand that OSM will not match but whose full name it does.
    "cary quad":               "Cary Quadrangle",
    "corec":                   "France A. Cordova Recreational Sports Center",
    "co-rec":                  "France A. Cordova Recreational Sports Center",
    "co rec":                  "France A. Cordova Recreational Sports Center",
    "rec center":              "France A. Cordova Recreational Sports Center",
    "ross ade":                "Ross-Ade Stadium",
    "ross-ade":                "Ross-Ade Stadium",
    "mackey":                  "Mackey Arena",
    "elliott hall":            "Elliott Hall of Music",
    "wetherill":               "Wetherill Laboratory of Chemistry",
    # Not shorthand -- a genuine trap. Bare "Beering" resolves to North Steven
    # Beering Drive, a road 1.3km from Beering Hall on the far side of campus,
    # so a dispatch the parser reduced to "Beering" put the truck in the wrong
    # place while looking perfectly resolved.
    "beering":                 "Beering Hall",
    "beering hall":            "Beering Hall",
}


# The one piece of dispatch shorthand not worth listing hall by hall. A
# dispatcher calls it Earhart Residence Hall; OSM calls it Earhart Hall, and the
# difference is the entire lookup -- "Earhart Residence Hall" and "Earhart
# Residence" both resolve to nothing at all, while "Earhart Hall" and "Earhart"
# both land on 40.42581, -86.92496. Both null forms are sitting in the live
# cache right now, which is a real dispatch that reached the screen with no
# location on it.
_RESIDENCE = re.compile(r"\s+residence(\s+hall)?\s*$", re.I)


def _alias(street):
    """Resolve dispatch shorthand to something a geocoder knows, or pass through."""
    key = " ".join(str(street).lower().replace(".", "").replace(",", "").split())
    if key in LANDMARK_ALIASES:
        return LANDMARK_ALIASES[key]
    fixed = _RESIDENCE.sub(" Hall", str(street))
    return fixed if fixed != str(street) else street


# ------------------------------------------------------------------ geocoding
# Nominatim first, Census second, and every result is checked against the city
# we asked for. That check is not defensive padding: the Census geocoder
# silently ignores the city and happily returns Lafayette matches for West
# Lafayette addresses -- "300 North St" comes back as 300 NORTH ST, LAFAYETTE,
# four miles away across the Wabash -- with no error and full confidence. An
# unvalidated match would put a fire on the wrong side of the river.
_CACHE_PATH = Path(__file__).parent.parent / ".geocache.json"
_cache = None
_last_nominatim = [0.0]


def _load_cache():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text())
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    try:
        _CACHE_PATH.write_text(json.dumps(_cache, indent=1, sort_keys=True))
    except Exception:
        pass        # a cold cache costs a lookup, not a call; never fail on it


def _same_city(a, b):
    """Exact city comparison, ignoring case, spacing and punctuation.

    Substring matching is what you reach for first and it is wrong here in the
    worst possible way: "Lafayette" is a substring of "West Lafayette", so a
    Lafayette address sails through a guard meant to keep the two apart -- the
    very confusion the guard exists to prevent, four miles and one river out.
    """
    norm = lambda x: "".join(c for c in str(x).lower() if c.isalnum())
    return norm(a) == norm(b)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


def _nominatim(street, city, state):
    # Nominatim's usage policy is one request per second, absolute. Dispatch
    # volume here is a few calls an hour, so simply sleeping is honest and
    # sufficient.
    wait = 1.1 - (time.time() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.time()
    # Structured first: it is the accurate form for street addresses, and it is
    # the only form that reliably honours the city. But it indexes streets, not
    # points of interest -- "Ross-Ade Stadium" and "Wetherill Laboratory of
    # Chemistry" both miss entirely -- so a free-text query is tried after it.
    # Campus dispatches name buildings far more often than streets, so without
    # this second pass most Purdue FD calls resolve to nothing.
    attempts = [{"street": street, "city": city, "state": state, "country": "US"},
                {"q": f"{street}, {city}, {state}"}]
    for extra in attempts:
        params = {"format": "json", "limit": 1, "addressdetails": 1, **extra}
        try:
            hits = _get("https://nominatim.openstreetmap.org/search?"
                        + urllib.parse.urlencode(params))
        except Exception:
            hits = []
        for hit in hits:
            addr = hit.get("address") or {}
            got = (addr.get("city") or addr.get("town") or addr.get("village") or "")
            if not got or _same_city(city, got):
                return float(hit["lat"]), float(hit["lon"]), hit.get("display_name", "")
        # Both attempts hit the network, so the rate limit applies between them.
        wait = 1.1 - (time.time() - _last_nominatim[0])
        if wait > 0:
            time.sleep(wait)
        _last_nominatim[0] = time.time()
    return None


def _reverse_city(lat, lon):
    """The city a point actually sits in, per OSM. None if it cannot be told."""
    wait = 1.1 - (time.time() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.time()
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "json",
                                "zoom": 10, "addressdetails": 1})
    a = (_get(f"https://nominatim.openstreetmap.org/reverse?{q}") or {}).get("address") or {}
    return a.get("city") or a.get("town") or a.get("village")


def _census(street, city, state):
    q = urllib.parse.urlencode({"street": street, "city": city, "state": state,
                                "benchmark": "Public_AR_Current", "format": "json"})
    body = _get("https://geocoding.geo.census.gov/geocoder/locations/address?" + q)
    for m in body["result"]["addressMatches"]:
        # The city in matchedAddress cannot be trusted to mean anything. Census
        # normalises to the USPS postal city and echoes back whatever was asked
        # for: query "500 W Navajo St, Lafayette" and it answers "500 W NAVAJO
        # ST, LAFAYETTE, IN, 47906" while handing over West Lafayette's
        # coordinates and West Lafayette's ZIP. So the returned point is checked
        # against what OSM says is actually there, rather than against a label.
        matched = m.get("matchedAddress", "")
        c = m["coordinates"]
        lat, lon = float(c["y"]), float(c["x"])
        try:
            actual = _reverse_city(lat, lon)
        except Exception:
            actual = None
        if actual and not _same_city(city, actual):
            continue
        return lat, lon, matched
    return None


def split_address(text, city="West Lafayette", state="IN"):
    """"123 Main St, West Lafayette, IN 47906" -> ("123 Main St", city, state).

    Both geocoders take street, city and state as separate fields, and cramming
    a whole one-line address into the street field breaks them in two different
    ways -- a trailing ZIP makes Nominatim return nothing at all, and an
    embedded city is simply not read, so the city guard below ends up checking
    the caller's default instead of what the user actually typed. Splitting here
    means "500 W Navajo St, Lafayette, IN" is looked up in Lafayette, and
    rejected if it does not exist there, instead of silently resolving to the
    West Lafayette street of the same name across the river.
    """
    parts = [x.strip() for x in str(text).split(",") if x.strip()]
    if not parts:
        return "", city, state
    street = parts[0]
    if len(parts) >= 2:
        city = parts[1]
    if len(parts) >= 3:
        # Third field is "IN" or "IN 47906"; the ZIP is dropped rather than
        # passed on, since neither geocoder needs it once the city is right.
        state = parts[2].split()[0]
    return street, city, state


def _lookup(street, city, state):
    """One address through both geocoders in turn. (lat, lon, label) or None."""
    for source in (_nominatim, _census):
        try:
            hit = source(street, city, state)
        except Exception:
            hit = None
        if hit:
            return hit
    return None


# Everything parse.py puts between the two streets of a corner, plus the "/" a
# dispatcher writes for a state-road junction.
_JOINER = re.compile(r"\s+(?:and|at|&)\s+|\s*/\s*", re.I)


def _key(text, city, state):
    """(street, city, state, cache key) for an address, or None if there is none."""
    if not text:
        return None
    street, city, state = split_address(text, city, state)
    if not street:
        return None
    # Aliases may expand to a full address, so re-split after substituting.
    aliased = _alias(street)
    if aliased != street:
        street, city, state = split_address(aliased, city, state)
    return street, city, state, f"{street.strip().lower()}|{city.lower()}|{state.lower()}"


def geocode(text, city="West Lafayette", state="IN"):
    """(lat, lon) for a dispatch address, or None. Cached on disk forever.

    Accepts either a bare street ("340 Sagamore Pkwy W"), a landmark ("Cary
    Quadrangle"), an intersection ("Third Street and Wood Street"), or a full
    one-line address with city, state and ZIP. Addresses repeat -- the same
    apartment block, the same intersection -- so the cache means a busy night
    mostly costs nothing and stays inside Nominatim's rate limit.
    """
    parts = _key(text, city, state)
    if not parts:
        return None
    street, city, state, key = parts
    cache = _load_cache()
    if key in cache:
        v = cache[key]
        return tuple(v[:2]) if v else None

    hit, approx = _lookup(street, city, state), False
    if not hit and _JOINER.search(street):
        # A corner resolves only where OSM carries the crossing node, and often
        # it does not: "Stadium Avenue and Martin Jischke Drive" comes back at
        # 40.431448, -86.921567 while "Third Street and Wood Street" and
        # "Marsteller Street and Third Street" come back with nothing at all,
        # though all three are real corners within a few blocks of campus. So a
        # miss retries on the first street named, which is the one the
        # dispatcher said first and the one the crew is driving down: a mile of
        # the right road beats the blank screen that a call with no location
        # leaves, and that blankness is the one failure this display cannot
        # absorb. approximate() below is how anyone downstream can tell.
        first = _JOINER.split(street)[0].strip()
        fall = geocode(first, city, state) if first else None
        if fall:
            hit, approx = (fall[0], fall[1], f"nearest street of {street}"), True
    # Written under the key that was asked for either way, so the corner OSM has
    # never heard of costs its two lookups once and not once per dispatch -- and
    # a total miss still stores null, which is what stops it being re-queried
    # forever.
    cache[key] = (list(hit) + [True] if approx else list(hit)) if hit else None
    _save_cache()
    return (hit[0], hit[1]) if hit else None


def approximate(text, city="West Lafayette", state="IN"):
    """Did geocode(text) answer with one street of an intersection?

    Reads the cache geocode() has just filled and never the network, so it costs
    nothing, and it stays honest for a caller who has replaced geocode() with a
    stub: an unlooked-up address is not approximate, it is simply unknown.
    """
    parts = _key(text, city, state)
    v = _load_cache().get(parts[3]) if parts else None
    return bool(v and len(v) > 3 and v[3])


# ------------------------------------------------------------------- the answer
_home_resolved = {}


def _home_point(cfg):
    """cfg["home"] as (lat, lon). A string is geocoded once and remembered."""
    home = cfg.get("home")
    if not home or isinstance(home, (tuple, list)):
        return tuple(home) if home else None
    if home not in _home_resolved:
        _home_resolved[home] = geocode(home)
    return _home_resolved[home]


def assess(call, cfg):
    """Attach ETA and proximity to a published call. Returns a dict or None.

    Answers the two questions in the module docstring, in the order the display
    wants them: if the run passes within earshot, when it passes you is the
    headline and the scene ETA is the footnote. If it does not, or you have set
    no home, the scene ETA is all there is.

    Re-runs cheaply, and is meant to: core calls it again whenever the radio
    says something that moves call["status"], because a heard status beats a
    modelled one. Every lookup it does is served from the geocode cache.
    """
    key = station_for(call.get("dept"), call.get("units"))
    if not key or not call.get("address"):
        return None
    _, slat, slon = STATIONS[key]
    station = (slat, slon)
    where = call.get("city") or "West Lafayette"
    incident = geocode(call["address"], where)
    if not incident:
        return None

    now = time.time()
    base = float(call.get("ts") or now)
    scene_m = haversine(station, incident)

    # Turnout is the guessed half of this sum, and the radio can replace it with
    # a fact. On the Medic 16 call the guess had the truck on scene at +140s
    # while at +152s the unit was still calling itself en route -- it had not
    # finished turnout at the moment we claimed it had finished the whole run.
    # So once a crew says it is rolling, that instant is the baseline and the
    # 80s target is dropped: only the drive is still being estimated.
    status = call.get("status") or {}
    rolling_at = (float(status["ts"]) if status.get("state") == "enroute"
                  else base + TURNOUT_SECONDS)

    # Absolute arrival instants as well as the relative seconds: the display
    # counts down every second, and a target it can subtract `now` from stays
    # right no matter how long the payload sat in flight or on screen.
    scene_at = rolling_at + travel_seconds(scene_m)
    # Clamped at zero: Broadcastify publishes a call 10-30s after it went out,
    # so a short run is often already over by the time we see it, and "on scene
    # in ~-243s" is not a thing the display should ever say.
    out = {"station": STATIONS[key][0],
           "station_key": key,
           "incident": [round(incident[0], 6), round(incident[1], 6)],
           # True when geocode() fell back to one street of an intersection, so
           # the point is somewhere on the right road rather than at the corner.
           # Every distance and ETA below is built on it, so the display should
           # be free to hedge them. Nothing reads it yet.
           "incident_approx": approximate(call["address"], where),
           "scene_metres": round(scene_m),
           "scene_eta": round(max(0, scene_at - now)),
           "scene_at": round(scene_at),
           # Never dropped and never conditional: every arrival time this module
           # produces is modelled, not heard, and the display has to be able to
           # say so in the one place a viewer reads it as a fact.
           "estimated": True,
           "passes_you": False}

    home = _home_point(cfg)
    if home:
        near_m, frac = nearest_on_path(home, station, incident)
        out["closest_metres"] = round(near_m)
        out["home_metres"] = round(haversine(home, incident))
        if near_m <= cfg.get("siren_metres", SIREN_METRES):
            out["passes_you"] = True
            # Distance travelled before the closest approach to you, off the
            # same baseline as the scene arrival for the same reason.
            pass_at = rolling_at + travel_seconds(scene_m * frac)
            out["pass_eta"] = round(max(0, pass_at - now))
            out["pass_at"] = round(pass_at)
    return out
