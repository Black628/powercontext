// 联通性自检：验证「后端 → seekdb 写入 → hybridSearch 检索」链路是否打通。
// 注意：这里用**随机向量**，只验证链路与时延，不验证检索质量（质量必须在浏览器里用真模型跑）。
const BASE = process.env.BASE || 'http://127.0.0.1:7860';

function randUnitVec(dim) {
  const v = Array.from({ length: dim }, () => Math.random() * 2 - 1);
  const norm = Math.hypot(...v) || 1;
  return v.map((x) => x / norm);
}

async function jsonFetch(path, init) {
  const resp = await fetch(`${BASE}${path}`, init);
  const text = await resp.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Error(`${path} 返回非 JSON（HTTP ${resp.status}）：${text.slice(0, 200)}`);
  }
  if (!resp.ok || body.ok === false) {
    throw new Error(`${path} 失败（HTTP ${resp.status}）：${body.error || text.slice(0, 200)}`);
  }
  return body;
}

const main = async () => {
  const cfg = await jsonFetch('/api/config');
  console.log(`配置：collection=${cfg.collection} dim=${cfg.dimension}`);

  const health = await jsonFetch('/api/health');
  console.log(`seekdb：${health.seekdb.host}:${health.seekdb.port}/${health.seekdb.database}，当前 ${health.count ?? '?'} 条`);

  const stamp = Date.now();
  const rows = [
    { id: `smoke-a-${stamp}`, document: 'seekdb 支持向量检索与全文检索', metadata: { source: 'smoke' } },
    { id: `smoke-b-${stamp}`, document: '全文索引使用 ik 分词器处理中文', metadata: { source: 'smoke' } },
  ].map((r) => ({ ...r, embedding: randUnitVec(cfg.dimension) }));

  const ingest = await jsonFetch('/api/ingest', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ rows }),
  });
  console.log(`写入：${ingest.inserted} 条，write=${ingest.writeMs?.toFixed(1)}ms refresh=${ingest.refreshMs?.toFixed(1)}ms，总数 ${ingest.total ?? '?'}`);

  const search = await jsonFetch('/api/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ vector: randUnitVec(cfg.dimension), text: '全文索引', topK: 3 }),
  });
  console.log(`检索：db=${search.dbMs?.toFixed(1)}ms，命中 ${search.results.length} 条`);
  search.results.forEach((r, i) => console.log(`  ${i + 1}. ${r.id}  score=${r.score ?? '—'}  ${r.document.slice(0, 40)}`));

  if (search.results.length === 0) {
    console.warn('警告：检索返回 0 条。检查 refresh_index 是否执行、集合是否为空。');
    process.exitCode = 2;
  }
};

main().catch((err) => {
  console.error(`自检失败：${err.message}`);
  process.exitCode = 1;
});
