#!/usr/bin/env bash
# Open the labelling UI over whatever audio has already been saved.
#
# Nothing is polled and nothing is published while this runs: it is the same
# server as the display, with no source attached, so you can work through
# yesterday's calls without new ones opening incidents underneath you.
#
#   ./scripts/run-review.sh                # serve /review and open a browser
#   ./scripts/run-review.sh --port 8421
#
# Type what was SAID into each clip. `firewall --score` then measures the
# recogniser against your transcripts, which is the only measurement that
# counts for your radio, your dispatchers and your talkgroups.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
bootstrap

say "serving the review UI (no source attached)"
run_firewall --review "$@"
