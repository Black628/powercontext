# 混合检索合规性证据（seekdb 内完成）

- 采集时间：2026-10-29T12:55:50.888Z
- seekdb：`127.0.0.1:2882/test`，集合 `edge_kb`，维度 384
- SDK：seekdb (seekdb-js) 1.4.0
- 采集方式：直接调用服务端模块 `server/seekdb.mjs` 的 `search()`（与 `npm start` 同一份代码），只在 `InternalClient.execute` 上挂只读探针记录 SQL。检索逻辑未改动。

## 1. 集合物理结构与索引

表名：`c$v2$fcd0334dd39711f1997f00155d1db70d`

```sql
CREATE TABLE `c$v2$fcd0334dd39711f1997f00155d1db70d` (
  `_id` varbinary(512) NOT NULL,
  `document` string DEFAULT NULL,
  `embedding` VECTOR(384) DEFAULT NULL,
  `metadata` json DEFAULT NULL,
  PRIMARY KEY (`_id`),
  FULLTEXT KEY `idx_fts` (`document`) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart") BLOCK_SIZE 16384,
  VECTOR KEY `idx_vec` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64, SYNC_MODE=ASYNC) BLOCK_SIZE 16384
) ORGANIZATION HEAP DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0 LOB_INROW_THRESHOLD=78643
```

| 索引 | 列 | 类型 |
| --- | --- | --- |
| `PRIMARY` | `_id` | BTREE |
| `idx_fts` | `document` | FULLTEXT |
| `idx_vec` | `embedding` | VECTOR |

## 2. 服务端发起的一次调用

```js
collection.hybridSearch({ knn: { queryEmbeddings, nResults: recallK }, query: { whereDocument: { $contains } }, rank: { rrf: {} }, nResults, include })
```

## 3. 实际执行的语句序列（含返回行数）

### [1] 执行成功（无结果集） / 1.25 ms

```sql
SET @search_parm = ?
```

参数：

```json
["{\"query\":{\"query_string\":{\"fields\":[\"document\"],\"query\":\"全文索引 分词器\"}},\"knn\":{\"field\":\"embedding\",\"k\":20,\"query_vector\":[<384 维查询向量>]},\"rank\":{\"rrf\":{}},\"size\":5}"]
```

### [2] 1 行 / 1.31 ms

```sql
SELECT DBMS_HYBRID_SEARCH.GET_SQL('c$v2$fcd0334dd39711f1997f00155d1db70d', @search_parm) as query_sql FROM dual
```

### [3] 5 行 / 46.31 ms

```sql
SELECT ifnull(`_fts`.`_id`, `_vs`.`_id`) as `_id`, ifnull(`_fts`.`document`, `_vs`.`document`) as `document`, ifnull(`_fts`.`embedding`, `_vs`.`embedding`) as `embedding`, ifnull(`_fts`.`metadata`, `_vs`.`metadata`) as `metadata`, (ifnull(1 / (`_fts`.`_keyword_rank` + 60), 0) + ifnull(1 / (`_vs`.`_semantic_rank` + 60), 0)) as `_score` FROM ((SELECT *, RANK() over( ORDER BY `_keyword_score` DESC) as `_keyword_rank` FROM (SELECT /*+ opt_param('hidden_column_visible', 'true') */`__pk_increment`, `_id`, `document`, `embedding`, `metadata`, match(`document`) against('全文索引 分词器' in natural language mode) as `_keyword_score` FROM `test`.`c$v2$fcd0334dd39711f1997f00155d1db70d` WHERE match(`document`) against('全文索引 分词器' in natural language mode) ORDER BY `_keyword_score` DESC LIMIT 100)) `_fts` full join (SELECT *, RANK() over( ORDER BY `_semantic_score` DESC) as `_semantic_rank` FROM (SELECT /*+ opt_param('hidden_column_visible', 'true') */cosine_distance(`embedding`, '[<384 维查询向量>]') as `_distance`, `__pk_increment`, `_id`, `document`, `embedding`, `metadata`, round(1 - cosine_distance(`embedding`, '[<384 维查询向量>]') / 2, 8) as `_semantic_score` FROM `test`.`c$v2$fcd0334dd39711f1997f00155d1db70d` ORDER BY `_distance` APPROXIMATE LIMIT 20)) `_vs` on `_fts`.`__pk_increment` = `_vs`.`__pk_increment`) ORDER BY `_score` DESC, ifnull(`_vs`.`__pk_increment`, `_fts`.`__pk_increment`) LIMIT 5
```

## 4. 关键结论（看第 3 条语句）

1. `SET @search_parm = ?` —— 客户端只把"查询串 + 查询向量 + RRF 配置"打包成一个 JSON 参数交给 seekdb；
2. `SELECT DBMS_HYBRID_SEARCH.GET_SQL(...)` —— SQL 由 **seekdb 自己生成**，不是客户端拼的；
3. 生成的最终 SQL 是**一条**语句，同时包含：
   - 全文路：`match(document) against('…' in natural language mode)`；
   - 向量路：`cosine_distance(embedding, '[…]')` + `ORDER BY _distance APPROXIMATE LIMIT 20`（HNSW 近似检索）；
   - 融合：`FULL JOIN` 两路结果后 `(1/(_keyword_rank+60) + 1/(_semantic_rank+60)) as _score`，即 **RRF 在 seekdb 内计算**；
4. 客户端只收到最终结果行，没有拿到两路中间结果，也没有做任何拼合/重排 —— 客户端代码里不存在融合逻辑。

本次返回行数：**5** 行，seekdb 侧耗时 49.88 ms。

## 5. 返回结果

| # | id | _score | 文档片段 |
| --- | --- | --- | --- |
| 1 | `trace-probe-1793278550327` | 0.0328 | 全文索引使用 ik 分词器，向量检索使用 HNSW 索引… |
| 2 | `fulltext-index.md#1` | 0.0315 | seekdb 自带 ik、beng、space、ngram、ngram2 等分词… |
| 3 | `fulltext-index.md#0` | 0.0313 | # seekdb 全文索引与分词器 ## 全文索引怎么用 建表后可以直接加全文索… |
| 4 | `amd-radeon.md#1` | 0.0161 | 部署成功后，在运行目录下的 run/telemetry.json 中读取 con… |
| 5 | `edge-embedding.md#0` | 0.0159 | # 端侧向量化与知识库检索 ## 为什么要端侧推理 在浏览器里跑 embeddi… |

> `_score` 是 RRF 分数。单路排名第 1 得 1/60≈0.0167，两路都第 1 得 2/60≈0.0333，与表中数值一致，可反推融合确实发生在数据库内。
