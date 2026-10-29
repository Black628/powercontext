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

const log = (msg) => {
  const el = $('log');
  el.textContent += `[${new Date().toLocaleTimeString()}] ${msg}\n`;
  el.scrollTop = el.scrollHeight;
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
    } else if (msg.type === 'download') {
      $('import-status').textContent = `模型下载中… ${msg.file || ''} ${Math.round((msg.progress || 0) * 100)}%`;
    } else if (msg.type === 'progress') {
      const pct = msg.total ? (msg.done / msg.total) * 100 : 0;
      $('import-bar').style.width = `${pct}%`;
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
    log(`浏览器 WebGPU：${gpu ? '可用' : '不可用'}，将${gpu ? '优先使用 WebGPU' : '使用 WASM'}`);

    state.worker.postMessage({
      type: 'init',
      model: cfg.modelId,
      modelHost: cfg.modelHost,
      device: gpu ? 'webgpu' : 'wasm',
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
    const names = ['seekdb-deploy.md', 'fulltext-index.md', 'amd-radeon.md'];
    const files = [];
    for (const name of names) {
      const resp = await fetch(`/data/corpus/${name}`);
      if (!resp.ok) continue;
      const text = await resp.text();
      files.push(new File([text], name, { type: 'text/markdown' }));
    }
    if (files.length) await importFiles(files);
    else log('示例语料加载失败');
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

boot();
