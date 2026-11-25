#!/usr/bin/env bash
# Read-only health check of the Manticore search path on the LoGaRT host.
#   bash ops/healthcheck.sh
. "$(dirname "$0")/common.sh"
remote 'bash -s' <<'EOF'
set -u
CNF=/etc/manticoresearch/logart-mysql.cnf
Q="SELECT id, book_id, page_no FROM pages WHERE MATCH(''\"舉人\"'') LIMIT 0,5000000 OPTION max_matches=5000000"
echo "== services";   systemctl is-active manticore mysql lg-api | paste -sd' '
echo "== manticore pages (should equal contents_myisam_ft rows)"
mysql -h127.0.0.1 -P9306 -N -e "select count(*) from pages"
echo "== contents_myisam_ft rows (MyISAM, instant)"
sudo mysql --defaults-extra-file=$CNF -N -e "select count(*) from gazetteer.contents_myisam_ft"
echo "== through FEDERATED (舉人, must equal direct Manticore count)"
mysql -h127.0.0.1 -P9306 -N -e "select count(*) from pages where match('\"舉人\"')" | sed 's/^/direct     /'
sudo mysql --defaults-extra-file=$CNF -N -e "select count(*) from gazetteer.mt_pages where query='$Q'" 2>&1 | sed 's/^/federated  /'
echo "== stale-connection errors in current query.log (should be 0)"
sudo grep -c 'unhandled statement' /var/log/manticore/query.log || true
echo "== keep-alive pieces installed"
ls -l /etc/cron.d/logart-mt-flush /etc/systemd/system/manticore.service.d/flush-federated.conf 2>&1
echo "== lg-api jar"
sudo md5sum /opt/lg-api/lg-api.jar
sudo unzip -p /opt/lg-api/lg-api.jar BOOT-INF/classes/de/mpiwg/lgapi/mapper/gazetteer/PageTextMapper.xml 2>/dev/null \
  | grep -q mt_pages && echo "PageTextMapper.xml: Manticore patch PRESENT" || echo "PageTextMapper.xml: patch ABSENT (original MySQL path)"
echo "== disk";  df -h / /logart_data 2>/dev/null | sed 1d
echo "== memory (searchd RSS kB)"; ps -o rss= -C searchd
EOF
