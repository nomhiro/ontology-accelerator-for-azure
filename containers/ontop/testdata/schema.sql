-- `ontop-check.test.sh` が使う題材 (P3-01、ADR-0046)。
--
-- **2 つの表を外部キーで結ぶ。** 1 表だけだと、SPARQL の結合が SQL の
-- 結合に書き換わったことを確かめられない(仮想グラフの要点はそこである)。
--
-- **日本語を入れる。** Ontop の `ONTOP_FILE_ENCODING`(既定 UTF-8)と
-- JDBC の経路で文字が壊れないことを併せて確かめる。
CREATE SCHEMA vkg;

CREATE TABLE vkg.customer (
  id        integer PRIMARY KEY,
  full_name text NOT NULL,
  tier      text
);

CREATE TABLE vkg."order" (
  id          integer PRIMARY KEY,
  customer_id integer NOT NULL REFERENCES vkg.customer(id),
  total_jpy   numeric(12,2) NOT NULL
);

INSERT INTO vkg.customer VALUES
  (1, '田中 太郎', 'gold'),
  (2, 'Sato Hanako', 'silver');

INSERT INTO vkg."order" VALUES
  (10, 1, 12800.00),
  (11, 1, 4500.50),
  (12, 2, 990.00);
