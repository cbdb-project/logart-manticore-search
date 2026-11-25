#!/usr/bin/env bash
# Rebuild the Manticore index online (searchd keeps serving the old one until the rotate).
# Run after ANY change to gazetteer.contents_myisam_ft (new books, re-imported pages, ...).
# Takes ~15 min and a full CPU on 2.9M pages; needs free space on / for a second copy of the index.
#   bash ops/reindex.sh
. "$(dirname "$0")/common.sh"
remote 'bash -s' <<'EOF'
set -eu
L=/var/log/manticore/indexer-full.log
if pgrep -x indexer >/dev/null; then echo "indexer already running"; exit 1; fi
df -h / | sed 1d
# detached from this SSH session: a dropped connection must not kill the build
sudo -u manticore setsid nohup sh -c "indexer --all --rotate > $L 2>&1; echo exit=\$? >> $L" >/dev/null 2>&1 </dev/null &
sleep 2; echo "indexer started; log: $L"
EOF
echo "Wait for it with:"
echo "  bash ops/reindex-wait.sh"
