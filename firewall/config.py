"""Single source of truth for configuration."""
import json, os, re
from pathlib import Path

# Talkgroups on the Tippecanoe County Government P25 system (RadioReference sid 9099).
# All fire/EMS talkgroups on this system are unencrypted.
#
# The default watches West Lafayette only. Each talkgroup is polled as its own
# server-side-filtered `groups` request, so you are billed for records from the
# departments you asked for and nothing else. Adding the county-wide talkgroups
# below is a real cost decision, not a free one: see COUNTY_FIRE.
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

    # --- source: trunk (local SDR) ----------------------------------------
    "trunk_dir": "./trunk-out",

    # --- shared ------------------------------------------------------------
    "talkgroups": WEST_LAFAYETTE_FIRE,
    "poll_seconds": 5,
    "whisper_model": "base.en",     # tiny.en | base.en | small.en
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
    "BCFY_TALKGROUPS": "talkgroups",
    "FIREWALL_POLL_SECONDS": "poll_seconds",
    "FIREWALL_TRUNK_DIR": "trunk_dir",
    "FIREWALL_WHISPER_MODEL": "whisper_model",
    "FIREWALL_USE_LLM_PARSER": "use_llm_parser",
    "FIREWALL_HOLD_SECONDS": "hold_seconds",
    "FIREWALL_PORT": "port",
    "FIREWALL_HOME": "home",
    "FIREWALL_SIREN_METRES": "siren_metres",
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
    "hold_seconds": int,
    "port": int,
    "purdue_poll_seconds": int,
    "use_llm_parser": lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
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
    return cfg
