"""Single source of truth for configuration."""
import json, os, re
from pathlib import Path

# Talkgroups on the Tippecanoe County Government P25 system (RadioReference sid 9099).
# All fire/EMS talkgroups on this system are unencrypted.
#
# The default watches Purdue FD and nothing else. Each talkgroup is polled as
# its own server-side-filtered `groups` request and Broadcastify bills per
# record read, so the list is the bill: every department added here is another
# department's whole day of traffic being downloaded and transcribed. Adding one
# is a cost decision, not a preference, which is why the others are kept below
# as data rather than left in the default.
PURDUE_FIRE = {
    2105: "Purdue FD",
}

# West Lafayette city fire. On the same system and the obvious first addition,
# and deliberately not in the default: it roughly doubles the record count.
WEST_LAFAYETTE_FIRE = {
    2021: "West Lafayette FD",
    2105: "Purdue FD",
}

# The rest of the county, kept here so re-adding one is a copy/paste rather than
# a trip back to RadioReference. Lafayette is the other side of the Wabash;
# 1827/1833 are county-wide and carry by far the most traffic.
COUNTY_FIRE = {
    1901: "Lafayette FD",
    1827: "Tippecanoe County Fire",
    1833: "Tippecanoe EMS",
}

DEFAULTS = {
    # --- source: broadcastify ---------------------------------------------
    # Register at bcfy.io/dev/apply; schema at bcfy.io/dev/docs.
    # Auth is a short-lived HS256 JWT, not the raw key: the key is the signing
    # secret, key_id goes in the JWT header (kid) and app_id in the payload
    # (iss). Both are on the developer portal. See bcfy_auth.py.
    "bcfy_api_key": None,
    "bcfy_key_id": None,
    "bcfy_app_id": None,
    # Live Calls needs an authenticated Broadcastify user in the JWT (sub/utk),
    # obtained by POSTing these to /common/v1/auth. Not needed for public
    # playlists. A free account works; premium only affects archive access.
    "bcfy_username": None,
    "bcfy_password": None,
    "bcfy_system_id": None,                      # RadioReference sid
    "bcfy_api_base": "https://api.bcfy.io",      # host only; paths are /{endpoint}/v1
    # How far behind the wall clock the Live Calls cursor is held, in seconds.
    # The API filters on the call START time but cannot publish a call until it
    # has ENDED and cleared a 10-30s ingest, so a cursor parked on the newest
    # timestamp steps over calls that publish late and never asks for them
    # again. 60 = 30s worst-case ingest + the longest transmission seen here
    # (18s) + one round-robin cycle. It is not free: the endpoint has no
    # upper-bound parameter, so the window is re-read every poll. See the cost
    # table in sources.broadcastify(). 0 restores the old lastPos cursor:
    # cheapest, and loses traffic.
    "bcfy_lag_seconds": 10,

    # --- source: trunk (local SDR) ----------------------------------------
    "trunk_dir": "./trunk-out",

    # --- shared ------------------------------------------------------------
    "talkgroups": PURDUE_FIRE,
    "poll_seconds": 5,
    # small.en is the floor that reliably handles 8kHz trunked radio: base.en
    # turns "Purdue" into "Pretty" and "Earhart" into "your heart" often enough
    # to lose the address. It is ~3x slower per clip and still well under the
    # gap between calls on a two-talkgroup watch.
    "whisper_model": "small.en",    # tiny.en | base.en | small.en | medium.en
    # Comma-separated names to bias the decoder toward. Empty uses the
    # West Lafayette / Purdue list in core.SCANNER_VOCAB.
    "whisper_vocab": None,
    # Silero VAD before transcription. Off: it truncates noisy transmissions.
    "whisper_vad": False,
    "whisper_beam": 8,
    # Re-decode, unbiased, when something that reads like a dispatch parsed
    # without an address. Costs a second or two, only on the calls that need it.
    "whisper_retry": True,
    # Directory to archive call audio into, for tuning transcription against
    # calls that came out wrong. Unset means clips are transcribed and dropped.
    "audio_dir": "~/Documents/firewall-data/clips",
    # Where incidents are kept: one directory per call, holding every
    # transmission's audio and an incident.json. This is what --replay reads.
    "incident_dir": "~/Documents/firewall-data/incidents",
    # Hand-typed truth for saved clips: firewall --label, firewall --score.
    "corpus_path": "~/Documents/firewall-data/corpus.jsonl",
    # Quiet time after which an incident is over even if nobody said "code 4".
    "incident_gap_seconds": 900,
    # A second dispatch this soon after the first is the same call being
    # restated, not a new one.
    "use_llm_parser": False,        # needs ANTHROPIC_API_KEY
    "hold_seconds": 600,            # how long a call stays on screen
    "port": 842,

    # --- eta / proximity ---------------------------------------------------
    # Your location, as "lat,lon" or a street address to geocode once. Unset
    # means the display shows the scene ETA only, with no "passes you" line.
    "home": None,
    # How close the run has to come to count as "you would hear it". 600m
    # covers the surrounding streets, not just your own.
    "siren_metres": 600,

    # --- who may read the transcripts --------------------------------------
    # Empty means nothing is gated and there is no login, which is the right
    # default for one screen on one wall on one network. Fill it in and the
    # words come off every page until somebody signs in; everything else --
    # call type, address, units, ETA, the audio itself -- stays public. See
    # auth.py for why that is the line, and `firewall --invite NAME` for how
    # to make an entry.
    #   FIREWALL_USERS=alex:reed-tumbler-42,sam:kiln-oxbow-9
    "users": {},
    # Signing key for the session cookie. Unset derives one from the
    # credentials above, so sessions survive a restart and every session ends
    # the moment you edit the list. See auth._secret.
    "session_secret": None,
    "session_days": 30,

    # --- who may read the API from another origin ---------------------------
    # Exact origins, comma-separated, allowed to make credentialed reads of
    # /api from a page they serve. Empty -- the default -- means nobody, which
    # is right for a screen on the same network as the server: same-origin
    # requests never involve this list.
    #
    # Fill it in when the tracker is hosted somewhere else, naming the hosted
    # origins and nothing more:
    #   FIREWALL_ALLOW_ORIGINS=https://tracker.example.com,https://tracker-git-main-you.vercel.app
    # Every origin here can read everything the browser of anyone signed in to
    # this server can read, so a wildcard is not offered and Vercel's preview
    # deployments each need naming rather than a pattern. Setting this also
    # moves the session cookie to SameSite=None; Secure, which needs HTTPS in
    # front of this server -- see _cookie in __main__.
    "allow_origins": [],

    # --- pushing to a hosted tracker ---------------------------------------
    # The other way to connect a hosted tracker, and the one that needs nothing
    # opened up: this machine posts a snapshot outward every few seconds and
    # the hosted half keeps the last day of it. See push.py and web/api/.
    #
    # Unset means no push, which is the default. Both of the first two are
    # needed: a url with no token is a write the far end will refuse for ever.
    "push_url": None,                # https://your-project.vercel.app/api/push
    "push_token": None,              # the same secret as FIREWALL_PUSH_TOKEN there
    "push_seconds": 10,
    # How much of the log goes up. The far end expires a snapshot after its own
    # RETAIN_HOURS whatever this says, so the two want to agree.
    "push_hours": 24,
    # This machine's public origin, if it has one. Used for two things and
    # needed for neither: it makes the hosted page's play buttons point back at
    # the audio, and it gives "sign in" somewhere to go. Unset -- the normal
    # case for a machine behind a router -- and the hosted page shows the calls
    # with the audio visibly unavailable.
    "public_url": None,
    # Push the words even though FIREWALL_USERS is set. Off, and deliberately
    # awkward to turn on: the gate exists because somebody decided the
    # transcripts are not for everybody, and a public URL is the last place to
    # undo that by accident.
    "push_speech": False,
}


# Environment variable name to config key. See .env.example.
ENV_KEYS = {
    "BCFY_API_KEY": "bcfy_api_key",
    "BCFY_KEY_ID": "bcfy_key_id",
    "BCFY_APP_ID": "bcfy_app_id",
    "BCFY_USERNAME": "bcfy_username",
    "BCFY_PASSWORD": "bcfy_password",
    "BCFY_SYSTEM_ID": "bcfy_system_id",
    "BCFY_API_BASE": "bcfy_api_base",
    "BCFY_LAG_SECONDS": "bcfy_lag_seconds",
    "BCFY_TALKGROUPS": "talkgroups",
    "FIREWALL_POLL_SECONDS": "poll_seconds",
    "FIREWALL_TRUNK_DIR": "trunk_dir",
    "FIREWALL_WHISPER_MODEL": "whisper_model",
    "FIREWALL_WHISPER_VOCAB": "whisper_vocab",
    "FIREWALL_WHISPER_VAD": "whisper_vad",
    "FIREWALL_WHISPER_BEAM": "whisper_beam",
    "FIREWALL_WHISPER_RETRY": "whisper_retry",
    "FIREWALL_AUDIO_DIR": "audio_dir",
    "FIREWALL_INCIDENT_DIR": "incident_dir",
    "FIREWALL_CORPUS": "corpus_path",
    "FIREWALL_INCIDENT_GAP": "incident_gap_seconds",
    "FIREWALL_USE_LLM_PARSER": "use_llm_parser",
    "FIREWALL_HOLD_SECONDS": "hold_seconds",
    "FIREWALL_PORT": "port",
    "FIREWALL_HOME": "home",
    "FIREWALL_SIREN_METRES": "siren_metres",
    "FIREWALL_USERS": "users",
    "FIREWALL_SECRET": "session_secret",
    "FIREWALL_SESSION_DAYS": "session_days",
    "FIREWALL_ALLOW_ORIGINS": "allow_origins",
    "FIREWALL_PUSH_URL": "push_url",
    "FIREWALL_PUSH_TOKEN": "push_token",
    "FIREWALL_PUSH_SECONDS": "push_seconds",
    "FIREWALL_PUSH_HOURS": "push_hours",
    "FIREWALL_PUBLIC_URL": "public_url",
    "FIREWALL_PUSH_SPEECH": "push_speech",
}

# Read from .env into os.environ by load_dotenv() but intentionally not mapped
# into cfg: the anthropic SDK picks it up from the environment itself.
PASSTHROUGH_ENV = ("ANTHROPIC_API_KEY",)

def _home(v):
    """"lat,lon" -> (float, float); anything else is passed through as an
    address for geo.geocode() to resolve later. Kept lazy so config.load()
    never has to touch the network."""
    parts = v.split(",")
    if len(parts) == 2:
        try:
            return (float(parts[0]), float(parts[1]))
        except ValueError:
            pass
    return v.strip()


_CASTS = {
    "home": _home,
    "siren_metres": int,
    "poll_seconds": int,
    "bcfy_lag_seconds": int,
    "incident_gap_seconds": int,
    "hold_seconds": int,
    "port": int,
    "purdue_poll_seconds": int,
    "use_llm_parser": lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
    "whisper_vad": lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
    "whisper_beam": int,
    "audio_dir": lambda v: str(Path(v).expanduser()),
    "incident_dir": lambda v: str(Path(v).expanduser()),
    "corpus_path": lambda v: str(Path(v).expanduser()),
    "trunk_dir": lambda v: str(Path(v).expanduser()),
    "whisper_retry": lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
    "session_days": int,
    # Origins compared as text against a browser's Origin header, so the
    # trailing slash a person naturally pastes off the address bar is taken
    # off here rather than silently failing to match for ever.
    "allow_origins": lambda v: [o.strip().rstrip("/") for o in v.split(",")
                                if o.strip()],
    "push_seconds": int,
    "push_hours": float,
    "push_speech": lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
    # name:password pairs. Split on the FIRST colon only, so a password may
    # contain one; a comma may not, and the generator never makes one.
    "users": lambda v: {
        pair.split(":", 1)[0].strip(): pair.split(":", 1)[1].strip()
        for pair in v.split(",") if ":" in pair
    },
    "talkgroups": lambda v: {
        int(pair.split(":", 1)[0].strip()): pair.split(":", 1)[1].strip()
        for pair in v.split(",") if ":" in pair
    },
}


def purdue_enabled(cfg):
    """Is the Purdue campus-status feed on for this run?

    "auto" resolves against the watched talkgroups rather than a GPS fix: if
    any West Lafayette or Purdue FD talkgroup is in the list, the screen is in
    West Lafayette and campus status is local news. Anywhere else it is not,
    and the bubble never appears.
    """
    mode = str(cfg.get("purdue_alerts", "auto")).strip().lower()
    if mode in ("on", "true", "1", "yes", "always"):
        return True
    if mode in ("off", "false", "0", "no", "never"):
        return False
    return any(int(tg) in WEST_LAFAYETTE_FIRE for tg in (cfg.get("talkgroups") or {}))


def find_file(name):
    """Locate a config file: walk up from cwd, then fall back to the repo root.

    Without this, running `firewall` from anywhere but the repo root silently
    drops .env, and every credential in it reads back as None.
    """
    for d in (Path.cwd(), *Path.cwd().parents):
        if (d / name).exists():
            return d / name
    fallback = Path(__file__).resolve().parent.parent / name
    return fallback if fallback.exists() else None


def load_dotenv(path=None):
    """Read a .env file into os.environ. Real environment variables win.

    Deliberately hand-rolled: mock mode has no dependencies, and that is worth
    more than the handful of edge cases python-dotenv handles better.
    """
    p = Path(path) if path else find_file(".env")
    if p is None or not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val[:1] == val[-1:] and val[:1] in ("'", '"'):
            val = val[1:-1]                     # quoted: take it literally
        else:
            # Unquoted: a whitespace-preceded '#' starts a comment. Without
            # this, "KEY=   # note" silently becomes the string "# note".
            val = re.split(r"\s#", val, 1)[0].strip()
        if key and val and key not in os.environ:
            os.environ[key] = val


def load(path=None):
    cfg = dict(DEFAULTS)
    # Paths written with ~ have to work whether they came from DEFAULTS, a
    # config.json or the environment, so expand after everything is merged.
    p = Path(path) if path else find_file("config.json")
    if p is not None and p.exists():
        user = json.loads(p.read_text())
        if "talkgroups" in user:
            user["talkgroups"] = {int(k): v for k, v in user["talkgroups"].items()}
        cfg.update(user)

    load_dotenv()
    for env_name, key in ENV_KEYS.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        cast = _CASTS.get(key)
        try:
            cfg[key] = cast(raw) if cast else raw
        except (ValueError, IndexError):
            raise SystemExit(f"{env_name} is malformed: {raw!r}")
    for key in ("audio_dir", "incident_dir", "corpus_path", "trunk_dir"):
        if isinstance(cfg.get(key), str):
            cfg[key] = str(Path(cfg[key]).expanduser())
    return cfg
