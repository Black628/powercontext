# 端侧 AI 推理 + seekdb 混合检索（迷你知识库）

浏览器里跑 embedding 模型，把文档变成向量；向量、原文和元数据一起写入 **seekdb**；
查询时同样在浏览器本地算查询向量，交给 seekdb 的 `hybridSearch()` 做**向量 + 全文的 RRF 融合检索**。

> 一句话边界：**向量计算只在浏览器里发生**，服务端不碰任何推理、也不做结果拼合。

```
┌──────────── 浏览器（端侧推理）────────────┐        ┌──────── Node 后端（薄转发）────────┐        ┌──────── seekdb ────────┐
│  文件读取 → 切片 → transformers.js 推理    │  HTTP  │  POST /api/ingest  存向量+原文      │  SQL   │  collection: edge_kb   │
│  WebGPU(fp16) → 失败自动降级 WASM(q8)      │ ─────► │  POST /api/search  收向量+查询串    │ ─────► │  HNSW + IK 全文索引     │
│  查询向量化 → 展示结果与耗时               │ ◄───── │  hybridSearch(rank.rrf) 原样转发    │ ◄───── │  RRF 在库内完成         │
└───────────────────────────────────────────┘        └────────────────────────────────────┘        └────────────────────────┘
```

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| Node.js | ≥ 20（`seekdb` SDK 要求；本机验证用 v24） |
| 浏览器 | Chrome / Edge 最新版。WebGPU 需要 `localhost` 或 HTTPS |
| seekdb | 服务器模式，可连接的 `host:2881`。**Windows 不支持嵌入式模式**，必须用 server 模式 |
| 硬件 | 纯前端可跑；有独立 GPU 时 WebGPU 路径更快 |

## 2. 启动 seekdb

任选一种：

```bash
# Docker（推荐）
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb

# 或已装系统服务
sudo systemctl start seekdb-server
```

确认可连：

```bash
mysql -h127.0.0.1 -P2881 -uroot -e "select version();"
```

默认 root 空密码，连的是 `test` 库（可用 `SEEKDB_DATABASE` 改名）。

## 3. 启动应用

```bash
cd stepfun
npm install          # 安装 seekdb 与 @huggingface/transformers
npm start            # 默认 http://127.0.0.1:7860
```

打开 `http://127.0.0.1:7860`：

1. 顶部徽章会显示 **后端（WebGPU/WASM）· dtype**、模型名、seekdb 连接与文档数；
2. 左栏「加载示例语料」或拖入自己的 `.txt/.md`；
3. 观察进度条与耗时，导入完成后左栏显示集合内条数；
4. 右栏输入查询（如「怎么在 AMD 上部署 seekdb」）→ 检索，结果带 score 与来源片段；
5. 底部「实测指标」记录各阶段耗时，可「导出指标 JSON」直接贴进测试记录。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PORT` | 7860 | HTTP 端口 |
| `SEEKDB_HOST` / `SEEKDB_PORT` | 127.0.0.1 / 2881 | seekdb 连接 |
| `SEEKDB_USER` / `SEEKDB_PASSWORD` | root / 空 | 账号 |
| `SEEKDB_DATABASE` | test | 库名 |
| `SEEKDB_COLLECTION` | edge_kb | 集合（表）名；换名即换知识库 |
| `EMBED_DIM` | 384 | 向量维度，换模型时必须同步改 |
| `MODEL_ID` | Xenova/all-MiniLM-L6-v2 | 端侧模型 |
| `MODEL_HOST` | https://huggingface.co | 模型来源；国内可设 `https://hf-mirror.com` |
| `RECALL_K` | 20 | 两路各召回多少条，再交给 RRF 融合 |

## 4. 模型与量化

- **模型**：`Xenova/all-MiniLM-L6-v2`（ONNX 版，Apache-2.0，384 维），来源 Hugging Face `Xenova` 组织。
- **量化**：由 transformers.js 的 `dtype` 参数控制，切换的是 ONNX 权重文件，程序逻辑不变：

| 方案 | dtype | 后端 | 说明 |
| --- | --- | --- | --- |
| FP32 基线 | `fp32` | WASM | 体积最大，精度上限 |
| INT8 | `q8` | WASM | 体积约 1/4，WASM 上的默认选择 |
| FP16 | `fp16` | WebGPU | 体积约 1/2，WebGPU 的默认选择 |

默认策略：`navigator.gpu` 可用 → `webgpu + fp16`；不可用 → `wasm + q8`。
WebGPU 初始化失败会自动回落到 `wasm + q8`，并在顶部徽章与日志里写明原因。

强制切换某一种做对比测试（浏览器控制台）：

```js
// 强制 WASM + INT8
document.querySelector('#badge-backend'); // 先看当前
// 直接改 app.js 里的 device 判定，或用 URL 参数见下
```

> 想固定后端做对照实验，最省事的办法是把 `web/app.js` 里 `device: gpu ? 'webgpu' : 'wasm'`
> 改成 `device: 'wasm'`（或 `'webgpu'`），重新导入语料即可，其余代码不动。

## 5. 目录结构

```
stepfun/
├── server/
│   ├── index.mjs       # HTTP 服务：静态资源 + 3 个 API（不解析文档、不计算向量）
│   ├── seekdb.mjs      # 唯一与 seekdb 交互的层：建集合 / 写入 / hybridSearch
│   └── config.mjs      # 全部可配置项
├── web/
│   ├── index.html      # UI
│   ├── app.js          # 流程编排（切片、批处理、计时、渲染）
│   ├── worker.js       # 端侧推理 Worker（transformers.js，WebGPU/WASM）
│   └── styles.css
├── data/corpus/*.md    # 示例语料（3 篇，可直接复现测试）
├── scripts/smoke.mjs   # 链路自检：随机向量验证「写入 → hybridSearch」是否通
└── docs/
    ├── TECH_REPORT.md  # 一页技术说明（提交材料 4.3）
    └── BENCHMARK.md    # 测试方法、记录表与填表说明
```

## 6. 数据与集合设计

| 层 | 内容 |
| --- | --- |
| 集合 | `edge_kb`（可用 `SEEKDB_COLLECTION` 改） |
| 向量索引 | HNSW，`dimension=384`，`distance=cosine`（建集合时显式指定） |
| 全文索引 | 默认 IK 分词器（`createCollection` 不传 fulltextIndex 即默认 ik） |
| 每条记录 | `id` = `文件名#片号@导入批次`、`document` = 切片原文、`embedding` = 浏览器算的向量、`metadata` = `{source, chunk, chars, importId}` |

服务端显式 **不设置 embeddingFunction**，所以 seekdb 永远不会自己去算向量——
这件事只发生在浏览器里。

## 7. 混合检索怎么落到 seekdb

```js
collection.hybridSearch({
  knn:   { queryEmbeddings: <浏览器算的查询向量>, nResults: RECALL_K },
  query: { whereDocument: { $contains: <原始查询串> }, nResults: RECALL_K },
  rank:  { rrf: {} },          // RRF 融合由 seekdb 完成
  nResults: topK,
  include: ['documents', 'metadatas', 'distances'],
});
```

两路召回与融合都在库内完成，客户端只把「查询向量 + 查询串」发过去、把结果拿回来，
不存在"客户端各取一路再自己拼"的情况。

## 8. 索引刷新与增量导入

- 每次 `add()` 之后立即调用 `collection.refresh_index()`（seekdb V1.3.0+ 的 HNSW 是异步维护，
  不刷新会让刚写入的文档短时间内查不到）；
- 每次导入用独立批次号拼 id（`@<importId>`），所以反复导入同一文件不会互相覆盖，
  增量追加即可；「清空集合」按钮会 drop 后重建，保证切换知识库时状态干净。

## 9. 自检

```bash
npm start            # 另开一个终端
npm run smoke        # 验证后端 → seekdb 写入 → hybridSearch 是否连通
```

`smoke` 用**随机向量**，只验证链路和时延，不验证检索质量（质量必须用浏览器里的真模型跑）。

## 10. 与验收标准对照

| 验收项 | 本实现 |
| --- | --- |
| 完整流程 | 导入 → 向量化 → 写入 → 查询 → 展示排序结果与原文 |
| 本地推理 | 推理全部在 `web/worker.js`，服务端无任何 embedding 调用（`createCollection` 不设 embeddingFunction） |
| 模型量化 | `dtype` 切换 fp32/q8/fp16，体积与压缩比记录在 `docs/TECH_REPORT.md` |
| 后端与降级 | 优先 WebGPU，失败自动降级 WASM，徽章与日志明确提示 |
| seekdb 数据层 | 向量/原文/元数据在 seekdb，`hybridSearch` 在库内完成 RRF |
| 状态与刷新 | 每次写入后 `refresh_index()`，支持增量导入与集合切换 |
| 可复现 | 见第 2、3 节；示例语料在 `data/corpus/` |

## 11. 已知限制（如实说明）

- 本项目的代码是在**没有 seekdb 实例的机器**上写完的，`npm run smoke` 与浏览器端到端流程
  需要在有 seekdb 的环境里各跑一次才算真正验证过；`docs/BENCHMARK.md` 里的数字必须由实跑填写。
- Windows 只能用 server 模式（嵌入式 seekdb 不支持 Windows），所以本机开发需要一台能连的 seekdb。
- `whereDocument.$contains` 走的是 seekdb 的全文检索语义；如果查询词在语料里没有精确 token，
  全文这一路会召回空，此时结果等价于纯向量检索（RRF 会自然退化为单路），属预期行为。
