#!/usr/bin/env bash
# Run firewall against the Broadcastify Calls API.
# Needs BCFY_API_KEY (and BCFY_SYSTEM_ID) in .env. Costs money per record read.
#
#   ./scripts/run-broadcastify.sh              # verify, then run
#   ./scripts/run-broadcastify.sh --open       # ...and open the display
#   ./scripts/run-broadcastify.sh --port 8421

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
bootstrap

# Verify the credential before entering the poll loop. Without this the loop
# swallows a bad key or a wrong endpoint and retries silently, on the clock.
say "checking credentials"
if ! "$VENV/bin/firewall" --check --source broadcastify; then
  die "credential check failed (see above); not starting the poll loop"
fi

say "starting broadcastify source"
run_firewall --source broadcastify "$@"
