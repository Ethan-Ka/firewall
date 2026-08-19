#!/usr/bin/env bash
# Run firewall against a local trunk-recorder output directory.
# No credentials and no metered billing: it just watches FIREWALL_TRUNK_DIR
# for new call .wav files and their .json sidecars.
#
#   ./scripts/run-trunk.sh                     # watch the configured dir
#   ./scripts/run-trunk.sh --open
#   FIREWALL_TRUNK_DIR=/path/to/out ./scripts/run-trunk.sh

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
bootstrap

DIR="$("$PY" -c 'from firewall import config; print(config.load()["trunk_dir"])')"
mkdir -p "$DIR"
say "watching $(cd "$DIR" && pwd)"
[ -z "$(find "$DIR" -name '*.wav' -print -quit 2>/dev/null)" ] &&
  say "note: no .wav files there yet; trunk-recorder must be writing into it"

say "starting trunk source"
run_firewall --source trunk "$@"
