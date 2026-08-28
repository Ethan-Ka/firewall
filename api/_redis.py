"""The same Redis the JavaScript functions read, reached from Python.

One file rather than a client library, for the reason api/_store.js gives: the
library would be a dependency, a cold start and a version to keep up with in
exchange for wrapping a POST. This is that POST, plus the pipeline form, and
between them they are every database call the collector makes.

The keys are _store.js's keys and the shapes are _store.js's shapes. That is the
contract this whole rearrangement rests on: the collector writes exactly what
the pusher used to write, so /api/current, /api/log, /api/history and /api/radio
carry on reading it without being told anything changed.
"""
import json
import os
import urllib.error
import urllib.request

# Vercel's own Redis integration sets the first pair; a database created
# directly with Upstash sets the second. Read in the same order _store.js reads
# them, so a deployment cannot have the two halves pointed at two databases.
_URL_ENV = ("KV_REST_API_URL", "UPSTASH_REDIS_REST_URL", "REDIS_REST_URL")
_TOKEN_ENV = ("KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_TOKEN", "REDIS_REST_TOKEN")

TIMEOUT = 15


def _first(names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.rstrip("/")
    return None


def configured():
    """Whether this deployment has a database at all."""
    return bool(_first(_URL_ENV) and _first(_TOKEN_ENV))


def _post(path, body):
    url = _first(_URL_ENV)
    token = _first(_TOKEN_ENV)
    if not (url and token):
        raise RuntimeError("no store is configured for this deployment")
    req = urllib.request.Request(
        url + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        # The body carries the reason -- a wrong token, a database that has been
        # deleted -- and the status alone would send somebody to the wrong
        # problem. Same reasoning as push.py's HTTPError branch.
        try:
            why = e.read().decode()[:200]
        except Exception:
            why = ""
        raise RuntimeError(f"store returned HTTP {e.code} {why}".strip()) from None


def command(*args):
    """One Redis command, as the REST API's own JSON form."""
    body = _post("", [str(a) for a in args])
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(body["error"])
    return (body or {}).get("result")


def pipeline(commands):
    """Several commands, one round trip. Raises on the first that failed."""
    if not commands:
        return []
    rows = _post("/pipeline", [[str(a) for a in c] for c in commands])
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("error"):
            raise RuntimeError(row["error"])
        out.append((row or {}).get("result"))
    return out


def get_json(key, default=None):
    """A key holding JSON, or `default` when it is missing or unreadable.

    Unreadable counts as missing on purpose. The only thing that writes these
    keys is the collector, so a value that will not parse is a half-written
    state from a function that was killed mid-flight -- and the honest response
    to that is to start again from nothing, not to crash on every invocation
    from here to the heat death of the universe.
    """
    raw = command("GET", key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def set_json(key, value, ttl=None):
    """Write a key, optionally with an expiry in seconds."""
    blob = json.dumps(value, separators=(",", ":"), default=str)
    if ttl:
        return command("SET", key, blob, "EX", int(ttl))
    return command("SET", key, blob)
