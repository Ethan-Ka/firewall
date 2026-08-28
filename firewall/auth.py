"""Who is allowed to read what was said.

The radio is public. What the radio said about a particular person at a
particular address, transcribed and left on a page, is not -- not in the way a
call type and a block number are. A structure fire at 340 Sagamore is a fact
about a building; "60 year old male, chest pain, conscious and breathing" is a
fact about somebody's grandfather, and it is the transcript that carries it.
So the transcript is the thing behind a login and nothing else is: the display
still shows the call, the map, the units, the ETA and it still plays the audio
to anyone who opens it. Only the words come out of the payload.

That split is deliberate and it is worth being honest about: gating the text
while serving the clip it was made from does not hide what was said from
someone determined to listen. It is not meant to. It stops the transcript being
a searchable, screenshottable, indexable record of a medical call, which is the
part that actually travels.

The model here is a house key, not an identity system. There are no accounts to
sign up for, no password reset, no database -- a name and a password per person
in .env, handed out by the person running this:

    FIREWALL_USERS=alex:reed-tumbler-42,sam:kiln-oxbow-9

`firewall --invite alex` generates one of those lines. A person is revoked by
deleting their line and restarting. Because those passwords live in a file in
the clear, they must be passwords that exist nowhere else -- which is why the
generator makes them rather than inviting anyone to choose one.

The session itself is a signed cookie and no server-side state, so this holds
across a restart and costs nothing to keep. It is signed, not encrypted: the
cookie says who you are and cannot be edited to say otherwise, and it has never
carried anything worth hiding.
"""
import base64, hashlib, hmac, secrets, threading, time

COOKIE = "firewall_session"

# A month. Long because the failure this trades against is a wall screen in a
# kitchen asking a household to log in again every week, and the thing being
# protected is a transcript of a radio broadcast, not a bank.
DEFAULT_DAYS = 30

# Words for the generated passwords. Short, unambiguous when read aloud over
# the phone or retyped off a screen, and no pairs that differ by one letter.
_WORDS = ("amber", "anvil", "birch", "cinder", "cobalt", "cordwood", "dune",
          "ember", "fathom", "ferrule", "gable", "harbor", "ingot", "juniper",
          "kiln", "lantern", "marrow", "nickel", "oxbow", "pewter", "quarry",
          "reed", "sable", "tumbler", "umber", "vellum", "walnut", "yarrow")


def accounts(cfg):
    """The name -> password pairs this installation will let in."""
    return {str(k).strip(): str(v) for k, v in (cfg.get("users") or {}).items()
            if str(k).strip() and v}


def required(cfg):
    """Is anything gated at all?

    No accounts means no gate: firewall started life as one screen on one wall
    on one network, that is still what most runs of it are, and a login nobody
    asked for would be a lock on a door in your own house. Adding the first
    FIREWALL_USERS line is what turns this on, and it turns on everywhere at
    once -- there is no half-locked state to reason about.
    """
    return bool(accounts(cfg))


def _secret(cfg):
    """The key session cookies are signed with.

    FIREWALL_SECRET if you set one. Otherwise it is derived from the credential
    list, which is the property worth having: it survives a restart, so nobody
    is logged out because the machine rebooted at 4am, and it changes the moment
    the credentials do, so editing FIREWALL_USERS ends every session signed
    under the old list. Revoking somebody is deleting their line -- and this is
    what makes that take effect immediately rather than in thirty days.

    The cost is that any change to the list logs everyone out, including
    adding a friend. Set FIREWALL_SECRET to a random string if that matters
    more to you than one-step revocation.
    """
    raw = cfg.get("session_secret")
    if raw:
        return str(raw).encode()
    acc = accounts(cfg)
    seed = "\n".join(f"{n}\x00{acc[n]}" for n in sorted(acc))
    return hashlib.sha256(b"firewall session v1\n" + seed.encode()).digest()


def _sign(msg, cfg):
    return hmac.new(_secret(cfg), msg, hashlib.sha256).digest()


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def check(name, password, cfg):
    """Are these the credentials of somebody who is allowed in?

    compare_digest on both halves, and an unknown name is still compared --
    against a value it cannot match -- so that the time this takes does not
    tell an unauthenticated caller which names exist.
    """
    acc = accounts(cfg)
    want = acc.get(str(name).strip())
    ok_name = want is not None
    got = str(password or "").encode()
    # Bytes, not str: compare_digest refuses a str with anything non-ASCII in
    # it, and a password is exactly the field somebody pastes an accent into.
    return hmac.compare_digest(
        got, want.encode() if ok_name else got + b"\x00") and ok_name


def issue(name, cfg, days=None):
    """A cookie value saying this person signed in, expiring `days` from now."""
    days = DEFAULT_DAYS if days is None else days
    body = f"{_b64(str(name).encode())}.{int(time.time() + days * 86400)}"
    return f"v1.{body}.{_b64(_sign(body.encode(), cfg))}"


def verify(token, cfg):
    """The name in `token`, or None if it is not one we signed and still honour.

    Every failure -- forged, expired, truncated, signed under credentials that
    have since changed, or naming somebody who has since been deleted -- is the
    same None. There is nothing a caller could usefully do differently with the
    reason, and a page that says "your signature is invalid" rather than "sign
    in" only ever helps whoever is trying signatures.
    """
    try:
        ver, name_b64, exp, sig = str(token or "").split(".")
        if ver != "v1":
            return None
        if not hmac.compare_digest(_unb64(sig),
                                   _sign(f"{name_b64}.{exp}".encode(), cfg)):
            return None
        if time.time() > int(exp):
            return None
        name = _unb64(name_b64).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return name if name in accounts(cfg) else None


def new_password(words=3):
    """A password for one friend. Generated rather than chosen, because these
    are stored in the clear in a .env file and a person choosing one here would
    reach for one they use somewhere it matters."""
    return "-".join(secrets.choice(_WORDS) for _ in range(words)) + \
           f"-{secrets.randbelow(90) + 10}"


# ------------------------------------------------------------- throttling
#
# A generated password is three words and two digits out of a 28-word list:
# a couple of million combinations, which is plenty against somebody typing and
# nothing at all against a script on a fast connection left running for a
# weekend. This is what makes the difference. Ten wrong answers from one
# address and that address waits five minutes, which turns weeks into
# centuries and costs an honest person who fat-fingered their password twice
# exactly nothing.
#
# Per address and in memory: no store to keep, and a restart clearing it is
# fine -- a restart is not something an attacker can cause.

_MAX_TRIES = 10
_WINDOW = 300

_fails = {}
_fail_lock = threading.Lock()


def locked_out(who):
    """Has this address used up its guesses? Also expires stale entries, which
    is the only reaping this dict gets -- an address that never comes back
    holds one small tuple until the process ends."""
    now = time.time()
    with _fail_lock:
        for k in [k for k, (_, seen) in _fails.items() if now - seen > _WINDOW]:
            del _fails[k]
        n, seen = _fails.get(who, (0, 0))
        return n >= _MAX_TRIES and now - seen <= _WINDOW


def note_failure(who):
    now = time.time()
    with _fail_lock:
        n, seen = _fails.get(who, (0, 0))
        _fails[who] = (n + 1 if now - seen <= _WINDOW else 1, now)


def clear_failures(who):
    """A correct password ends the lockout. The counter exists to slow guessing,
    and somebody who has just proved they are not guessing should not spend the
    next five minutes locked out by whoever else shares their address."""
    with _fail_lock:
        _fails.pop(who, None)


# ------------------------------------------------------------- redaction
#
# Below is the whole list of places a transmission's words reach a browser.
# They are enumerated by hand, and that is a decision: a scrubber that walked
# the payload looking for keys named "text" would quietly start passing any
# field added later, and the failure mode of this code is that a medical
# transcript ships to somebody who is not signed in. A new field carrying
# speech should break a test, not slip through a pattern match.

def _mute(d, *keys):
    """Null out `keys` on a dict that may be None. Idempotent on purpose: the
    same call dict is reachable twice in one payload (calls[0] IS "call"), and
    the second pass has to be harmless rather than clever."""
    if isinstance(d, dict):
        for k in keys:
            if d.get(k) is not None:
                d[k] = None


def strip_current(payload):
    """/api/current with the words taken out.

    Everything that is not speech stays: ids, timings, urls, dispatch flags.
    The display still draws the tape, still numbers the rows, still plays them.
    A row reads as locked rather than as silence -- which is why the text goes
    to null and not to "", since "" already means "nothing was said here".
    """
    payload["speech"] = False
    for call in payload.get("calls") or []:
        _mute(call, "transcript")
        _mute(call.get("status"), "text")
        for r in call.get("reopenings") or []:
            _mute(r, "text")
        for row in call.get("radio") or []:
            _mute(row, "text")
    for row in payload.get("feed") or []:
        _mute(row, "text")
    # "call" and "radio" are the same objects as calls[0] and its rows, so the
    # loop above has already done them. Repeated anyway: that aliasing is a
    # detail of snapshot() and this function must not be the thing that breaks
    # if it ever stops being true.
    _mute(payload.get("call"), "transcript")
    _mute((payload.get("call") or {}).get("status"), "text")
    for row in payload.get("radio") or []:
        _mute(row, "text")
    return payload


def strip_log(payload):
    """/api/log with the words taken out. The log is types, addresses, units
    and times -- all of which stay. Only the line quoted off the radio goes."""
    payload["speech"] = False
    for call in payload.get("calls") or []:
        _mute(call.get("status"), "text")
        for u in call.get("unit_states") or []:
            _mute(u, "text")
    return payload
