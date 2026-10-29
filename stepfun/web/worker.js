// 端侧推理 Worker：transformers.js 在这里跑，主线程只负责 UI。
// 优先 WebGPU，失败自动降级 WASM；两种后端的 dtype 默认值不同，见 DEFAULT_DTYPE。
let pipe = null;
let runtime = { device: null, dtype: null, model: null, loadMs: 0, fallbackFrom: null };

const DEFAULT_DTYPE = { webgpu: 'fp16', wasm: 'q8' };

async function loadTransformers() {
  // 优先用本地 node_modules 里的副本（由后端 /vendor/ 提供），失败再走 CDN
  try {
    return await import('/vendor/@huggingface/transformers/dist/transformers.min.js');
  } catch (e) {
    const cdn = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.5.1/dist/transformers.min.js';
    return import(/* @vite-ignore */ cdn);
  }
}

async function buildPipeline({ model, dtype, device, modelHost }) {
  const mod = await loadTransformers();
  const { pipeline, env } = mod;
  if (env) {
    env.allowLocalModels = false;
    if (modelHost) env.remoteHost = modelHost;
  }
  const opts = {
    dtype,
    progress_callback: (p) => {
      if (p && p.status === 'progress' && typeof p.progress === 'number') {
        self.postMessage({ type: 'download', file: p.file, progress: p.progress, loaded: p.loaded, total: p.total });
      }
    },
  };
  if (device === 'webgpu') opts.device = 'webgpu';
  return pipeline('feature-extraction', model, opts);
}

async function init(msg) {
  const t0 = performance.now();
  const wantDevice = msg.device === 'webgpu' && !!self.navigator?.gpu ? 'webgpu' : 'wasm';
  const dtype = msg.dtype || DEFAULT_DTYPE[wantDevice];

  let fallbackFrom = null;
  try {
    pipe = await buildPipeline({ ...msg, device: wantDevice, dtype });
    runtime.device = wantDevice;
  } catch (err) {
    if (wantDevice === 'webgpu') {
      fallbackFrom = `WebGPU 初始化失败：${err?.message || err}`;
      pipe = await buildPipeline({ ...msg, device: 'wasm', dtype: DEFAULT_DTYPE.wasm });
      runtime.device = 'wasm';
    } else {
      throw err;
    }
  }

  runtime.dtype = pipe ? (msg.dtype && runtime.device === wantDevice ? msg.dtype : DEFAULT_DTYPE[runtime.device]) : null;
  runtime.model = msg.model;
  runtime.loadMs = performance.now() - t0;
  runtime.fallbackFrom = fallbackFrom;

  self.postMessage({ type: 'ready', ...runtime, webgpuSupported: !!self.navigator?.gpu });
}

async function embed(msg) {
  if (!pipe) throw new Error('模型尚未初始化');
  const texts = msg.texts || [];
  const batchSize = Math.max(1, msg.batchSize || 8);
  const vectors = [];
  const t0 = performance.now();

  for (let i = 0; i < texts.length; i += batchSize) {
    const batch = texts.slice(i, i + batchSize);
    const out = await pipe(batch, { pooling: 'mean', normalize: true });
    const rows = out.tolist();
    for (const row of rows) vectors.push(row);
    self.postMessage({ type: 'progress', id: msg.id, done: vectors.length, total: texts.length });
  }

  const ms = performance.now() - t0;
  self.postMessage({
    type: 'embedded',
    id: msg.id,
    vectors,
    ms,
    throughput: texts.length > 0 ? (texts.length / ms) * 1000 : 0,
  });
}

self.onmessage = async (event) => {
  const msg = event.data || {};
  try {
    if (msg.type === 'init') await init(msg);
    else if (msg.type === 'embed') await embed(msg);
    else if (msg.type === 'ping') self.postMessage({ type: 'pong' });
  } catch (err) {
    self.postMessage({ type: 'error', id: msg.id, stage: msg.type, error: err?.message || String(err) });
  }
};
