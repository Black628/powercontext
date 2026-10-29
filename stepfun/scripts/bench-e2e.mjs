// 端到端与分阶段计时：真实语料 → 真实向量 → 真实 seekdb。
//
// 分工：
//   * 向量化：Node + onnxruntime-node（CPU EP），与浏览器同一模型同一权重，
//     但**不是**浏览器数字，报告里必须分开标注。
//   * 写入 / 索引刷新 / 混合检索：走真实 HTTP 服务 + 真实 seekdb（服务端计时）。
//
// 用法（先 npm start，另开一个终端）：
//   $env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:NODE_USE_ENV_PROXY="1"
//   node scripts/bench-e2e.mjs            # 默认 dtype=q8, chunkSize=420（与 web/app.js 默认一致）
//   $env:DTYPE="fp32"; node scripts/bench-e2e.mjs
import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const BASE = process.env.BASE || 'http://127.0.0.1:7860';
const MODEL = process.env.MODEL_ID || 'Xenova/all-MiniLM-L6-v2';
const DTYPE = process.env.DTYPE || 'q8';
const CHUNK_SIZE = Number(process.env.CHUNK_SIZE || 420); // web/app.js 默认 420
const BATCH_SIZE = Number(process.env.BATCH_SIZE || 8); // web/app.js 默认 8
const TOP_K = Number(process.env.TOP_K || 5);
const REPS = Number(process.env.REPS || 5);
const OUT_JSON = join(ROOT, 'docs', process.env.BENCH_OUT || 'bench_e2e.json');

const QUERIES = [
  '怎么在 AMD 平台上部署 seekdb',
  '全文索引用哪个分词器处理中文',
  '如何获取部署实例的遥测 ID',
  '向量检索和全文检索怎么融合',
  '为什么写入后马上查不到',
  'INT8 和 FP16 怎么选',
  'WebGPU 不可用会怎样',
  '增量导入后索引怎么刷新',
  'seekdb 的最小资源要求',
  '批处理为什么更快',
];

// 与 web/app.js 的 chunkText 一致
function chunkText(text, size) {
  const clean = text.replace(/\r\n/g, '\n').trim();
  if (clean.length <= size) return [clean];
  const chunks = [];
  const paragraphs = clean.split(/\n{2,}/);
  let buf = '';
  for (const p of paragraphs) {
    if ((buf + '\n\n' + p).length > size && buf) {
      chunks.push(buf.trim());
      buf = p;
    } else {
      buf = buf ? `${buf}\n\n${p}` : p;
    }
    while (buf.length > size) {
      chunks.push(buf.slice(0, size).trim());
      buf = buf.slice(size);
    }
  }
  if (buf.trim()) chunks.push(buf.trim());
  return chunks.filter((c) => c.length > 0);
}

const median = (arr) => {
  const s = [...arr].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};
const pct = (arr, p) => {
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))];
};
const round = (n, d = 2) => Number(Number(n).toFixed(d));

async function jsonFetch(path, init) {
  const resp = await fetch(`${BASE}${path}`, init);
  const text = await resp.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Error(`${path} 返回非 JSON（HTTP ${resp.status}）：${text.slice(0, 200)}`);
  }
  if (!resp.ok || body.ok === false) throw new Error(`${path} 失败（HTTP ${resp.status}）：${body.error || text.slice(0, 200)}`);
  return body;
}

// ------------------------------------------------------------------ 语料 ---
const corpusDir = join(ROOT, 'data', 'corpus');
const files = (await readdir(corpusDir)).filter((f) => f.endsWith('.md')).sort();
const rows = [];
for (const f of files) {
  const text = await readFile(join(corpusDir, f), 'utf8');
  chunkText(text, CHUNK_SIZE).forEach((piece, i) => {
    rows.push({ id: `${f}#${i}`, document: piece, metadata: { source: f, chunk: i, chars: piece.length } });
  });
}
console.log(`# 语料 ${files.length} 篇 → ${rows.length} 片（chunkSize=${CHUNK_SIZE}, batch=${BATCH_SIZE}, dtype=${DTYPE}）`);

// ------------------------------------------------------------------ 模型 ---
const { pipeline } = await import('@huggingface/transformers');
const loadStart = performance.now();
const extractor = await pipeline('feature-extraction', MODEL, { dtype: DTYPE, device: 'cpu' });
const modelLoadMs = performance.now() - loadStart;
const encode = async (texts) => (await extractor(texts, { pooling: 'mean', normalize: true })).tolist();
await encode(['warmup']);
console.log(`# 模型加载 ${modelLoadMs.toFixed(0)} ms（含缓存读取）`);

// -------------------------------------------------------------- 清空集合 ---
const reset = await jsonFetch('/api/reset', { method: 'POST' });
console.log(`# 已清空集合：${JSON.stringify(reset)}`);

// ------------------------------------------------- 文档向量化 + 写入刷新 ---
const batchStats = [];
const ingestStart = performance.now();
for (let i = 0; i < rows.length; i += BATCH_SIZE) {
  const batch = rows.slice(i, i + BATCH_SIZE);
  const t0 = performance.now();
  const vectors = await encode(batch.map((r) => r.document));
  const embedMs = performance.now() - t0;
  const resp = await jsonFetch('/api/ingest', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ rows: batch.map((r, k) => ({ ...r, embedding: vectors[k] })) }),
  });
  batchStats.push({
    batch: batch.length,
    embedMs: round(embedMs),
    writeMs: round(resp.writeMs),
    refreshMs: round(resp.refreshMs),
    total: resp.total,
  });
  console.log(
    `  batch ${i / BATCH_SIZE}: embed=${embedMs.toFixed(1)}ms write=${resp.writeMs?.toFixed(1)}ms refresh=${resp.refreshMs?.toFixed(1)}ms 总条数=${resp.total}`
  );
}
const ingestTotalMs = performance.now() - ingestStart;
const embedAll = batchStats.reduce((a, b) => a + b.embedMs, 0);
const writeAll = batchStats.reduce((a, b) => a + b.writeMs, 0);
const refreshAll = batchStats.reduce((a, b) => a + b.refreshMs, 0);
console.log(
  `# 写入阶段合计 ${ingestTotalMs.toFixed(0)} ms：向量化 ${embedAll.toFixed(0)} ms / 写入 ${writeAll.toFixed(0)} ms / refresh ${refreshAll.toFixed(0)} ms`
);

const health = await jsonFetch('/api/health');
console.log(`# /api/health：${JSON.stringify(health)}`);

// ------------------------------------------------------------ 检索计时 ---
const samples = [];
for (const q of QUERIES) {
  for (let r = 0; r < REPS; r++) {
    const t0 = performance.now();
    const [vector] = await encode([q]);
    const qEmbedMs = performance.now() - t0;

    const t1 = performance.now();
    const resp = await jsonFetch('/api/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ vector, text: q, topK: TOP_K }),
    });
    const httpMs = performance.now() - t1;

    samples.push({
      query: q,
      rep: r,
      qEmbedMs: round(qEmbedMs),
      httpMs: round(httpMs),
      dbMs: round(resp.dbMs),
      // 端到端 = 查询向量化 + 一次混合检索往返
      e2eMs: round(qEmbedMs + httpMs),
      resultCount: resp.results.length,
      top1: resp.results[0]?.id ?? null,
      top1Score: resp.results[0]?.score ?? null,
      results: r === 0 ? resp.results.map((x) => ({ id: x.id, score: x.score })) : undefined,
    });
    if (r === 0) console.log(`  ${q} → ${resp.results.length} 条，top1=${resp.results[0]?.id} score=${resp.results[0]?.score}`);
  }
}

const qEmbedAll = samples.map((s) => s.qEmbedMs);
const dbAll = samples.map((s) => s.dbMs);
const httpAll = samples.map((s) => s.httpMs);
const e2eAll = samples.map((s) => s.e2eMs);
const zeroHits = samples.filter((s) => s.resultCount === 0).length;

const summary = {
  dtype: DTYPE,
  model: MODEL,
  modelLoadMs: round(modelLoadMs),
  chunkSize: CHUNK_SIZE,
  batchSize: BATCH_SIZE,
  topK: TOP_K,
  chunkCount: rows.length,
  queryCount: QUERIES.length,
  repsPerQuery: REPS,
  sampleCount: samples.length,
  docEmbedMsTotal: round(embedAll),
  docThroughputDocsPerSec: round((rows.length / embedAll) * 1000),
  writeMsTotal: round(writeAll),
  refreshMsTotal: round(refreshAll),
  writeMedianPerBatchMs: round(median(batchStats.map((b) => b.writeMs))),
  refreshMedianPerBatchMs: round(median(batchStats.map((b) => b.refreshMs))),
  ingestTotalMs: round(ingestTotalMs),
  seekdbLatencyMedianMs: round(median(dbAll)),
  seekdbLatencyP95Ms: round(pct(dbAll, 0.95)),
  queryEmbedMedianMs: round(median(qEmbedAll)),
  httpRoundtripMedianMs: round(median(httpAll)),
  e2eMedianMs: round(median(e2eAll)),
  e2eP95Ms: round(pct(e2eAll, 0.95)),
  zeroHitSamples: zeroHits,
  collectionCount: health.count,
};

await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(
  OUT_JSON,
  JSON.stringify(
    {
      measuredAt: new Date().toISOString(),
      base: BASE,
      runtime: 'Node ' + process.version + ' / onnxruntime-node CPU EP + HTTP + seekdb',
      summary,
      batchStats,
      samples,
    },
    null,
    2
  ),
  'utf8'
);

console.log('\n| 指标 | 中位数 | P95 | 样本数 |');
console.log('| --- | --- | --- | --- |');
console.log(`| 查询向量化（Node/CPU, ${DTYPE}） | ${summary.queryEmbedMedianMs} ms | ${round(pct(qEmbedAll, 0.95))} ms | ${samples.length} |`);
console.log(`| seekdb 混合检索（服务端计时） | ${summary.seekdbLatencyMedianMs} ms | ${summary.seekdbLatencyP95Ms} ms | ${samples.length} |`);
console.log(`| HTTP 往返（本机） | ${summary.httpRoundtripMedianMs} ms | ${round(pct(httpAll, 0.95))} ms | ${samples.length} |`);
console.log(`| 端到端（向量化 + 检索往返） | ${summary.e2eMedianMs} ms | ${summary.e2eP95Ms} ms | ${samples.length} |`);
console.log(`| 文档向量化吞吐 | ${summary.docThroughputDocsPerSec} docs/s | — | ${rows.length} 片 |`);
console.log(`| 写入+refresh（${batchStats.length} 批） | write ${summary.writeMedianPerBatchMs} / refresh ${summary.refreshMedianPerBatchMs} ms | — | ${batchStats.length} |`);
console.log(`\n零命中样本：${zeroHits}/${samples.length}；集合条数：${health.count}`);
console.log(`写入 ${OUT_JSON}`);
// onnxruntime-node 在 Windows 上退出时偶发 libuv 断言，显式退出避免把成功运行变成崩溃退出码
process.exit(zeroHits > 0 ? 2 : 0);
