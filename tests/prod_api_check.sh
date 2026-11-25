#!/usr/bin/env bash
# Run ON THE SERVER. Calls lg-api on localhost and prints, per keyword:
#   count, count time, first-page list time (sorted by book_year, as the UI does), md5 of the page ids
# Use it before the switch (baseline) and after (compare). Output is TSV.
#
#   LG_USER=<login> LG_PASS=<password> bash prod_api_check.sh > /tmp/check_<label>.tsv
# (from your workstation: bash ops/api_check.sh, which uploads and runs this file)
#
# Keywords are a mix of rare names and very common words; keep the list stable so runs are comparable.
set -u
BASE=${LG_BASE:-http://localhost:5633/lg-api}
J=$(mktemp); H='X-Requested-With: XMLHttpRequest'
trap 'rm -f "$J"' EXIT
code=$(curl -s -o /dev/null -w '%{http_code}' -c "$J" -H "$H" -H 'Content-Type: application/json' \
  -d "{\"loginId\":\"${LG_USER}\",\"password\":\"${LG_PASS}\"}" "$BASE/auth/login")
[ "$code" = 200 ] || { echo "login failed: $code" >&2; exit 1; }
enc() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$1"; }
printf 'keyword\tfilters\tcount\tcount_s\tlist_s\tlist_ids_md5\n'
while IFS='|' read -r kw filt; do
  [ -z "$kw" ] && continue
  # SKIP='之|舉人' skips keywords (e.g. very common ones that take minutes on the old MySQL path)
  if [ -n "${SKIP:-}" ] && printf '%s' "$kw" | grep -qxE "$SKIP"; then continue; fi
  k=$(enc "$kw"); f=$(enc "$filt")
  t0=$(date +%s.%N)
  c=$(curl -s -m 900 -b "$J" -H "$H" "$BASE/pagetexts/count?keywords=$k&filters=$f&isTurbo=0")
  t1=$(date +%s.%N)
  l=$(curl -s -m 900 -b "$J" -H "$H" "$BASE/pagetexts?keywords=$k&filters=$f&page=1&rows=25&sortField=book_year&sortOrder=asc&isTurbo=0")
  t2=$(date +%s.%N)
  ids=$(printf '%s' "$l" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); d=d.get("data",d) if isinstance(d,dict) else d
    print(",".join(str(x.get("id")) for x in d))
except Exception as e: print("ERR")' | md5sum | cut -c1-8)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$kw" "$filt" "$c" \
    "$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.2f", b-a}')" \
    "$(awk -v a="$t1" -v b="$t2" 'BEGIN{printf "%.2f", b-a}')" "$ids"
done <<'EOF'
李安仁|
高攀龍|
周燦|
鄭成功|
程伯祥|
加○級○封|
史○蒼○以子|
李安仁 高攀龍|
+舉人 +知縣|
海塘|
水利|
舉人|book_name:江南通志;
舉人|
之|
EOF
