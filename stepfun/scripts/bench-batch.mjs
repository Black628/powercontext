// 批处理收益实测（Node/CPU，q8 与 fp32 各一次）：同一批文本按不同 batch size 向量化，比吞吐。
// 目的：给 TECH_REPORT「推理批处理」这一行一个真实数字，而不是"待实测"。
//
// 用法：node scripts/bench-batch.mjs
import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const MODEL = process.env.MODEL_ID || 'Xenova/all-MiniLM-L6-v2';
const BATCHES = [1, 4, 8, 16];
const DTYPES = process.env.DTYPES ? process.env.DTYPES.split(',') : ['fp32', 'q8'];
const PASSES = Number(process.env.PASSES || 5);
const OUT = join(ROOT, 'docs', process.env.BENCH_OUT || 'bench_batch.json');

const dir = join(ROOT, 'data', 'corpus');
const REPEAT = Number(process.env.REPEAT || 6); // 语料很小，重复几遍凑够稳态负载
const SLICE = Number(process.env.SLICE || 400); // 每条文本的字符数
const baseTexts = [];
for (const f of (await readdir(dir)).filter((x) => x.endsWith('.md')).sort()) {
  const text = await readFile(join(dir, f), 'utf8');
  // 用较长切片，让单条成本不至于太小，batch 差异更明显
  for (let i = 0; i < text.length; i += SLICE) baseTexts.push(text.slice(i, i + SLICE));
}
const texts = [];
for (let r = 0; r < REPEAT; r++) for (const t of baseTexts) texts.push(t);
console.log(`# ${baseTexts.length} 段语料 ×${REPEAT} = ${texts.length} 条，每档跑 ${PASSES} 轮取中位数`);

const { pipeline } = await import('@huggingface/transformers');
const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

const results = [];
for (const dtype of DTYPES) {
  const ex = await pipeline('feature-extraction', MODEL, { dtype, device: 'cpu' });
  const encode = async (t) => (await ex(t, { pooling: 'mean', normalize: true })).tolist();
  await encode(['warmup']);
  for (const batch of BATCHES) {
    const perPass = [];
    for (let p = 0; p < PASSES; p++) {
      const t0 = performance.now();
      for (let i = 0; i < texts.length; i += batch) {
        await encode(texts.slice(i, i + batch));
      }
      const ms = performance.now() - t0;
      perPass.push((texts.length / ms) * 1000);
    }
    const docsPerSec = median(perPass);
    results.push({ dtype, batch, docsPerSec: Number(docsPerSec.toFixed(2)), passes: perPass.map((x) => Number(x.toFixed(2))) });
    console.log(`  ${dtype} batch=${String(batch).padStart(2)} → ${docsPerSec.toFixed(1)} docs/s`);
  }
}

// 相对 batch=1 的加速比
for (const r of results) {
  const base = results.find((x) => x.dtype === r.dtype && x.batch === 1);
  r.speedupVsBatch1 = Number((r.docsPerSec / base.docsPerSec).toFixed(2));
}

await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(
  OUT,
  JSON.stringify({ model: MODEL, runtime: 'Node ' + process.version + ' / onnxruntime-node CPU EP', textCount: texts.length, passes: PASSES, measuredAt: new Date().toISOString(), results }, null, 2),
  'utf8'
);

console.log('\n| dtype | batch=1 | batch=4 | batch=8 | batch=16 |');
console.log('| --- | --- | --- | --- | --- |');
for (const dtype of DTYPES) {
  const cells = BATCHES.map((b) => {
    const r = results.find((x) => x.dtype === dtype && x.batch === b);
    return `${r.docsPerSec.toFixed(1)} (${r.speedupVsBatch1.toFixed(2)}×)`;
  });
  console.log(`| ${dtype} | ${cells.join(' | ')} |`);
}
console.log(`\n写入 ${OUT}`);
process.exit(0);
