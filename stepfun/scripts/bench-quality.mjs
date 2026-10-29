// 检索质量与吞吐实测（Node 侧，同模型同权重）。
//
// 重要口径说明：
//   * 这里的数字来自 **Node + onnxruntime-node (CPU EP)**，不是浏览器 WebGPU/WASM。
//     浏览器侧数值需在页面上用同一查询集复测，差异原因见 docs/BENCHMARK.md。
//   * 检索质量用"稠密向量精确 top-5"评估（纯 embedding 质量，不含 seekdb 的 RRF 融合），
//     这样量化前后对比只反映模型本身。
//
// 用法：
//   $env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:NODE_USE_ENV_PROXY="1"
//   node scripts/bench-quality.mjs            # 默认 fp32 / q8 / fp16
//   node scripts/bench-quality.mjs fp32 q8    # 只跑指定档位
import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const MODEL = process.env.MODEL_ID || 'Xenova/all-MiniLM-L6-v2';
const CHUNK_SIZE = Number(process.env.CHUNK_SIZE || 200);
const BATCH_SIZE = Number(process.env.BATCH_SIZE || 8);
const QUERY_REPEATS = Number(process.env.QUERY_REPEATS || 15);
const THROUGHPUT_PASSES = Number(process.env.THROUGHPUT_PASSES || 5);
const DTYPES = process.argv.slice(2).length ? process.argv.slice(2) : ['fp32', 'q8', 'fp16'];
const OUT_JSON = join(ROOT, 'docs', process.env.BENCH_OUT || 'bench_quality.json');

// ---------------------------------------------------------------- 切片 ---
// 与 web/app.js 的 chunkText 完全一致的实现（同一份切片逻辑，保证可复现）
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

// 标注口径：按**内容子串**标注，而不是按模型输出标注 —— 与切片长度无关，可复现。
// grade 2 = 该切片就是答案所在；grade 1 = 部分相关（同主题但不直接回答）。
const QUERIES = [
  { q: '怎么在 AMD 平台上部署 seekdb', g2: ['docker run -d --name seekdb'], g1: ['申请算力'] },
  { q: '全文索引用哪个分词器处理中文', g2: ['ik 面向中文', 'WITH PARSER ik'], g1: ['beng 面向英文'] },
  { q: '如何获取部署实例的遥测 ID', g2: ['telemetry.json'], g1: ['run/telemetry'] },
  { q: '向量检索和全文检索怎么融合', g2: ['RRF'], g1: ['混合检索'] },
  { q: '为什么写入后马上查不到', g2: ['刷新索引这一步容易漏', '异步维护'], g1: [] },
  { q: 'INT8 和 FP16 怎么选', g2: ['INT8（q8）体积'], g1: ['FP16 体积约减半'] },
  { q: 'WebGPU 不可用会怎样', g2: ['优雅降级'], g1: ['navigator.gpu'] },
  { q: '增量导入后索引怎么刷新', g2: ['刷新索引'], g1: ['写入数据库'] },
  { q: 'seekdb 的最小资源要求', g2: ['至少 1 核 CPU'], g1: [] },
  { q: '批处理为什么更快', g2: ['批处理比逐条推理快'], g1: [] },
];

// -------------------------------------------------------------- 语料装载 ---
const corpusDir = join(ROOT, 'data', 'corpus');
const files = (await readdir(corpusDir)).filter((f) => f.endsWith('.md')).sort();
const chunks = [];
for (const f of files) {
  const text = await readFile(join(corpusDir, f), 'utf8');
  chunkText(text, CHUNK_SIZE).forEach((piece, i) => {
    chunks.push({ id: `${f}#${i}`, source: f, chunk: i, chars: piece.length, text: piece });
  });
}

// 把查询的标注映射到切片
for (const item of QUERIES) {
  const rel = {};
  for (const c of chunks) {
    if (item.g2.some((m) => c.text.includes(m))) rel[c.id] = 2;
    else if (item.g1.some((m) => c.text.includes(m))) rel[c.id] = 1;
  }
  item.relevance = rel;
  item.relevantCount = Object.keys(rel).length;
  item.grade2Count = Object.values(rel).filter((g) => g >= 2).length;
}

console.log(`# corpus: ${files.length} 篇 → ${chunks.length} 片（chunkSize=${CHUNK_SIZE}, batch=${BATCH_SIZE}）`);
for (const f of files) {
  const n = chunks.filter((c) => c.source === f).length;
  console.log(`#   ${f}: ${n} 片`);
}
const badQueries = QUERIES.filter((q) => q.relevantCount === 0);
if (badQueries.length) {
  console.log(`# 警告：${badQueries.length} 条查询没有标注到任何相关切片（标注子串与语料不匹配）`);
  for (const q of badQueries) console.log(`#   ${q.q}`);
}

const median = (arr) => {
  const s = [...arr].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};
const dot = (a, b) => {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
};

// ---------------------------------------------------------------- 主循环 ---
const { pipeline } = await import('@huggingface/transformers');
const results = [];

for (const dtype of DTYPES) {
  console.log(`\n=== dtype=${dtype} ===`);
  const loadStart = performance.now();
  let extractor;
  try {
    extractor = await pipeline('feature-extraction', MODEL, { dtype, device: 'cpu' });
  } catch (err) {
    console.log(`pipeline 构建失败，跳过：${err?.message || err}`);
    results.push({ dtype, error: String(err?.message || err) });
    continue;
  }
  const loadMs = performance.now() - loadStart;

  const encode = async (texts) => {
    const out = await extractor(texts, { pooling: 'mean', normalize: true });
    return out.tolist();
  };

  // 预热（不计入统计）
  await encode(['warmup']);

  // 文档向量化吞吐：跑 THROUGHPUT_PASSES 轮，每轮 batch=BATCH_SIZE
  const passStats = [];
  let chunkVectors = null;
  for (let pass = 0; pass < THROUGHPUT_PASSES; pass++) {
    const t0 = performance.now();
    const vecs = [];
    for (let i = 0; i < chunks.length; i += BATCH_SIZE) {
      const batch = chunks.slice(i, i + BATCH_SIZE).map((c) => c.text);
      const rows = await encode(batch);
      for (const r of rows) vecs.push(r);
    }
    const ms = performance.now() - t0;
    passStats.push({ ms: Number(ms.toFixed(1)), docsPerSec: Number(((chunks.length / ms) * 1000).toFixed(2)) });
    chunkVectors = vecs;
  }

  // 检索质量：稠密余弦 top-5
  const K = 5;
  const perQuery = [];
  for (const item of QUERIES) {
    const [qv] = await encode([item.q]);
    const scored = chunks
      .map((c, i) => ({ id: c.id, score: dot(qv, chunkVectors[i]) }))
      .sort((a, b) => b.score - a.score);
    const topK = scored.slice(0, K);

    const grades = topK.map((s) => item.relevance[s.id] || 0);
    const dcg = grades.reduce((acc, g, i) => acc + (Math.pow(2, g) - 1) / Math.log2(i + 2), 0);
    const ideal = Object.values(item.relevance).sort((a, b) => b - a).slice(0, K);
    const idcg = ideal.reduce((acc, g, i) => acc + (Math.pow(2, g) - 1) / Math.log2(i + 2), 0);
    const hits = topK.filter((s) => (item.relevance[s.id] || 0) > 0).length;
    const strictHits = topK.filter((s) => (item.relevance[s.id] || 0) >= 2).length;
    const firstHitRank = topK.findIndex((s) => (item.relevance[s.id] || 0) > 0);
    perQuery.push({
      query: item.q,
      relevantCount: item.relevantCount,
      grade2Count: item.grade2Count,
      retrievedTop5: topK.map((s) => ({ id: s.id, score: Number(s.score.toFixed(4)), grade: item.relevance[s.id] || 0 })),
      hitsInTop5: hits,
      // 严格召回：答案所在切片（grade=2）被召回的比例
      strictRecallAt5: item.grade2Count ? strictHits / Math.min(item.grade2Count, K) : null,
      // 宽松召回：任何 grade>=1 的相关切片被召回的比例
      recallAt5: item.relevantCount ? hits / Math.min(item.relevantCount, K) : null,
      hit: hits > 0 ? 1 : 0,
      mrr: firstHitRank >= 0 ? Number((1 / (firstHitRank + 1)).toFixed(3)) : 0,
      ndcgAt5: idcg > 0 ? dcg / idcg : 0,
    });
  }

  // 查询向量化延迟：单条文本，重复 QUERY_REPEATS 次
  const qLat = [];
  for (let i = 0; i < QUERY_REPEATS; i++) {
    const q = QUERIES[i % QUERIES.length].q;
    const t0 = performance.now();
    await encode([q]);
    qLat.push(Number((performance.now() - t0).toFixed(2)));
  }

  const meanRecall = perQuery.reduce((a, b) => a + (b.recallAt5 ?? 0), 0) / perQuery.length;
  const meanStrictRecall = perQuery.reduce((a, b) => a + (b.strictRecallAt5 ?? 0), 0) / perQuery.length;
  const hitRate = perQuery.reduce((a, b) => a + b.hit, 0) / perQuery.length;
  const meanNdcg = perQuery.reduce((a, b) => a + b.ndcgAt5, 0) / perQuery.length;
  const meanMrr = perQuery.reduce((a, b) => a + b.mrr, 0) / perQuery.length;
  const throughputMedian = median(passStats.map((p) => p.docsPerSec));
  const queryMedian = median(qLat);

  results.push({
    dtype,
    loadMs: Number(loadMs.toFixed(1)),
    chunkCount: chunks.length,
    batchSize: BATCH_SIZE,
    throughputPasses: passStats,
    throughputMedianDocsPerSec: Number(throughputMedian.toFixed(2)),
    queryLatencySamples: qLat,
    queryLatencyMedianMs: Number(queryMedian.toFixed(2)),
    queryLatencyP95Ms: Number([...qLat].sort((a, b) => a - b)[Math.floor(0.95 * (qLat.length - 1))].toFixed(2)),
    // BENCHMARK.md 定义：命中相关切片的查询数 ÷ 查询总数
    recallAt5: Number(hitRate.toFixed(4)),
    // 更严格口径：相关切片被召回的微平均比例
    strictRecallAt5: Number(meanStrictRecall.toFixed(4)),
    looseRecallAt5: Number(meanRecall.toFixed(4)),
    ndcgAt5: Number(meanNdcg.toFixed(4)),
    mrrAt5: Number(meanMrr.toFixed(4)),
    perQuery,
  });
  console.log(
    `Recall@5(命中率)=${hitRate.toFixed(3)} Recall@5(严格)=${meanStrictRecall.toFixed(3)} ` +
      `nDCG@5=${meanNdcg.toFixed(3)} MRR=${meanMrr.toFixed(3)} ` +
      `吞吐=${throughputMedian.toFixed(1)} docs/s 查询向量化中位数=${queryMedian.toFixed(1)} ms`
  );
}

const base = results.find((r) => r.dtype === 'fp32' && !r.error);
for (const r of results) {
  if (!base || r.error) continue;
  r.deltaVsFp32 = {
    recallAt5: Number((r.recallAt5 - base.recallAt5).toFixed(4)),
    strictRecallAt5: Number((r.strictRecallAt5 - base.strictRecallAt5).toFixed(4)),
    ndcgAt5: Number((r.ndcgAt5 - base.ndcgAt5).toFixed(4)),
    throughputMedianDocsPerSec: Number(
      (r.throughputMedianDocsPerSec - base.throughputMedianDocsPerSec).toFixed(2)
    ),
  };
}

await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(
  OUT_JSON,
  JSON.stringify(
    {
      model: MODEL,
      runtime: 'Node ' + process.version + ' / onnxruntime-node CPU EP',
      measuredAt: new Date().toISOString(),
      chunkSize: CHUNK_SIZE,
      batchSize: BATCH_SIZE,
      queryRepeats: QUERY_REPEATS,
      queryCount: QUERIES.length,
      chunks,
      queries: QUERIES.map((q) => ({ q: q.q, relevantCount: q.relevantCount, relevance: q.relevance })),
      results,
    },
    null,
    2
  ),
  'utf8'
);

console.log('\n| 档位 | 模型加载 | Recall@5 | 严格Recall@5 | nDCG@5 | MRR | 吞吐 (docs/s) | 查询向量化中位数 (ms) |');
console.log('| --- | --- | --- | --- | --- | --- | --- | --- |');
for (const r of results) {
  if (r.error) {
    console.log(`| ${r.dtype} | 失败：${r.error} | — | — | — | — | — | — |`);
    continue;
  }
  console.log(
    `| ${r.dtype} | ${(r.loadMs / 1000).toFixed(2)} s | ${r.recallAt5.toFixed(3)} | ${r.strictRecallAt5.toFixed(3)} | ${r.ndcgAt5.toFixed(3)} | ${r.mrrAt5.toFixed(3)} | ${r.throughputMedianDocsPerSec.toFixed(1)} | ${r.queryLatencyMedianMs.toFixed(1)} |`
  );
}
console.log(`\n写入 ${OUT_JSON}`);
