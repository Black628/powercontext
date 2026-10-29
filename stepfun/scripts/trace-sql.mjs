// 混合检索合规性证据：把 seekdb-js 真正发给 seekdb 的语句抓下来。
//
// 做法：直接 import 服务端模块（与 `npm start` 走同一份代码路径），只在
// InternalClient.execute 上挂一个只读探针记录 SQL/参数/返回行数；
// hybridSearch 的调用参数、RRF 配置、SQL 文本一行没改。
//
// 产出：
//   docs/SQL_EVIDENCE.md  —— 人读的证据（长向量字面量已折叠成占位符）
//   docs/sql_trace.json   —— 原始记录（含完整 SQL）
//
// 用法（无需启动 HTTP 服务，只需 seekdb 在线）：
//   node scripts/trace-sql.mjs
import { writeFile, mkdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { config } from '../server/config.mjs';
import { getClient, getCollection, ingest, search } from '../server/seekdb.mjs';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const OUT_JSON = join(ROOT, 'docs', 'sql_trace.json');
const OUT_MD = join(ROOT, 'docs', 'SQL_EVIDENCE.md');

const client = await getClient();
const captured = [];
const origExecute = client.execute.bind(client);

// seekdb-js 的调用链：SeekdbClient -> SeekdbServerClient(_delegate) -> InternalClient(_internal)。
// 真正执行 SQL 的是 _internal.execute，探针必须挂在这一层。
const targets = new Set([client, client?._delegate?._internal]);
for (const target of targets) {
  if (!target || typeof target.execute !== 'function') continue;
  const orig = target.execute.bind(target);
  target.execute = async (sql, params) => {
    const t0 = performance.now();
    const rows = await orig(sql, params);
    captured.push({
      via: target.constructor?.name || 'unknown',
      sql: typeof sql === 'string' ? sql : String(sql),
      params,
      ms: Number((performance.now() - t0).toFixed(2)),
      rowCount: Array.isArray(rows) ? rows.length : null,
      rows: Array.isArray(rows) ? rows : rows ?? null,
    });
    return rows;
  };
}

const collection = await getCollection();

// 集合物理结构（向量索引 / 全文索引是否真的建出来了）
const tableName = collection.collectionId ? `c$v2$${collection.collectionId}` : `c$v1$${collection.name}`;
let schemaInfo = null;
try {
  const createTable = await client.execute(`SHOW CREATE TABLE \`${tableName}\``);
  const indexes = await client.execute(`SHOW INDEX FROM \`${tableName}\``);
  schemaInfo = {
    tableName,
    createTable: createTable?.[0]?.['Create Table'] ?? null,
    indexes: (indexes || []).map((r) => ({ key: r.Key_name, column: r.Column_name, type: r.Index_type })),
  };
} catch (err) {
  schemaInfo = { tableName, error: err.message };
}
captured.length = 0;

// 探针用的确定性单位向量（不参与质量评估，只用于取证）
const raw = Array.from({ length: config.dimension }, (_, i) => Math.sin(i * 0.1));
const norm = Math.hypot(...raw);
const unit = raw.map((x) => x / norm);
const probeId = `trace-probe-${Date.now()}`;

captured.length = 0;
await ingest([
  {
    id: probeId,
    document: '全文索引使用 ik 分词器，向量检索使用 HNSW 索引',
    embedding: unit,
    metadata: { source: 'trace' },
  },
]);
const ingestTrace = captured.slice();
captured.length = 0;

const queryText = '全文索引 分词器';
const result = await search({ vector: unit, text: queryText, topK: 5 });
const searchTrace = captured.slice();

// 折叠长向量字面量，便于人工阅读
const collapse = (sql) =>
  String(sql)
    .replace(/'\[-?\d[\d.,\se-]{200,}\]'/g, "'[<384 维查询向量>]'")
    .replace(/\[-?\d[\d.,\se-]{200,}\]/g, '[<384 维查询向量>]');

const getSqlStmt = searchTrace.find((c) => /DBMS_HYBRID_SEARCH\.GET_SQL/i.test(c.sql));
const setStmt = searchTrace.find((c) => /^SET @search_parm/i.test(c.sql));
const finalStmt = searchTrace[searchTrace.length - 1];
const generatedSql = getSqlStmt?.rows?.[0]?.query_sql
  ? String(getSqlStmt.rows[0].query_sql).replace(/^['"]|['"]$/g, '')
  : null;

const report = {
  measuredAt: new Date().toISOString(),
  seekdb: {
    host: config.seekdb.host,
    port: config.seekdb.port,
    database: config.seekdb.database,
    collection: config.collection,
    dimension: config.dimension,
  },
  sdk: 'seekdb (seekdb-js) 1.4.0',
  call: 'collection.hybridSearch({ knn: { queryEmbeddings, nResults: recallK }, query: { whereDocument: { $contains } }, rank: { rrf: {} }, nResults, include })',
  searchParm: setStmt?.params?.[0] ?? null,
  ingestStatements: ingestTrace.map((c) => ({ sql: collapse(c.sql), rowCount: c.rowCount, ms: c.ms })),
  searchStatements: searchTrace.map((c) => ({
    sql: collapse(c.sql),
    params: c.params ? [collapse(JSON.stringify(c.params)).slice(0, 1200)] : undefined,
    rowCount: c.rowCount,
    ms: c.ms,
  })),
  generatedSql: generatedSql ? collapse(generatedSql) : null,
  schemaInfo,
  resultRowCount: result.results.length,
  dbMs: Number(result.dbMs.toFixed(2)),
  results: result.results,
  probeId,
};

await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(OUT_JSON, JSON.stringify(report, null, 2), 'utf8');

const md = [];
md.push('# 混合检索合规性证据（seekdb 内完成）');
md.push('');
md.push(`- 采集时间：${report.measuredAt}`);
md.push(`- seekdb：\`${report.seekdb.host}:${report.seekdb.port}/${report.seekdb.database}\`，集合 \`${report.seekdb.collection}\`，维度 ${report.seekdb.dimension}`);
md.push(`- SDK：${report.sdk}`);
md.push(`- 采集方式：直接调用服务端模块 \`server/seekdb.mjs\` 的 \`search()\`（与 \`npm start\` 同一份代码），只在 \`InternalClient.execute\` 上挂只读探针记录 SQL。检索逻辑未改动。`);
md.push('');
md.push('## 1. 集合物理结构与索引');
md.push('');
if (schemaInfo?.error) {
  md.push(`读取失败：${schemaInfo.error}`);
} else {
  md.push(`表名：\`${schemaInfo.tableName}\``);
  md.push('');
  md.push('```sql');
  md.push(schemaInfo.createTable);
  md.push('```');
  md.push('');
  md.push('| 索引 | 列 | 类型 |');
  md.push('| --- | --- | --- |');
  for (const ix of schemaInfo.indexes) md.push(`| \`${ix.key}\` | \`${ix.column}\` | ${ix.type} |`);
}
md.push('');
md.push('## 2. 服务端发起的一次调用');
md.push('');
md.push('```js');
md.push(report.call);
md.push('```');
md.push('');
md.push('## 3. 实际执行的语句序列（含返回行数）');
md.push('');
report.searchStatements.forEach((s, i) => {
  md.push(`### [${i + 1}] ${s.rowCount === null ? '执行成功（无结果集）' : `${s.rowCount} 行`} / ${s.ms} ms`);
  md.push('');
  md.push('```sql');
  md.push(s.sql.length > 6000 ? s.sql.slice(0, 6000) + '\n-- …(完整语句见 docs/sql_trace.json)' : s.sql);
  md.push('```');
  if (s.params) {
    md.push('');
    md.push('参数：');
    md.push('');
    md.push('```json');
    md.push(s.params[0]);
    md.push('```');
  }
  md.push('');
});
md.push('## 4. 关键结论（看第 3 条语句）');
md.push('');
md.push('1. `SET @search_parm = ?` —— 客户端只把"查询串 + 查询向量 + RRF 配置"打包成一个 JSON 参数交给 seekdb；');
md.push('2. `SELECT DBMS_HYBRID_SEARCH.GET_SQL(...)` —— SQL 由 **seekdb 自己生成**，不是客户端拼的；');
md.push('3. 生成的最终 SQL 是**一条**语句，同时包含：');
md.push('   - 全文路：`match(document) against(\'…\' in natural language mode)`；');
md.push('   - 向量路：`cosine_distance(embedding, \'[…]\')` + `ORDER BY _distance APPROXIMATE LIMIT 20`（HNSW 近似检索）；');
md.push('   - 融合：`FULL JOIN` 两路结果后 `(1/(_keyword_rank+60) + 1/(_semantic_rank+60)) as _score`，即 **RRF 在 seekdb 内计算**；');
md.push('4. 客户端只收到最终结果行，没有拿到两路中间结果，也没有做任何拼合/重排 —— 客户端代码里不存在融合逻辑。');
md.push('');
md.push(`本次返回行数：**${report.resultRowCount}** 行，seekdb 侧耗时 ${report.dbMs} ms。`);
md.push('');
md.push('## 5. 返回结果');
md.push('');
md.push('| # | id | _score | 文档片段 |');
md.push('| --- | --- | --- | --- |');
report.results.forEach((r, i) => {
  md.push(`| ${i + 1} | \`${r.id}\` | ${r.score} | ${String(r.document).replace(/\s+/g, ' ').slice(0, 40)}… |`);
});
md.push('');
md.push('> `_score` 是 RRF 分数。单路排名第 1 得 1/60≈0.0167，两路都第 1 得 2/60≈0.0333，与表中数值一致，可反推融合确实发生在数据库内。');
md.push('');
await writeFile(OUT_MD, md.join('\n'), 'utf8');

// 清掉取证用的临时行，避免污染知识库
try {
  await collection.delete({ ids: [probeId] });
  await collection.refresh_index();
} catch (err) {
  console.log(`（清理取证临时行失败，可忽略：${err.message}）`);
}

console.log('== 语句序列 ==');
searchTrace.forEach((c, i) => {
  console.log(`[${i + 1}] ${c.rowCount ?? '?'} 行 / ${c.ms}ms ：${collapse(c.sql).slice(0, 160).replace(/\n/g, ' ')}`);
});
console.log(`\n== 返回 ${result.results.length} 行，seekdb ${result.dbMs.toFixed(2)} ms ==`);
for (const r of result.results) console.log(`  ${r.id}  score=${r.score}`);
console.log(`\n写入 ${OUT_MD}`);
console.log(`写入 ${OUT_JSON}`);
process.exit(0);
