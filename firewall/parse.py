"""Dispatch transcript -> {type, address, city, units}."""
import json, re, sys

UNIT_RE = re.compile(
    r"\b((?:engine|medic|ladder|truck|squad|rescue|tanker|brush|battalion|ambulance|car|chief)"
    r"\s*\d{1,3}|[EMLTSRBA]\d{1,3})\b", re.I)

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
          r"way|place|pl|circle|cir|parkway|pkwy|highway|hwy|terrace|trail)")
ADDR_RE = re.compile(
    rf"\b(\d{{1,5}}\s+(?:(?:north|south|east|west|[NSEW])\.?\s+)?"
    rf"(?:[A-Z][\w'-]*\s+){{0,4}}{STREET}\b\.?"
    rf"(?:\s+(?:north|south|east|west))?)", re.I)


def _unit(raw):
    m = re.match(r"([a-z]+)\s*(\d+)", raw.strip(), re.I)
    if m and m.group(1).lower() in ABBR:
        return ABBR[m.group(1).lower()] + m.group(2)
    return raw.upper().replace(" ", "")


def by_regex(text):
    units, seen = [], set()
    for m in UNIT_RE.finditer(text):
        u = _unit(m.group(1))
        if u not in seen:
            seen.add(u)
            units.append(u)
    addr = ADDR_RE.search(text)
    return {
        "units": units[:6],
        "type": next((lab for pat, lab in TYPE_HINTS if re.search(pat, text, re.I)), None),
        "address": addr.group(1).title() if addr else None,
        "city": None,
    }


def by_llm(text):
    """Much more robust on garbled transcripts. ~$0.0002 per call."""
    import anthropic
    prompt = (
        "This is a US fire/EMS radio dispatch transcript. It may be garbled.\n"
        'Return ONLY JSON: {"type": <short call type, Title Case>, '
        '"address": <street address or landmark, or null>, '
        '"city": <city or null>, "units": [<short ids like E2, M5, L1>]}\n'
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
    return out


def parse(text, cfg):
    if cfg.get("use_llm_parser"):
        try:
            return by_llm(text)
        except Exception as e:
            print(f"  ! llm parser failed ({e}), using regex", file=sys.stderr)
    return by_regex(text)
