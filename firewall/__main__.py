"""firewall. One command runs the whole thing.

    firewall                      # mock calls, opens the display
    firewall --source broadcastify
    firewall --source trunk
"""
import argparse, json, re, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from . import config as _config, core, corpus as _corpus, geo, incidents as _incidents, parse as _parse, purdue, sources

HERE = Path(__file__).parent
DISPLAY = HERE / "display.html"
REVIEW = HERE / "review.html"

AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav",
               ".m4a": "audio/mp4", ".ogg": "audio/ogg"}


class _Handler(BaseHTTPRequestHandler):
    cfg = {}

    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
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
            self.send_header("Content-Range", f"bytes */{n}")
            self.end_headers()
            return
        chunk = body[start:end + 1]
        self.send_response(206)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Range", f"bytes {start}-{end}/{n}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
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
            self._send(json.dumps(payload).encode(), "application/json")
        elif route.path == "/api/clips":
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
        elif route.path.rstrip("/") == "/review":
            self._send(REVIEW.read_bytes(), "text/html; charset=utf-8")
        else:
            # Read from disk every request so you can edit the design and just
            # hit refresh. No restart, no build step.
            self._send(DISPLAY.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        route = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad request body"}, 400)
        p = self._clip_path(body.get("path", ""))
        if not p:
            return self._json({"error": "not a saved clip"}, 404)
        if route.path == "/api/label":
            _corpus.save(self.cfg, str(p), str(body.get("text", "")).strip())
            self._json({"ok": True})
        elif route.path == "/api/transcribe":
            self._json({"text": core.transcribe(p, self.cfg)})
        else:
            self._json({"error": "no such endpoint"}, 404)


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
    ap.add_argument("--source", choices=list(sources.ALL), default="mock",
                    help="where calls come from (default: mock)")
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
    ap.add_argument("--check", action="store_true",
                    help="verify credentials and exit, without starting the server")
    args = ap.parse_args()

    cfg = _config.load(args.config)

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
        if args.source != "broadcastify":
            print(f"  {args.source} needs no credentials.")
            return 0
        rc, err = sources.bcfy_check(cfg)
        if err:
            print(f"\n  FAILED: {err}")
        return rc
    port = args.port or cfg["port"]
    # Mock dispatches are fiction, and fiction does not belong in the incident
    # log next to real calls -- particularly once you are labelling clips from
    # it and scoring the recogniser against them.
    if args.source == "mock":
        cfg["incident_dir"] = None
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
    # yesterday's, and mock would write fictional ones into the incident log.
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
        threading.Thread(target=sources.ALL[args.source], args=(cfg,),
                         daemon=True).start()
        if _config.purdue_enabled(cfg):
            threading.Thread(target=purdue.poll, args=(cfg,), daemon=True).start()
        print(f"\n  firewall · source={args.source} · {url}"
              f"\n  review   · {url}review\n")
        if args.open:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    raise SystemExit(main())
