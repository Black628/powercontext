// 附加观测：同一查询向量的「混合检索」vs「纯向量检索」在 seekdb 内的耗时与结果差异。
// 目的：说明 dbMs 的构成，避免把混合检索延迟误读成数据规模相关。
//
// 用法（先 npm start）：node scripts/bench-search-modes.mjs
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const BASE = process.env.BASE || 'http://127.0.0.1:7860';
const REPS = Number(process.env.REPS || 20);
const OUT = join(ROOT, 'docs', 'bench_search_modes.json');
const Q = process.env.QUERY || '怎么在 AMD 平台上部署 seekdb';

const { pipeline } = await import('@huggingface/transformers');
const ex = await pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', { dtype: process.env.DTYPE || 'q8', device: 'cpu' });
const [vector] = (await ex([Q], { pooling: 'mean', normalize: true })).tolist();

const post = async (body) => {
  const t0 = performance.now();
  const r = await fetch(`${BASE}/api/search`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  return { json: j, wallMs: Number((performance.now() - t0).toFixed(2)) };
};

const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

const modes = {};
for (const mode of ['hybrid', 'vectorOnly']) {
  const db = [];
  const wall = [];
  let top = null;
  for (let i = 0; i < REPS; i++) {
    const body = mode === 'hybrid' ? { vector, text: Q, topK: 5 } : { vector, topK: 5 };
    const { json, wallMs } = await post(body);
    db.push(json.dbMs);
    wall.push(wallMs);
    if (i === 0) top = json.results.map((r) => ({ id: r.id, score: r.score }));
  }
  modes[mode] = {
    samples: REPS,
    dbMedianMs: Number(median(db).toFixed(2)),
    dbMinMs: Number(Math.min(...db).toFixed(2)),
    dbMaxMs: Number(Math.max(...db).toFixed(2)),
    wallMedianMs: Number(median(wall).toFixed(2)),
    top5: top,
  };
  console.log(`${mode}: db 中位数 ${modes[mode].dbMedianMs} ms（${modes[mode].dbMinMs}–${modes[mode].dbMaxMs}），top1=${top[0]?.id}`);
}

await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(
  OUT,
  JSON.stringify({ measuredAt: new Date().toISOString(), query: Q, reps: REPS, modes }, null, 2),
  'utf8'
);
console.log(`写入 ${OUT}`);
process.exit(0);
