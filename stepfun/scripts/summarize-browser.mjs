// 把 docs/bench_browser_*.json 汇总成一张 markdown 表（供文档引用）。
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';

const dir = 'docs';
const files = (await readdir(dir)).filter((f) => /^bench_browser_.*\.json$/.test(f)).sort();
const rows = [];
for (const f of files) {
  const j = JSON.parse(await readFile(join(dir, f), 'utf8'));
  const m = j.kb?.metrics || {};
  const imp = m.import || {};
  const se = m.search || {};
  rows.push({
    file: f,
    label: j.label,
    backend: `${j.kb?.backend || '?'} / ${j.kb?.dtype || '?'}`,
    load: j.dom?.load || '—',
    importWall: j.importWallMs,
    embedMs: imp.embedMs,
    docs: imp.docs,
    throughput: imp.embedMs ? imp.docs / (imp.embedMs / 1000) : NaN,
    writeMs: imp.writeMs,
    refreshMs: imp.refreshMs,
    qEmbed: se.qEmbedMs,
    db: se.dbMs,
    e2e: se.e2eMs,
    hits: se.hits,
    repeat: se.repeat,
    cold: j.freshProfile,
    adapter: j.kb?.adapter || '',
  });
}

const num = (v, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '—');
console.log('| 运行 | 后端 / 精度 | 模型加载 | 分片 | 向量化 | 吞吐 | 写入 | 索引刷新 | 查询向量化 | seekdb 检索 | 端到端 | 命中 |');
console.log('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |');
for (const r of rows) {
  console.log(
    `| ${r.label}${r.cold ? '（冷启动）' : '（热）'} | ${r.backend} | ${r.load} | ${r.docs ?? '—'} 片 | ` +
      `${num(r.embedMs, 0)} ms | ${num(r.throughput, 1)} 片/s | ${num(r.writeMs, 0)} ms | ${num(r.refreshMs, 0)} ms | ` +
      `${num(r.qEmbed)} ms | ${num(r.db)} ms | ${num(r.e2e)} ms | ${r.hits ?? '—'} |`
  );
}
console.log('\n原始数据：' + rows.map((r) => r.file).join('、'));
