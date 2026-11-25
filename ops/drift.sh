#!/usr/bin/env bash
# Compare the files this repo manages with what is installed on the server (read-only).
#   bash ops/drift.sh          # summary
#   bash ops/drift.sh -v       # also print diffs
. "$(dirname "$0")/common.sh"
VERBOSE=${1:-}
rc=0
for pair in "${MANAGED_FILES[@]}"; do
  local_path=${pair%%|*}; server_path=${pair##*|}
  remote_txt=$(remote "sudo cat '$server_path' 2>/dev/null" || true)
  if [ -z "$remote_txt" ]; then
    echo "MISSING  $server_path"; rc=1; continue
  fi
  if diff -q <(printf '%s\n' "$remote_txt") "$ROOT/$local_path" >/dev/null; then
    echo "same     $server_path"
  else
    echo "DIFFERS  $server_path   (repo: $local_path)"; rc=1
    [ "$VERBOSE" = "-v" ] && diff -u --label "server:$server_path" --label "repo:$local_path" \
      <(printf '%s\n' "$remote_txt") "$ROOT/$local_path" || true
  fi
done
exit $rc
