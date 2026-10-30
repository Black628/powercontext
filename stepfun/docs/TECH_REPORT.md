# 一页技术说明

**赛题**：端侧 AI 推理性能优化与 seekdb 混合检索
**产物**：浏览器本地完成向量化的迷你知识库，检索由 seekdb 的 `hybridSearch` 在库内完成向量 + 全文的 RRF 融合。

> 所有数字都来自本机实跑，采集环境见 `docs/BENCHMARK.md` 第 0 节；原始样本存于 `docs/bench_*.json`。
> 其中**浏览器侧**（WebGPU / WASM 的加载、向量化、检索、降级）另有一组实测，
> 方法、原始 JSON 与截图见 `docs/BROWSER_BENCH.md`，本文第 2、4 节引用其结论。

---

## 1. 模型与量化

| 项 | 内容 |
| --- | --- |
| 模型 | `Xenova/all-MiniLM-L6-v2`（ONNX 导出，Apache-2.0，输出 384 维，mean pooling + L2 normalize） |
| 来源 | Hugging Face `Xenova` 组织；本次实跑下载的是仓库 `main` 分支，tokenizer/config 侧车文件共计 712,677 B |
| 运行时 | `@huggingface/transformers` 3.5.x（transformers.js）；浏览器侧由后端 `/vendor/` 本地托管，不依赖外部 CDN |
| 量化方式 | 只切换 ONNX 权重档位（transformers.js 的 `dtype`），推理代码零改动 |

**体积实测（从 HF 真实下载后按文件系统字节数统计，不是页面标注值）**

| 配置 | dtype | ONNX 文件 | 体积（字节） | 体积（MiB） | 压缩比 | 相比 FP32 节省 | 含侧车文件总体积 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FP32 基线 | `fp32` | `model.onnx` | 90,387,606 | 86.20 | 1.00× | — | 91,100,283 B（86.88 MiB） |
| INT8 | `q8` | `model_quantized.onnx` | 22,972,370 | 21.91 | **3.94×** | 74.6% | 23,685,047 B（22.59 MiB） |
| FP16 | `fp16` | `model_fp16.onnx` | 45,297,825 | 43.20 | **2.00×** | 49.9% | 46,010,502 B（43.88 MiB） |

> 压缩比 = FP32 体积 ÷ 该档体积。脚本会先删除 ONNX 缓存再逐档下载，并把"本次新出现的文件名"与该 dtype 的预期文件名比对
> （`fp32→model.onnx`、`fp16→model_fp16.onnx`、`q8→model_quantized.onnx`），三档均匹配，见 `docs/bench_model_size.json` 的 `mappingMatches`。

**质量变化（Node 侧同模型实测，稠密向量精确 top-5）**

| 配置 | Recall@5 | 严格 Recall@5 | nDCG@5 | MRR@5 | 与基线差异 |
| --- | --- | --- | --- | --- | --- |
| FP32 基线 | 0.900 | 0.700 | 0.620 | 0.700 | — |
| INT8（q8） | 0.900 | 0.700 | 0.618 | 0.700 | nDCG −0.003，召回无变化 |
| FP16 | 0.900 | 0.700 | 0.620 | 0.700 | 与基线一致 |

测量配置：语料 `data/corpus/*.md`（3 篇）按 200 字切片 → 14 片；10 条人工标注查询；batch=8。
`Recall@5` = 命中相关切片的查询数 ÷ 查询数；`严格 Recall@5` = 答案所在切片（grade=2）被召回的比例。完整逐条结果见 `docs/BENCHMARK.md` 第 4 节。

> 结论：**INT8 与 FP16 在这套语料上没有可观测的检索质量下降**（nDCG 差 0.003 远小于 0.1 的判定阈值）。
> 该语料只有 14 个候选片，区分度有限，结论强度也有限——这一点如实写在 BENCHMARK 里。

## 2. 端侧运行

- **推理位置**：全部在 `web/worker.js`（Web Worker）里完成。服务端只收到「向量 + 原文」，`createCollection` 显式传
  `embeddingFunction: null`，从机制上排除服务端向量化；服务端代码里没有任何 embedding 调用。
- **后端选择**：`navigator.gpu` 存在 → `device:'webgpu' + dtype:'fp16'`；否则 → `device:'wasm' + dtype:'q8'`。
  页面顶部徽章常显 `后端：WebGPU · FP16` / `后端：WASM · Q8`，并用 `adapter.info` 打印实际 GPU。
- **实测（Chrome 156，NVIDIA Blackwell，详见 `docs/BROWSER_BENCH.md`）**：

  | 场景 | 模型加载 | 6 片向量化 | 查询向量化 | seekdb 检索 | 端到端 |
  | --- | --- | --- | --- | --- | --- |
  | WebGPU + FP16（冷 / 热） | 8.74 s / 0.89 s | 897 ms / **107 ms（56 片/s）** | 10.2 / 9.1 ms | 4.9 / 4.7 ms | 17.6 / 16.3 ms |
  | WASM + INT8（冷 / 热） | 12.14 s / 0.51 s | 1054 ms / 879 ms（6.8 片/s） | 7.6 / 7.2 ms | 4.6 / 4.7 ms | 15.3 / 14.1 ms |

  冷启动主要是下载模型（22–43 MB），热启动 0.5–0.9 s；WebGPU 的优势集中在**批量**向量化（约 8×），
  单条查询两者接近（一条文本撑不起 GPU 调度开销）。
  > 上表的 seekdb 检索/端到端是 **seekdb 与 Node 同机原生**时的数字。seekdb 放进 WSL 经转发后，
  > 同脚本复测得 seekdb 检索 49.1 ms、端到端 59.9 ms（向量化仍是 7.6 ms，没变）。
  > 差的 10 倍在库内检索路径，两种环境都列在 `docs/BROWSER_BENCH.md` 第 6 节。
- **自动降级**：WebGPU 初始化抛错时 worker 内部用 `wasm + q8` 重建 pipeline，把降级原因回传主线程；
  徽章变为 `后端：WASM · Q8`，日志写 `已自动降级：WebGPU 初始化失败：…`。
  另外「环境根本没有 WebGPU」也实测了：抹掉 `navigator.gpu` 后页面自动选 WASM，
  徽章 `WASM · Q8`、日志 `未检测到 WebGPU，已使用 WASM 后端`，导入与检索完整可跑（`docs/screenshots/wasm-degrade.png`）。

## 3. seekdb 使用

| 项 | 内容 |
| --- | --- |
| 接入方式 | `seekdb`（seekdb-js 1.4.0）SDK，`SeekdbClient` 服务器模式，`127.0.0.1:2882`（root/空密码，库 `test`） |
| 端口处理 | 本机 seekdb 监听 **2882**，2881 未监听；`server/config.mjs` 默认改为 2882，且未显式设 `SEEKDB_PORT` 时会按 `[2882, 2881]` 探测并握手 `select 1` 确认 |
| 集合结构 | 集合 `edge_kb` → 物理表 `c$v2$<collectionId>`：`_id` varbinary(512) 主键 / `document` string / `embedding` VECTOR(384) / `metadata` json |
| 全文索引 | `FULLTEXT KEY idx_fts (document) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart")` |
| 向量索引 | `VECTOR KEY idx_vec (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64, SYNC_MODE=ASYNC)` |
| 混合检索 | `collection.hybridSearch({ knn:{queryEmbeddings,nResults:RECALL_K}, query:{whereDocument:{$contains}}, rank:{rrf:{}}, nResults, include })` |
| 增量写入 | 每次 `add()` 后立即 `refresh_index()`（`CALL dbms_index_manager.refresh();`）；id 带批次号 `@<importId>`，重复导入不覆盖 |

**混合检索确实在 seekdb 内完成（证据见 `docs/SQL_EVIDENCE.md`）**：抓到的语句序列是

1. `SET @search_parm = ?` —— 客户端只把「查询串 + 查询向量 + `rank.rrf` 配置」打包成一个 JSON 参数；
2. `SELECT DBMS_HYBRID_SEARCH.GET_SQL('<表名>', @search_parm)` —— SQL 由 **seekdb 自己生成**；
3. 执行出来的**一条** SQL：

```sql
SELECT … (ifnull(1/(`_fts`.`_keyword_rank`+60),0) + ifnull(1/(`_vs`.`_semantic_rank`+60),0)) as `_score`
FROM (… match(`document`) against('全文索引 分词器' in natural language mode) … LIMIT 100) `_fts`
full join (… cosine_distance(`embedding`, '[…]') … ORDER BY `_distance` APPROXIMATE LIMIT 20) `_vs`
ON `_fts`.`__pk_increment` = `_vs`.`__pk_increment`
ORDER BY `_score` DESC LIMIT 5
```

两路召回、RRF 融合（`1/(rank+60)`）、排序全部发生在数据库里；客户端只收到最终结果行，
**没有**拿到两路中间结果，也不存在任何拼合/重排代码。返回的 `_score` 数值可反证这一点：
两路都排第 1 时 `_score≈0.0333`，只有单路命中时 `_score≈0.0167`，与实测完全吻合。

## 4. 状态与优化

**状态管理**

- 集合即知识库：换 `SEEKDB_COLLECTION` 即换库；「清空集合」走 `deleteCollection` 后按需重建。
- 增量导入：id = `文件名#片号@导入批次`，同一文件重复导入生成新 id，历史数据保留。
- 前端把最近一次导入/检索指标存进 `state.metrics`，可一键导出 JSON。

**优化点与实测效果（Node/CPU 与浏览器两组，均为真实测量）**

| 优化 | 做法 | 实测效果 |
| --- | --- | --- |
| 量化档位 | 只换 ONNX 权重，不动推理代码 | 体积 3.94× 压缩（FP32→INT8），检索质量 nDCG@5 0.620→0.618 |
| 推理后端 | 有 WebGPU 走 fp16，否则 WASM q8 | 热态批量向量化 **56.1 片/s（WebGPU） vs 6.8 片/s（WASM）**，约 8×；单条查询两者接近（9.1 vs 7.2 ms） |
| 批处理 | 一次 `pipe(texts[])` 处理 batch 条（默认 8，可调） | 浏览器侧 batch=8 时 WebGPU 热态 6 片 107 ms；Node/CPU 上批处理反而变慢（padding 到最长序列的开销超过并行收益），说明收益强依赖后端，见 BENCHMARK 第 6 节 |
| 模型加载分离 | 模型只加载一次，加载耗时与推理耗时分开统计 | 浏览器冷启动 8.74 s（WebGPU/fp16）/ 12.14 s（WASM/q8），热启动 0.89 s / 0.51 s；Node 侧缓存命中 129–137 ms |
| 索引刷新时机 | 每批写入后刷新一次，而不是每条刷新 | 浏览器 6 片：write 100–142 ms + refresh 345–668 ms；刷新是写入侧最贵的一段，按批刷新比按条省得多 |
| 查询侧分段计时 | 向量化、HTTP、库内检索分别计时 | 浏览器端到端 14–18 ms = 查询向量化 7–10 ms + 库内检索 4.6–4.9 ms + 往返/渲染（seekdb 同机原生）；seekdb 在 WSL 经转发时同口径为 59.9 ms，其中库内 49.1 ms；Node 侧 51.1 ms 中库内占 46.7 ms |

**关键取舍**：混合检索不在客户端做，所以"融合质量"完全由 seekdb 的 RRF 决定；客户端能优化的只剩
向量化吞吐、批量写入与刷新时机。本实现刻意让服务端保持"薄"，避免任何隐藏的服务端推理。

---

## 附 A：计时口径

| 指标 | 起点 | 终点 |
| --- | --- | --- |
| 模型加载 | pipeline 创建调用 | pipeline 就绪（Node 脚本口径） |
| 文档向量化 | 第一批 embed | 最后一批返回（不含网络） |
| 写入 + 索引刷新 | 进入 `collection.add()` | `refresh_index()` 返回（服务端计时） |
| 查询向量化 | 单条文本进入 encode | 向量返回 |
| seekdb 检索 | 进入 `hybridSearch` | 返回结果（服务端计时） |
| 端到端 | 查询文本进入 encode | 收到 HTTP 检索响应 |

重复测量取中位数；样本数与原始值见 `docs/bench_*.json`。

## 附 B：本次修掉的问题

1. `server/config.mjs` 默认 seekdb 端口是 2881，但本机实际监听 **2882** → 默认改为 2882，并加端口探测 + `select 1` 握手。
2. `server/seekdb.mjs` 的 `client.hasCollection({ name })`、`client.deleteCollection({ name })` 传参错误：
   seekdb-js 1.4.0 这两个方法收**字符串**，传对象时 `hasCollection` 直接返回 `false`。
   后果是**服务重启后第一次访问必 500**（`Duplicate entry 'edge_kb' for key 'idx_name'`），「清空集合」静默失败。
   已改为传字符串，并在元数据/表不一致时自愈重建。
3. `reset()` 吞掉了所有异常，掩盖了上面这个 bug；现在只忽略 "not found"，其余错误如实抛出。
4. 浏览器实测阶段发现 onnxruntime-web 的 wasm/mjs 默认从 jsDelivr 加载，和本地 `/vendor` 的 transformers 版本不一致时直接报
   `no available backend found`。已在 worker 里把 `env.backends.onnx.wasm.wasmPaths` 指到本地 `/vendor/.../dist/`，
   并设 `numThreads=1`（没有 COOP/COEP 就没有 SharedArrayBuffer）；现在断网也能跑。
5. seekdb 侧空闲会关连接，应用重启后首次导入报 `Can't add new command when connection is in closed state`。
   已加 `withReconnect`：命中连接类错误时重建 client/collection 并重试一次。
6. 「加载示例语料」里写了一个不存在的文件名（`seekdb-deploy.md`），实际语料是 `edge-embedding.md`；
   导致示例只导入 2 篇。已修正。下载进度按 0–1 与 0–100 两种口径兼容，不再显示 `10000%`。
