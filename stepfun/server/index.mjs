// 极简 HTTP 服务：托管前端静态资源 + 三个 API。
// 设计上有意保持"薄"：不解析文档、不切块、不算向量 —— 这些都在浏览器里完成。
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { config } from './config.mjs';
import { getCollection, ingest, reset, search, stats } from './seekdb.mjs';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const WEB_DIR = join(ROOT, 'web');
const VENDOR_DIR = join(ROOT, 'node_modules');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.onnx': 'application/octet-stream',
  '.md': 'text/markdown; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.map': 'application/json; charset=utf-8',
};

const MAX_BODY = 64 * 1024 * 1024; // 一次导入的向量体积可能较大

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  });
  res.end(body);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error('请求体过大');
    chunks.push(chunk);
  }
  if (size === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function serveFile(res, filePath) {
  try {
    const info = await stat(filePath);
    if (!info.isFile()) return false;
    const data = await readFile(filePath);
    res.writeHead(200, {
      'content-type': MIME[extname(filePath).toLowerCase()] || 'application/octet-stream',
      'content-length': data.length,
      'cache-control': 'no-cache',
    });
    res.end(data);
    return true;
  } catch (_) {
    return false;
  }
}

// 只允许访问白名单目录，防止路径穿越
function safeJoin(baseDir, urlPath) {
  const clean = normalize(decodeURIComponent(urlPath)).replace(/^(\.\.[/\\])+/, '');
  const full = join(baseDir, clean);
  return full.startsWith(baseDir) ? full : null;
}

const routes = {
  'GET /api/health': async () => {
    const s = await stats();
    return {
      ok: true,
      seekdb: { host: config.seekdb.host, port: config.seekdb.port, database: config.seekdb.database },
      ...s,
    };
  },

  'GET /api/config': async () => ({
    dimension: config.dimension,
    collection: config.collection,
    defaultTopK: config.defaultTopK,
    recallK: config.recallK,
    modelId: config.modelId,
    modelHost: config.modelHost,
  }),

  'POST /api/ingest': async (req) => {
    const body = await readJson(req);
    const rows = body.rows ?? body;
    const result = await ingest(rows);
    const s = await stats();
    return { ok: true, ...result, total: s.count };
  },

  'POST /api/search': async (req) => {
    const body = await readJson(req);
    const { results, dbMs } = await search({
      vector: body.vector,
      text: body.text,
      topK: body.topK,
    });
    return { ok: true, dbMs, results };
  },

  'POST /api/reset': async () => ({ ok: true, ...(await reset()) }),
};

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = url.pathname;
  const key = `${req.method} ${pathname}`;

  try {
    if (routes[key]) {
      const payload = await routes[key](req);
      return sendJson(res, 200, payload);
    }
    if (pathname.startsWith('/api/')) {
      return sendJson(res, 404, { ok: false, error: `未知接口 ${key}` });
    }

    // 前端资源
    if (pathname === '/' || pathname === '/index.html') {
      if (await serveFile(res, join(WEB_DIR, 'index.html'))) return;
    }
    if (pathname.startsWith('/web/')) {
      const f = safeJoin(WEB_DIR, pathname.slice('/web/'.length));
      if (f && (await serveFile(res, f))) return;
    }
    // transformers.js 从本地 node_modules 提供，避免依赖外部 CDN
    if (pathname.startsWith('/vendor/')) {
      const f = safeJoin(VENDOR_DIR, pathname.slice('/vendor/'.length));
      if (f && (await serveFile(res, f))) return;
    }
    // 抽样语料（前端"加载示例语料"按钮用）
    if (pathname.startsWith('/data/')) {
      const f = safeJoin(join(ROOT, 'data'), pathname.slice('/data/'.length));
      if (f && (await serveFile(res, f))) return;
    }

    sendJson(res, 404, { ok: false, error: 'Not Found' });
  } catch (err) {
    const message = err?.message || String(err);
    console.error('[500]', key, message);
    sendJson(res, 500, { ok: false, error: message });
  }
});

server.listen(config.httpPort, () => {
  console.log(`[edge-kb] http://127.0.0.1:${config.httpPort}`);
  console.log(`[edge-kb] seekdb ${config.seekdb.host}:${config.seekdb.port} db=${config.seekdb.database} collection=${config.collection} dim=${config.dimension}`);
  // 启动即预热连接，让 /api/health 尽快反映真实状态
  getCollection().catch((e) => console.error('[edge-kb] seekdb 连接失败:', e.message));
});
