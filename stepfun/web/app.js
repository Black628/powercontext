// 主线程：UI 与流程编排。向量计算全部在 worker（端侧）完成。
const $ = (id) => document.getElementById(id);

const state = {
  cfg: null,
  worker: null,
  ready: false,
  backend: null, // webgpu | wasm
  dtype: null,
  importing: false,
  docs: [],
  metrics: {},
};

// 允许用 ?device=wasm / ?device=webgpu 固定推理后端，方便做对照实验（默认自动选择）
const params = new URLSearchParams(location.search);
const forcedDevice = params.get('device');

const log = (msg) => {
  const el = $('log');
  el.textContent += `[${new Date().toLocaleTimeString()}] ${msg}\n`;
  el.scrollTop = el.scrollHeight;
};

// transformers.js 不同版本给的 progress 可能是 0~1 也可能是 0~100，统一成百分数
const pct = (v) => {
  const n = Number(v) || 0;
  return Math.max(0, Math.min(100, n <= 1 ? n * 100 : n));
};

const fmt = (ms) => (Number.isFinite(ms) ? `${ms.toFixed(1)} ms` : '—');
const fmtSec = (ms) => (Number.isFinite(ms) ? `${(ms / 1000).toFixed(2)} s` : '—');
const median = (arr) => {
  if (!arr.length) return NaN;
  const s = [...arr].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};

// ---------------------------------------------------------------- worker ---
function initWorker() {
  const worker = new Worker('/web/worker.js', { type: 'module' });

  worker.onmessage = (event) => {
    const msg = event.data || {};
    if (msg.type === 'ready') {
      state.ready = true;
      state.backend = msg.device;
      state.dtype = msg.dtype;
      const isGpu = msg.device === 'webgpu';
      const badge = $('badge-backend');
      badge.textContent = `后端：${isGpu ? 'WebGPU' : 'WASM'} · ${String(msg.dtype).toUpperCase()}`;
      badge.className = `badge ${isGpu ? 'badge-ok' : 'badge-warn'}`;
      $('badge-model').textContent = `模型：${msg.model}`;
      $('m-load').textContent = fmtSec(msg.loadMs);
      if (msg.fallbackFrom) {
        log(`已自动降级：${msg.fallbackFrom}`);
        badge.title = msg.fallbackFrom;
      } else if (!msg.webgpuSupported) {
        log('当前浏览器未检测到 WebGPU，已使用 WASM 后端');
      }
      log(`模型就绪：device=${msg.device} dtype=${msg.dtype} 加载 ${fmtSec(msg.loadMs)}`);
    } else if (msg.type === 'log') {
      log(msg.message);
    } else if (msg.type === 'download') {
      $('import-status').textContent = `模型下载中… ${msg.file || ''} ${Math.round(pct(msg.progress))}%`;
    } else if (msg.type === 'progress') {
      const ratio = msg.total ? (msg.done / msg.total) * 100 : 0;
      $('import-bar').style.width = `${ratio}%`;
      $('import-status').textContent = `向量化 ${msg.done}/${msg.total}`;
    } else if (msg.type === 'error') {
      log(`错误(${msg.stage})：${msg.error}`);
      $('import-status').textContent = `失败：${msg.error}`;
      state.importing = false;
    }
  };
  return worker;
}

function rpc(msg) {
  return new Promise((resolve, reject) => {
    const worker = state.worker;
    const handler = (event) => {
      const data = event.data || {};
      if (data.id !== msg.id) return;
      if (data.type === 'embedded') {
        worker.removeEventListener('message', handler);
        resolve(data);
      } else if (data.type === 'error') {
        worker.removeEventListener('message', handler);
        reject(new Error(data.error));
      }
    };
    worker.addEventListener('message', handler);
    worker.postMessage(msg);
  });
}

const nextId = () => `req_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;

// ------------------------------------------------------------------ 切片 ---
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

// ---------------------------------------------------------------- 导入 ---
async function importFiles(files) {
  if (state.importing) return;
  if (!state.ready) {
    log('模型尚未就绪，请稍候');
    return;
  }
  state.importing = true;
  $('btn-search').disabled = true;

  const chunkSize = Number($('chunk-size').value) || 420;
  const batchSize = Number($('batch-size').value) || 8;
  const importId = Date.now();

  const rows = [];
  for (const file of files) {
    const text = await file.text();
    const pieces = chunkText(text, chunkSize);
    pieces.forEach((piece, i) => {
      rows.push({
        id: `${file.name}#${i}@${importId}`,
        document: piece,
        metadata: { source: file.name, chunk: i, chars: piece.length, importId },
      });
    });
  }
  if (!rows.length) {
    log('没有解析到文本');
    state.importing = false;
    return;
  }

  $('import-status').textContent = `准备向量化 ${rows.length} 片…`;
  const t0 = performance.now();
  let embedMs = 0;
  let writeMs = 0;
  let refreshMs = 0;
  let done = 0;

  try {
    for (let i = 0; i < rows.length; i += batchSize) {
      const batch = rows.slice(i, i + batchSize);
      const res = await rpc({
        type: 'embed',
        id: nextId(),
        texts: batch.map((r) => r.document),
        batchSize,
      });
      embedMs += res.ms;

      const payload = batch.map((r, k) => ({ ...r, embedding: res.vectors[k] }));
      const resp = await fetch('/api/ingest', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ rows: payload }),
      });
      const json = await resp.json();
      if (!json.ok) throw new Error(json.error || '写入失败');
      writeMs += json.writeMs || 0;
      refreshMs += json.refreshMs || 0;
      done += batch.length;
      $('import-bar').style.width = `${(done / rows.length) * 100}%`;
      $('import-status').textContent = `已写入 ${done}/${rows.length}，当前集合 ${json.total ?? '?'} 条`;
    }

    const total = performance.now() - t0;
    state.docs.push(...rows);
    state.metrics.import = { docs: rows.length, embedMs, writeMs, refreshMs, totalMs: total, backend: state.backend, dtype: state.dtype };

    $('m-embed').textContent = `${fmtSec(embedMs)} (${(rows.length / (embedMs / 1000)).toFixed(1)} 片/秒)`;
    $('m-write').textContent = `${fmtSec(writeMs + refreshMs)}（其中刷新 ${fmtSec(refreshMs)}）`;
    $('import-status').textContent = `导入完成：${rows.length} 片，用时 ${fmtSec(total)}`;
    log(`导入完成 ${rows.length} 片 / ${files.length} 个文件，总耗时 ${fmtSec(total)}`);
    await refreshHealth();
  } catch (err) {
    log(`导入失败：${err.message}`);
    $('import-status').textContent = `导入失败：${err.message}`;
  } finally {
    state.importing = false;
    $('btn-search').disabled = false;
  }
}

// ---------------------------------------------------------------- 检索 ---
async function doSearch() {
  const query = $('query').value.trim();
  if (!query) return;
  if (!state.ready) {
    log('模型尚未就绪');
    return;
  }
  const topK = Number($('topk').value) || 5;
  const repeat = $('repeat').checked ? 5 : 1;

  $('btn-search').disabled = true;
  $('search-status').textContent = '查询向量化…';
  const e2eList = [];
  const dbList = [];
  const qEmbedList = [];
  let lastResults = [];

  try {
    for (let r = 0; r < repeat; r++) {
      const t0 = performance.now();
      const emb = await rpc({ type: 'embed', id: nextId(), texts: [query], batchSize: 1 });
      const qEmbedMs = emb.ms;
      $('search-status').textContent = 'seekdb 混合检索中…';

      const resp = await fetch('/api/search', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ vector: emb.vectors[0], text: query, topK }),
      });
      const json = await resp.json();
      if (!json.ok) throw new Error(json.error || '检索失败');
      const e2e = performance.now() - t0;

      qEmbedList.push(qEmbedMs);
      dbList.push(json.dbMs);
      e2eList.push(e2e);
      lastResults = json.results;
    }

    renderResults(lastResults);

    const db = median(dbList);
    const qe = median(qEmbedList);
    const e2e = median(e2eList);
    $('m-qembed').textContent = fmt(qe);
    $('m-db').textContent = fmt(db);
    $('m-e2e').textContent = fmt(e2e);
    state.metrics.search = {
      query, topK, repeat,
      backend: state.backend, dtype: state.dtype,
      qEmbedMs: qe, dbMs: db, e2eMs: e2e,
      samples: { qEmbed: qEmbedList, db: dbList, e2e: e2eList },
      hits: lastResults.length,
    };
    $('search-status').textContent = `命中 ${lastResults.length} 条 · ${repeat > 1 ? `中位数（${repeat} 次）` : '单次'} · 端到端 ${fmt(e2e)}`;
    log(`检索 "${query}"：db=${fmt(db)} 端到端=${fmt(e2e)} 命中=${lastResults.length}`);
  } catch (err) {
    log(`检索失败：${err.message}`);
    $('search-status').textContent = `检索失败：${err.message}`;
  } finally {
    $('btn-search').disabled = false;
  }
}

function renderResults(results) {
  const el = $('results');
  el.innerHTML = '';
  if (!results?.length) {
    el.innerHTML = '<li><span class="mini">没有命中结果</span></li>';
    return;
  }
  results.forEach((r, i) => {
    const li = document.createElement('li');
    const score = r.score === null || r.score === undefined ? '—' : Number(r.score).toFixed(4);
    const src = r.metadata?.source ? `${r.metadata.source}#${r.metadata.chunk ?? '?'}` : r.id;
    li.innerHTML = `
      <div class="head"><span>#${i + 1}</span><span>score ${score}</span></div>
      <p class="doc"></p>
      <div class="src">${src}</div>`;
    li.querySelector('.doc').textContent = r.document || '';
    el.appendChild(li);
  });
}

// ------------------------------------------------------------ 状态与工具 ---
async function refreshHealth() {
  const badge = $('badge-db');
  try {
    const resp = await fetch('/api/health');
    const json = await resp.json();
    badge.textContent = `seekdb：${json.collection} · ${json.count ?? '?'} 条`;
    badge.className = 'badge badge-ok';
    $('collection-info').textContent = `集合：${json.collection}（${json.dimension} 维）· 当前 ${json.count ?? '?'} 条`;
  } catch (err) {
    badge.textContent = 'seekdb：未连接';
    badge.className = 'badge badge-err';
    log(`seekdb 健康检查失败：${err.message}`);
  }
}

async function boot() {
  state.worker = initWorker();

  try {
    const cfg = await (await fetch('/api/config')).json();
    state.cfg = cfg;
    $('collection-info').textContent = `集合：${cfg.collection}（${cfg.dimension} 维）`;
    await refreshHealth();

    const gpu = !!navigator.gpu;
    const device = forcedDevice === 'wasm' || forcedDevice === 'webgpu' ? forcedDevice : gpu ? 'webgpu' : 'wasm';
    log(
      `浏览器 WebGPU：${gpu ? '可用' : '不可用'}；推理后端=${device}` +
        (forcedDevice ? `（由 URL 参数固定：?device=${forcedDevice}）` : gpu ? '（自动优先 WebGPU）' : '（自动降级 WASM）')
    );

    if (gpu) {
      try {
        const adapter = await navigator.gpu.requestAdapter();
        const info = adapter?.info || {};
        state.adapter = adapter ? `${info.vendor || '?'} / ${info.architecture || '?'} / ${info.description || ''}`.trim() : 'no-adapter';
        log(`WebGPU 适配器：${state.adapter}`);
      } catch (err) {
        state.adapter = `adapter 获取失败：${err.message}`;
        log(`WebGPU 适配器获取失败：${err.message}`);
      }
    }

    state.worker.postMessage({
      type: 'init',
      model: cfg.modelId,
      modelHost: cfg.modelHost,
      device,
    });
  } catch (err) {
    log(`初始化失败：${err.message}`);
  }
}

// ---------------------------------------------------------------- 交互 ---
$('dropzone').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', (e) => importFiles([...e.target.files]));

['dragenter', 'dragover'].forEach((t) =>
  $('dropzone').addEventListener(t, (e) => {
    e.preventDefault();
    $('dropzone').classList.add('over');
  })
);
['dragleave', 'drop'].forEach((t) =>
  $('dropzone').addEventListener(t, (e) => {
    e.preventDefault();
    $('dropzone').classList.remove('over');
  })
);
$('dropzone').addEventListener('drop', (e) => importFiles([...e.dataTransfer.files]));

$('btn-sample').addEventListener('click', async (e) => {
  e.stopPropagation();
  try {
    const names = ['edge-embedding.md', 'fulltext-index.md', 'amd-radeon.md'];
    const files = [];
    const missing = [];
    for (const name of names) {
      const resp = await fetch(`/data/corpus/${name}`);
      if (!resp.ok) {
        // 静默跳过会让"示例语料只导了一半"看起来像 bug，这里把缺的文件写进日志
        missing.push(`${name}(${resp.status})`);
        continue;
      }
      const text = await resp.text();
      files.push(new File([text], name, { type: 'text/markdown' }));
    }
    if (missing.length) log(`示例语料缺失，已跳过：${missing.join(', ')}`);
    if (files.length) await importFiles(files);
    else log('示例语料加载失败：/data/corpus 下没有可读文件');
  } catch (err) {
    log(`示例语料加载失败：${err.message}`);
  }
});

$('btn-search').addEventListener('click', doSearch);
$('query').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch();
});

$('btn-reset').addEventListener('click', async () => {
  if (!confirm('确认清空当前 collection？')) return;
  try {
    const json = await (await fetch('/api/reset', { method: 'POST' })).json();
    log(`已清空集合 ${json.collection}`);
    state.docs = [];
    await refreshHealth();
  } catch (err) {
    log(`清空失败：${err.message}`);
  }
});

$('btn-export').addEventListener('click', async () => {
  const payload = {
    exportedAt: new Date().toISOString(),
    ua: navigator.userAgent,
    webgpu: !!navigator.gpu,
    backend: state.backend,
    dtype: state.dtype,
    metrics: state.metrics,
  };
  const text = JSON.stringify(payload, null, 2);
  try {
    await navigator.clipboard.writeText(text);
    $('export-status').textContent = '已复制到剪贴板';
  } catch (_) {
    console.log(text);
    $('export-status').textContent = '剪贴板不可用，已打印到控制台';
  }
});

// 给自动化测试/截图用的只读快照（不含任何推理逻辑）
window.__kb = () => ({
  ready: state.ready,
  backend: state.backend,
  dtype: state.dtype,
  adapter: state.adapter ?? null,
  forcedDevice: forcedDevice ?? null,
  docs: state.docs.length,
  metrics: state.metrics,
  status: {
    import: $('import-status').textContent,
    search: $('search-status').textContent,
  },
});

boot();
