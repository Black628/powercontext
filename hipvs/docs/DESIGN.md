# 设计：用 hipVS 加速 seekdb 的 HNSW 索引构建

## 1. 问题在哪

seekdb 的向量索引走 VSAG（`LIB=VSAG`），HNSW 构建时的开销几乎全在**每插入一个点都要做一次图搜索**：
按 `ef_construction`（seekdb 默认 200）在已有图上贪心扩展，算 `ef_construction × log N` 次距离。

这就是为什么构建耗时同时随 **点数** 和 **ef_construction** 增长（本机实测，见 `RESULTS.md` 第 2 节）：

| seekdb 建索引（20k×128, M=16） | 耗时 |
| --- | --- |
| ef_construction=200 | 7,894 ms |
| ef_construction=40 | 3,332 ms |
| ef_construction=10 | 见 RESULTS.md（接近固定开销下限） |

而**距离计算本身是高度可并行的批量运算**——这正是 GPU 擅长的。hipVS 提供 GPU 上的
brute-force / NN-descent / CAGRA 等建图原语，官方定位就是 "Fast index build" 与
"Interoperability (build on GPU, deploy on CPU)"。

## 2. 总体思路：把"逐点搜索"换成"一次批量建图 + 邻接表装配"

HNSW 插入一个点要做两件事：

1. **找候选邻居**：在图上搜索出 `ef_construction` 个近邻（贵，且串行）；
2. **选边并写入**：启发式裁剪到 `M` 条、更新反向边（便宜，O(M)）。

我们的做法是把第 1 步整体搬到 GPU：

```
                 hipVS (GPU)                      CPU (装配器)
向量 ──► 批量 kNN 图（每点 k 个近邻+距离）──► 层级 + 邻接表直接写入 HNSW 内存
         brute_force / nn_descent              启发式裁剪 / 反向边
```

这样 HNSW 构建时**不再做任何图搜索**，代价从 `O(N · ef · log N · d)` 降到
`graph(GPU) + O(N · k · d)`（k 通常 32~64，且这一步是流式的）。

### 为什么可行

* HNSW 的搜索结果本质就是"当前点的候选邻居集合"，GPU 上用精确/近似的 kNN 图可以给出**更好**的候选；
* 装配端只依赖 HNSW 的内存布局（层级、邻接表、入口点），不依赖搜索路径，所以
  `kg_hnsw.h` 里 200 行代码即可完成；
* 层级仍然按 HNSW 的标准分布抽样（`-log(U)·mL`），上层图由同一张 kNN 图按层级过滤得到，
  因此搜索语义不变。

## 3. 与 seekdb / VSAG 的接入点

seekdb 的构建链路：`CREATE VECTOR INDEX` → `ob_vsag_adaptor` → `vsag::Index::Build(Dataset)`
→ `HNSW::build()`（`src/index/hnsw.cpp:103`）→ 循环 `alg_hnsw_->addPoint(...)`。

补丁只需要动两处（见 `vsag_patch/`）：

| 位置 | 改动 |
| --- | --- |
| `src/index/hnsw.cpp` `HNSW::build` | 若设置环境变量 `VSAG_KG_GRAPH=<路径>`，则读图并调用装配函数，替代 addPoint 循环 |
| `src/algorithm/hnswlib/hnswalg.h` | 新增 `BuildFromKgGraph(levels, neighbors, vectors, n, k, M, M0, entry)`，按 HNSW 内存布局写入 |

这样**不需要改 seekdb 一行代码**：重编出的 `libvsag.so` 直接替换
`deps/3rd/usr/local/oceanbase/deps/devel/lib/vsag_lib/libvsag.so` 即可（drop-in），
`CREATE VECTOR INDEX` 自动走 GPU 建图；不设环境变量时行为与原生完全一致（零回归风险）。

> 为什么不在 seekdb 里调用 hipVS：seekdb 是 C++ 服务，hipVS 的 Python/C++ API 都需要
> ROCm 运行时。把 GPU 建图做成**离线一步**（`graphbuild/build_graph.py`）再让 VSAG 读图，
> 既避免在 seekdb 主进程里引入 ROCm 依赖，也让"建图"可以独立调参、缓存、复用。

## 4. 本仓库验证了什么 / 没验证什么

因为 hipVS 官方只支持 **AMD Instinct（MI355X/MI325X/MI300X/MI250/MI210）+ ROCm 7.2.3**，
而本机只有 AMD Radeon 610M iGPU（无 ROCm、无 `/dev/kfd`）与一块 NVIDIA RTX 5060，
所以：

| 项 | 状态 |
| --- | --- |
| kNN 图格式、层级抽样、装配算法 | **已实现并在 CPU 建图下实测**（`bench/`） |
| HNSW 装配 vs 标准构建的提速 | **已实测**：单线程 20k 为 70×、50k 为 83× |
| seekdb 真实构建耗时基线 | **已实测**：20k×128/M16/ef200 = 7,894 ms |
| 装配后 recall 不下降 | **已实测**：recall@10 均为 1.0（≥ 基线的 0.79~0.92） |
| hipVS 在 AMD GPU 上的建图耗时 | **未实测**（无 AMD GPU），给出脚本与运行步骤 |
| VSAG 补丁编译通过 | **未编译验证**（缺 vsag 的重依赖），补丁按源码结构编写并通过 `git apply --check` |

`RESULTS.md` 把"实测"和"估算/待测"严格分开，没有任何把 CPU 数字说成 GPU 数字的地方。

## 5. 参数与取舍

* `k`（图里每点邻居数）：32~64。太小会降低召回，太大会让装配阶段的距离计算变多；`k ≥ M` 是前提。
* `M` / `M0`：与 seekdb 索引参数一致（`M0 = 2M`），装配时用 HNSW 官方启发式裁剪。
* 上层处理：按 `level >= l` 过滤同一张 kNN 图，不单独建图（省 GPU 时间与显存）。
* 反向边：装配完补一轮反向边（受 `M0`/`M` 容量约束），否则单向图会显著掉召回。
* 度量：图用 cosine 建（seekdb 侧 `DISTANCE=COSINE` 对齐），装配端归一化后用 L2 距离等价计算。
