"""firewall — one command runs the whole thing.

    firewall                      # mock calls, opens the display
    firewall --source broadcastify
    firewall --source trunk
"""
import argparse, json, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as _config, core, sources

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
            # hit refresh — no restart, no build step.
            self._send(DISPLAY.read_bytes(), "text/html; charset=utf-8")


def main():
    ap = argparse.ArgumentParser(prog="firewall", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=list(sources.ALL), default="mock",
                    help="where calls come from (default: mock)")
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--port", type=int)
    ap.add_argument("--open", action="store_true",
                    help="open the display in your browser on start")
    args = ap.parse_args()

    cfg = _config.load(args.config)
    port = args.port or cfg["port"]
    _Handler.cfg = cfg

    threading.Thread(target=sources.ALL[args.source], args=(cfg,), daemon=True).start()

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
    main()
