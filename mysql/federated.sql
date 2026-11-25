-- Bridge table: MySQL -> Manticore (requires `federated` enabled in mysqld: mysql/zz-federated.cnf).
-- The user name in CONNECTION must be literally FEDERATED (Manticore switches into
-- "query column" mode for it); the database part must be `Manticore`.
-- Columns other than `query` are bound BY NAME to the Manticore attributes the query returns
-- (id, book_id, page_no) -- the patched mapper aliases this table as `cs` on its fast path.
-- `query` VARCHAR(700): FEDERATED caps key length at 3072 bytes (utf8mb4 -> 768 chars max).
SET NAMES utf8mb4;
USE gazetteer;
DROP TABLE IF EXISTS mt_pages;
CREATE TABLE mt_pages (
  id      BIGINT       NOT NULL,
  book_id VARCHAR(5)   NULL,
  page_no BIGINT       NULL,
  query   VARCHAR(700) NOT NULL,
  INDEX (query)
) ENGINE=FEDERATED DEFAULT CHARSET=utf8mb4
  CONNECTION='mysql://FEDERATED@127.0.0.1:9306/Manticore/pages';

-- smoke test (must return the same number as Manticore directly)
SELECT count(*) FROM mt_pages
 WHERE query = 'SELECT id, book_id, page_no FROM pages WHERE MATCH(''"舉人"'') LIMIT 0,5000000 OPTION max_matches=5000000';
