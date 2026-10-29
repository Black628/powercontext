# 一页技术说明

**赛题**：端侧 AI 推理性能优化与 seekdb 混合检索
**产物**：可在浏览器本地完成向量化的迷你知识库，检索由 seekdb 的 `hybridSearch` 完成

---

## 1. 模型与量化

| 项 | 内容 |
| --- | --- |
| 模型 | `Xenova/all-MiniLM-L6-v2`（ONNX 导出版，Apache-2.0，输出 384 维） |
| 来源 | Hugging Face `Xenova` 组织（`huggingface.co/Xenova/all-MiniLM-L6-v2`） |
| 运行时 | `@huggingface/transformers` v3（transformers.js），本地文件由后端 `/vendor/` 提供，不依赖外部 CDN |
| 量化方式 | 切换 ONNX 权重档位：`fp32`（基线）/ `q8`（INT8）/ `fp16`（半精度），不改任何推理代码 |

体积与压缩比（**实测后填入，填写口径见 `docs/BENCHMARK.md`**）：

| 配置 | 模型文件总体积 | 压缩比 | 检索质量（Recall@5 / nDCG@5） |
| --- | --- | --- | --- |
| FP32 基线 | 待实测 | 1.00× | 待实测 |
| INT8（q8） | 待实测 | 待实测 | 待实测 |
| FP16 | 待实测 | 待实测 | 待实测 |

> 压缩比 = 量化前体积 ÷ 量化后体积。体积按浏览器缓存里该模型的全部文件求和，口径一致才可比。

## 2. 端侧运行

- **推理位置**：全部在 `web/worker.js` 这个 Web Worker 里，用 transformers.js 对文本做 mean pooling + normalize。
  服务端只收到"向量 + 原文"，`createCollection` 显式传 `embeddingFunction: null`，从机制上排除了服务端向量化的可能。
- **后端选择**：`navigator.gpu` 存在 → `device: 'webgpu'` + `dtype: 'fp16'`；否则 → `device: 'wasm'` + `dtype: 'q8'`。
- **自动降级**：WebGPU 初始化抛错时，worker 内部以 WASM 重建 pipeline，并把降级原因回传主线程，
  顶部徽章显示 `后端：WASM · Q8`，日志里写明 `WebGPU 初始化失败：…`。降级后导入、检索全流程照常。
- **可验证**：
  - Chrome/Edge 打开 `chrome://gpu` 确认 WebGPU 状态；
  - 用 `chrome://flags` 关闭 WebGPU 或在不支持的浏览器打开，观察徽章与日志变为 WASM；
  - 两种情况下检索结果应一致（同一模型不同精度，Top-5 可能有 0~1 条差异，属正常）。

## 3. seekdb 使用

| 项 | 内容 |
| --- | --- |
| 接入方式 | `seekdb`（seekdb-js）SDK，`SeekdbClient` 服务器模式连接 `127.0.0.1:2881` |
| 集合结构 | 集合 `edge_kb`：`id` / `document`（原文切片）/ `embedding`（384 维）/ `metadata`（`source`、`chunk`、`chars`、`importId`） |
| 向量索引 | HNSW，`dimension=384`，`distance=cosine`，建集合时显式声明 |
| 全文索引 | 使用 seekdb 默认 IK 分词器（不传 `fulltextIndex` 即默认） |
| 混合检索 | `collection.hybridSearch({ knn: {queryEmbeddings}, query: {whereDocument: {$contains}}, rank: {rrf: {}}, nResults })` —— 两路召回与 RRF 融合**都在 seekdb 内完成**，客户端不发两路请求、不做拼合 |
| 增量写入 | 每次 `add()` 后立即 `refresh_index()`；文档 id 带批次号 `@<importId>`，重复导入不互相覆盖 |
| 索引刷新 | HNSW 在 seekdb V1.3.0+ 为异步维护，不刷新会出现"刚写完查不到"，本实现每次写入后同步刷新并记录刷新耗时 |

## 4. 状态与优化

**状态管理**

- 集合即知识库：换 `SEEKDB_COLLECTION` 即切库；「清空集合」走 `deleteCollection` 后重建，避免残留。
- 导入批次号隔离：同一文件重复导入生成新 id，历史数据保留，符合"增量导入"要求。
- 前端把最近一次导入/检索的指标存进 `state.metrics`，可一键导出 JSON，便于出测试记录。

**优化点与量化**

| 优化 | 做法 | 效果（实测填） |
| --- | --- | --- |
| 推理批处理 | 一次 `pipe(texts[])` 处理 batch 条（默认 8，可调） | 吞吐提升：待实测 |
| 后端选择 | WebGPU + fp16 优先，自动降级 WASM + q8 | 端到端延迟：待实测 |
| 模型加载分离 | 模型只在启动时加载一次，与推理耗时分开统计 | 首次 vs 后续：见 BENCHMARK |
| 索引刷新时机 | 每批写入后刷新一次，而不是每条刷新 | 写入+刷新总耗时：待实测 |
| 查询侧 | 查询向量化与数据库检索分段计时，避免混淆 | 见指标面板 |

**关键取舍**：本项目不做客户端结果拼合（赛题禁止），因此"混合检索质量"完全取决于 seekdb 的 RRF 行为，
优化空间集中在向量化吞吐、写入批处理与索引刷新策略上。

---

## 附：计时口径

| 指标 | 起点 | 终点 |
| --- | --- | --- |
| 模型加载 | worker 开始 init | pipeline 就绪 |
| 文档向量化 | 第一批 embed 调用 | 最后一批返回（不含网络） |
| 写入 + 索引刷新 | 第一次 POST /api/ingest | 最后一响应（服务端返回 writeMs / refreshMs） |
| 查询向量化 | 查询文本进入 worker | 向量返回 |
| seekdb 检索 | 服务端进入 `hybridSearch` | 返回结果（服务端计时） |
| 端到端 | 按下检索 | 首屏渲染完成 |

首次请求（含模型下载/预热）与后续请求分开记录；重复测量取中位数，保留原始样本。
