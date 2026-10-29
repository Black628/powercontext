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
| seekdb | 服务器模式，可连接的 `host:2882`（本机实测监听 2882；设 `SEEKDB_PORT` 可覆盖，未设置时脚本会按 2882→2881 自动探测）。**Windows 不支持嵌入式模式**，必须用 server 模式 |
| 硬件 | 纯前端可跑；有独立 GPU 时 WebGPU 路径更快 |

## 2. 启动 seekdb

任选一种：

```bash
# Docker（推荐）
docker run -d --name seekdb -p 2882:2882 oceanbase/seekdb

# 或已装系统服务
sudo systemctl start seekdb-server
```

确认可连：

```bash
mysql -h127.0.0.1 -P2882 -uroot -e "select version();"
```

默认 root 空密码，连的是 `test` 库（可用 `SEEKDB_DATABASE` 改名）。
本机实测输出：`5.7.25-OceanBase seekdb-v1.4.0.0`。

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
| `SEEKDB_HOST` / `SEEKDB_PORT` | 127.0.0.1 / **2882** | seekdb 连接；不设 `SEEKDB_PORT` 时按 2882→2881 探测 |
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

**实测体积（从 HF 真实下载后按文件字节数统计）**

| 配置 | ONNX 文件 | 字节 | MiB | 压缩比（FP32/该档） |
| --- | --- | --- | --- | --- |
| FP32 | `model.onnx` | 90,387,606 | 86.20 | 1.00× |
| FP16 | `model_fp16.onnx` | 45,297,825 | 43.20 | 2.00× |
| INT8（q8） | `model_quantized.onnx` | 22,972,370 | 21.91 | 3.94× |

检索质量（Node 侧同模型实测）：FP32 与 INT8 的 Recall@5 都是 0.900，nDCG@5 0.620 → 0.618。
完整方法、命令、样本与原始 JSON 见 [`docs/BENCHMARK.md`](docs/BENCHMARK.md)。

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
├── scripts/
│   ├── smoke.mjs              # 链路自检：写入 → refresh → hybridSearch
│   ├── env-info.mjs           # 采集测量环境（Node/OS/CPU/seekdb 版本）
│   ├── bench-model-size.mjs   # 三档 dtype 真实下载 + 体积/压缩比
│   ├── bench-quality.mjs      # Recall@5 / nDCG@5 / 吞吐 / 查询延迟
│   ├── bench-e2e.mjs          # 写入+refresh / 检索 / 端到端分段计时
│   ├── bench-batch.mjs        # 批大小对吞吐的影响
│   ├── bench-search-modes.mjs # 混合检索 vs 纯向量检索耗时对照
│   ├── bench-startup.mjs      # 启动耗时
│   └── trace-sql.mjs          # 抓 seekdb 实际执行的 SQL（合规性证据）
└── docs/
    ├── TECH_REPORT.md   # 一页技术说明（含实测体积/质量/性能）
    ├── BENCHMARK.md     # 测试方法、命令、样本数、中位数、环境
    ├── SQL_EVIDENCE.md  # 混合检索在 seekdb 内完成的证据（SQL + DDL + 行数）
    └── bench_*.json     # 上述脚本产出的原始数据
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
npm run smoke        # 验证后端 → seekdb 写入 → refresh_index → hybridSearch 是否连通
```

`smoke` 用**随机向量**，只验证链路和时延，不验证检索质量（质量见第 10 节）。

本机实测：退出码 0，输出形如

```
配置：collection=edge_kb dim=384
seekdb：127.0.0.1:2882/test，当前 6 条
写入：2 条，write=10.2ms refresh=602.0ms，总数 8
检索：db=48.3ms，命中 3 条
```

## 10. 如何复现 docs 里的实测数字

前置：seekdb 在 `127.0.0.1:2882` 可连；`npm install` 完成；如需从 HF 下模型，Windows 上要给 Node 设代理
（Node 的 `fetch` 默认不读系统代理，需要 `NODE_USE_ENV_PROXY=1`）。

```powershell
# 0) 环境快照（Node / OS / CPU / seekdb 版本），与 docs/BENCHMARK.md 第 0 节对照
node scripts/env-info.mjs

# 以下脚本都需要联网下载模型（首次 ~160 MB），Windows 走系统代理：
$env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:NODE_USE_ENV_PROXY="1"

# 1) 模型体积与压缩比（会先删 ONNX 缓存再逐档真实下载）
node scripts/bench-model-size.mjs

# 2) 检索质量 Recall@5 / nDCG@5 / 吞吐 / 查询延迟（chunkSize=200）
node scripts/bench-quality.mjs
#    应用默认切片长度（420）的对照
$env:CHUNK_SIZE="420"; $env:BENCH_OUT="bench_quality_420.json"; node scripts/bench-quality.mjs fp32 q8

# 3) 批处理收益
node scripts/bench-batch.mjs
$env:SLICE="200"; $env:BENCH_OUT="bench_batch_slice200.json"; node scripts/bench-batch.mjs

# 4) 端到端分段计时（需要 npm start 已在另一个终端跑起来）
node scripts/bench-e2e.mjs
$env:DTYPE="fp32"; $env:BENCH_OUT="bench_e2e_fp32.json"; node scripts/bench-e2e.mjs

# 5) 混合检索 vs 纯向量检索耗时对照
node scripts/bench-search-modes.mjs

# 6) 启动耗时（3 次取中位数；会自己起停服务，端口别被占用）
node scripts/bench-startup.mjs

# 7) 混合检索合规性证据：抓 seekdb 实际执行的 SQL + 集合 DDL
node scripts/trace-sql.mjs        # 产出 docs/SQL_EVIDENCE.md 与 docs/sql_trace.json

# 8) 一条命令跑完（不含 bench-startup，因为它要独占端口）
npm run bench:all
```

每个脚本都会把原始样本写到 `docs/bench_*.json`，`docs/TECH_REPORT.md` 与 `docs/BENCHMARK.md` 里的
表格就是这些文件的摘要。**浏览器侧（WebGPU/WASM）的数字必须按 `docs/BENCHMARK.md` 第 7 节在页面上采集**，
仓库里没有、也不应该有伪造的浏览器数据。

## 11. 与验收标准对照

| 验收项 | 本实现 |
| --- | --- |
| 完整流程 | 导入 → 向量化 → 写入 → 查询 → 展示排序结果与原文 |
| 本地推理 | 推理全部在 `web/worker.js`，服务端无任何 embedding 调用（`createCollection` 不设 embeddingFunction） |
| 模型量化 | `dtype` 切换 fp32/q8/fp16；体积 86.20 / 43.20 / 21.91 MiB，压缩比 1.00× / 2.00× / 3.94× |
| 后端与降级 | 优先 WebGPU，失败自动降级 WASM，徽章与日志明确提示（浏览器侧行为本次未实测，见 BENCHMARK 第 5 节） |
| seekdb 数据层 | 向量/原文/元数据在 seekdb，`hybridSearch` 在库内完成 RRF（`docs/SQL_EVIDENCE.md`） |
| 状态与刷新 | 每次写入后 `refresh_index()`，支持增量导入与集合切换 |
| 可复现 | 第 2、3、10 节；示例语料在 `data/corpus/`；所有数字有原始 JSON |

## 12. 已知限制（如实说明）

- **浏览器侧指标未实测**：本次交付在无 GUI 的环境里完成，WebGPU/WASM 的毫秒数与"禁用 WebGPU 自动降级"
  只做了代码路径核对，没有真实跑过。`docs/BENCHMARK.md` 第 5、7 节写了原因和 3 分钟复测步骤。
- **Node 数字不等于浏览器数字**：`docs/BENCHMARK.md` 里的向量化/检索性能来自 Node + onnxruntime-node（CPU EP），
  与浏览器同模型同权重，但执行器不同，只能当对照参考。
- Windows 只能用 server 模式（嵌入式 seekdb 不支持 Windows），所以本机开发需要一台能连的 seekdb。
- `whereDocument.$contains` 走的是 seekdb 的全文检索语义；如果查询词在语料里没有精确 token，
  全文这一路会召回空，此时结果等价于纯向量检索（RRF 会自然退化为单路），属预期行为。
- 语料只有 3 篇（6～14 片），所以"K=5"覆盖了候选池的很大比例，Recall 的区分度有限；
  这是测量口径问题，已写在 `docs/BENCHMARK.md` 第 4.3 节。
- 本机 `npm`（8.19.4，内部 node v16）不会装 `sharp` 的 win32 可选二进制，Node 侧 bench 脚本第一次跑需要
  `npm install --no-save @img/sharp-win32-x64@0.34.5`；`npm start` / `npm run smoke` 不受影响。
