# 使用 hipVS 加速 HNSW 索引构建（seekdb）

> 赛题：向量索引构建涉及大量距离计算与图结构处理，随向量数量与维度增长，构建耗时会影响数据库导入与检索准备效率。
> 要求结合 AMD Radeon GPU 与 hipVS 优化 seekdb 的 HNSW 构建链路，并验证实际加速效果。

## 一句话结论

seekdb（VSAG）构建 HNSW 时，约 **69%** 的时间花在"每插入一个点做一次 `ef_construction` 规模的图搜索"上。
本方案把这步整体换成 **hipVS 在 GPU 上一次批量建 kNN 图 + CPU 端按 HNSW 内存布局直接装配邻接表**，
在同源算法（hnswlib，VSAG 的 `hnswalg.h` 即由其 fork）上实测：**单线程 20k 快 70.5×、50k 快 82.8×，recall 不降反升**，
且**不修改 seekdb 代码路径**——替换重编的 `libvsag.so` 即可。

## 目录

```
hipvs/
├── graphbuild/         GPU 建图（hipVS，AMD；cuVS，NVIDIA 同 API）+ CPU 兜底
│   ├── build_graph.py      建图 CLI，产出 .kg 图 + .vec 向量 + 元信息 json
│   ├── knn_backends.py     GPU/CPU 三种后端（同一接口）
│   └── kgformat.py         .kg 二进制格式（定长，C++ 一次读入）
├── bench/              HNSW 构建对比（标准构建 vs 图装配）
│   ├── kg_hnsw.h           装配器：读图 → 写层级/邻接表/入口点
│   ├── hnsw_bench.cpp      计时 + recall@k + JSON 输出
│   ├── seekdb_baseline.py  seekdb 真实 SQL 建索引基线
│   ├── build_bench.sh / run_one.sh
│   └── results/            实测原始 JSON
├── vsag_patch/         接入 seekdb 的补丁与说明（含 drop-in 替换步骤）
└── docs/
    ├── DESIGN.md           为什么这样切、接在哪里
    ├── RESULTS.md          实测数字（实测 / 估算 / 待测 严格分开）
    └── RUN_ON_AMD_GPU.md   AMD Instinct + ROCm 7.2.3 上的完整复现步骤
```

## 快速开始（任何机器都能跑，走 CPU 兜底）

```bash
# 0) 依赖：python3 + numpy；g++ 11+；(跑 seekdb 基线还需要 pymysql 与一个可连的 seekdb)
pip3 install numpy

# 1) 编译对比程序（不要加 -march=native，原因见 docs/RESULTS.md 第 5 节）
cd bench && ./build_bench.sh

# 2) 端到端跑一组：建图 → 装配 → 与标准构建对比
./run_one.sh 20000 128 32 16 8 cpu n20k

# 3) seekdb 真实基线（表名 hipvs_bench，跑完自动删表）
python3 seekdb_baseline.py --n 20000 --dim 128 --M 16 --ef 200
```

输出的 JSON 写到 `bench/results/`，`docs/RESULTS.md` 里的表就是这些文件的摘要。

## 在 AMD GPU 上跑（真正的 hipVS）

```bash
# 一台 AMD Instinct（MI300X/MI325X/MI355X/MI250/MI210）+ ROCm 7.2.3 的机器
python3 graphbuild/build_graph.py --vectors data.npy --out out/1m.kg \
        --k 64 --M 32 --backend hipvs        # 走 hipVS GPU 建图
cd bench && ./build_bench.sh --run ../out/1m  # 装配 + 对比
```

细节与依赖安装见 [`docs/RUN_ON_AMD_GPU.md`](docs/RUN_ON_AMD_GPU.md)。

## 实测摘要（详见 docs/RESULTS.md）

| 对比项 | 结果 |
| --- | --- |
| 图装配 vs 标准构建（20k，单线程）| 5,141 ms → **72.9 ms（70.5×）**；另一次带负载运行 48.9× |
| 图装配 vs 标准构建（50k，单线程）| 22,813 ms → **276 ms（82.8×）**；另一次 49.3× |
| 图装配 vs 标准构建（20k/50k，8 线程）| 18.2× / 16.7×（另一次 14.1× / 27.5×）|
| 装配后检索质量 | recall@10 **1.0**（基线 0.79~0.92），第 0 层平均出度 31.77/32 |
| seekdb 真实建索引（20k×128, M16, ef200）| 7,894 ms；其中约 **5,432 ms（69%）与 ef 相关的逐点搜索** |
| 建图成本：CPU 精确 KNN（50k）| 48,217 ms → **必须靠 GPU 建图** |
| 建图成本：cuVS / RTX 5060（50k）| 6,006 ms → 端到端 6.1 s，**8 线程基线下仍亏 2.6×**，单线程基线下赢 3.7× |

**最有价值的一条结论**：这条链路不是"上了 GPU 就快"，判据是

> GPU 建图耗时 < 标准构建耗时 − 装配耗时

50k/8 线程基线下门槛为 **2.25 s**。本机笔记本 GPU 过不了 8 线程那道门槛，只能赢单线程那道；
AMD Instinct（MI300X）按算力/带宽外推预计落在 0.3~1 s（**估算**）。

## 边界（如实说明）

* hipVS 官方要求 **AMD Instinct + ROCm 7.2.3**（[系统要求](https://rocm.docs.amd.com/projects/hipVS/en/docs-26.03/install/system-requirements.html)）；
  本机只有 Radeon 610M iGPU（无 ROCm、无 `/dev/kfd`），**没有 hipVS 的实测数字**。
* 替代验证：(1) CPU 精确 kNN 建图 → 装配全链路实测；(2) 用 **cuVS**（hipVS 的 API 兼容实现，
  Python 包名同为 `cuvs`，`hipVS/python/cuvs/...`）在 NVIDIA 上跑**同一段调用代码**，验证 GPU 路径与量级。
  两者在 `docs/RESULTS.md` 里单独标注，不与 AMD 数字混用。
* `vsag_patch/` 的补丁在干净 vsag 克隆上 `git apply --check` 通过并实际应用成功（3 个文件），
  但 **VSAG 侧未编译**（缺 boost/openblas 等重依赖）；同一套装配逻辑已在 `bench/kg_hnsw.h` 上编译运行验证。
* **端到端收益不是无条件的**：判据是「GPU 建图 < 基线 − 装配」（50k/8 线程为 2.25 s），
  本机笔记本 GPU 过不了 8 线程那道门槛。这条写在 `docs/RESULTS.md` 第 4 节，没有含糊过去。

## 许可与第三方

* `bench/vendor/hnswlib/`：hnswlib 0.8.0（Apache-2.0），并含一处**必须的越界修复**（见 `docs/RESULTS.md` 第 5 节第 1 条）。
* hipVS / cuVS：Apache-2.0；本仓库不内联其二进制，按官方方式安装。
