// 模型体积实测：把 fp32 / fp16 / q8 三档 ONNX 权重真正下载到本地缓存，再统计字节数。
// 口径：文件系统实际字节数（不是 HF 页面标注值）。每档 dtype 单独构建一次 pipeline，
// 由 transformers.js 自己决定 dtype -> 文件名映射，并把"本次新下载的文件"记为该档归属。
//
// 用法（Windows，需要走系统代理）：
//   $env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:NODE_USE_ENV_PROXY="1"
//   node scripts/bench-model-size.mjs
import { stat, readdir, mkdir, writeFile, rm } from 'node:fs/promises';
import { dirname } from 'node:path';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const MODEL = process.env.MODEL_ID || 'Xenova/all-MiniLM-L6-v2';
const DTYPES = ['fp32', 'fp16', 'q8'];
// dtype -> transformers.js 实际使用的 ONNX 文件名（脚本会验证下载结果与该映射一致）
const EXPECTED = { fp32: 'model.onnx', fp16: 'model_fp16.onnx', q8: 'model_quantized.onnx' };
const OUT_JSON = join(ROOT, 'docs', 'bench_model_size.json');

const { pipeline, env } = await import('@huggingface/transformers');
env.allowLocalModels = false;
const CACHE = env.cacheDir;
console.log(`# model=${MODEL}\n# cacheDir=${CACHE}`);

async function walk(dir, acc = []) {
  let entries = [];
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) await walk(p, acc);
    else acc.push({ path: p, size: (await stat(p)).size });
  }
  return acc;
}

const rel = (p) => p.slice(CACHE.length);

// 先清掉缓存里的 ONNX 权重，确保每档都是"这次真的从 HF 下载下来的字节"
const pre = await walk(CACHE);
for (const f of pre) {
  if (f.path.endsWith('.onnx')) await rm(f.path, { force: true });
}
console.log(`# 已清除 ${pre.filter((f) => f.path.endsWith('.onnx')).length} 个 ONNX 缓存文件，强制重新下载`);

// 逐档测量：记录构建前后的缓存文件差集，归入该 dtype
const results = [];
for (const dtype of DTYPES) {
  const before = new Set((await walk(CACHE)).map((f) => f.path));
  console.log(`\n== dtype=${dtype} ==`);
  const t0 = performance.now();
  let pipelineOk = true;
  let error = null;
  // 网络抖动时重试；pipeline 构建成功即代表权重已落盘
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await pipeline('feature-extraction', MODEL, { dtype, device: 'cpu' });
      pipelineOk = true;
      error = null;
      break;
    } catch (err) {
      pipelineOk = false;
      error = err?.message || String(err);
      console.log(`  第 ${attempt} 次 pipeline 构建失败：${error}`);
      await new Promise((r) => setTimeout(r, 1000 * attempt));
    }
  }

  const onnxDir = dirname(
    (await walk(CACHE)).find((f) => /config\.json$/.test(f.path))?.path ||
      join(CACHE, MODEL.replace('/', '\\'), 'config.json')
  );
  const targetPath = join(onnxDir, 'onnx', EXPECTED[dtype]);
  let fallbackDownloaded = null;
  try {
    await stat(targetPath);
  } catch {
    // pipeline 没拿到权重时，退化为直接按 HF 的 resolve 路径下载（体积口径不变：真实字节数）
    const host = process.env.MODEL_HOST || 'https://huggingface.co';
    const url = `${host}/${MODEL}/resolve/main/onnx/${EXPECTED[dtype]}`;
    console.log(`  权重缺失，直接下载：${url}`);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`下载失败 HTTP ${resp.status}`);
    const buf = Buffer.from(await resp.arrayBuffer());
    await mkdir(dirname(targetPath), { recursive: true });
    await writeFile(targetPath, buf);
    fallbackDownloaded = buf.length;
  }
  const loadMs = performance.now() - t0;

  const after = await walk(CACHE);
  const fresh = after.filter((f) => !before.has(f.path));
  const freshOnnx = fresh.filter((f) => f.path.endsWith('.onnx'));
  const downloaded = freshOnnx[0] ? rel(freshOnnx[0].path).split(/[\\/]/).pop() : null;
  const expected = EXPECTED[dtype];
  if (downloaded && downloaded !== expected) {
    console.log(`! 警告：dtype=${dtype} 实际下载 ${downloaded}，与预期映射 ${expected} 不一致`);
  }
  const target = after.find((f) => rel(f.path).split(/[\\/]/).pop() === expected);
  const onnxBytes = target ? target.size : 0;
  console.log(
    `新增文件: ${fresh.map((f) => `${rel(f.path)} ${f.size}B`).join(' | ') || '(无，命中缓存)'}`
  );
  results.push({
    dtype,
    pipelineOk,
    error,
    loadMs: Number(loadMs.toFixed(1)),
    downloadedFile: downloaded,
    expectedFile: expected,
    mappingMatches: downloaded ? downloaded === expected : null,
    newFiles: fresh.map((f) => ({ file: rel(f.path), bytes: f.size })),
    fallbackDownloadedBytes: fallbackDownloaded,
    onnxFile: expected,
    onnxBytes,
  });
}

// 侧车文件（tokenizer/config），三档共用，不计入压缩比
const all = await walk(CACHE);
const sidecar = all.filter((f) => !f.path.endsWith('.onnx'));
const sidecarBytes = sidecar.reduce((a, b) => a + b.size, 0);

const baseBytes = results.find((r) => r.dtype === 'fp32')?.onnxBytes || 0;
for (const r of results) {
  r.baselineOnnxBytes = baseBytes;
  r.compressionVsFp32 = r.onnxBytes ? Number((baseBytes / r.onnxBytes).toFixed(3)) : null;
  r.savingPercent = r.onnxBytes ? Number(((1 - r.onnxBytes / baseBytes) * 100).toFixed(1)) : null;
  r.modelTotalWithSidecar = r.onnxBytes ? r.onnxBytes + sidecarBytes : null;
}

await mkdir(join(ROOT, 'docs'), { recursive: true });
await writeFile(
  OUT_JSON,
  JSON.stringify(
    {
      model: MODEL,
      cacheDir: CACHE,
      measuredAt: new Date().toISOString(),
      sidecarFiles: sidecar.map((f) => ({ file: rel(f.path), bytes: f.size })),
      sidecarBytes,
      results,
    },
    null,
    2
  ),
  'utf8'
);

console.log('\n| 档位 | ONNX 文件 | 字节 | MiB | 压缩比 | 相比 FP32 节省 |');
console.log('| --- | --- | --- | --- | --- | --- |');
for (const r of results) {
  console.log(
    `| ${r.dtype} | ${r.onnxFile} | ${r.onnxBytes} | ${(r.onnxBytes / 1048576).toFixed(2)} | ${r.compressionVsFp32}× | ${r.savingPercent}% |`
  );
}
console.log(`\n侧车文件（tokenizer/config/vocab）合计 ${sidecarBytes} B`);
console.log(`写入 ${OUT_JSON}`);
