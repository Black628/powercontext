# 性能与质量测试记录（实测）

> 本文件所有数字都来自本机真实运行，命令、样本数与原始 JSON 都列在下面。
> **浏览器侧**（WebGPU/WASM 的加载、向量化、检索与降级）已单独实测，见 `docs/BROWSER_BENCH.md`；
> 本文第 5、7 节保留为浏览器侧的采集方法与页面口径，实际结果以 BROWSER_BENCH 为准。

## 0. 固定测试条件

| 项 | 值 |
| --- | --- |
| 设备 / CPU | AMD Ryzen 9 8940HX with Radeon Graphics（16 核 32 线程，2.4 GHz 基频） |
| 内存 | 15.3 GB |
| 操作系统 | Windows 11 家庭版中文版 10.0.26200 (x64) |
| Node | v24.9.0（`node` 实际解析到 DSH 内置 node.exe） |
| npm | 8.19.4（⚠ 该 npm 自带 node v16.20.2，见第 1 节说明） |
| onnxruntime | `onnxruntime-node`（随 `@huggingface/transformers` 装上），执行提供器 = **CPU EP** |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0`（`OceanBase 4.3.5.3 seekdb (r1.4.0.0)`） |
| seekdb 部署 | 本机服务，`127.0.0.1:2882`，库 `test`，root 空密码；**2881 未监听** |
| seekdb SDK | `seekdb`（seekdb-js）1.4.0 |
| HTTP 服务 | `http://127.0.0.1:7860`（`npm start`，Node HTTP，本机回环） |
| 语料 | `data/corpus/*.md`，3 篇，合计 3,717 字节；200 字切片 → 14 片；420 字切片 → 6 片 |
| 查询集 | 10 条（第 4 节），标注方式见第 4 节 |
| 模型 | `Xenova/all-MiniLM-L6-v2`（HF `main` 分支，本次下载时间 2026-10-29） |
| 精度 | `fp32` / `q8`(INT8) / `fp16` |
| 批大小 | 8（与 `web/app.js` 默认一致） |
| 网络 | Windows 系统代理 `http://127.0.0.1:7897`；Node 需 `NODE_USE_ENV_PROXY=1` 才走代理 |

环境快照可用 `node scripts/env-info.mjs` 重新生成。

## 1. 准备与命令

```bash
cd stepfun
npm install          # 实测 4.98 s（62 个包，命中本地缓存）
npm start            # 另开终端；启动到 /api/health 返回 ok 中位数 131 ms
npm run smoke        # 链路自检
```

实测记录：

| 项 | 结果 |
| --- | --- |
| `npm install` 耗时 | **4.98 s**（`added 62 packages`），退出码 0 |
| `npm start` 启动耗时 | **131 ms**（3 次：129 / 131 / 140 ms，中位数；口径 = spawn 到 `/api/health` 返回 `ok:true`） |
| `npm run smoke` 退出码 | **0** |

`npm run smoke` 真实输出（本机）：

```
配置：collection=edge_kb dim=384
seekdb：127.0.0.1:2882/test，当前 6 条
写入：2 条，write=10.2ms refresh=602.0ms，总数 8
检索：db=48.3ms，命中 3 条
  1. fulltext-index.md#0  score=0.0325  # seekdb 全文索引与分词器 …
  2. smoke-b-1793278554295  score=0.0313  全文索引使用 ik 分词器处理中文
  3. edge-embedding.md#1  score=0.0161  导入：读取文件 → 切片 → 批量向量化 …
```

> smoke 用**随机向量**，只验证「写入 → `refresh_index` → 混合检索」链路与时延，**不验证检索质量**。
> 它的可检索性由全文路保证（查询串命中），这也是为什么它能稳定返回结果。

**环境坑（如实记录）**：本机 `npm` 来自 `C:\Program Files\nodejs\npm.cmd`，它内部用的是 **node v16**，
而 `node` 命令解析到 v24.9.0。因此 `npm install` 会打印 `EBADENGINE` 警告，并且**不会**安装 `sharp` 的
win32 可选二进制。`npm start` / `npm run smoke` 不受影响（不依赖 sharp），但 Node 侧 bench 脚本要 import
`@huggingface/transformers`，需要补一次：

```bash
npm install --no-save @img/sharp-win32-x64@0.34.5    # 实测 5 s
```

若你的 npm 属于 node ≥ 20，这一步通常不需要。

## 2. 模型体积（真实下载后统计）

命令：

```bash
# Windows 走系统代理（Node 需要 NODE_USE_ENV_PROXY 才会读 HTTPS_PROXY）
$env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:NODE_USE_ENV_PROXY="1"
node scripts/bench-model-size.mjs
```

脚本行为：先删掉缓存里的 ONNX 权重 → 逐档 `pipeline(..., { dtype })` 触发真实下载 → 统计**文件系统字节数** →
校验"新出现的文件名"是否等于该 dtype 的预期文件名。

| 配置 | dtype | ONNX 文件 | 文件数 | 体积（字节） | 体积（MiB） | 压缩比 | 相比 FP32 节省 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FP32 基线 | `fp32` | `model.onnx` | 1 | 90,387,606 | 86.20 | 1.00× | — |
| INT8 | `q8` | `model_quantized.onnx` | 1 | 22,972,370 | 21.91 | **3.935×** | 74.6% |
| FP16 | `fp16` | `model_fp16.onnx` | 1 | 45,297,825 | 43.20 | **1.995×** | 49.9% |

侧车文件（三档共用，`config.json` 650 B + `tokenizer.json` 711,661 B + `tokenizer_config.json` 366 B）
合计 **712,677 B**。因此"该模型在浏览器缓存里的全部文件"口径为：

| 配置 | 全模型体积（字节） | 全模型体积（MiB） |
| --- | --- | --- |
| FP32 | 91,100,283 | 86.88 |
| INT8 | 23,685,047 | 22.59 |
| FP16 | 46,010,502 | 43.88 |

原始数据：`docs/bench_model_size.json`（含每档"本次新下载的文件名 + 字节数"）。

> 该脚本跑了两轮，两轮字节数完全一致（90,387,606 / 45,297,825 / 22,972,370），可作为可复现性证据。
>
> 下载耗时（供参考，走系统代理 `127.0.0.1:7897`）：把三档 ONNX 全部删除后重下一次，**合计约 53 s**（162 MB）；
> 冷缓存下第一次 `pipeline` 构建（fp32，含 86.20 MiB 下载 + ORT 初始化）实测 32.2 s。

## 3. 端到端与分阶段计时

### 3.1 测量方式

```bash
npm start                              # 终端 A
# 终端 B（Windows 代理环境变量）
$env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:NODE_USE_ENV_PROXY="1"
node scripts/bench-e2e.mjs                       # dtype=q8（WASM 降级档的默认精度）
$env:DTYPE="fp32"; $env:BENCH_OUT="bench_e2e_fp32.json"; node scripts/bench-e2e.mjs
```

脚本流程：`/api/reset` 清空集合 → 用**真模型**给 6 个切片算向量 → 分批 `POST /api/ingest`（每批 8 条，
服务端返回 `writeMs` / `refreshMs`）→ 10 条查询 × 每条重复 5 次，记录「查询向量化」「HTTP 往返」
「seekdb 服务端计时（`dbMs`）」「端到端」→ 全部取中位数。样本数 **50**（10 查询 × 5 次）。

### 3.2 结果（样本数 50，除特别说明）

| 指标 | q8（INT8） | fp32 基线 | 备注 |
| --- | --- | --- | --- |
| 模型加载（Node，缓存命中） | 128.7 ms | 136.8 ms | 首次含下载另计（见第 2 节下载耗时） |
| 文档向量化吞吐（6 片） | 75.1 docs/s | 64.0 docs/s | batch=8，切片 420 字 |
| 写入耗时 `writeMs`（6 片，1 批） | 130.6 ms | 36.7 ms | 服务端计时 |
| 索引刷新 `refreshMs`（1 批） | 716.1 ms | 402.4 ms | 首次建 HNSW 索引，波动大 |
| 导入整体（向量化+写入+刷新） | 1010.8 ms | 617.2 ms | 墙钟 |
| 查询向量化（单条） | **2.25 ms**（P95 2.72） | 3.18 ms（P95 3.76） | Node/CPU |
| seekdb 混合检索（服务端计时） | **46.72 ms**（P95 48.35） | 47.12 ms（P95 48.17） | 集合仅 6 行 |
| HTTP 往返（含上面检索，本机回环） | 48.52 ms | 48.73 ms | |
| **端到端**（查询向量化 + 检索往返） | **51.11 ms**（P95 52.73） | 51.92 ms（P95 52.71） | |
| 零命中样本 | 0/50 | 0/50 | |

原始样本：`docs/bench_e2e.json`、`docs/bench_e2e_fp32.json`（含每批 `writeMs/refreshMs` 与 50 条逐次样本）。

### 3.3 混合检索延迟的构成（附加观测）

```bash
node scripts/bench-search-modes.mjs        # 同一查询向量，hybrid vs 纯向量，各 20 次
```

| 模式 | db 中位数 | 最小–最大 | top1 |
| --- | --- | --- | --- |
| 混合（向量 + 全文 + RRF） | 46.84 ms | 45.75–134.18 ms | `amd-radeon.md#0` |
| 纯向量（`rank`/`query` 都不传） | 46.83 ms | 43.17–51.01 ms | `amd-radeon.md#0` |

**解读**：两条路径耗时相同，说明这 ~47 ms 不是"全文路"或"数据量"带来的，而是 seekdb 这条
`hybridSearch` 调用路径的固定开销：`SET @search_parm`（≈1.3 ms）
+ `DBMS_HYBRID_SEARCH.GET_SQL` 生成 SQL（≈1.4 ms）+ 执行动态生成的大 SQL（≈44–46 ms，其中向量字面量出现两次）。
集合只有 6 行，所以这个数字**不能**外推为 seekdb 的检索扩展性指标；它反而说明小集合场景下
"生成并解析动态 SQL"是主要成本。原始数据：`docs/bench_search_modes.json`。

## 4. 检索质量（量化前后对比）

### 4.1 方法

```bash
node scripts/bench-quality.mjs                 # fp32 / q8 / fp16，chunkSize=200（14 片）
$env:CHUNK_SIZE="420"; $env:BENCH_OUT="bench_quality_420.json"; node scripts/bench-quality.mjs fp32 q8
```

- **检索方式**：脚本内做**稠密向量精确 top-5**（在 14/6 个候选片上算余弦），不经过 seekdb。
  这样对比的只是 embedding 模型本身，不掺入 RRF 行为。
- **标注方式**：按**内容子串**标注（不是看模型输出标注），与切片长度无关、可复现：
  `grade 2` = 该切片就是答案所在（含指定关键子串），`grade 1` = 部分相关（同主题）。
- **指标定义**：
  - `Recall@5`（BENCHMARK 原定义）= 命中至少一条相关切片的查询数 ÷ 查询数
  - `严格 Recall@5` = 答案切片（grade 2）被召回的比例
  - `nDCG@5` = 按 2/1/0 分级、`(2^rel−1)/log2(i+1)` 的 DCG ÷ IDCG
  - `MRR@5` = 首个相关切片排名的倒数均值
- **查询集（10 条，与脚本一致）**：

| # | 查询 | grade 2 关键子串 | grade 1 |
| --- | --- | --- | --- |
| 1 | 怎么在 AMD 平台上部署 seekdb | `docker run -d --name seekdb` | `申请算力` |
| 2 | 全文索引用哪个分词器处理中文 | `ik 面向中文` / `WITH PARSER ik` | `beng 面向英文` |
| 3 | 如何获取部署实例的遥测 ID | `telemetry.json` | `run/telemetry` |
| 4 | 向量检索和全文检索怎么融合 | `RRF` | `混合检索` |
| 5 | 为什么写入后马上查不到 | `刷新索引这一步容易漏` / `异步维护` | — |
| 6 | INT8 和 FP16 怎么选 | `INT8（q8）体积` | `FP16 体积约减半` |
| 7 | WebGPU 不可用会怎样 | `优雅降级` | `navigator.gpu` |
| 8 | 增量导入后索引怎么刷新 | `刷新索引` | `写入数据库` |
| 9 | seekdb 的最小资源要求 | `至少 1 核 CPU` | — |
| 10 | 批处理为什么更快 | `批处理比逐条推理快` | — |

### 4.2 汇总（chunkSize=200，14 片候选，10 查询，batch=8）

| 配置 | Recall@5 | 严格 Recall@5 | nDCG@5 | MRR@5 | 与基线差异 | 吞吐 (docs/s) | 查询向量化中位数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FP32 基线 | 0.900 | 0.700 | 0.620 | 0.700 | — | 145.9 | 1.87 ms |
| INT8（q8） | 0.900 | 0.700 | 0.618 | 0.700 | nDCG **−0.003** | 191.4 | 1.64 ms |
| FP16 | 0.900 | 0.700 | 0.620 | 0.700 | 0 | 80.4 | 7.48 ms |

### 4.3 汇总（chunkSize=420，应用默认，6 片候选）

| 配置 | Recall@5 | 严格 Recall@5 | nDCG@5 | MRR@5 | 吞吐 (docs/s) | 查询向量化中位数 |
| --- | --- | --- | --- | --- | --- | --- |
| FP32 基线 | 1.000 | 1.000 | 0.826 | 0.790 | 58.5 | 1.65 ms |
| INT8（q8） | 1.000 | 1.000 | 0.831 | 0.795 | 66.7 | 1.64 ms |

> 两种切片长度的结论一致：**量化前后质量差异在噪声量级内（nDCG 差 ≤0.003），远小于 0.1 的判定阈值**。
> 但要注意区分度：chunkSize=200 时候选 14 片、K=5 约占 36%；chunkSize=420 时只有 6 片、K=5 占 83%，
> 后者几乎等于"全给"，所以 4.3 的 1.000 说明力弱，4.2 才是更严格的对比。

### 4.4 逐条结果（chunkSize=200，FP32 基线 / q8 对照）

FP32 基线：

| 查询 | 相关片数 | 命中数 | 严格 Recall | nDCG@5 | MRR | Top-5（括号内为标注等级） |
| --- | --- | --- | --- | --- | --- | --- |
| 怎么在 AMD 平台上部署 seekdb | 2 | 1 | 0.000 | 0.275 | 1.0 | `amd-radeon.md#0(1)` `fulltext-index.md#0(0)` `fulltext-index.md#3(0)` `amd-radeon.md#2(0)` `edge-embedding.md#1(0)` |
| 全文索引用哪个分词器处理中文 | 2 | 1 | 0.500 | 0.264 | 0.25 | `fulltext-index.md#0(0)` `fulltext-index.md#4(0)` `edge-embedding.md#3(0)` `fulltext-index.md#3(2)` `edge-embedding.md#0(0)` |
| 如何获取部署实例的遥测 ID | 2 | 1 | 0.500 | 0.264 | 0.25 | `edge-embedding.md#3(0)` `fulltext-index.md#0(0)` `amd-radeon.md#0(0)` `amd-radeon.md#2(2)` `fulltext-index.md#4(0)` |
| 向量检索和全文检索怎么融合 | 2 | 1 | 1.000 | 0.521 | 0.5 | `fulltext-index.md#0(0)` `fulltext-index.md#4(2)` `fulltext-index.md#3(0)` `edge-embedding.md#3(0)` `edge-embedding.md#0(0)` |
| 为什么写入后马上查不到 | 1 | 1 | 1.000 | 1.000 | 1.0 | `edge-embedding.md#3(2)` … |
| INT8 和 FP16 怎么选 | 1 | 1 | 1.000 | 1.000 | 1.0 | `edge-embedding.md#1(2)` … |
| WebGPU 不可用会怎样 | 1 | 1 | 1.000 | 1.000 | 1.0 | `edge-embedding.md#2(2)` … |
| 增量导入后索引怎么刷新 | 2 | 2 | 1.000 | 0.877 | 1.0 | `edge-embedding.md#3(2)` … `edge-embedding.md#2(2)` |
| seekdb 的最小资源要求 | 1 | 0 | 0.000 | 0.000 | 0 | 全部不相关（`amd-radeon.md#4` 未被召回） |
| 批处理为什么更快 | 1 | 1 | 1.000 | 1.000 | 1.0 | `edge-embedding.md#3(2)` … |

q8（INT8）：10 条查询里有 5 条的 Top-5 **顺序**与上表不同（多为第 2–5 名互换，例如
`INT8 和 FP16 怎么选`、`增量导入后索引怎么刷新`、`批处理为什么更快`、`网页遥测 ID` 的第 4/5 名互换，
`怎么在 AMD 平台上部署 seekdb` 的第 5 名从 `edge-embedding.md#1` 换成 `amd-radeon.md#4`），
但**命中数与严格召回完全一致**（同为 9/10 查询命中、7/10 严格命中），所以 nDCG 只差 0.003。
逐条明细见 `docs/bench_quality.json` 的 `perQuery`。

> 两个真实的失败案例（不值当美化）：
> 1. 查询 1 在 200 字切片下，答案切片（只有一行 `docker run …` 的英文代码块）**没进 Top-5**，严格召回 0；
> 2. 查询 9（"最小资源要求"）无论 FP32 还是 q8 都**完全没召回**答案切片（`amd-radeon.md#4`）。
> 这两例都是 MiniLM（英文为主）对中文问句 + 中英混排切片的语义对齐不足，也正是应用保留**全文检索路**的理由：
> 关键词精确命中可以补上语义路的漏召（见 `docs/SQL_EVIDENCE.md` 中 `match … against` 与 RRF 的行为）。

## 5. 降级验证（未测，附替代证据）

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| WebGPU 可用 | 徽章显示 `后端：WebGPU · FP16` | **未测** |
| 禁用 WebGPU | 徽章显示 `后端：WASM · Q8`，日志写明降级原因 | **未测** |
| 两种后端 Top-5 差异 ≤ 1 条 | — | **未测** |

**为什么未测**：本次执行环境没有可自动化驱动的浏览器（无 GUI 会话、无 Chrome/Edge 开发者工具通道），
`web/worker.js` 的 WebGPU 分支需要真实 `navigator.gpu` 才能走到。

**替代证据（可复查）**：

- 代码路径：`web/worker.js` 的 `init()` 里 `wantDevice = msg.device === 'webgpu' && !!self.navigator?.gpu`，
  `buildPipeline` 抛错时进入 `catch`，用 `wasm + DEFAULT_DTYPE.wasm('q8')` 重建，并把
  `fallbackFrom = 'WebGPU 初始化失败：…'` 回传主线程；`web/app.js` 收到 `fallbackFrom` 后会写日志并设置徽章 `title`。
- 精度映射已被真实数据佐证：三档 dtype → ONNX 文件名的映射由脚本实测验证（`docs/bench_model_size.json` 的
  `downloadedFile` / `mappingMatches`），说明"切 dtype"确实切到了不同权重文件，而不是名义切换。
- 复测步骤见第 7 节，3 分钟内可完成。

## 6. 批处理收益（Node/CPU，诚实结论：没有想象中好）

```bash
node scripts/bench-batch.mjs                                  # 420 字切片 ×6 轮 = 36 条
$env:SLICE="200"; $env:BENCH_OUT="bench_batch_slice200.json"; node scripts/bench-batch.mjs
```

| dtype | batch=1 | batch=4 | batch=8 | batch=16 |
| --- | --- | --- | --- | --- |
| fp32（420 字切片） | 82.1 docs/s (1.00×) | 64.9 (0.79×) | 45.5 (0.55×) | 37.5 (0.46×) |
| q8（420 字切片） | 91.8 docs/s (1.00×) | 82.0 (0.89×) | 48.7 (0.53×) | 42.2 (0.46×) |
| fp32（200 字切片） | 152.8 docs/s (1.00×) | 140.5 (0.92×) | 118.5 (0.78×) | 99.9 (0.65×) |
| q8（200 字切片） | 165.8 docs/s (1.00×) | **190.6 (1.15×)** | 157.0 (0.95×) | 117.8 (0.71×) |

**结论**：在本机 Node/CPU 上，batch>1 **通常更慢**——切片长度不齐时（420 字那组实际是 420/153/420/217/420/324）
整批要 padding 到最长序列，白算的部分超过并行收益；切片长度均匀（200 字）时 q8 的 batch=4 才有 1.15× 的收益。
所以"批处理一定更快"这句话在本环境下**不成立**，README/TECH_REPORT 已按实测口径改写。
浏览器侧（WASM SIMD / WebGPU）的批处理行为不同，需按第 7 节复测。

原始数据：`docs/bench_batch.json`、`docs/bench_batch_slice200.json`（含每轮原始值）。

## 7. 浏览器侧（WebGPU / WASM）复测步骤 —— 这部分数字待你在页面上采

1. `npm start`，用 Chrome/Edge 打开 `http://127.0.0.1:7860`；
2. 等徽章显示后端与 dtype（`WebGPU · FP16` 或 `WASM · Q8`），记下 `模型加载` 卡片的秒数；
3. 点「加载示例语料」，记下 `向量化`（秒 + 片/秒）、`写入（其中刷新）`、`导入完成总耗时`；
4. 右栏逐条粘贴第 4.1 节的 10 条查询，"重复 5 次"勾上，记下 `查询向量化` / `seekdb 检索` / `端到端` 三个中位数，
   并人工判断 Top-5 是否正确（对照第 4.1 节的标注）；
5. 点「导出指标 JSON」，把 JSON 存成 `docs/bench-browser-<backend>.json`；
6. 在 `chrome://flags` 或 `chrome://gpu` 关掉 WebGPU 后重开页面，重复 2–5，得到 WASM 组数据；
7. 对比两组：端到端延迟、导入吞吐、Top-5 差异条数（期望 ≤1 条）。

**为什么必须复测**：Node 的 onnxruntime 走 CPU EP，浏览器走 WASM（SIMD/多线程）或 WebGPU，
执行器、线程模型、内存都在不同数量级；本文件里的 Node 数字只能作为"同模型、同权重的参考下界/对照"，
不能当作浏览器成绩。

## 8. 原始数据文件清单

| 文件 | 内容 |
| --- | --- |
| `docs/bench_model_size.json` | 三档 dtype 的真实下载文件名、字节数、压缩比、映射校验 |
| `docs/bench_quality.json` | chunkSize=200 的逐查询 Top-5、标注等级、Recall/nDCG/MRR、每轮吞吐与查询延迟原始样本 |
| `docs/bench_quality_420.json` | chunkSize=420（应用默认）同上 |
| `docs/bench_e2e.json` / `docs/bench_e2e_fp32.json` | 每批 write/refresh 耗时、50 条检索样本（向量化/HTTP/db/端到端） |
| `docs/bench_search_modes.json` | 混合 vs 纯向量的库内耗时对照 |
| `docs/bench_batch.json` / `docs/bench_batch_slice200.json` | 批处理吞吐与每轮原始值 |
| `docs/bench_startup.json` | 启动耗时 3 次原始值 |
| `docs/sql_trace.json` / `docs/SQL_EVIDENCE.md` | 混合检索实际执行的 SQL、返回行数、集合 DDL 与索引 |

## 9. 结论（3 条）

1. **量化有效且几乎无损**：INT8 把 ONNX 权重从 86.20 MiB 压到 21.91 MiB（3.94×），FP16 压到 43.20 MiB（2.00×）；
   同一语料上 Recall@5 保持 0.900、nDCG@5 仅 −0.003（FP32 0.620 → q8 0.618）。
2. **端到端 51 ms（Node/CPU，样本 50）**，其中查询向量化只占 2.25 ms，seekdb 混合检索占 46.7 ms；
   且混合检索并不比纯向量检索慢（46.84 vs 46.83 ms），延迟来自 `hybridSearch` 这条动态 SQL 路径的固定开销，
   与集合规模无关，不能外推为 seekdb 的扩展性结论。
3. **两处反直觉但真实的实测**：(a) 本机 Node/CPU 上批处理不加速（420 字切片 batch=8 仅 0.53×）；
   (b) 语料太小导致 chunkSize=420 时 K=5 覆盖 83% 候选，"Recall=1.000"说明力弱——真正有区分度的是 200 字那组。

## 10. 仍未验证的项（清单）

| 项 | 状态 | 原因 / 替代 |
| --- | --- | --- |
| 浏览器 WebGPU 延迟、WASM 延迟 | **未测** | 无可自动化的浏览器；按第 7 节 3 分钟可复测 |
| WebGPU→WASM 自动降级的实际触发 | **未测** | 同上；代码路径见第 5 节，`docs/TECH_REPORT.md` 第 2 节 |
| 两后端 Top-5 一致性 | **未测** | 需两组浏览器数据 |
| 大语料（上千片）的写入与检索规模表现 | **未测** | 只有 3 篇语料；如需可复制 `data/corpus/*.md` 若干份再跑 `bench-e2e.mjs` |
| FP16 在真实 GPU 上的速度 | **未测** | Node/CPU 的 FP16 是模拟执行（80 docs/s，比 fp32 慢），不能代表 WebGPU |
