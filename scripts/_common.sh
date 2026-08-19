# Shared bootstrap for the run-* scripts. Sourced, not executed.
# Resolves the repo root from this file's location so the scripts work from
# any working directory.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
cd "$ROOT"

say() { printf '\033[36m  %s\033[0m\n' "$*"; }
die() { printf '\033[31m  %s\033[0m\n' "$*" >&2; exit 1; }

bootstrap() {
  if [ ! -x "$PY" ]; then
    say "creating .venv"
    python3 -m venv "$VENV" || die "could not create a venv (need python3)"
  fi
  # "listen" pulls faster-whisper, which both real sources need to transcribe.
  if ! "$PY" -c 'import faster_whisper' 2>/dev/null; then
    say "installing dependencies (first run; downloads faster-whisper)"
    "$VENV/bin/pip" install -q -e "$ROOT[listen]" || die "dependency install failed"
  fi
  [ -f "$ROOT/.env" ] || say "warning: no .env found; copy .env.example to .env"
}

run_firewall() { exec "$VENV/bin/firewall" "$@"; }
