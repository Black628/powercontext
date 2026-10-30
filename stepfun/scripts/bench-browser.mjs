// 浏览器侧实测：用 CDP 驱动真实 Chrome，跑完「导入 → 向量化 → 写入 seekdb → 混合检索」，
// 采集页面自报的分段耗时、后端与 dtype，并截图存档。
//
// 用法：
//   node scripts/bench-browser.mjs --label wasm --query "怎么在 AMD 上部署 seekdb"
//   CHROME_FLAGS="--headless=new --enable-unsafe-webgpu" node scripts/bench-browser.mjs --label webgpu
//
// 依赖：本机装有 Chrome，且 `npm start` 已在 --url 上监听。
import { spawn } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const argv = process.argv.slice(2);
const arg = (name, dflt) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
};

const label = arg('label', 'wasm');
const url = arg('url', 'http://127.0.0.1:7860/');
const query = arg('query', '怎么在 AMD 上部署 seekdb');
const port = Number(arg('cdp-port', '9333'));
const outJson = resolve(arg('out', `docs/bench_browser_${label}.json`));
const shotPath = resolve(arg('shot', `docs/screenshots/${label}.png`));
const headful = argv.includes('--headful');
const freshProfile = argv.includes('--fresh');
const doReset = argv.includes('--reset');
const repeat = Number(arg('repeat', '5'));
const profileDir = resolve('.tmp', `chrome-kb-${label}`);
const chromePath =
  process.env.CHROME_PATH || 'C:/Users/WZB/AppData/Local/Google/Chrome/Application/chrome.exe';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const flags = [
  `--remote-debugging-port=${port}`,
  '--user-data-dir=' + profileDir,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  '--hide-scrollbars',
  '--window-size=1440,1100',
];
if (!headful) flags.push('--headless=new');
if (process.env.CHROME_FLAGS) flags.push(...process.env.CHROME_FLAGS.split(' ').filter(Boolean));

// --fresh：删掉浏览器 profile（连带 Cache Storage 里的模型），测冷启动下载+加载
if (freshProfile) await rm(profileDir, { recursive: true, force: true });

const chrome = spawn(chromePath, [...flags, 'about:blank'], { stdio: 'ignore' });

let ws;
let seq = 0;
const pending = new Map();
const events = [];

function send(method, params = {}, timeout = 30000) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((res, rej) => {
    pending.set(id, { res, rej });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        rej(new Error(`${method} timeout`));
      }
    }, timeout);
  });
}

async function evaluate(expression) {
  const r = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' + (r.result?.description || ''));
  return r.result?.value;
}

async function waitFor(fn, { timeout = 600000, interval = 1000, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = await evaluate(`(${fn.toString()})()`);
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`等待超时：${what}`);
    await sleep(interval);
  }
}

const logText = `() => document.getElementById('log').textContent`;

async function main() {
  // 找到调试目标
  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(300);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find((t) => t.type === 'page');
    } catch {}
  }
  if (!target) throw new Error('连不上 Chrome 调试端口');

  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', () => rej(new Error('CDP 连接失败')), { once: true });
  });
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result);
    } else if (msg.method === 'Runtime.consoleAPICalled' || msg.method === 'Log.entryAdded') {
      events.push(msg);
    }
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1100,
    deviceScaleFactor: 1,
    mobile: false,
  });

  const t0 = Date.now();
  if (doReset) {
    const r = await fetch(new URL('/api/reset', url), { method: 'POST' });
    const body = await r.json().catch(() => ({}));
    console.log('reset collection:', JSON.stringify(body));
  }

  // --no-webgpu：在页面脚本执行前抹掉 navigator.gpu，用来验证"运行环境不支持 WebGPU 时自动降级 WASM"。
  const emulatedNoWebgpu = argv.includes('--no-webgpu');
  if (emulatedNoWebgpu) {
    await send('Page.addScriptToEvaluateOnNewDocument', {
      source: "Object.defineProperty(navigator, 'gpu', { get: () => undefined, configurable: true });",
    });
  }

  await send('Page.navigate', { url });

  const ua = await evaluate('navigator.userAgent');
  const webgpuExposed = await evaluate('!!navigator.gpu');

  // 等模型加载完（window.__kb().ready 为 true）
  const ready = await waitFor(
    function () {
      const kb = window.__kb && window.__kb();
      return kb && kb.ready ? `${kb.backend}/${kb.dtype}` : '';
    },
    { timeout: 900000, what: '模型就绪' }
  );
  const loadDoneAt = Date.now();

  const stage = { modelLoadedAt: loadDoneAt, ready };

  // 导入示例语料
  const beforeImport = await evaluate(logText);
  const tImport = Date.now();
  await evaluate("document.getElementById('btn-sample').click()");
  await waitFor(
    function () {
      return /导入完成|导入失败/.test(document.getElementById('log').textContent);
    },
    { timeout: 900000, what: '导入完成' }
  );
  const importMs = Date.now() - tImport;

  // 检索 5 次（页面内部对 5 次取样取中位数）
  // 检索：勾上「重复 5 次」，页面内部对每次采样取中位数
  const repeatCheckbox = await evaluate(
    "(() => { const c = document.getElementById('repeat'); if (c && !c.checked) c.click(); return c && c.checked; })()"
  );
  await evaluate(`(() => {
    const q = document.getElementById('query');
    q.value = ${JSON.stringify(query)};
  })()`);
  const tSearch = Date.now();
  await evaluate("document.getElementById('btn-search').click()");
  await waitFor(
    function () {
      return /命中|检索失败/.test(document.getElementById('search-status').textContent);
    },
    { timeout: 300000, what: '检索完成' }
  );
  const searchWallMs = Date.now() - tSearch;

  await sleep(500);
  const dom = await evaluate(`(() => {
    const t = (id) => document.getElementById(id)?.textContent?.trim() || '';
    const rows = [...document.querySelectorAll('#results li')].map((li) => ({
      head: li.querySelector('.head')?.textContent?.trim() || '',
      src: li.querySelector('.src')?.textContent?.trim() || '',
      doc: (li.querySelector('.doc')?.textContent || '').slice(0, 120),
    }));
    return {
      backend: t('badge-backend'), model: t('badge-model'), db: t('badge-db'),
      load: t('m-load'), embed: t('m-embed'), write: t('m-write'),
      qembed: t('m-qembed'), dbMs: t('m-db'), e2e: t('m-e2e'),
      importStatus: t('import-status'), searchStatus: t('search-status'),
      collection: t('collection-info'), rows,
      log: document.getElementById('log').textContent.split('\\n').filter(Boolean).slice(-14),
    };
  })()`);

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  await mkdir(resolve(shotPath, '..'), { recursive: true });
  await writeFile(shotPath, Buffer.from(shot.data, 'base64'));

  const kb = await evaluate('window.__kb ? window.__kb() : null');

  const report = {
    label,
    when: new Date().toISOString(),
    url,
    query,
    ua,
    webgpuExposed,
    emulatedNoWebgpu,
    headful,
    chromeFlags: flags,
    repeatCheckbox,
    repeatRequested: repeat,
    freshProfile,
    resetCollection: doReset,
    modelLoadedMs: stage.modelLoadedAt - t0,
    importWallMs: importMs,
    searchWallMs,
    kb,
    dom,
    consoleErrors: events
      .filter((e) => e.method === 'Log.entryAdded' && e.params?.entry?.level === 'error')
      .map((e) => e.params.entry.text)
      .slice(0, 20),
  };

  await mkdir(resolve(outJson, '..'), { recursive: true });
  await writeFile(outJson, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ label, backend: dom.backend, load: dom.load, e2e: dom.e2e, db: dom.dbMs, rows: dom.rows.length, outJson }, null, 2));
}

try {
  await main();
} catch (err) {
  console.error('FAILED:', err.message);
  try {
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    await mkdir(resolve(shotPath, '..'), { recursive: true });
    await writeFile(shotPath.replace(/\.png$/, '_fail.png'), Buffer.from(shot.data, 'base64'));
  } catch {}
  process.exitCode = 1;
} finally {
  try {
    ws?.close();
  } catch {}
  chrome.kill();
}
