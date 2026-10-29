// 启动耗时测量：从 spawn `npm start` 到 /api/health 首次返回 ok 的墙钟时间。
// 用法：node scripts/bench-startup.mjs   （重复 3 次取中位数）
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const OUT = join(ROOT, 'docs', 'bench_startup.json');
const PORT = Number(process.env.PORT || 7860);
const RUNS = Number(process.env.RUNS || 3);

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function healthOk() {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/api/health`);
    const j = await r.json();
    return r.ok && j.ok === true;
  } catch {
    return false;
  }
}

async function oneRun() {
  const t0 = performance.now();
  const child = spawn(process.execPath, ['server/index.mjs'], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(PORT) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let out = '';
  child.stdout.on('data', (d) => (out += d.toString()));
  child.stderr.on('data', (d) => (out += d.toString()));
  let ready = null;
  for (let i = 0; i < 400; i++) {
    if (await healthOk()) {
      ready = performance.now() - t0;
      break;
    }
    await wait(25);
  }
  // 记录首行日志时间点（服务监听成功）；随后关掉进程
  child.kill();
  await wait(200);
  return { ms: ready === null ? null : Number(ready.toFixed(0)), log: out.trim() };
}

const runs = [];
for (let i = 0; i < RUNS; i++) {
  const r = await oneRun();
  runs.push(r);
  console.log(`run ${i + 1}: ${r.ms} ms`);
  if (i === 0) console.log(r.log);
  await wait(500);
}
const values = runs.map((r) => r.ms).filter((x) => x !== null);
const median = values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(OUT, JSON.stringify({ measuredAt: new Date().toISOString(), runs: RUNS, values, medianMs: median }, null, 2), 'utf8');
console.log(`中位数 ${median} ms（${values.length} 次）→ ${OUT}`);
process.exit(0);
