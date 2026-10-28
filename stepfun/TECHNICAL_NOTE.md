# 技术说明：Qwen embedding 与 seekdb 原生混合检索

## 目标与边界

本项目把 embedding 和检索职责明确分开。Node API 负责调用 Qwen embedding 服务，并把向量、原文和元数据写入 seekdb；浏览器只负责文件读取、交互和结果展示。全文检索、向量召回和融合排名均留在 seekdb 内完成。

## 数据流

```text
.txt / .md
    │ 浏览器读取
    ▼
规范化与重叠分块（700 字符，100 字符 overlap）
    │
    ▼
Node API + Qwen OpenAI-compatible API
qwen3.7-text-embedding，1024 维
    │
    ▼
Node API 参数校验
    │
    ▼
seekdb collection.upsert()
    │
    ▼
seekdb collection.refresh_index()
```

查询使用同一 Worker 和同一模型生成向量。Node API 随后执行一次：

```ts
collection.hybridSearch({
  query: { whereDocument: { $contains: queryText } },
  knn: { queryEmbeddings: queryVector },
  rank: { rrf: { rankWindowSize: 50, rankConstant: 60 } },
  nResults,
});
```

应用层不分别执行关键词检索和向量检索，也不合并两组结果。返回顺序就是 seekdb RRF 的最终顺序。

## 端侧推理策略

模型固定为 `qwen3.7-text-embedding`。Node API 通过 OpenAI 兼容的 `/embeddings` 接口向 Qwen 发送批量文本，返回向量后再写入 seekdb。API 密钥只存在本机 `.env`，不会下发到浏览器。

## 增量导入

每个文件使用 `SHA-256(filename + content)` 的前 96 位作为稳定指纹，文本块 ID 为 `fingerprint:chunkIndex`。同名同内容文件再次导入时 ID 不变，因此 `upsert` 会更新已有记录。前端按 16 个文本块批量推理与写入，服务端上限为 64，防止单请求占用过多内存。

每批写入后显式调用 `refresh_index()`，保证刚导入的向量可立即检索。UI 也提供手动刷新入口。

## seekdb Schema

collection 同时启用：

- `FulltextIndexConfig("ik", { ik_mode: "smart" })`
- 1024 维 HNSW vector index；距离沿用 `seekdb@1.4.0` 的默认 cosine
- `embeddingFunction: null`

显式设置 `embeddingFunction: null` 是必要的职责声明：向量由 Qwen API 生成，seekdb 不应安装或调用默认 embedding 包。

## 性能采集

浏览器使用 `performance.now()`，服务端也返回数据库操作的阶段耗时。采集项包括：

- 文件读取和文本分块
- 文档 embedding
- Qwen API 请求
- seekdb 写入
- `refresh_index()`
- 查询 embedding
- `hybridSearch()`

界面展示最近六项数据，并可导出包含运行后端、降级原因、数据库目标、总耗时和完整明细的 JSON。服务端耗时只覆盖对应 seekdb 调用，不混入浏览器网络往返时间。

## 风险控制

- API 严格验证 collection 名、批量大小、文档长度和 1024 维有限数值向量。
- 模型 ID、精度和维度集中定义，避免前后端参数漂移。
- 原文只发送到本机 Node API；页面不直接调用远程 embedding API。
- Qwen 配置缺失或请求失败会在页面显示错误；密钥不会经过浏览器。
