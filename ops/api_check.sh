#!/usr/bin/env bash
# Run tests/prod_api_check.sh ON the server (talks to lg-api on localhost) and print its TSV.
#   bash ops/api_check.sh > check_after.tsv          # LG_USER / LG_PASS from .env
#   SKIP='之' bash ops/api_check.sh                   # skip very common words (slow on the MySQL path)
. "$(dirname "$0")/common.sh"
: "${LG_USER:?set LG_USER in .env}" "${LG_PASS:?set LG_PASS in .env}"
upload "$ROOT/tests/prod_api_check.sh" /tmp/prod_api_check.sh
# credentials go through stdin, not the command line
printf 'LG_USER=%q LG_PASS=%q LG_BASE=%q SKIP=%q bash /tmp/prod_api_check.sh; rm -f /tmp/prod_api_check.sh\n' \
  "$LG_USER" "$LG_PASS" "${LG_BASE:-http://localhost:5633/lg-api}" "${SKIP:-}" | remote 'bash -s'
