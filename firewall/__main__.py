"""firewall. One command runs the whole thing.

    firewall                      # watches Broadcastify, opens the display
    firewall --source trunk       # a local trunk-recorder directory instead
"""
import argparse, json, re, threading, time, webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from pathlib import Path

from . import (auth, config as _config, core, corpus as _corpus, geo,
               incidents as _incidents, parse as _parse, purdue, push, sources)

HERE = Path(__file__).parent
DISPLAY = HERE / "display.html"
REVIEW = HERE / "review.html"
LOGIN = HERE / "login.html"
# Where `npm run build` in web/ puts the call tracker.
#
# Outside the package on purpose. The tracker is a static site that is meant to
# be hosted somewhere else -- Vercel, in practice -- and reach a firewall server
# over the network, so its build is a build of web/ and not a directory of this
# Python package. Serving it from here as well is a convenience for anyone
# running the whole thing on one machine, and it is the same files either way.
#
# Nothing in the repo writes this directory and a fresh checkout does not have
# one, so everything below has to keep working when it is missing: the display
# is the program and the tracker is a second screen for it.
DIST = HERE.parent / "web" / "dist"
TRACKER = DIST / "index.html"

AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav",
               ".m4a": "audio/mp4", ".ogg": "audio/ogg"}

# What the built screen is made of. A browser will not run a module served as
# text/plain and will not apply a stylesheet served as anything but text/css, so
# guessing wrong here fails as a blank page with no error on it -- which is why
# the fallback is application/octet-stream rather than something plausible:
# a download prompt is at least a symptom you can read.
STATIC_TYPES = {".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".mjs": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json",
                ".map": "application/json",
                ".svg": "image/svg+xml",
                ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
                ".png": "image/png", ".ico": "image/x-icon"}

# Vite writes the build hash into every filename under assets/, so one of those
# URLs can only ever mean one file and may be cached until the heat death of the
# browser. index.html is the opposite: it is the one file whose name never
# changes and whose whole job is to name today's hashed bundles, so a cached
# copy of it points a rebuilt app at assets that are no longer on disk.
ASSET_CACHE = "public, max-age=31536000, immutable"

# Served when web/ has never been built. A 404 with nothing in it, or a
# traceback, both read as "the tracker is broken"; the truth is that it was
# never made, and the fix is one command, so the command is the page.
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>tracker not built</title>
<body style="font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;padding:2rem">
<p>The call tracker has not been built yet.</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>That writes web/dist/index.html and the assets it loads.
The display at / and the review page at /review do not need it.</p>
<p>The hosted copy, if you deployed one, is a separate build of the same
directory and does not need this server to have one.</p>
"""


def _cookie(value, cfg, clear=False):
    """One Set-Cookie line for the session.

    HttpOnly because no script here needs to read it and one that could would
    hand it to anything injected into the page. SameSite=Lax so a link from a
    friend's message still arrives signed in while a form posted from
    somewhere else does not. Not Secure: this is served over plain HTTP on a
    home network, and a Secure cookie would simply never be stored -- put it
    behind a TLS tunnel to reach it from outside, and see the README.

    Both of those flip once allow_origins names a hosted tracker, because that
    tracker is a different site and Lax means the browser never sends this with
    its fetches: signing in would appear to work and the words would stay
    missing for ever. SameSite=None is what makes it travel, None is only
    honoured with Secure, and Secure is only stored over HTTPS -- which is
    already true of anything reachable by a hosted page, so the three move
    together. Setting allow_origins on a plain-HTTP server breaks its own
    login, and that is the honest failure: the configuration says the browser
    is elsewhere.
    """
    days = 0 if clear else int(cfg.get("session_days") or auth.DEFAULT_DAYS)
    site = "None; Secure" if cfg.get("allow_origins") else "Lax"
    return (f"{auth.COOKIE}={value}; Path=/; HttpOnly; SameSite={site}; "
            f"Max-Age={days * 86400}")


class _Handler(BaseHTTPRequestHandler):
    cfg = {}

    def log_message(self, *a):
        pass

    def _who(self):
        """The signed-in name on this request, or None.

        None is also what an installation with no FIREWALL_USERS returns for
        everybody, which is why every caller asks _gated() rather than this:
        "nobody is signed in" and "there is nothing to sign in to" are opposite
        answers to the question that actually matters.
        """
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie(raw)
        except Exception:                       # a malformed jar is not a login
            return None
        got = jar.get(auth.COOKIE)
        return auth.verify(got.value, self.cfg) if got else None

    def _gated(self):
        """Should the words be taken out of what this request is about to get?"""
        return auth.required(self.cfg) and not self._who()

    def _needs_login(self, back):
        """Send a person to the sign-in page, remembering where they were.

        For pages. An API answers 401 instead: a fetch() handed a login page
        with a 200 on it has no way to tell that from its data.
        """
        self.send_response(302)
        self.send_header("Location", "/login?next=" + quote(back, safe="/"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors(self):
        """Answer, in headers, "may that page read this response?".

        Only for the origins allow_origins names. A hosted tracker is a page on
        somebody else's domain making credentialed reads of this server, which
        is exactly the shape of a cross-site request forgery, so the list is
        an allowlist of exact origins and never a wildcard -- and it cannot be
        one anyway: a browser refuses `*` the moment credentials are involved.

        Vary is sent whatever the answer, because the response a permitted
        origin gets and the response anybody else gets are different responses,
        and a cache that has not been told that will hand one to the other.
        """
        self.send_header("Vary", "Origin")
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and origin in (self.cfg.get("allow_origins") or ()):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")

    def _send(self, body, ctype, cache="no-store", code=200, cookie=None):
        """`cache` and `code` default to what every caller before the tracker
        wanted: nothing is stored, and a page that is served at all is a 200.
        The built screen needs both -- its hashed assets are cacheable for a
        year, and its missing-build notice is a real 404 that still has a page
        in it."""
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_media(self, body, ctype):
        """Serve audio, honouring a Range request.

        The scrub bar on the display is why this exists. A media element can
        only seek within a response it is allowed to ask for pieces of: served
        as one plain 200, the clip plays fine but every click on the bar snaps
        it back to the start, and Safari will not report a duration at all.
        One range per request is enough -- browsers never ask for more when
        seeking a single file.
        """
        n = len(body)
        rng = (self.headers.get("Range") or "").strip()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
        if not m or not (m.group(1) or m.group(2)):
            self.send_response(200)
            self._cors()
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(n))
            self.end_headers()
            return self.wfile.write(body)
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else n - 1
        else:                                   # bytes=-500: the last 500 bytes
            start, end = max(0, n - int(m.group(2))), n - 1
        end = min(end, n - 1)
        if start > end:
            self.send_response(416)
            self._cors()
            self.send_header("Content-Range", f"bytes */{n}")
            self.end_headers()
            return
        chunk = body[start:end + 1]
        self.send_response(206)
        self._cors()
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Range", f"bytes {start}-{end}/{n}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def _json(self, payload, code=200, cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _clip_path(self, raw):
        """Resolve a requested clip, or None if it is not one of ours.

        The review page hands back paths this server gave it, but it is still
        a path from the network: without this check, /api/audio?path=/etc/passwd
        would be a file server for the whole disk.
        """
        try:
            p = Path(raw).resolve()
        except (OSError, ValueError):
            return None
        if not p.is_file() or p.suffix.lower() not in AUDIO_TYPES:
            return None
        return p if any(r in p.parents for r in _corpus.roots(self.cfg)) else None

    def _dist_path(self, raw):
        """Resolve a URL path to a file of the built tracker, or None.

        The same rule as _clip_path and for the same reason -- this is a path
        off the network -- but the attack is easier to reach: the tracker's own
        assets are the one route a browser walks unprompted, and
        "/assets/../../.env" is a perfectly legal URL that names this
        repository's credentials. So the test is on the RESOLVED path rather
        than on the text of it. The string may contain whatever it likes,
        percent-encoded dots included, as long as what it finally points at
        lives under web/dist.
        """
        rel = unquote(raw).lstrip("/")
        if not rel:
            return None
        try:
            root = DIST.resolve()
            p = (DIST / rel).resolve()
        except (OSError, ValueError):
            return None
        if root not in p.parents:
            return None
        return p if p.is_file() else None

    def _send_asset(self, p, hashed):
        self._send(p.read_bytes(),
                   STATIC_TYPES.get(p.suffix.lower(), "application/octet-stream"),
                   cache=ASSET_CACHE if hashed else "no-store")

    def _tracker(self):
        """The built call tracker, or the one command that builds it."""
        if not TRACKER.is_file():
            return self._send(NOT_BUILT.encode(), "text/html; charset=utf-8",
                              code=404)
        self._send(TRACKER.read_bytes(), "text/html; charset=utf-8")

    def do_GET(self):
        route = urlparse(self.path)
        if route.path.startswith("/api/current"):
            payload = core.snapshot()
            payload["hold_seconds"] = self.cfg.get("hold_seconds", 600)
            # Every age, offset and countdown on the display is a subtraction
            # against a timestamp this machine produced, so a viewing device
            # whose clock is off by a minute renders all of them wrong by a
            # minute -- and a wall-mounted screen is exactly the device nobody
            # checks the clock on. Sending our own now lets it correct for the
            # skew instead of trusting its own clock.
            payload["now"] = time.time()
            # The words come out here, at the wire, rather than in snapshot():
            # core builds one truth and this decides who is shown which parts
            # of it. Doing it upstream would mean the process holding a
            # transcript in memory also had to know who was asking for it.
            payload["speech"] = True
            if self._gated():
                auth.strip_current(payload)
            self._send(json.dumps(payload).encode(), "application/json")
        elif route.path == "/api/clips":
            # No redaction to do here: the catalogue is hand-typed labels and
            # saved transcripts end to end, so with the words removed there is
            # nothing left of it. It is either yours to read or it is 401.
            if self._gated():
                self._json({"error": "sign in to read transcripts"}, 401)
            else:
                self._json({"clips": _corpus.catalogue(self.cfg),
                            "summary": _corpus.summary(self.cfg)})
        elif route.path == "/api/clip":
            # Live replay for the display: the clip the source just handed us,
            # served from memory. Separate from /api/audio, which reads saved
            # files off disk for the review UI and needs a path guard; an id
            # from the tape can only ever name a clip this process kept.
            got = core.clip(parse_qs(route.query).get("id", [""])[0])
            if not got:
                self._json({"error": "clip is no longer held"}, 404)
            else:
                self._send_media(got[0], got[1])
        elif route.path == "/api/audio":
            raw = parse_qs(route.query).get("path", [""])[0]
            p = self._clip_path(raw)
            if not p:
                self._json({"error": "not a saved clip"}, 404)
            else:
                self._send_media(p.read_bytes(), AUDIO_TYPES[p.suffix.lower()])
        elif route.path == "/api/log":
            payload = core.roster(self.cfg, since=_since(route.query))
            # Stamped again here, at the wire, for the reason spelled out at
            # /api/current: roster() has just walked the incident directory,
            # and the clock a screen corrects its own against should be read as
            # close to the response as it can be.
            payload["now"] = time.time()
            payload["speech"] = True
            if self._gated():
                auth.strip_log(payload)
            self._json(payload)
        elif route.path.rstrip("/") == "/review":
            # The labelling UI is a transcript editor. There is no version of
            # it with the words gone, so it is the one page that is behind the
            # login rather than merely quieter behind it.
            if self._gated():
                self._needs_login("/review")
            else:
                self._send(REVIEW.read_bytes(), "text/html; charset=utf-8")
        elif route.path.rstrip("/") == "/login":
            # Nothing to sign in to when no credentials are configured, and a
            # form that cannot succeed reads as a broken login rather than as
            # an absent one. Send them where they were going.
            if not auth.required(self.cfg):
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(LOGIN.read_bytes(), "text/html; charset=utf-8")
        elif route.path.rstrip("/") == "/logout":
            # A GET, deliberately. Signing out is the one action here a person
            # needs to be able to take from a screen that has no controls on
            # it, by typing a URL; the worst a forged one can do is show
            # somebody a sign-in page.
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", _cookie("", self.cfg, clear=True))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif route.path == "/api/session":
            # What every front end needs to render the state honestly: whether
            # there is a gate, who is through it, and therefore whether the
            # empty transcript it is holding means "locked" or "silence".
            who = self._who()
            self._json({"required": auth.required(self.cfg), "user": who,
                        "speech": bool(who) or not auth.required(self.cfg),
                        # The origins a hosted tracker may be served from, so
                        # the sign-in form can send somebody back to the page
                        # they came from. Published rather than assumed: only
                        # this server knows the list, and the form has to be
                        # able to tell a return address it should honour from
                        # one somebody put in a link.
                        "origins": list(self.cfg.get("allow_origins") or [])})
        elif route.path.rstrip("/") == "/tracker":
            self._tracker()
        elif route.path.startswith("/assets/"):
            # Vite's hashed bundles, at the paths its index.html asks for.
            p = self._dist_path(route.path)
            if not p:
                # Falling through to the catch-all here would answer a missing
                # bundle with display.html, and a browser handed HTML where it
                # asked for javascript reports a syntax error in a file that
                # does not exist.
                self._json({"error": "no such asset"}, 404)
            else:
                self._send_asset(p, hashed=True)
        else:
            # Anything else the tracker's page loads by name -- its favicon,
            # anything dropped in web/public. Unhashed, so no long cache, and
            # "/" is never one of them: the display owns the root and is not
            # something a build gets to shadow.
            p = self._dist_path(route.path) if route.path != "/" else None
            if p:
                self._send_asset(p, hashed=False)
            else:
                # Read from disk every request so you can edit the design and
                # just hit refresh. No restart, no build step.
                self._send(DISPLAY.read_bytes(), "text/html; charset=utf-8")

    def do_OPTIONS(self):
        """The preflight a hosted tracker's browser sends before a real read.

        Answered for everybody and permissive only for the origins _cors lets
        through: a preflight with no Allow-Origin on it is the browser being
        told no, which is the correct answer and not an error to report.
        """
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _login(self, body):
        """Trade a name and a password for a signed cookie.

        The two failures answer differently on purpose. 401 is "those are not
        the credentials", said the same way for a name that does not exist and
        a password that is wrong, because distinguishing them tells somebody
        guessing which half to keep. 429 is "this address has had its ten
        tries", which is not a secret and is the only honest thing to say to
        a person who has genuinely forgotten and is now locked out for five
        minutes.
        """
        who = self.client_address[0]
        if auth.locked_out(who):
            return self._json({"error": "too many attempts; wait a few minutes"},
                              429)
        name = str(body.get("username", "")).strip()
        if not auth.check(name, body.get("password", ""), self.cfg):
            auth.note_failure(who)
            return self._json({"error": "wrong name or password"}, 401)
        auth.clear_failures(who)
        self._json({"ok": True, "user": name},
                   cookie=_cookie(auth.issue(name, self.cfg,
                                             self.cfg.get("session_days")),
                                  self.cfg))

    def do_POST(self):
        route = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad request body"}, 400)
        if route.path == "/api/login":
            if not auth.required(self.cfg):
                return self._json({"error": "no accounts are configured"}, 404)
            return self._login(body)
        if route.path == "/api/logout":
            return self._json({"ok": True},
                              cookie=_cookie("", self.cfg, clear=True))
        # Everything past here reads or writes a transcript: /api/transcribe
        # makes one, /api/label saves what a person typed against one.
        if self._gated():
            return self._json({"error": "sign in to read transcripts"}, 401)
        p = self._clip_path(body.get("path", ""))
        if not p:
            return self._json({"error": "not a saved clip"}, 404)
        if route.path == "/api/label":
            # `note` is why the truth could not be published onto the
            # transmission -- a loose clip, or a grant holding an exchange. Not
            # an error and not a failure to save: the corpus has it either way,
            # and --score reads the corpus. The UI says so quietly.
            _, note = _corpus.save(self.cfg, str(p),
                                   str(body.get("text", "")).strip())
            self._json({"ok": True, "note": note})
        elif route.path == "/api/transcribe":
            self._json({"text": core.transcribe(p, self.cfg)})
        else:
            self._json({"error": "no such endpoint"}, 404)


def _since(query):
    """`?hours=24` as a timestamp, or None for the whole log.

    A duration rather than an instant, because the machine asking is a browser
    and a browser's clock is the one clock on this screen that is allowed to be
    wrong -- the tracker corrects its own display against `now` for exactly that
    reason. "The last day" means the same thing on both machines; "since
    1787885091" means whatever the asking device thinks a day ago was.

    Anything unreadable is no window at all rather than an error. This is a
    narrowing, and the honest failure for a narrowing nobody could parse is to
    send everything: a 400 would take a working tracker off the air over a query
    string, and a silently tiny window would read as a department that stopped
    getting calls.
    """
    raw = parse_qs(query).get("hours", [""])[0]
    try:
        hours = float(raw)
    except ValueError:
        return None
    return time.time() - hours * 3600 if hours > 0 else None


def _lan_host():
    """This machine's address on the local network, for the invitation.

    The UDP socket sends nothing -- connect() on a datagram socket only picks
    the route -- and 8.8.8.8 is a destination, not a server being contacted.
    It is the one reliable way to ask "which of my interfaces would a friend
    on the wifi reach me on", since gethostbyname() answers 127.0.0.1 as often
    as not.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "localhost"
    finally:
        s.close()


def _invite(name, cfg):
    """Make one friend a password and print what to do with it.

    Prints rather than writes. .env is a file a person hand-edits and may have
    a FIREWALL_USERS line in it already; a tool that rewrites it is a tool that
    can lose the rest of it, and the whole line is right here to paste.
    """
    name = name.strip()
    if not name or ":" in name or "," in name:
        print("  a name cannot be empty or contain ':' or ','")
        return 1
    users = dict(auth.accounts(cfg))
    again = name in users
    users[name] = auth.new_password()
    line = "FIREWALL_USERS=" + ",".join(f"{n}:{users[n]}" for n in sorted(users))
    port = cfg["port"]
    print(f"\n  {'new password for' if again else 'invite'} · {name}\n")
    print(f"    {line}\n")
    print("  Put that line in .env, replacing the one there, and restart.")
    print("  Then send them, somewhere private:\n")
    print(f"    where      http://{_lan_host()}:{port}/login")
    print(f"    name       {name}")
    print(f"    password   {users[name]}\n")
    print("  Everyone is signed out when that line changes, including you:")
    print("  the cookies are signed with it. They sign in again once and stay.")
    if len(users) > 1 and not again:
        print(f"  {len(users)} people can read transcripts now.")
    return 0


def _access_line(cfg):
    """One line at startup saying who can read what was said.

    Two, once a hosted tracker is allowed in. Cross-origin access is the one
    setting here that widens who can reach the API without changing anything
    a person on this machine would see, so it says so on every start rather
    than living only in a .env nobody re-reads.
    """
    n = len(auth.accounts(cfg))
    if not n:
        line = ("  access    transcripts are OPEN to anyone who can reach this "
                "server\n            (firewall --invite NAME to change that)")
    else:
        line = (f"  access    transcripts gated · {n} account"
                f"{'' if n == 1 else 's'} · everything else public")
    origins = cfg.get("allow_origins") or []
    if origins:
        line += ("\n  hosted    /api readable by " + ", ".join(origins)
                 + "\n            (needs HTTPS in front of this server; "
                   "the cookie is SameSite=None)")
    return line


def _home_line(cfg):
    """One line naming where FIREWALL_HOME actually landed.

    Printed at startup because a geocode is the one setting here that can be
    confidently wrong: a mistyped street still resolves to somewhere, and a
    silently misplaced home makes every "passes you" claim wrong without ever
    looking broken. Coordinates can be checked against a map in five seconds.
    """
    raw = cfg.get("home")
    if not raw:
        return "  home      not set (scene ETA only; set FIREWALL_HOME in .env)"
    try:
        pt = geo._home_point(cfg)
    except Exception as e:
        return f"  home      {raw!r} -> lookup failed ({type(e).__name__}: {e})"
    if not pt:
        return (f"  home      {raw!r} -> NO MATCH. Check the city: an address in "
                f"the wrong one returns nothing rather than a wrong answer.")
    return (f"  home      {pt[0]:.6f}, {pt[1]:.6f}"
            + (f"  ({raw})" if isinstance(raw, str) else "")
            + f"  siren radius {cfg['siren_metres']}m")


def main():
    ap = argparse.ArgumentParser(prog="firewall", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=list(sources.ALL), default="broadcastify",
                    help="where calls come from (default: broadcastify, which "
                         "is billed per record read)")
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--port", type=int)
    ap.add_argument("--open", action="store_true",
                    help="open the display in your browser on start")
    ap.add_argument("--transcribe", metavar="FILE",
                    help="transcribe one saved clip and print the parse, then exit "
                         "(FIREWALL_WHISPER_MODEL=base.en firewall --transcribe x.mp3 "
                         "to compare models)")
    ap.add_argument("--review", action="store_true",
                    help="serve the labelling UI and nothing else: no source, "
                         "no calls published, opens /review")
    ap.add_argument("--label", metavar="PATH",
                    help="play saved clips and type what was said, building "
                         "the corpus --score measures against")
    ap.add_argument("--score", action="store_true",
                    help="transcribe every labelled clip with the current "
                         "settings and report word error rate")
    ap.add_argument("--incidents", action="store_true",
                    help="list recorded incidents, newest first, and exit")
    ap.add_argument("--replay", metavar="ID", nargs="?", const="latest",
                    help="print one incident as a timeline and exit "
                         "(default: the most recent)")
    ap.add_argument("--play", action="store_true",
                    help="with --replay, play each transmission's audio")
    ap.add_argument("--invite", metavar="NAME",
                    help="make a password for one person and print the "
                         "FIREWALL_USERS line to paste into .env")
    ap.add_argument("--check", action="store_true",
                    help="verify credentials and exit, without starting the server")
    args = ap.parse_args()

    cfg = _config.load(args.config)

    if args.invite:
        return _invite(args.invite, cfg)

    if args.label:
        return _corpus.label(cfg, args.label)

    if args.score:
        return _corpus.score(cfg)

    if args.incidents:
        rows = _incidents.listing(cfg)
        if not rows:
            print(f"  no incidents in {cfg.get('incident_dir')!r} yet")
            return 0
        for iid, opened, dept, kind, addr, n in rows:
            when = time.strftime("%m-%d %H:%M", time.localtime(opened))
            print(f"  {when}  {dept:16} {str(kind):24} {str(addr)[:28]:28} "
                  f"{n:2d} transmissions   {iid}")
        return 0

    if args.replay:
        return _incidents.replay(cfg, args.replay, play=args.play)

    if args.transcribe:
        # Tuning loop for bad transcripts: point FIREWALL_AUDIO_DIR at a folder,
        # let it collect real calls, then replay one here against a different
        # model or vocabulary without waiting for the department to run again.
        t0 = time.time()
        text = core.transcribe(args.transcribe, cfg)
        print(f"  model     {cfg['whisper_model']}  ({time.time() - t0:.1f}s)")
        print(f"  text      {text!r}")
        print(f"  parsed    {_parse.parse(text, cfg)}")
        return 0

    if args.check:
        env = _config.find_file(".env")
        print(f"  .env      {env if env else 'not found'}")
        print(f"  source    {args.source}")
        print(f"  purdue    {'on' if _config.purdue_enabled(cfg) else 'off'} "
              f"(purdue_alerts={cfg.get('purdue_alerts', 'auto')})")
        print(_home_line(cfg))
        print(_access_line(cfg))
        if push.line(cfg):
            print(push.line(cfg))
            # Actually send one. A push is the one setting here that is checked
            # by somebody else's machine -- a wrong token, a project with no
            # store connected, a url with a typo in it -- and every one of those
            # failures otherwise shows up as a hosted tracker that is simply
            # empty, which reads as the radio being quiet.
            try:
                answer = push.push_once(cfg) or {}
                print("            push accepted")
                # The far end reports this inside a success, because the live
                # copy landed and only the archive behind it did not. It is
                # exactly the kind of half-configured deployment this check
                # exists to catch, so it is not left in a JSON body nobody
                # reads.
                if answer.get("archive_error"):
                    print("            HISTORY NOT KEPT: "
                          + str(answer["archive_error"]))
                elif answer.get("archived") is not None:
                    print(f"            {answer['archived']} records archived")
            except Exception as e:
                print(f"            PUSH FAILED: {type(e).__name__}: {e}")
        if args.source != "broadcastify":
            print(f"  {args.source} needs no credentials.")
            return 0
        rc, err = sources.bcfy_check(cfg)
        if err:
            print(f"\n  FAILED: {err}")
        return rc
    port = args.port or cfg["port"]
    _Handler.cfg = cfg
    url = f"http://localhost:{port}/"

    # Two copies of this on one port is a mistake worth naming rather than
    # tracebacking: on Broadcastify the second one polls the same talkgroups
    # and is billed for the same records, so "already in use" here usually
    # means money is being spent twice, not that a port number is wrong.
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except OSError as e:
        if e.errno not in (48, 98):                     # EADDRINUSE, macOS/Linux
            raise
        print(f"\n  port {port} is already in use — firewall is probably "
              f"already running.\n  Open {url} to check, or:\n"
              f"    lsof -nP -iTCP:{port} -sTCP:LISTEN     # what holds it\n"
              f"    firewall --port {port + 1} ...         # run beside it")
        return 1

    # Review mode runs the same server with no source attached. Deliberate: a
    # source would publish calls and open incidents while you are labelling
    # yesterday's while you are labelling them.
    if args.review:
        n = len(_corpus.catalogue(cfg))
        print(f"  review · {url}review · {n} clip(s) in "
              f"{cfg.get('incident_dir')!r} and {cfg.get('audio_dir')!r}")
        if not n:
            print("  no saved audio yet: set FIREWALL_AUDIO_DIR or "
                  "FIREWALL_INCIDENT_DIR and let a source run first")
        threading.Timer(0.6, lambda: webbrowser.open(url + "review")).start()
    else:
        print(_home_line(cfg))
        print(_access_line(cfg))
        if push.line(cfg):
            print(push.line(cfg))
        threading.Thread(target=sources.ALL[args.source], args=(cfg,),
                         daemon=True).start()
        if _config.purdue_enabled(cfg):
            threading.Thread(target=purdue.poll, args=(cfg,), daemon=True).start()
        # Started after the source, so the first snapshot that goes up has had
        # a moment to be about something. Daemon like the rest: a push in
        # flight is never a reason a Ctrl-C has to wait.
        if push.enabled(cfg):
            threading.Thread(target=push.poll, args=(cfg,), daemon=True).start()
        print(f"\n  firewall · source={args.source} · {url}"
              f"\n  review   · {url}review"
              f"\n  tracker  · {url}tracker"
              + (f"\n  sign in  · {url}login" if auth.required(cfg) else "")
              + "\n")
        if args.open:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    raise SystemExit(main())
