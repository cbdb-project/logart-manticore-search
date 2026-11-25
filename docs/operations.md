# Operations guide

**English** · [繁體中文](#運維手冊)

Paths assume a Debian/Ubuntu host with:

- `lg-api` installed as `/opt/lg-api/lg-api.jar`, run as an init.d/systemd service `lg-api`;
- MySQL 8.0 with database `gazetteer`;
- Manticore from its official APT repository.

Adjust the paths to your host. Run each step only after the previous checkpoint looks right.

## 0. Baseline

```bash
# from your workstation (.env filled in); SKIP very common words, they take minutes on MySQL
SKIP='之' bash ops/api_check.sh > check_before.tsv
# on the server
df -h / ; free -h
sudo md5sum /opt/lg-api/lg-api.jar          # record it: this is your ORIGINAL jar
```

## 1. Install Manticore and build the index

The website is not affected while you do this.

```bash
cd /tmp && wget https://repo.manticoresearch.com/manticore-repo.noarch.deb
sudo dpkg -i manticore-repo.noarch.deb && sudo apt-get update
sudo apt-get install -y --no-install-recommends manticore-server manticore-tools   # not the meta package (Buddy etc.)
sudo systemctl stop manticore
sudo cp /etc/manticoresearch/manticore.conf /etc/manticoresearch/manticore.conf.dist
sudo install -m 644 manticore/manticore.conf manticore/logart_tsv.py /etc/manticoresearch/

# read-only MySQL credentials for the indexer, the drop-in and the cron job
#   [client]
#   user=<a MySQL user with SELECT on gazetteer.* and RELOAD (for FLUSH TABLES)>
#   password=<...>
sudo install -o manticore -g manticore -m 600 /dev/null /etc/manticoresearch/logart-mysql.cnf
sudo tee /etc/manticoresearch/logart-mysql.cnf >/dev/null <<'EOF'
[client]
user=...
password=...
EOF

# full build: 2.9 M pages ≈ 14 min, one CPU busy, lots of sequential reads → do it off-peak.
# Detach from the SSH session; log into a directory the manticore user can write.
L=/var/log/manticore/indexer-full.log
sudo -u manticore setsid nohup sh -c "indexer --all > $L 2>&1; echo exit=\$? >> $L" >/dev/null 2>&1 </dev/null &
while pgrep -x indexer >/dev/null; do sleep 15; done; sudo grep -v 'Broken pipe' $L | tail
# expect "total <rows of contents_myisam_ft> docs" and exit=0
sudo systemctl enable --now manticore
mysql -h127.0.0.1 -P9306 -e "select count(*) from pages where match('\"舉人\"'); show meta"
```

**Checkpoint:** run a few direct Manticore queries and check their speed (a rare name, 舉人, 之). Also
check `ps -o rss -C searchd`.

If the build fails with `not all columns found`, a page contains a raw control character.
`logart_tsv.py` already maps 0x00–0x1F, 0x7F, U+0085, U+2028 and U+2029 to spaces; extend its `TR`
table if your data has others.

## 2. Enable FEDERATED and create the bridge table

This restarts MySQL once, which takes seconds.

```bash
sudo install -m 644 mysql/zz-federated.cnf /etc/mysql/mysql.conf.d/zz-federated.cnf
sudo systemctl restart mysql
mysql -e "select engine,support from information_schema.engines where engine='FEDERATED'"
mysql < mysql/federated.sql            # smoke-test count must equal the direct Manticore count
```

The account `lg-api` connects with needs `SELECT` on `gazetteer.mt_pages`. A grant on `gazetteer.*`
covers it. FEDERATED itself logs in to Manticore as the literal user `FEDERATED`, which needs no
MySQL privileges.

**Checkpoint:** time the fast-path SQL inside MySQL. For reference, on production 水利 took 0.4 s,
舉人 1.2 s and 之 9 s.

```sql
SELECT count(*) FROM mt_pages cs INNER JOIN books bs ON bs.id = cs.book_id
 WHERE cs.query = 'SELECT id, book_id, page_no FROM pages WHERE MATCH(''"舉人"'') LIMIT 0,5000000 OPTION max_matches=5000000';
```

Do **not** count with `contents_myisam_ft cs … WHERE cs.id IN (SELECT id FROM mt_pages …)`. To get
`cs.book_id`, MySQL would then read the MyISAM text row by row; 舉人 takes 50 s that way.

## 3. Keep-alive pieces (required)

```bash
sudo install -D -m 644 systemd/flush-federated.conf /etc/systemd/system/manticore.service.d/flush-federated.conf
sudo systemctl daemon-reload
sudo install -m 644 cron/logart-mt-flush /etc/cron.d/logart-mt-flush
```

[Troubleshooting](#troubleshooting) explains why. Without these pieces, search fails after any night
with no searches.

## 4. Build and install the patched jar

On any machine with Python 3:

```bash
python lgapi/build_patched_jar.py lg-api.jar lg-api.jar.patched --keep-xml work/
# self-check line must read: content differs: [...PageTextMapper.xml] | compress_type differs: [] | prefix ... kept: True
# review work/PageTextMapper.orig.xml vs work/PageTextMapper.patched.xml if you like (work/ is git-ignored)
```

On the server:

```bash
sudo cp -p /opt/lg-api/lg-api.jar /opt/lg-api/lg-api.jar.pre-manticore
# Spring Boot recommends marking an init.d service jar immutable; if lsattr shows 'i', lift it first
sudo chattr -i /opt/lg-api/lg-api.jar
sudo install -o <service user> -g <service group> -m 500 /tmp/lg-api.jar.patched /opt/lg-api/lg-api.jar
sudo chattr +i /opt/lg-api/lg-api.jar
sudo systemctl restart lg-api
```

Then, from your workstation:

```bash
bash ops/api_check.sh > check_after.tsv
paste check_before.tsv check_after.tsv | column -t -s $'\t'
```

Expected result:

- Rare words give the same count, or slightly more (U+3000 and `∈` hits).
- The md5 of the first result page is the same.
- Times drop by one to two orders of magnitude.

Then try search, the statistics charts and the CSV export in the browser.

## Rollback

```bash
sudo chattr -i /opt/lg-api/lg-api.jar
sudo install -o <service user> -g <service group> -m 500 /opt/lg-api/lg-api.jar.pre-manticore /opt/lg-api/lg-api.jar
sudo chattr +i /opt/lg-api/lg-api.jar
sudo systemctl restart lg-api
```

An idle Manticore and `mt_pages` affect nothing. To remove them completely:

1. `DROP TABLE gazetteer.mt_pages`
2. Delete `zz-federated.cnf`, the drop-in and the cron file.
3. Restart MySQL.
4. `apt remove manticore-server manticore-tools`

## Re-indexing after text changes

Re-index after **any** change to `contents_myisam_ft`, such as new books or re-imported pages:

```bash
bash ops/reindex.sh && bash ops/reindex-wait.sh
# expect: total <rows> docs, "rotating tables: successfully sent SIGHUP", exit=0
```

The rebuild is online: searches keep using the old index until the rotate. It needs free space on the
index volume for a second copy. Changes to books or tables of contents do **not** need a re-index;
those still come from MySQL.

**Symptoms of a stale index:**

- New pages cannot be found.
- Deleted or re-imported pages still count in `/count` and the statistics, but are missing from the
  list. The list then shows fewer rows than the count.

## Upgrading lg-api

A new `lg-api` jar replaces the patch. Re-run `lgapi/build_patched_jar.py` on the **new original** jar:

- If upstream changed one of the patched fragments, the script stops with
  `expected exactly one occurrence` instead of producing a wrong patch.
- If the upstream change is real, adapt `patch_mapper.py` and validate it in the local test
  environment before deploying.

## Health check

```bash
bash ops/healthcheck.sh     # services, index rows vs. table rows, FEDERATED vs. direct count,
                            # stale-connection errors, cron/drop-in present, jar patched?, disk, memory
bash ops/drift.sh -v        # what differs between this repo and the server
```

Logs:

- Manticore: `/var/log/manticore/query.log` (every query) and `searchd.log`.
- lg-api: wherever your service writes it.
- `searchd.log` warns `secondary library not loaded`. This is harmless; `manticore-columnar-lib` is not needed.

## Troubleshooting

### Every keyword search returns HTTP 500 "Lost connection to MySQL server during query"

**Symptoms:**

- lg-api returns 500 with `ERROR 1430 … : 2013 : Lost connection…` or `ERROR 1430 … : 0 :`.
- Direct queries to Manticore (`mysql -h127.0.0.1 -P9306`) work fine.
- `query.log` shows `SET NAMES utf8mb4 # error=unhandled statement type`.

A healthy FEDERATED connection starts with a `SELECT … WHERE 1=0` probe instead.

**Cause:**

1. MySQL FEDERATED caches one connection per open table handler and reuses it across sessions.
2. Manticore closes SQL connections that have been idle longer than its limits:
   - `client_timeout` (default 5 min; `manticore.conf` sets it to 365 d);
   - the session `wait_timeout` (28800 s = **8 h**). This one can only be changed from a VIP
     listener and resets on restart.
3. On the next use of a closed connection, libmysqlclient reconnects automatically and first sends
   `SET NAMES`. Manticore rejects any non-SELECT statement from the `FEDERATED` user.
4. As a result, a night without searches is enough to break search the next morning.

**Fix:**

- Immediately: run `FLUSH TABLES gazetteer.mt_pages;` in MySQL. It takes seconds, has no side effects
  and changes no data.
- Permanently:
  - install `cron/logart-mt-flush`, which flushes every 30 minutes;
  - install the systemd drop-in, which flushes after every Manticore restart.

*Untested alternative:* add a `listen = 127.0.0.1:9312:mysql_vip` listener and run
`SET GLOBAL wait_timeout=…` through it after each start. A normal connection is refused with
`Only VIP connections can change global wait_timeout value`.

### Manticore is down

Every search with keywords fails, as do statistics and export. Browsing, tables of contents, books and
accounts keep working.

1. Run `sudo systemctl restart manticore`. The drop-in flushes `mt_pages` automatically.
2. If Manticore will not start, [roll back](#rollback) the jar. Search then runs on MySQL again.

### Counts are capped

`MAX_MATCHES` in `patch_mapper.py` (5,000,000) must exceed the total number of pages. Raise it,
rebuild the jar and redeploy.

### Known design risk

The fast-path list relies on MySQL keeping the `ORDER BY` of a derived table when it is joined to the
outer query. MySQL 8.0 behaves this way, and every test agrees, but the SQL standard does not
guarantee it. Re-test after a MySQL major upgrade.

## Local test environment

Use this to validate a new patch or a new `lg-api` version without touching production. It needs
Docker; limit the containers to 2 CPU / 4 GB to mimic a small cloud instance.

1. **Sample data.**
   - Export about 100 k pages, chosen book by book (`id, book_id, page_no, content` from
     `contents_myisam_ft`), plus their `books` rows.
   - Export the schema with `mysqldump --no-data gazetteer`.
   - Export the small configuration tables: `application_functions`, `permissions`, `sorted_char`,
     `tag_list`, `coordinates_books`.
   - Export the sample books' `sections_index`.
   - Export a single test user.
   - Keep all of it outside this repository.
2. **Containers** (one Docker network):
   - `mysql:8.0`:
     - start it with `--ngram_token_size=1 --ft_min_word_len=1 --secure-file-priv=`;
     - put `zz-federated.cnf` in `/etc/mysql/conf.d/`;
     - create the MyISAM + FULLTEXT table yourself, then `LOAD DATA`.
   - `manticoresearch/manticore:29.9.0`, using this `manticore.conf` with these changes:
     - `tsvpipe_command` becomes `cat <sample.tsv>`, where the TSV was produced with `logart_tsv.py`'s
       `unescape` + `TR`;
     - `listen` becomes `9306:mysql`;
     - in `federated.sql`, replace `127.0.0.1` with the container name.
   - Two `eclipse-temurin:21-jre` containers running the original and the patched jar, on ports 15633
     and 15634. Configure them with a copy of `application.properties` that points at the MySQL
     container, and start them with `--spring.config.additional-location=file:<props>`.
     Do not use `--spring.config.location`: it replaces the jar's built-in config, including the
     context path, and every request then returns `401 JWT Token expired`.
3. **Compare:** `LG_USER=… LG_PASS=… python tests/api_compare.py`. The only expected `DIFF` lines are
   the [behaviour differences](../README.md#behaviour-differences).

---

# 運維手冊

[English](#operations-guide) · **繁體中文**

以下路徑假設主機是 Debian/Ubuntu，並且：

- `lg-api` 安裝在 `/opt/lg-api/lg-api.jar`，以 `lg-api` 服務執行；
- MySQL 8.0，資料庫名 `gazetteer`；
- Manticore 從官方 APT 源安裝。

請按你的主機調整路徑。每一步都在上一步的檢查點正常之後再做。各步驟的完整命令與上方英文部分相同，這裡說明要點。

## 0. 基線

- 在本機跑 `SKIP='之' bash ops/api_check.sh > check_before.tsv`。常用字在 MySQL 上要跑好幾分鐘，所以跳過。
- 在伺服器上記錄 `df -h /`、`free -h`，以及 `sudo md5sum /opt/lg-api/lg-api.jar`。這個 md5 就是你的**原版 jar**。

## 1. 安裝 Manticore、建索引

這一步不影響網站。

- 只裝 `manticore-server` 和 `manticore-tools`，加 `--no-install-recommends`。不要裝 meta 套件 `manticore`，它會帶上 Buddy 等元件。
- 把 `manticore.conf`、`logart_tsv.py` 放到 `/etc/manticoresearch/`；先備份套件自帶的配置。
- 建立 `/etc/manticoresearch/logart-mysql.cnf`（`[client]` 的 user 和 password；擁有者 manticore，權限 600）。
  這個 MySQL 帳號需要 `gazetteer.*` 的 SELECT 權限，以及 RELOAD 權限（`FLUSH TABLES` 要用）。
- 全量建索引：290 萬頁約 14 分鐘，佔滿一個 CPU，並有大量順序讀，請放在低峰。
  - 必須用 `setsid nohup` 讓它脫離 SSH 會話，日誌要寫到 manticore 使用者能寫的目錄。
  - 等待時用 `pgrep -x indexer`。
  - 預期輸出 `total <contents_myisam_ft 行數> docs` 和 `exit=0`。
- 若報 `not all columns found`，說明某頁正文含有原始控制字元。`logart_tsv.py` 的 `TR` 表已把
  0x00–0x1F、0x7F、U+0085、U+2028、U+2029 換成空格；資料裡若還有別的，就擴充這張表。

**檢查點**：直接查 Manticore，看幾個查詢的速度（一個罕見人名、舉人、之），以及 `searchd` 的 RSS。

## 2. 開啟 FEDERATED、建轉發表

這一步會重啟 MySQL 一次，只需幾秒。

- 安裝 `zz-federated.cnf`，重啟 MySQL，然後執行 `federated.sql`。冒煙測試的數字必須等於直接查 Manticore 的結果。
- `lg-api` 用來連線的 MySQL 帳號需要 `gazetteer.mt_pages` 的 SELECT 權限，有 `gazetteer.*` 的授權就夠了。
  FEDERATED 連 Manticore 時用的是字面量使用者 `FEDERATED`，與 MySQL 權限無關。

**檢查點**：在 MySQL 裡對快速路徑的 SQL 計時。參考值：生產上水利 0.4 s、舉人 1.2 s、之 9 s。

**不要**用 `contents_myisam_ft cs … WHERE cs.id IN (SELECT id FROM mt_pages …)` 來計數。那樣 MySQL 為了取得
`cs.book_id`，會逐行隨機讀 MyISAM 正文，舉人要 50 秒。

## 3. 保活元件（必裝）

安裝 systemd drop-in（`/etc/systemd/system/manticore.service.d/flush-federated.conf`，之後執行 `daemon-reload`）
和 `/etc/cron.d/logart-mt-flush`。原因見[故障排除](#故障排除)。不裝的話，只要一個晚上沒人檢索，第二天檢索就會壞。

## 4. 生成並換上改版 jar

- 在本機執行 `python lgapi/build_patched_jar.py lg-api.jar lg-api.jar.patched --keep-xml work/`。
  自檢那一行必須顯示：只有 `PageTextMapper.xml` 內容不同、`compress_type differs: []`、前綴保留為 `True`。
- 在伺服器上：
  1. 先把原版保存為 `lg-api.jar.pre-manticore`。
  2. jar 若帶 immutable 屬性，替換前先 `chattr -i`。
  3. 換上新 jar，再 `chattr +i`。
  4. 重啟 `lg-api`。
- 在本機跑 `bash ops/api_check.sh > check_after.tsv`，和切換前的結果並排比對。預期：
  - 罕見詞的計數相同或略多（U+3000 和 `∈` 處的命中）；
  - 第一頁結果的 md5 相同；
  - 耗時下降一到兩個數量級。

  最後在瀏覽器裡試一次檢索、統計圖和 CSV 匯出。

## 回退

換回 `lg-api.jar.pre-manticore`，重啟 `lg-api`。jar 的 immutable 屬性同樣要先去掉、換完再加回。
Manticore 和 `mt_pages` 閒置時不影響任何東西。要徹底移除：

1. `DROP TABLE gazetteer.mt_pages`
2. 刪除 `zz-federated.cnf`、drop-in 和 cron 檔。
3. 重啟 MySQL。
4. `apt remove manticore-server manticore-tools`

## 正文變動後重建索引

`contents_myisam_ft` 有**任何**變動（新書、重新匯入頁面）都要重建：`bash ops/reindex.sh && bash ops/reindex-wait.sh`。

重建是線上進行的，在切換之前檢索繼續使用舊索引；索引所在的磁碟要有能再放一份索引的空間。
只改書目或目錄**不需要**重建，因為它們仍由 MySQL 查詢。

**索引過期時的表現**：

- 新頁面搜不到。
- 已刪除或重新匯入的頁面仍會算進計數和統計，但不會出現在列表裡，所以列表行數會少於計數。

## 升級 lg-api

新版 jar 會覆蓋補丁。用**新的原版** jar 重跑 `lgapi/build_patched_jar.py`：

- 如果上游改動了補丁要替換的片段，腳本會報錯 `expected exactly one occurrence`，而不會生成錯誤的補丁。
- 如果上游確實改了，就調整 `patch_mapper.py`，並在本地測試環境驗證通過後再上線。

## 健康檢查

- `bash ops/healthcheck.sh` 檢查：
  - 服務狀態；
  - 索引頁數與正文表行數是否相等；
  - 經 FEDERATED 與直接查 Manticore 的計數是否相等；
  - 失效連線錯誤的次數；
  - cron 和 drop-in 是否已安裝；
  - jar 是否帶補丁；
  - 磁碟和記憶體。
- `bash ops/drift.sh -v` 顯示倉庫與伺服器上已安裝檔案的差異。

日誌：

- Manticore：`/var/log/manticore/query.log`（每條查詢）和 `searchd.log`。
- `searchd.log` 裡的 `secondary library not loaded` 警告無害，本方案不需要 `manticore-columnar-lib`。

## 故障排除

### 所有帶關鍵詞的檢索都回 HTTP 500「Lost connection to MySQL server during query」

**症狀**：

- lg-api 回 500，錯誤為 `ERROR 1430 … : 2013 : Lost connection…` 或 `ERROR 1430 … : 0 :`。
- 直接查 Manticore 一切正常。
- `query.log` 裡出現 `SET NAMES utf8mb4 # error=unhandled statement type`。

正常的 FEDERATED 連線，第一句是探測用的 `SELECT … WHERE 1=0`。

**原因**：

1. MySQL FEDERATED 為每個開啟的 table handler 快取一條連線，並跨 session 重用。
2. Manticore 會關閉空閒超過上限的 SQL 連線。上限有兩個：
   - `client_timeout`（預設 5 分鐘，`manticore.conf` 已設為 365 天）；
   - session 層的 `wait_timeout`（28800 秒 = **8 小時**）。它只能經 VIP 監聽埠修改，而且重啟後失效。
3. 下次用到已關閉的連線時，libmysqlclient 會自動重連並先發 `SET NAMES`，而 Manticore 拒絕 `FEDERATED` 使用者發的任何非 SELECT 語句。
4. 結果是：只要一個晚上沒人檢索，第二天的檢索就會失敗。

**處理**：

- 立即恢復：在 MySQL 裡執行 `FLUSH TABLES gazetteer.mt_pages;`。只需幾秒，沒有副作用，也不改任何資料。
- 根治：
  - 安裝 `cron/logart-mt-flush`，每 30 分鐘 FLUSH 一次；
  - 安裝 systemd drop-in，Manticore 每次重啟後自動 FLUSH。

*未經測試的替代方案*：加一個 `listen = 127.0.0.1:9312:mysql_vip` 監聽埠，每次 Manticore 啟動後經它執行
`SET GLOBAL wait_timeout=…`。用普通連線執行會被拒絕：`Only VIP connections can change global wait_timeout value`。

### Manticore 停了

所有帶關鍵詞的檢索、統計和匯出都會報錯；瀏覽、目錄、書目和帳號功能照常。

1. 執行 `sudo systemctl restart manticore`；drop-in 會自動 FLUSH `mt_pages`。
2. 如果起不來，就[回退](#回退) jar，全文檢索回到 MySQL。

### 計數被截斷

`patch_mapper.py` 的 `MAX_MATCHES`（5,000,000）必須大於總頁數。超過時調大它，重新生成 jar 並部署。

### 已知設計風險

快速路徑的列表依賴「派生表的 `ORDER BY` 順序在與外層查詢關聯後被保留」。MySQL 8.0 實際如此，所有測試也一致，
但 SQL 標準並不保證。MySQL 大版本升級後要重新測試。

## 本地測試環境

用來驗證新補丁或新版 `lg-api`，不碰生產。需要 Docker；容器限制為 2 核 4 GB，以模擬小型雲主機。

1. **樣本資料**：
   - 按書抽約 10 萬頁（`contents_myisam_ft` 的 `id, book_id, page_no, content`）和對應的 `books` 行；
   - 用 `mysqldump --no-data gazetteer` 匯出全部表結構；
   - 匯出幾張小配置表：`application_functions`、`permissions`、`sorted_char`、`tag_list`、`coordinates_books`；
   - 匯出樣本書的 `sections_index`；
   - 匯出一個測試帳號。
   - 這些全部放在本倉庫之外。
2. **容器**（放在同一個 Docker network）：
   - `mysql:8.0`：
     - 啟動參數 `--ngram_token_size=1 --ft_min_word_len=1 --secure-file-priv=`；
     - 把 `zz-federated.cnf` 放進 `/etc/mysql/conf.d/`；
     - 自己建 MyISAM + FULLTEXT 表，再 `LOAD DATA`。
   - `manticoresearch/manticore:29.9.0`，使用本倉庫的 `manticore.conf`，改這幾處：
     - `tsvpipe_command` 換成 `cat <樣本 tsv>`（樣本 TSV 用 `logart_tsv.py` 的 `unescape` 和 `TR` 生成）；
     - `listen` 換成 `9306:mysql`；
     - `federated.sql` 裡的 `127.0.0.1` 換成容器名。
   - 兩個 `eclipse-temurin:21-jre` 容器，分別跑原版和改版 jar（埠 15633、15634）。
     - 用一份改過的 `application.properties` 副本，讓 jdbc 指向 MySQL 容器；
     - 啟動參數用 `--spring.config.additional-location=file:<props>`。
     - 不要用 `--spring.config.location`：它會替換掉 jar 內建的配置（包括 context path），導致所有請求都回 `401 JWT Token expired`。
3. **對比**：`LG_USER=… LG_PASS=… python tests/api_compare.py`。預期只有[行為差異](../README.md#行為差異)裡列出的那些 `DIFF`。
