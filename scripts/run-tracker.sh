#!/usr/bin/env bash
# Serve the tracker screen: the call-type chart, the call tracker and the
# transcript, at /tracker.
#
#   ./scripts/run-tracker.sh                      # broadcastify, opens /tracker
#   ./scripts/run-tracker.sh --source trunk       # a local recording dir instead
#   ./scripts/run-tracker.sh --source broadcastify
#   ./scripts/run-tracker.sh --rebuild            # force the front end to rebuild
#
# Unlike the other run-* scripts this one has a second toolchain behind it. The
# tracker is a React app in web/ that compiles to web/dist, and the build is not
# committed -- the hosted copy is built by Vercel and this one is built here, so
# Node is needed the first time. After that the staleness check below is what
# notices an edit for you: editing a component and then wondering why the screen
# has not changed is the one mistake a build step invites.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
bootstrap

BUILT="$ROOT/web/dist/index.html"
REBUILD=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--rebuild" ]; then REBUILD=1; else ARGS+=("$a"); fi
done

# Newer source than build, or no build at all. `find -newer` rather than a
# timestamp file: the sources are what a person edits, and comparing against
# them directly cannot go stale in a way a marker file can.
if [ ! -f "$BUILT" ]; then
  REBUILD=1
  say "no built tracker in web/dist"
elif [ -n "$(find "$ROOT/web/src" "$ROOT/web/index.html" "$ROOT/web/package.json" \
             -newer "$BUILT" -print -quit 2>/dev/null)" ]; then
  REBUILD=1
  say "web/ has changed since the last build"
fi

if [ "$REBUILD" = 1 ]; then
  command -v npm >/dev/null 2>&1 ||
    die "the tracker needs building but npm is not installed. Install Node,
  or open the hosted tracker instead -- it talks to this server over the
  network once FIREWALL_ALLOW_ORIGINS names it. See the README."
  [ -d "$ROOT/web/node_modules" ] && say "building the tracker" ||
    { say "installing front-end dependencies (first run)"
      (cd "$ROOT/web" && npm install) || die "npm install failed"; }
  (cd "$ROOT/web" && npm run build) || die "the tracker build failed (see above)"
fi

# The port the browser is about to be sent to has to be the port the server
# actually binds, so a --port on the command line wins over the configured one
# exactly as it does inside firewall itself. Getting this wrong opens a tab on
# whatever else happens to be listening, which looks like the tracker failing.
PORT="$("$PY" -c 'from firewall import config; print(config.load()["port"])')"
take_next=0
for a in "${ARGS[@]+"${ARGS[@]}"}"; do
  if [ "$take_next" = 1 ]; then PORT="$a"; take_next=0
  elif [ "$a" = "--port" ]; then take_next=1
  else case "$a" in --port=*) PORT="${a#--port=}";; esac
  fi
done
URL="http://localhost:$PORT/tracker"

# Opened from here rather than with `firewall --open`, which opens the display
# at / -- this script exists to put you on the tracker. The delay is the same
# one __main__ uses: the server has to be listening before the browser asks.
( sleep 1.2
  command -v open >/dev/null 2>&1 && open "$URL" ||
  command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" ) >/dev/null 2>&1 &

say "tracker · $URL"
run_firewall "${ARGS[@]+"${ARGS[@]}"}"
