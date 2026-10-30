// seekdb 访问层：唯一与数据库打交道的地方。
// 关键约束：这里 **不做任何向量计算**，只负责把浏览器算好的向量写进去、
// 并把浏览器算好的查询向量交给 seekdb 做混合检索（RRF 由数据库完成）。
import { config } from './config.mjs';

let clientPromise = null;
let collectionPromise = null;
// 实际连上的端口（config.seekdb.port 可能被自动探测改写），供 /api/health 暴露
export const resolved = { port: null };

// 两类"缓存失效"都靠重建再重试一次解决：
//  1) seekdb 侧因为空闲/重启把 mysql 连接关掉；
//  2) 集合被**另一个进程**删掉（例如页面开着时别人跑了 bench 脚本的 --reset，或另开了服务），
//     本进程缓存的 collection 句柄指向已不存在的物理表，会一直报 Table ... doesn't exist。
// 评测时这两种情况都很常见，不能靠重启服务解决。
const CACHE_STALE =
  /closed state|PROTOCOL_CONNECTION_LOST|Connection lost|ECONNRESET|ER_CLIENT_INTERACTION_TIMEOUT|doesn'?t exist|does not exist|table not found/i;
async function withReconnect(fn) {
  try {
    return await fn();
  } catch (err) {
    if (!CACHE_STALE.test(err?.message || '')) throw err;
    clientPromise = null;
    collectionPromise = null;
    return await fn(); // 清缓存后重试一次：重连 + 集合不存在则按需重建
  }
}

async function importSdk() {
  const sdk = await import('seekdb');
  if (!sdk.SeekdbClient) {
    throw new Error('seekdb SDK 未导出 SeekdbClient，请确认已 npm install seekdb@^1.4.0');
  }
  return sdk;
}

export async function getClient() {
  if (!clientPromise) {
    clientPromise = (async () => {
      const { SeekdbClient } = await importSdk();
      const { host, user, password, database } = config.seekdb;
      const candidates = [...new Set(config.seekdb.probePorts)];
      let lastErr = null;
      for (const port of candidates) {
        const client = new SeekdbClient({ host, port, user, password, database });
        try {
          // 真正握手一次，确认端口上确实是可用的 seekdb（而不只是端口开着）
          await client.execute('select 1 as ok');
          resolved.port = port;
          config.seekdb.port = port;
          return client;
        } catch (err) {
          lastErr = err;
        }
      }
      throw new Error(
        `seekdb 连接失败（尝试端口 ${candidates.join('/')}）：${lastErr?.message || lastErr}`
      );
    })().catch((err) => {
      clientPromise = null; // 失败后允许下次重试
      throw err;
    });
  }
  return clientPromise;
}

// 建表：显式指定维度 + cosine，并关闭服务端 embeddingFunction
// （赛题禁止服务端向量化，向量一律来自浏览器）
async function buildSchema() {
  try {
    const { Schema, VectorIndexConfig } = await import('seekdb');
    if (Schema && VectorIndexConfig) {
      return new Schema({
        vectorIndex: new VectorIndexConfig({
          hnsw: { dimension: config.dimension, distance: 'cosine' },
        }),
        // fulltextIndex 不指定时使用默认 IK 分词器
      });
    }
  } catch (_) {
    /* 老版本 SDK 没有 Schema，走默认建表 */
  }
  return undefined;
}

export async function getCollection() {
  if (!collectionPromise) {
    collectionPromise = (async () => {
      const client = await getClient();
      const name = config.collection;

      let exists = false;
      try {
        // seekdb-js 1.4.0：hasCollection(name) / deleteCollection(name) 收**字符串**，
        // createCollection/getCollection 收对象。早先这里传了 { name }，
        // hasCollection 对非字符串直接返回 false（见 SDK hasCollection 实现），
        // 于是"集合已存在"被误判成"不存在"，接着 createCollection 撞上
        // sdk_collections 的唯一索引：Duplicate entry ... for key 'idx_name'。
        // 表现就是：服务重启（或清空集合）后第一次访问直接 500。
        exists = await client.hasCollection(name);
      } catch (_) {
        exists = false;
      }

      // embeddingFunction: null —— 显式声明"本集合不注册任何服务端嵌入函数"。
      // 不传这个参数时 SDK 会默认挂 default-embed，等于把向量化搬到服务端，
      // 既违反赛题要求，也会因为没装 @seekdb/default-embed 直接报错。
      // 老版本 SDK 若不认这个字段，再退回不带它的写法。
      if (exists) {
        try {
          return await client.getCollection({ name, embeddingFunction: null });
        } catch (e1) {
          try {
            return await client.getCollection({ name });
          } catch (e2) {
            // 元数据在、表不在（例如上一次进程在 drop 表之后、删元数据之前被打断）。
            // 这种半残状态不自动修复的话，服务永远起不来；清掉元数据后重建同一个集合。
            if (/table not found/i.test(e2?.message || '')) {
              try {
                await client.deleteCollection(name);
              } catch (_) {
                /* 表本来就不在，删不掉也无所谓 */
              }
              exists = false;
            } else {
              throw e2;
            }
          }
        }
      }

      if (!exists) {
        const schema = await buildSchema();
        const attempts = [
          { name, schema, embeddingFunction: null },
          { name, schema },
        ];
        let lastErr = null;
        for (const opts of attempts) {
          try {
            return await client.createCollection(opts);
          } catch (err) {
            lastErr = err;
          }
        }
        throw lastErr;
      }
    })().catch((err) => {
      collectionPromise = null; // 失败后允许下次重试
      throw err;
    });
  }
  return collectionPromise;
}

/**
 * 写入文档。rows: [{ id, document, embedding: number[], metadata?: object }]
 * 写完后调用 refresh_index()，保证 HNSW 异步索引对检索可见。
 */
export async function ingest(rows) {
  return withReconnect(() => ingestOnce(rows));
}

async function ingestOnce(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('没有可写入的文档');
  }
  for (const row of rows) {
    if (!row || !row.id || typeof row.document !== 'string') {
      throw new Error('文档缺少 id/document 字段');
    }
    if (!Array.isArray(row.embedding) || row.embedding.length !== config.dimension) {
      throw new Error(
        `文档 ${row.id} 的向量维度不正确：期望 ${config.dimension}，实际 ${row.embedding?.length}`
      );
    }
  }

  const collection = await getCollection();
  const t0 = performance.now();
  await collection.add({
    ids: rows.map((r) => String(r.id)),
    documents: rows.map((r) => r.document),
    embeddings: rows.map((r) => r.embedding),
    metadatas: rows.map((r) => r.metadata ?? {}),
  });
  // 异步向量索引：不刷新可能查不到刚写入的数据
  let refreshMs = 0;
  if (typeof collection.refresh_index === 'function') {
    const t1 = performance.now();
    await collection.refresh_index();
    refreshMs = performance.now() - t1;
  }

  return {
    inserted: rows.length,
    writeMs: performance.now() - t0 - refreshMs,
    refreshMs,
  };
}

/**
 * 混合检索：全文（IK 分词）+ 向量，由 seekdb 内部 RRF 融合。
 * vector 来自浏览器；text 为原始查询串（不是向量）。
 */
export async function search(params) {
  return withReconnect(() => searchOnce(params));
}

async function searchOnce({ vector, text, topK }) {
  if (!Array.isArray(vector) || vector.length !== config.dimension) {
    throw new Error(`查询向量维度不正确：期望 ${config.dimension}`);
  }
  const collection = await getCollection();
  const nResults = Number(topK) > 0 ? Number(topK) : config.defaultTopK;
  const queryText = (text || '').trim();

  const req = {
    knn: {
      queryEmbeddings: vector,
      nResults: config.recallK,
    },
    rank: { rrf: {} },
    nResults,
    include: ['documents', 'metadatas', 'distances'],
  };
  // 有查询串时才启用全文这一路；纯向量查询也能走通
  if (queryText) {
    req.query = {
      whereDocument: { $contains: queryText },
      nResults: config.recallK,
    };
  }

  const t0 = performance.now();
  const raw = await collection.hybridSearch(req);
  const dbMs = performance.now() - t0;

  const ids = raw?.ids?.[0] ?? [];
  const docs = raw?.documents?.[0] ?? [];
  const metas = raw?.metadatas?.[0] ?? [];
  const dists = raw?.distances?.[0] ?? [];

  return {
    dbMs,
    results: ids.map((id, i) => ({
      id,
      document: docs[i] ?? '',
      metadata: metas[i] ?? {},
      score: typeof dists[i] === 'number' ? dists[i] : null,
    })),
  };
}

export async function stats() {
  return withReconnect(statsOnce);
}

async function statsOnce() {
  const collection = await getCollection();
  let count = null;
  let countError = null;
  // 建表瞬间偶发失败（元数据刚写、表还没就绪）时重试一次；
  // 仍失败就把原因带出去，让 /api/health 能看到，而不是界面只显示一个无从判断的 "?"。
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      count = typeof collection.count === 'function' ? await collection.count() : null;
      countError = null;
      break;
    } catch (err) {
      const msg = err?.message || String(err);
      // 表被外部删掉这类错误必须往上抛，交给 withReconnect 清缓存后重建；
      // 在这里吞掉就成了"永久 countError"，只有重启服务才能好。
      if (CACHE_STALE.test(msg)) throw err;
      countError = msg;
      if (attempt === 0) await new Promise((r) => setTimeout(r, 200));
    }
  }
  return { collection: config.collection, dimension: config.dimension, count, countError };
}

export async function reset() {
  return withReconnect(resetOnce);
}

async function resetOnce() {
  const client = await getClient();
  // 注意：seekdb-js 1.4.0 的 deleteCollection(name) 接收**字符串**；
  // 早先传 { name } 会被 validateCollectionName 拒绝并吞进 catch，
  // 结果是表没删、sdk_collections 里的元数据还在，随后 createCollection 报
  // "Duplicate entry ... for key 'idx_name'"。这里改为传字符串并把真实错误暴露出来。
  let dropped = false;
  try {
    await client.deleteCollection(config.collection);
    dropped = true;
  } catch (err) {
    const msg = err?.message || String(err);
    if (!/not found/i.test(msg)) {
      // 真的删不掉要让人看见，而不是静默留下半残状态
      collectionPromise = null;
      throw new Error(`清空集合失败：${msg}`);
    }
  }
  collectionPromise = null;
  return { collection: config.collection, dropped };
}
