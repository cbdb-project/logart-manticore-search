#!/usr/bin/env bash
# Wait for a running reindex (ops/reindex.sh) and print the result. Safe to re-run / interrupt.
. "$(dirname "$0")/common.sh"
remote 'bash -s' <<'EOF'
L=/var/log/manticore/indexer-full.log
while pgrep -x indexer >/dev/null; do sleep 15; done
sudo grep -v 'Broken pipe' $L | tail -n 8
# expected: "total <N> docs" (N = rows of contents_myisam_ft), "successfully sent SIGHUP", "exit=0"
EOF
