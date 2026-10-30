# 接入 seekdb：VSAG 侧补丁与替换步骤

## 为什么动 VSAG 而不是 seekdb

seekdb 的建索引链路是：

```
CREATE VECTOR INDEX
  └─ ob_vsag_adaptor            (seekdb)
       └─ vsag::Index::Build    (libvsag.so)
            └─ HNSW::build      (src/index/hnsw.cpp:103)
                 └─ alg_hnsw_->addPoint(...) ← 每个点做一次 ef_construction 规模的搜索（贵）
```

要消灭的正是最后那行。所以补丁落在 VSAG，**seekdb 一行代码都不用改**：
重编出的 `libvsag.so` 是 drop-in 替换，而且不设环境变量时行为与原生完全一致（零回归风险）。

## 补丁内容

`0001-hnsw-build-from-hipvs-kg-graph.patch`：

| 文件 | 改动 |
| --- | --- |
| `src/algorithm/hnswlib/kg_build.h` | **新增**：`.kg` 图文件读取 + `assemble_from_kg_graph()`（装配算法，与 `hipvs/bench/kg_hnsw.h` 同源）|
| `src/algorithm/hnswlib/hnswalg.h` | 新增 `BuildFromKgGraph(...)` 声明 |
| `src/index/hnsw.cpp` | `HNSW::build` 里加分支：`getenv("VSAG_KG_GRAPH")` 存在则读图装配，否则走原生 `addPoint` 循环 |

成员命名与 hnswlib 上游的差异已在 `kg_build.h` 顶部注明：
`linkLists_`→`link_lists_`、`data_level0_memory_`（`char*`）→ `BlockManager*`、`element_levels_`（`vector<int>`）→ `int*`。

## 应用与编译

```bash
# 1) 取 vsag 源码（版本要和 seekdb 依赖一致，本仓库按 1.1.0 的头文件结构编写）
git clone https://github.com/oceanbase/vsag && cd vsag
git apply /path/to/hipvs/vsag_patch/0001-hnsw-build-from-hipvs-kg-graph.patch

# 2) 把 kg_build.h 放到补丁期望的位置
cp /path/to/hipvs/vsag_patch/kg_build.h src/algorithm/hnswlib/

# 3) 编译（vendored 依赖；本机实测 clone 里的 extern/ 自带绝大多数依赖，
#    boost / openblas / spdlog 需要联网下载。国内可用 vsag 的阿里云缓存）
mkdir -p build_release && cd build_release
cmake .. -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF -DENABLE_TOOLS=OFF
cmake --build . -j"$(nproc)"
ls -lh libvsag*.so
```

## 替换进 seekdb

```bash
# 先备份原库
cp <seekdb>/deps/3rd/usr/local/oceanbase/deps/devel/lib/vsag_lib/libvsag.so{,.bak}
# 覆盖
cp build_release/libvsag.so <seekdb>/deps/3rd/usr/local/oceanbase/deps/devel/lib/vsag_lib/libvsag.so
```

`libvsag.so` 是动态链接的，替换后重启 seekdb 即可，**不需要重编 seekdb**。
若 seekdb 起不来，多半是 ABI 不匹配（vsag 版本对不上），换回备份即可。

## 使用

```bash
# 1) 用 hipVS/CPU 建好图（见 docs/RUN_ON_AMD_GPU.md）
# 2) 让 VSAG 用这张图建索引
export VSAG_KG_GRAPH=/abs/path/out/100k.kg
# 3) 正常建索引即可
python3 bench/seekdb_baseline.py --n 100000 --dim 128 --M 32 --ef 200
```

不设 `VSAG_KG_GRAPH` 时走原生路径；设了但图上点数/维度与数据不符时，`HNSW::build` 会直接报错返回
（`CHECK_ARGUMENT`），不会静默产出一个错误索引。

## 验证状态（如实标注）

| 项 | 状态 |
| --- | --- |
| 补丁文本可应用性 | ✅ 在干净克隆上 `git apply --check` 通过，并实际 apply 成功（3 个文件：1 新增 + 2 修改）|
| 装配算法正确性 | ✅ 同一算法在 `hipvs/bench/kg_hnsw.h`（hnswlib）上编译运行，recall/加速比有实测（`docs/RESULTS.md`）|
| VSAG 侧编译通过 | ❌ **未验证**：vsag 需要 boost/openblas/spdlog 等重依赖，本机未完成完整构建 |
| seekdb 端到端加速 | ❌ **未验证**，需 AMD Instinct 机器（`docs/RUN_ON_AMD_GPU.md` 第 6 节）|

```bash
# 可复现的验证方式（已在干净克隆上跑过）
git -c core.autocrlf=false clone https://github.com/oceanbase/vsag vsag-check
cd vsag-check && git config core.autocrlf false
git apply --check /path/to/hipvs/vsag_patch/0001-hnsw-build-from-hipvs-kg-graph.patch && echo "patch applies"
```

> Windows 上克隆 vsag 时 git 会把文件转成 CRLF，导致补丁上下文（LF）对不上——
> 所以验证时必须先 `git config core.autocrlf false`。Linux 上没有这个问题。
