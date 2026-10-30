# 浏览器端实测（WebGPU / WASM）

本文记录**在真实 Chrome 里跑出来的**端侧推理与检索数字，用来补上此前缺失的浏览器侧证据。
所有数值由 `scripts/bench-browser.mjs` 通过 CDP 驱动页面采集，原始数据在 `docs/bench_browser_*.json`，
截图在 `docs/screenshots/`。

## 0. 固定条件

| 项 | 值 |
| --- | --- |
| 设备 / CPU | AMD Ryzen 9 8940HX with Radeon Graphics（16 核 32 线程） |
| 内存 | 15.3 GB |
| 操作系统 | Windows 11 家庭版中文版 10.0.26200 (x64) |
| 浏览器 | Chrome 156.0.0.0（headless=new，`--remote-debugging-port` 驱动） |
| WebGPU 适配器 | `nvidia / blackwell`（页面里 `adapter.info` 读取，硬件 GPU，非 SwiftShader） |
| 模型 | `Xenova/all-MiniLM-L6-v2`，384 维；WebGPU 用 `fp16`（43.20 MiB），WASM 用 `q8`（21.91 MiB） |
| 运行时 | `@huggingface/transformers` 3.8.1 + onnxruntime-web（wasm/mjs 文件由后端 `/vendor/` 本地托管，不走 CDN） |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0`，`@oceanbase/seekdb-node` 1.4.0 起的本地 server，`127.0.0.1:61188` |
| 集合 | `edge_kb`：`VECTOR(384)` + HNSW(cosine) + `FULLTEXT ... WITH PARSER ik` |
| 语料 | `data/corpus/*.md`（3 篇 → 420 字切片 6 片），查询 10 条固定的中文问题 |
| 排序融合 | `hybridSearch({knn, query, rank:{rrf:{}}})`，`RECALL_K=20`，`TopK=5` |
| 采样 | 检索跑 5 次取**中位数**（页面上「连续 5 次取中位数」），表格中的 ms 均为中位数 |
| 冷 / 热 | 冷 = 删掉浏览器 profile（含 Cache Storage 里的模型）后首次加载；热 = 同 profile 二次加载 |

## 1. 结果

| 运行 | 后端 / 精度 | 模型加载 | 分片 | 向量化 | 吞吐 | 写入 | 索引刷新 | 查询向量化 | seekdb 检索 | 端到端 | 命中 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wasm-cold（冷启动） | wasm / q8 | 12.14 s | 6 片 | 1054 ms | 5.7 片/s | 108 ms | 668 ms | 7.6 ms | 4.6 ms | 15.3 ms | 5 |
| wasm（热） | wasm / q8 | 0.51 s | 6 片 | 879 ms | 6.8 片/s | 142 ms | 561 ms | 7.2 ms | 4.7 ms | 14.1 ms | 5 |
| webgpu-cold（冷启动） | webgpu / fp16 | 8.74 s | 6 片 | 897 ms | 6.7 片/s | 100 ms | 446 ms | 10.2 ms | 4.9 ms | 17.6 ms | 5 |
| webgpu（热） | webgpu / fp16 | 0.89 s | 6 片 | 107 ms | **56.1 片/s** | 110 ms | 345 ms | 9.1 ms | 4.7 ms | 16.3 ms | 5 |
| wasm-degrade（抹掉 WebGPU） | wasm / q8 | 16.96 s | 6 片 | — | — | — | — | — | 4.9 ms | 15.8 ms | 5 |

读法：

- **冷启动**的 8.7–17 s 主要是下载模型（q8 22 MB / fp16 43 MB）+ 首次建 ONNX 会话；热启动降到 0.5–0.9 s，
  因为模型进了浏览器 Cache Storage。
- **WebGPU 的优势在批处理**：热态下 6 片只用 107 ms（56 片/s），WASM 是 879 ms（6.8 片/s），约 **8×**。
  冷态下 WebGPU 反而慢（897 ms），因为首次要编译着色器。
- **单条查询**两者接近（7.2 ms vs 9.1 ms）：一条文本撑不起 GPU 的调度开销，WebGPU 的快要在批量里才体现。
- **seekdb 检索稳定在 4.6–4.9 ms**（库内完成两路召回 + RRF），端到端 14–18 ms，其中查询向量化占大头。

## 2. 降级验证

`bench-browser.mjs --no-webgpu` 会在页面脚本执行前抹掉 `navigator.gpu`，模拟"运行环境不支持 WebGPU"：

```bash
node scripts/bench-browser.mjs --label wasm-degrade --reset --no-webgpu --url http://127.0.0.1:7860/
```

结果（`docs/bench_browser_wasm-degrade.json`、`docs/screenshots/wasm-degrade.png`）：

- 页面自动选 `wasm + q8`，徽章显示 `后端：WASM · Q8`；
- 日志写明 `浏览器 WebGPU：不可用；推理后端=wasm（自动降级 WASM）` 与 `当前浏览器未检测到 WebGPU，已使用 WASM 后端`；
- 导入 6 片、检索命中 5 条，流程完整可跑，端到端 15.8 ms。

另外源码里还有一条更严格的降级路径：`navigator.gpu` 存在但 `pipeline(...device:'webgpu')` 抛错时，
worker 会用 `wasm + q8` 重建 pipeline 并把原因回传，页面日志显示 `已自动降级：WebGPU 初始化失败：…`。
这一条在正常机器上不会触发，属于防御性代码。

## 3. 模型体积与检索质量

体积与质量的口径见 `docs/BENCHMARK.md` 第 2、4 节（真实下载统计 + 人工标注查询集）：

| 配置 | ONNX 体积 | 压缩比 | Recall@5 | nDCG@5 |
| --- | --- | --- | --- | --- |
| FP32 基线 | 86.20 MiB | 1.00× | 0.900 | 0.620 |
| INT8（q8） | 21.91 MiB | 3.94× | 0.900 | 0.618 |
| FP16 | 43.20 MiB | 2.00× | 0.900 | 0.620 |

浏览器侧两种后端分别用 q8 / fp16，与上表一一对应；检索质量不因换后端而变化（同一模型权重）。

## 4. 复现

```bash
# 1) 起 seekdb（示例：本地 Docker）
docker run -d --name seekdb -p 2882:2882 oceanbase/seekdb

# 2) 起应用
cd stepfun && npm install && SEEKDB_PORT=2882 npm start

# 3) 跑浏览器实测（需要本机 Chrome；脚本自己起 headless Chrome）
node scripts/bench-browser.mjs --label webgpu --reset --url "http://127.0.0.1:7860/?device=webgpu"
node scripts/bench-browser.mjs --label wasm   --reset --url "http://127.0.0.1:7860/?device=wasm"
node scripts/bench-browser.mjs --label wasm-degrade --reset --no-webgpu
node scripts/summarize-browser.mjs      # 汇总成上面那张表
```

`?device=webgpu|wasm` 用于固定后端做对照实验；不带参数时按 WebGPU 可用性自动选择。

## 5. 限制（如实说明）

- 语料只有 3 篇 / 6 片，Top-5 覆盖了候选池的大部分，**延迟随数据规模的变化没有测**；
  检索延迟（4.6 ms）只代表这个量级的数据。
- 冷启动耗时受网络影响很大（下载 22–43 MB），本表的冷启动数字是在当前网络环境下测的。
- WebGPU 适配器来自 headless Chrome；有头模式下的窗口合成略有不同，但推理后端与模型一致。
- 表格里的“向量化”是页面自报的 `performance.now()` 区间，不含网络往返；端到端包含一次 HTTP 往返。
