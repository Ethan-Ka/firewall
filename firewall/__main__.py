"""firewall. One command runs the whole thing.

    firewall                      # mock calls, opens the display
    firewall --source broadcastify
    firewall --source trunk
"""
import argparse, json, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as _config, core, geo, purdue, sources

HERE = Path(__file__).parent
DISPLAY = HERE / "display.html"


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

    def do_GET(self):
        if self.path.startswith("/api/current"):
            payload = core.snapshot()
            payload["hold_seconds"] = self.cfg.get("hold_seconds", 600)
            self._send(json.dumps(payload).encode(), "application/json")
        else:
            # Read from disk every request so you can edit the design and just
            # hit refresh. No restart, no build step.
            self._send(DISPLAY.read_bytes(), "text/html; charset=utf-8")


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
    ap.add_argument("--check", action="store_true",
                    help="verify credentials and exit, without starting the server")
    args = ap.parse_args()

    cfg = _config.load(args.config)

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
    _Handler.cfg = cfg
    print(_home_line(cfg))

    threading.Thread(target=sources.ALL[args.source], args=(cfg,), daemon=True).start()
    if _config.purdue_enabled(cfg):
        threading.Thread(target=purdue.poll, args=(cfg,), daemon=True).start()

    url = f"http://localhost:{port}/"
    print(f"\n  firewall · source={args.source} · {url}\n")
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    raise SystemExit(main())
