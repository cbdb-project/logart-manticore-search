# LoGaRT Manticore Search

**English** · [繁體中文](#繁體中文)

Fast full-text search for [LoGaRT](https://logart.mpiwg-berlin.mpg.de/) (Local Gazetteers Research Tools)
deployments, backed by [Manticore Search](https://manticoresearch.com/). Nothing changes for users or
API clients: the LGServices web UI and the `lg-api` REST endpoints stay the same. The difference is
that keyword searches over ~2.9 million gazetteer pages take about a second instead of 15–160 seconds.

This repository contains **configuration and scripts only**. It ships no gazetteer data and no
`lg-api` code (see [Licensing and credits](#licensing-and-credits)).

| Query (2.88 M pages, AWS t3.medium) | count: before → after | first result page: before → after |
|---|---|---|
| 李安仁 | 53.9 s → 0.77 s | 34.2 s → 0.44 s |
| 鄭成功 | 17.2 s → 0.17 s | 8.0 s → 0.23 s |
| 水利 | 55.5 s → 0.36 s | 75.7 s → 0.50 s |
| `+舉人 +知縣` | 80.8 s → 1.23 s | 106.7 s → 1.24 s |
| 舉人 (filtered to one book) | 88.5 s → 1.13 s | 90.5 s → 1.21 s |
| 舉人 | 91.6 s → 1.29 s | 155.3 s → 1.65 s |
| 之 (2.18 M hits) | — → 9.3 s | — → 15.2 s |

In the before/after comparison of 13 test queries, every first result page held the same records as
before. Counts stayed the same or rose slightly; the extra hits are real matches that MySQL missed (see
[Behaviour differences](#behaviour-differences)).

## Why search was slow

`lg-api` searches `contents_myisam_ft`, a MyISAM table with a `FULLTEXT … WITH PARSER ngram` index
(`ngram_token_size=1`). MyISAM full-text indexes do not store word positions. A phrase query such as
舉人 therefore first collects every page containing both 舉 and 人 (≈650 k pages). MySQL then re-reads
each of those rows from a ~9.5 GB table to check that the two characters are adjacent. On a 4 GB
machine almost all of that is random disk I/O.

## How it works

```
 browser ──► Apache ──► lg-api (Spring Boot, MyBatis)      ← one mapper XML patched, nothing else
                              │  SQL
                              ▼
                     MySQL 8.0  gazetteer
                     ├─ books, sections_index, contents_myisam_ft   (unchanged, still the source of truth)
                     └─ mt_pages  ENGINE=FEDERATED ──────────────┐
                                                                 │ MySQL protocol, 127.0.0.1:9306
                                                                 ▼
                                                    Manticore searchd, table `pages`
                                                    (positions stored, CJK unigrams; returns id, book_id, page_no)
                                                                 ▲
                              indexer (tsvpipe) ◄── logart_tsv.py ◄── SELECT … FROM contents_myisam_ft
```

1. **Manticore** indexes every page as single CJK characters *with positions*, so it can answer phrase
   queries directly. It stores no text and returns only `id, book_id, page_no`.
2. **`mt_pages`**, a MySQL `FEDERATED` table, forwards a query string to Manticore. MySQL joins the result like an ordinary table.
3. **`PageTextMapper.xml`** in `lg-api` is the only file changed:
   - It rewrites the backend's MySQL boolean keyword string into Manticore syntax.
   - For count, statistics and the result list, it uses `mt_pages` in place of the page table, so MySQL never scans the 9.5 GB text table.
   - The result list reads page text and section names only for the 25 rows that are displayed.
   - Every other query (export, comparison, tasks) keeps its original SQL. Only the `MATCH … AGAINST` condition is replaced with `id IN (SELECT id FROM mt_pages …)`.

Filtering, sorting, sections and page text still come from MySQL. Book metadata, tables of contents
and accounts are untouched.

## Repository layout

| Path | What it is |
|---|---|
| `manticore/manticore.conf` | Manticore configuration: table definition, CJK tokenisation, phrase boundaries, `searchd` |
| `manticore/logart_tsv.py` | Indexing pipeline. Streams `contents_myisam_ft` out of MySQL as TSV for the `tsvpipe` source |
| `mysql/zz-federated.cnf` | Enables the FEDERATED engine |
| `mysql/federated.sql` | Creates the bridge table `gazetteer.mt_pages` and runs a smoke test |
| `lgapi/patch_mapper.py` | Generates the patched `PageTextMapper.xml` from **your** copy of the original. Stops with an error if upstream changed |
| `lgapi/repack_jar.py` | Swaps one entry inside a Spring Boot fat jar. Keeps the launch-script prefix and per-entry compression, and checks its own output |
| `lgapi/build_patched_jar.py` | One step: original `lg-api.jar` → patched `lg-api.jar` (extract, patch, repack, print md5s) |
| `systemd/flush-federated.conf` | `manticore.service` drop-in that drops MySQL's stale FEDERATED connections after a Manticore restart |
| `cron/logart-mt-flush` | Keeps FEDERATED connections fresh. **Required**, see [Troubleshooting](docs/operations.md#troubleshooting) |
| `ops/*.sh` | Run from your workstation over SSH: `healthcheck`, `drift` (repo vs. server), `reindex`, `api_check` |
| `tests/prod_api_check.sh` | Fixed keyword set → count, timings, md5 of the first result page. Run it before and after a change |
| `tests/api_compare.py` | Compares an original and a patched `lg-api` instance request by request (local test setup) |
| `docs/operations.md` | Full deployment, rollback, re-indexing, upgrade, troubleshooting and local test procedures |

## Requirements

- A working LoGaRT backend: `lg-api` (Spring Boot jar, package `de.mpiwg.lgapi`) on **MySQL 8.0**, with
  page text in `gazetteer.contents_myisam_ft`. Tested with MySQL 8.0.46.
- Manticore Search **29.9.0**: packages `manticore-server` and `manticore-tools`; the Buddy sidecar is not needed.
- Disk: about 1.1× the size of the page text for the index (≈3.6 GB for 2.9 M pages), plus the same again during an online rebuild.
- Memory: `searchd` uses about 160–330 MB.
- Python 3 on the server (for the indexer pipeline). Python 3 with `requests` on your workstation, for the tests.

## Deployment in brief

Every step is reversible. Full commands, checkpoints and rollback are in
[docs/operations.md](docs/operations.md).

1. **Baseline:** run `tests/prod_api_check.sh` against the unmodified site and keep its output.
2. **Install Manticore and build the index.** Put `manticore.conf` and `logart_tsv.py` in
   `/etc/manticoresearch/`, create the read-only MySQL option file, then run `indexer --all`
   (about 15 min for 2.9 M pages). The website is unaffected.
3. **Enable FEDERATED** (one MySQL restart) and run `mysql/federated.sql`. Time the fast-path query in MySQL.
4. **Install the keep-alive pieces:** `systemd/flush-federated.conf` and `cron/logart-mt-flush`.
5. **Build and install the patched jar:**
   `python lgapi/build_patched_jar.py lg-api.jar lg-api.jar.patched`. Keep the original as
   `lg-api.jar.pre-manticore`, replace the jar and restart `lg-api`.
6. **Compare** `prod_api_check.sh` output before and after, then try search, statistics and export in the web UI.

> **Important: idle connections break search.**
> MySQL FEDERATED caches its connection to Manticore. Manticore closes SQL connections that have been
> idle for 8 hours (`wait_timeout`). When MySQL reconnects, it sends `SET NAMES`, which Manticore
> rejects. After any quiet night, every keyword search then fails with HTTP 500
> `Lost connection to MySQL server during query`. This happened in production twice, including a
> four-day outage.
>
> - Quick fix: run `FLUSH TABLES gazetteer.mt_pages;` in MySQL.
> - Prevention: the cron job `cron/logart-mt-flush` (every 30 min) plus the systemd drop-in.
> - Full explanation: [docs/operations.md → Troubleshooting](docs/operations.md#troubleshooting).

**Rollback:** put back `lg-api.jar.pre-manticore` and restart `lg-api`. Search runs on MySQL again,
slow but complete. Manticore and `mt_pages` can be left idle or removed.

**Whenever page text changes** (new books, re-imported pages), rebuild the index online:
`bash ops/reindex.sh`. Until you do, new pages cannot be found, and the result list can show fewer rows
than the count.

## Backend API: searching LoGaRT programmatically

The web UI is a thin client of `lg-api`, so anything the search page does can be scripted. The base
URL is `https://<your-logart-host>/lg-api`. On the server itself it is `http://localhost:5633/lg-api`.
This project does not change the API. The notes below describe the API as it is, with the pitfalls we
found. Read them before you write a client.

### Authentication and conventions

```bash
BASE=https://<your-logart-host>/lg-api
H='X-Requested-With: XMLHttpRequest'          # REQUIRED on every request
curl -s -c jar.txt -H "$H" -H 'Content-Type: application/json' \
     -d '{"loginId":"<login>","password":"<password>"}' "$BASE/auth/login"
# success: HTTP 200 + cookie LG-Token=<JWT>; send that cookie with every later request (-b jar.txt)
```

- **Send `X-Requested-With: XMLHttpRequest` on every request.** Without it, login fails with
  `401 The authentication method is not supported`, which is not a password error.
- An expired token returns `401 JWT Token expired`. Log in again when you see it.
- Results only include the text sources your account may use. `GET /permissions/availableTextSources`
  lists them, for example `["eastview","openlogart"]`.
- Swagger: `GET /v2/api-docs`, after you log in.
- Always pass `isTurbo=0`. The alternative Elasticsearch path is disabled in standard deployments.

### Search endpoints

| Method | Path | Purpose | Main parameters |
|---|---|---|---|
| GET | `/pagetexts/count` | Number of matching pages | `keywords`, `filters`, `isTurbo` |
| GET | `/pagetexts` | One page of results | `+ page` (1-based page number), `rows`, `sortField`, `sortOrder`, optional `get_section_ids=1` |
| GET | `/pagetexts/NoPagination` | All results, no paging. **Can be huge**; prefer the CSV export | `keywords`, `filters`, `sortField`, `sortOrder` |
| GET | `/pagetexts/statistics` | Hit counts grouped by a field | `countField` (`dynasty`, `book_name`, `level1`, `source`, `short_section_name`), `sortField=count`, `sortOrder` |
| GET | `/pagetexts/drillDownStatistics` | Same as above, inside one group | `+ drillDownParams`, e.g. `dynasty:清;` |
| POST | `/pagetexts/export.csv` | CSV of all hits (form-encoded) | `keywords`, `filters`, `isTurbo`, `excludes` (comma-separated page ids to leave out), **`fileName` (required)** |
| POST | `/pagetexts/export.map` | Same, in LGMap format | as above |
| GET | `/comparison` | Hits laid out for side-by-side comparison | same as `/pagetexts` |
| GET | `/comparison/bookId/{bookId}/pageNo/{pageNo}/action/{previous\|next}` | Previous or next page of a book in comparison view | path only |

The same controller also has endpoints for saved searches (`/pagetexts/savedSearchs…`) and tagging
tasks (`/pagetexts/tasks`, `/pagetexts/{pageTextId}/tasks`). This project does not touch them.

### `keywords` syntax

| You type | Meaning |
|---|---|
| `舉人` | Pages containing the phrase 舉人. Quotes are not needed and have no effect |
| `李安仁 高攀龍` | Space-separated terms are separate phrases, joined by **OR** |
| `+舉人 +知縣` | **AND**: both phrases. Once any `+` is present, terms without `+` are ignored |
| `舉人 -知縣` | 舉人 but **not** the phrase 知縣. `-` can be combined with `+` terms |
| `加○級○封` | `○` (and any whitespace in the source text) is transparent, so this matches 加級封, 加 級 封, 加○級○封 |

- Matching is by single CJK characters with positions, so any phrase of any length works.
- Punctuation in the source text (。，、；：！？「」『』（）《》〈〉【】〔〕 and ASCII `,.;:!()[]`) ends a phrase.
  A phrase never matches across a sentence break.
- The characters `' " \ | ( ) ^ $` are removed from keywords.
- No variant-character folding is applied: 舉 and 举 are different characters.

### `filters` syntax

`filters` is **not JSON**. It has the form `key:value1,value2;key2:value;`. Send it empty for no filter.

| Key | How it matches |
|---|---|
| `book_name` | Regular expression (MySQL `RLIKE`) on the book id or book title, e.g. `book_name:江南通志;` |
| `level1`, `level2` | Regular expression on the province / prefecture fields |
| `section_name` | Regular expression on table-of-contents entry names |
| `dynasty`, `period`, `admin_type`, `source` | Exact values. Separate several with commas and **do not quote them**: `dynasty:清,明;source:eastview;` |

### Results

`GET /pagetexts` returns a JSON array:

```json
[{"id":"4368","bookId":"0005","book_name":"順天府志","level1":"北京市","level2":"NULL",
  "dynasty":"清","period":"光緒","edition":"","volume":"一百三十卷附錄一卷","author":"",
  "book_year":"0","edition_year":"0","source":"eastview","admin_type":"0",
  "section_name":"1-順天府志","taskCreated":"False","page_no":"4368","content":"…full page text…"}]
```

- `section_name` lists every table-of-contents entry that covers the page, as `level-name`.
- `page_no` is a **corpus-wide** page number, not a page number within the book.
- `/count` returns a bare number.
- `/statistics` returns `[{"id":"清","count":177}, …]`.

`sortField` accepts `book_year`, `book_name`, `dynasty`, `author`, `volume`, `source`, `page_no` and
`section_name`. `sortOrder` is `asc` or `desc`.

### Python example

```python
import requests

BASE = 'https://<your-logart-host>/lg-api'
s = requests.Session()
s.headers['X-Requested-With'] = 'XMLHttpRequest'
s.post(BASE + '/auth/login', json={'loginId': '<login>', 'password': '<password>'}).raise_for_status()

q = {'keywords': '+舉人 +知縣', 'filters': 'dynasty:清;', 'isTurbo': 0}
total = int(s.get(BASE + '/pagetexts/count', params=q).text)
rows = s.get(BASE + '/pagetexts', params=dict(q, page=1, rows=25,
                                              sortField='book_year', sortOrder='asc')).json()
by_book = s.get(BASE + '/pagetexts/statistics', params=dict(q, countField='book_name',
                                                            sortField='count', sortOrder='desc')).json()
csv = s.post(BASE + '/pagetexts/export.csv', data=dict(q, excludes='', fileName='result')).text
print(total, len(rows), by_book[:3], len(csv.splitlines()))
```

Please keep scripted use polite. Use the CSV export instead of paging through tens of thousands of
results, and do not run many requests in parallel. A shared server may be small.

## Behaviour differences

Every difference from the MySQL search path was checked by hand. Each one is a correction:

- **Slightly more hits.** MySQL breaks a phrase at an ideographic space (U+3000) and at the marker `∈`,
  both common in some text sources. Those true matches are now found (舉人: 207,199 → 207,505).
- **`a -b` excludes the phrase b.** MySQL excluded every page containing all the characters of b
  anywhere on the page, which over-excluded: `舉人 -知縣` returned 2,890 pages, versus 4,066 now.
- **User-added quotes.** On MySQL, quoting a term turned it into "any of these characters". Quotes are
  now simply ignored.
- **Placeholders.** `○` and `?` in the source text no longer break a phrase. Punctuation still does,
  as before.

## Maintaining a deployment from this repository

This repository can be the single source of truth for a running site. Host-specific secrets stay out of git:

- `.env`, copied from `.env.example` and ignored by git, holds the SSH target and key path, plus an `lg-api`
  login for the smoke test. The `ops/` and `tests/` scripts are the only things that read it.
- On the server, the only secret is the read-only MySQL option file
  `/etc/manticoresearch/logart-mysql.cnf` (mode 600, owner `manticore`). It is created there and never
  committed.
- `bash ops/drift.sh -v` shows how the files installed on the server differ from the repository.
  `bash ops/healthcheck.sh` checks services, index size against table size, FEDERATED, stale-connection
  errors, the jar patch, disk and memory. Both scripts are read-only.
- The patched jar is **built, not stored**. Each deployment keeps its own original `lg-api.jar` and
  regenerates the patch with `lgapi/build_patched_jar.py`. If MPIWG ships a new `lg-api`, the same
  command either produces a correct patch or stops with an error naming the fragment that changed.

## Licensing and credits

- **LoGaRT** was created by the Max Planck Institute for the History of Science (MPIWG), Department III.
  When you use LoGaRT, cite it as its authors request; see <https://logart.mpiwg-berlin.mpg.de/>.
  This project is an independent operational add-on. It is **not affiliated with or endorsed by MPIWG**.
- **`lg-api` is not open source.** This repository does not contain the `lg-api` jar or its mapper XML, and
  never should. `patch_mapper.py` works on a copy the operator extracts from their own, legitimately
  obtained jar. The script contains only the replacement SQL and the short fragments it needs to find
  its place in that file. `.gitignore` excludes `*.jar` and `PageTextMapper*.xml`.
- **No data is included.** Gazetteer texts come from commercial (e.g. Eastview) and open collections. The
  OpenLoGaRT texts are licensed **CC BY-NC-SA 4.0** by MPIWG. Index files built from them must not be
  published here.
- **Manticore Search** is GPL-3.0-or-later. This project only ships a configuration file for it and talks
  to it over its MySQL protocol; it does not redistribute Manticore.
- **License of this repository:** [MIT](LICENSE). It covers only the files in this repository. It
  grants no rights to `lg-api`, LoGaRT data or Manticore.

---

# 繁體中文

[English](#logart-manticore-search) · **繁體中文**

為 [LoGaRT](https://logart.mpiwg-berlin.mpg.de/)（地方志研究工具，Local Gazetteers Research Tools）部署提供
基於 [Manticore Search](https://manticoresearch.com/) 的快速全文檢索。**使用者和 API 呼叫方完全無感**：
LGServices 網頁介面和 `lg-api` REST 介面都不變，只是在約 290 萬頁方志全文裡檢索關鍵詞，從 15–160 秒
降到 1 秒左右。

本倉庫**只有配置和腳本**：不含任何方志資料，也不含 `lg-api` 的程式碼（見[授權與致謝](#授權與致謝)）。

| 查詢（288 萬頁，AWS t3.medium） | 計數：切換前 → 後 | 第一頁結果：切換前 → 後 |
|---|---|---|
| 李安仁 | 53.9 s → 0.77 s | 34.2 s → 0.44 s |
| 鄭成功 | 17.2 s → 0.17 s | 8.0 s → 0.23 s |
| 水利 | 55.5 s → 0.36 s | 75.7 s → 0.50 s |
| `+舉人 +知縣` | 80.8 s → 1.23 s | 106.7 s → 1.24 s |
| 舉人（限一本書） | 88.5 s → 1.13 s | 90.5 s → 1.21 s |
| 舉人 | 91.6 s → 1.29 s | 155.3 s → 1.65 s |
| 之（218 萬命中） | — → 9.3 s | — → 15.2 s |

切換前後對比 13 組測試查詢，第一頁結果的記錄全部相同；計數相同或略多，多出的都是 MySQL 漏掉的真實命中
（見[行為差異](#行為差異)）。

## 為什麼原來慢

`lg-api` 檢索的是 MyISAM 表 `contents_myisam_ft`，全文索引為 `FULLTEXT … WITH PARSER ngram`（`ngram_token_size=1`）。
MyISAM 全文索引**不存詞位置**，所以「舉人」這樣的短語檢索要先取出所有同時含「舉」「人」的頁（約 65 萬頁），
再從約 9.5 GB 的表裡逐行讀出來核對兩字是否相鄰。在 4 GB 記憶體的機器上，這幾乎全是隨機磁碟讀取。

## 工作原理

（架構圖見上方英文部分。）

1. **Manticore** 把每頁按單個漢字建索引，**並記錄位置**，因此可以直接回答短語查詢。它不存原文，只回傳 `id, book_id, page_no`。
2. **`mt_pages`** 是 MySQL 的 `FEDERATED` 表，把查詢字串轉發給 Manticore。MySQL 可以像普通表一樣關聯它的結果。
3. `lg-api` 裡只改了 **`PageTextMapper.xml`** 這一個檔案：
   - 把後端拼好的 MySQL boolean 關鍵詞改寫成 Manticore 語法。
   - 計數、統計、結果列表直接用 `mt_pages` 取代正文表，MySQL 不再掃描 9.5 GB 的正文。
   - 列表只為當頁顯示的 25 行讀正文、查目錄名。
   - 其餘查詢（匯出、對比、任務）保持原 SQL，只把 `MATCH … AGAINST` 換成 `id IN (SELECT id FROM mt_pages …)`。

書目過濾、排序、目錄、正文仍由 MySQL 提供；書目、目錄、帳號等功能完全沒動。

## 目錄結構

| 路徑 | 內容 |
|---|---|
| `manticore/manticore.conf` | Manticore 配置：表定義、漢字切分、短語邊界、`searchd` |
| `manticore/logart_tsv.py` | 建索引的資料管道：從 MySQL 串流讀出 `contents_myisam_ft`，以 TSV 餵給 `tsvpipe` |
| `mysql/zz-federated.cnf` | 開啟 FEDERATED 引擎 |
| `mysql/federated.sql` | 建立轉發表 `gazetteer.mt_pages`，並做冒煙測試 |
| `lgapi/patch_mapper.py` | 由**你自己的**原版 mapper 生成改版 `PageTextMapper.xml`；上游改動時直接報錯 |
| `lgapi/repack_jar.py` | 只替換 Spring Boot fat jar 裡的一個條目；保留啟動腳本前綴和各條目的壓縮方式，並自檢 |
| `lgapi/build_patched_jar.py` | 一步完成：原版 `lg-api.jar` → 改版 `lg-api.jar`（解出、打補丁、重新打包、印出 md5） |
| `systemd/flush-federated.conf` | `manticore.service` 的 drop-in：Manticore 重啟後清掉 MySQL 裡失效的 FEDERATED 連線 |
| `cron/logart-mt-flush` | 讓 FEDERATED 連線保持新鮮。**必裝**，見[故障排除](docs/operations.md#故障排除) |
| `ops/*.sh` | 在本機經 SSH 執行：`healthcheck`、`drift`（倉庫與伺服器的差異）、`reindex`、`api_check` |
| `tests/prod_api_check.sh` | 固定的一組關鍵詞 → 計數、耗時、第一頁 md5。在改動前後各跑一次 |
| `tests/api_compare.py` | 原版與改版 `lg-api` 兩個實例逐個請求對比（本地測試環境用） |
| `docs/operations.md` | 完整的部署、回退、重建索引、升級、故障排除、本地測試流程 |

## 需求

- 一套可運作的 LoGaRT 後端：`lg-api`（Spring Boot jar，套件 `de.mpiwg.lgapi`）跑在 **MySQL 8.0** 上，
  正文在 `gazetteer.contents_myisam_ft`。已在 MySQL 8.0.46 上測試。
- Manticore Search **29.9.0**：裝 `manticore-server`、`manticore-tools` 兩個套件，不需要 Buddy。
- 磁碟：索引約為正文的 1.1 倍（290 萬頁約 3.6 GB），線上重建期間再要一份同樣大小的空間。
- 記憶體：`searchd` 約 160–330 MB。
- 伺服器上要有 Python 3（建索引用）；本機要有 Python 3 和 `requests`（跑測試用）。

## 部署摘要

每一步都可以回退。完整命令、檢查點和回退方法見 [docs/operations.md](docs/operations.md)。

1. **基線**：對未改動的網站跑 `tests/prod_api_check.sh`，保存輸出。
2. **安裝 Manticore、建索引**：把 `manticore.conf`、`logart_tsv.py` 放到 `/etc/manticoresearch/`，
   建好唯讀的 MySQL 憑據檔，再跑 `indexer --all`（290 萬頁約 15 分鐘）。網站不受影響。
3. **開啟 FEDERATED**（MySQL 重啟一次），執行 `mysql/federated.sql`，並在 MySQL 裡對快速路徑的查詢計時。
4. **安裝保活元件**：`systemd/flush-federated.conf` 和 `cron/logart-mt-flush`。
5. **生成並換上改版 jar**：`python lgapi/build_patched_jar.py lg-api.jar lg-api.jar.patched`。
   原版保存為 `lg-api.jar.pre-manticore`，替換 jar 後重啟 `lg-api`。
6. **比對**切換前後的 `prod_api_check.sh` 輸出，再在網頁上手動試檢索、統計和匯出。

> **重要：閒置的連線會讓檢索失效。**
> MySQL FEDERATED 會快取它到 Manticore 的連線；Manticore 會關閉閒置 8 小時（`wait_timeout`）的 SQL 連線。
> MySQL 重連時會先發 `SET NAMES`，而 Manticore 拒絕這句。結果是只要一個晚上沒人檢索，之後所有帶關鍵詞的檢索都回
> HTTP 500 `Lost connection to MySQL server during query`。生產上發生過兩次，其中一次斷了四天。
>
> - 立即恢復：在 MySQL 裡執行 `FLUSH TABLES gazetteer.mt_pages;`。
> - 預防：cron `cron/logart-mt-flush`（每 30 分鐘）加上 systemd drop-in。
> - 詳細說明：[docs/operations.md → 故障排除](docs/operations.md#故障排除)。

**回退**：換回 `lg-api.jar.pre-manticore`，重啟 `lg-api`。全文檢索回到 MySQL，慢但結果完整。
Manticore 和 `mt_pages` 可以閒置，也可以移除。

**正文一有變動**（匯入新書、重新匯入頁面），就要線上重建索引：`bash ops/reindex.sh`。
在重建之前，新頁面搜不到，結果列表的行數也可能少於計數。

## 後台 API：用程式檢索 LoGaRT

網頁前端只是 `lg-api` 的薄客戶端，檢索頁能做的事都可以用腳本完成。Base URL 是
`https://<你的 LoGaRT 主機>/lg-api`；在伺服器本機上是 `http://localhost:5633/lg-api`。
本專案沒有改動 API。以下是現有 API 的說明和實際踩過的坑，寫客戶端之前請先看完。

### 登入與通用約定

```bash
BASE=https://<你的 LoGaRT 主機>/lg-api
H='X-Requested-With: XMLHttpRequest'          # 每個請求都必須帶
curl -s -c jar.txt -H "$H" -H 'Content-Type: application/json' \
     -d '{"loginId":"<帳號>","password":"<密碼>"}' "$BASE/auth/login"
# 成功：HTTP 200，並回傳 cookie LG-Token=<JWT>；之後每個請求都帶上這個 cookie（-b jar.txt）
```

- **每個請求都必須帶 `X-Requested-With: XMLHttpRequest`**。不帶的話，登入會回
  `401 The authentication method is not supported`，這不是密碼錯誤。
- token 過期會回 `401 JWT Token expired`，重新登入即可。
- 結果只包含你的帳號有權使用的文字來源。用 `GET /permissions/availableTextSources` 查看，例如 `["eastview","openlogart"]`。
- Swagger：登入後讀 `GET /v2/api-docs`。
- 一律傳 `isTurbo=0`。另一條 Elasticsearch 路徑在一般部署中是關閉的。

### 檢索相關端點

| 方法 | 路徑 | 用途 | 主要參數 |
|---|---|---|---|
| GET | `/pagetexts/count` | 命中頁數 | `keywords`、`filters`、`isTurbo` |
| GET | `/pagetexts` | 一頁結果 | 另加 `page`（**頁碼**，從 1 開始）、`rows`、`sortField`、`sortOrder`，可選 `get_section_ids=1` |
| GET | `/pagetexts/NoPagination` | 全部結果，不分頁。**可能非常大**，建議改用 CSV 匯出 | `keywords`、`filters`、`sortField`、`sortOrder` |
| GET | `/pagetexts/statistics` | 按欄位分組統計命中數 | `countField`（`dynasty`、`book_name`、`level1`、`source`、`short_section_name`）、`sortField=count`、`sortOrder` |
| GET | `/pagetexts/drillDownStatistics` | 在某一組內再細分統計 | 另加 `drillDownParams`，如 `dynasty:清;` |
| POST | `/pagetexts/export.csv` | 匯出全部命中為 CSV（表單編碼） | `keywords`、`filters`、`isTurbo`、`excludes`（要排除的頁 id，逗號分隔）、**`fileName`（必填）** |
| POST | `/pagetexts/export.map` | 同上，LGMap 格式 | 同上 |
| GET | `/comparison` | 以對比檢視排列的命中 | 與 `/pagetexts` 相同 |
| GET | `/comparison/bookId/{bookId}/pageNo/{pageNo}/action/{previous\|next}` | 對比檢視裡某本書的上一頁或下一頁 | 只有路徑參數 |

同一個控制器還有儲存檢索（`/pagetexts/savedSearchs…`）和標註任務（`/pagetexts/tasks`、
`/pagetexts/{pageTextId}/tasks`）的端點，本專案沒有改動它們。

### `keywords` 語法

| 輸入 | 含義 |
|---|---|
| `舉人` | 含短語「舉人」的頁。不需要加引號，加了也沒有作用 |
| `李安仁 高攀龍` | 以空格分隔的每一段是一個短語，彼此為 **OR** |
| `+舉人 +知縣` | **AND**：兩個短語都要出現。只要出現任何 `+`，沒帶 `+` 的詞就會被忽略 |
| `舉人 -知縣` | 含「舉人」但**不含**短語「知縣」。`-` 可以和 `+` 詞一起用 |
| `加○級○封` | `○`（以及原文裡的空白）是透明的，所以這能匹配 加級封、加 級 封、加○級○封 |

- 按單個漢字加位置匹配，任意長度的短語都可以。
- 原文裡的標點（。，、；：！？「」『』（）《》〈〉【】〔〕以及 ASCII `,.;:!()[]`）會截斷短語，短語不會跨句匹配。
- 關鍵詞裡的 `' " \ | ( ) ^ $` 會被移除。
- 不做異體字或繁簡歸併：「舉」和「举」是不同的字。

### `filters` 語法

`filters` **不是 JSON**，格式是 `key:值1,值2;key2:值;`。不過濾就傳空字串。

| key | 匹配方式 |
|---|---|
| `book_name` | 以正則（MySQL `RLIKE`）匹配書號或書名，例如 `book_name:江南通志;` |
| `level1`、`level2` | 以正則匹配省、府欄位 |
| `section_name` | 以正則匹配目錄條目名 |
| `dynasty`、`period`、`admin_type`、`source` | 精確值，多個值用逗號分隔，**不要加引號**：`dynasty:清,明;source:eastview;` |

### 回傳格式

`GET /pagetexts` 回傳一個 JSON 陣列，欄位見上方英文部分的範例。

- `section_name` 列出覆蓋該頁的所有目錄條目，格式為 `層級-名稱`。
- `page_no` 是**全語料庫範圍**的頁碼，不是書內頁碼。
- `/count` 直接回傳一個數字。
- `/statistics` 回傳 `[{"id":"清","count":177}, …]`。

`sortField` 可用 `book_year`、`book_name`、`dynasty`、`author`、`volume`、`source`、`page_no`、`section_name`；
`sortOrder` 是 `asc` 或 `desc`。

Python 範例見上方英文部分。用腳本存取時請有節制：大量結果用 CSV 匯出，不要逐頁翻幾萬條，也不要大量並行請求。
共用的伺服器可能規格很小。

## 行為差異

與 MySQL 檢索路徑的每一處差異都已人工核實，全部是更正確的結果：

- **命中略多**：MySQL 會在全形空格（U+3000）和標記符 `∈` 處截斷短語，這兩者在部分文字來源裡很常見。
  這些真實命中現在能搜到了（舉人：207,199 → 207,505）。
- **`a -b` 按短語排除**：MySQL 會排除「頁內任何位置同時含有 b 所有字」的頁，排除過多。
  `舉人 -知縣` 以前是 2,890 頁，現在是 4,066 頁。
- **使用者自己加的引號**：在 MySQL 上，加引號會把詞變成「任一字出現」；現在引號直接被忽略。
- **佔位符**：原文裡的 `○`、`?` 不再截斷短語；標點仍然截斷，與以前一致。

## 用本倉庫維護一個部署

本倉庫可以作為線上網站的唯一來源。與主機相關的機密都不進 git：

- `.env` 由 `.env.example` 複製而來，已被 git 忽略。它存 SSH 目標和金鑰路徑，以及冒煙測試用的 `lg-api` 帳號。
  只有 `ops/` 和 `tests/` 的腳本會讀它。
- 伺服器上唯一的機密是唯讀的 MySQL 憑據檔 `/etc/manticoresearch/logart-mysql.cnf`
  （權限 600，擁有者 `manticore`）。它只在伺服器上建立，從不提交。
- `bash ops/drift.sh -v` 顯示伺服器上已安裝的檔案與倉庫的差異。
  `bash ops/healthcheck.sh` 檢查服務狀態、索引頁數與正文表行數是否一致、FEDERATED、失效連線錯誤、jar 補丁、
  磁碟和記憶體。兩個腳本都是唯讀的。
- 改版 jar **每次重新生成，不存檔**。每個部署保留自己的原版 `lg-api.jar`，用 `lgapi/build_patched_jar.py` 生成補丁。
  MPIWG 若發布新版 `lg-api`，同一條命令要麼生成正確的補丁，要麼報錯並指出上游改了哪一段。

## 授權與致謝

- **LoGaRT** 由馬克斯·普朗克科學史研究所（MPIWG）第三部門開發。使用 LoGaRT 時請按其作者要求引用，
  見 <https://logart.mpiwg-berlin.mpg.de/>。本專案是獨立的運維附加元件，**與 MPIWG 沒有隸屬或背書關係**。
- **`lg-api` 不是開源軟體**。本倉庫不含、也不應包含 `lg-api` 的 jar 或其 mapper XML。
  `patch_mapper.py` 處理的是運維者從自己合法取得的 jar 裡解出的副本；腳本裡只有替換用的 SQL，
  以及定位所需的少量片段。`.gitignore` 已排除 `*.jar` 和 `PageTextMapper*.xml`。
- **不含任何資料**。方志全文來自商業資料庫（如 Eastview）和開放資料集；其中 OpenLoGaRT 全文由 MPIWG 以
  **CC BY-NC-SA 4.0** 授權。由這些資料建成的索引檔也不得發布到這裡。
- **Manticore Search** 採用 GPL-3.0-or-later。本專案只提供它的配置檔，並透過 MySQL 協定與它通訊，不再散布 Manticore 本身。
- **本倉庫的授權**：[MIT](LICENSE)。只適用於本倉庫裡的檔案，不涉及 `lg-api`、LoGaRT 資料或 Manticore 的任何權利。
