"""Single source of truth for configuration."""
import json, os
from pathlib import Path

# Talkgroups on the Tippecanoe County Government P25 system (RadioReference sid 9099).
# All fire/EMS talkgroups on this system are unencrypted.
TIPPECANOE_FIRE = {
    2105: "Purdue FD",
    2021: "West Lafayette FD",
    1901: "Lafayette FD",
    1827: "Tippecanoe County Fire",
    1833: "Tippecanoe EMS",
}

DEFAULTS = {
    # --- source: broadcastify ---------------------------------------------
    # Register at bcfy.io/dev/apply; schema at bcfy.io/dev/docs.
    "bcfy_api_key": None,
    "bcfy_system_id": None,
    "bcfy_api_base": "https://api.bcfy.io/v1",   # VERIFY against live docs

    # --- source: trunk (local SDR) ----------------------------------------
    "trunk_dir": "./trunk-out",

    # --- shared ------------------------------------------------------------
    "talkgroups": TIPPECANOE_FIRE,
    "poll_seconds": 5,
    "whisper_model": "base.en",     # tiny.en | base.en | small.en
    "use_llm_parser": False,        # needs ANTHROPIC_API_KEY
    "hold_seconds": 600,            # how long a call stays on screen
    "port": 842,
}


def load(path=None):
    cfg = dict(DEFAULTS)
    p = Path(path) if path else Path.cwd() / "config.json"
    if p.exists():
        user = json.loads(p.read_text())
        if "talkgroups" in user:
            user["talkgroups"] = {int(k): v for k, v in user["talkgroups"].items()}
        cfg.update(user)
    cfg["bcfy_api_key"] = cfg.get("bcfy_api_key") or os.environ.get("BCFY_API_KEY")
    return cfg
