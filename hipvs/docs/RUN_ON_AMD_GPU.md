# 在 AMD Instinct + ROCm 上复现（真正的 hipVS 路径）

本机没有 AMD GPU（只有 Radeon 610M iGPU，无 ROCm、无 `/dev/kfd`），所以下面这些步骤是**待执行**的清单，
不是已经跑过的记录。跑完后把数字填回 `docs/RESULTS.md` 第 4 节。

## 1. 环境要求（官方口径）

hipVS 的[系统要求](https://rocm.docs.amd.com/projects/hipVS/en/docs-26.03/install/system-requirements.html)：

| 项 | 要求 |
| --- | --- |
| ROCm | **7.2.3** |
| OS | Ubuntu 22.04 / 24.04 |
| GPU | **AMD Instinct**：MI355X(gfx950) / MI325X·MI300X(gfx942) / MI250·MI210(gfx90a) |
| Python | 3.10 ~ 3.13 |

> 消费级 Radeon（含本机的 610M iGPU）不在支持列表里；即使有 `/dev/kfd` 也不保证可用。
> 拿到 Instinct 实例（AMD Developer Cloud / 竞赛提供的 Radeon 或 Instinct 机器）后按下述步骤走。

## 2. 装 ROCm 与 hipVS

```bash
# 2.1 ROCm 7.2.3（Ubuntu 22.04 示例，版本号按官方仓库替换）
sudo apt update && sudo apt install -y wget
wget https://repo.radeon.com/amdgpu-install/7.2.3/ubuntu/jammy/amdgpu-install_7.2.3.*.deb
sudo apt install -y ./amdgpu-install_*.deb
sudo amdgpu-install -y --usecase=rocm

# 2.2 验证 GPU 可见
rocminfo | grep -E "Name:|gfx" | head -10
rocm-smi --showproductname

# 2.3 Python 环境（官方建议 conda，Miniforge 即可）
conda create -n hipvs python=3.11 -y && conda activate hipvs
pip install cupy-rocm-6x numpy        # ROCm 上的 cupy

# 2.4 装 hipVS
# 方式 A：官方发行包（按 install 文档的渠道，若不可用走方式 B）
# 方式 B：源码编译（仓库自带 build.sh）
git clone https://github.com/AMD-Ecosystem/hipVS.git
cd hipVS && ./build.sh libcuvs python
# 产物是名为 cuvs 的 Python 包（hipVS 是 cuVS 的 drop-in 替代，包名沿用 cuvs）

# 2.5 验证：这一步通过说明后面全部可用
python -c "from cuvs.neighbors import brute_force; print('hipVS (as cuvs) OK')"
```

## 3. 用 GPU 建图

```bash
# 3.1 准备向量（.npy，float32，行=点）。也可以用随机向量先验证链路
cd hipvs
python3 graphbuild/build_graph.py --vectors data/1m-128.npy --out out/1m.kg \
        --k 64 --M 32 --backend hipvs

# 期望输出类似：
#   建图完成: backend=hipVS device=AMD Instinct MI300X knn=<GPU 毫秒> total=... → out/1m.kg
# 注意 backend 字段会显示 hipVS（代码按 cuvs 包的安装路径判断）
```

`k`（图里每点邻居数）建议 `2M ~ 4M`；`M` 要和 seekdb 索引参数一致。

## 4. 装配并对比

```bash
cd bench && ./build_bench.sh --run ../out/1m
# 输出：标准构建 ms / 图装配 ms / 加速比 / recall@10 / 第 0 层平均出度
```

这一步在 CPU 上跑（装配不需要 GPU），所以即使在 GPU 机器上也很快。

## 5. 接进 seekdb

1. 按 `vsag_patch/README.md` 重编 `libvsag.so`（带 `VSAG_KG_GRAPH` 支持）；
2. 用新的 `libvsag.so` 替换
   `<seekdb>/deps/3rd/usr/local/oceanbase/deps/devel/lib/vsag_lib/libvsag.so`；
3. 起 seekdb 后执行建索引：

```bash
export VSAG_KG_GRAPH=/abs/path/out/1m.kg
python3 bench/seekdb_baseline.py --n 100000 --dim 128 --M 32 --ef 200
# 与不设环境变量的一次对比，即 seekdb 端到端的真实加速比
```

## 6. 需要记录的数字（填回 RESULTS.md 第 4 节）

- [ ] hipVS 建图耗时（`out/*.json` 的 `knn_ms`）与 GPU 型号/ROCm 版本
- [ ] 装配耗时（`bench_*.json` 的 `build_kg_ms`）
- [ ] 端到端 = 建图 + 装配，对比 seekdb 原生建索引（`seekdb-n*.json` 的 `build_index_ms`）
- [ ] 装配后 seekdb 侧检索质量（recall@10 或业务评测集），确认与原生构建持平
- [ ] GPU 显存峰值（1M×128 的图约 `1M×64×(8+4)` = 768 MB，需确认放得下）

## 7. 常见坑

* `from hipvs.neighbors import ...` 会 `ModuleNotFoundError`——hipVS 的包名是 `cuvs`，上面的 import 才是对的。
* 显存不够时把 `--k` 从 64 降到 32，或分批调用 `brute_force.search` 按行分块写图。
* `brute_force` 是精确 KNN，复杂度 `O(n²·d)`；百万级以上建议改用 `nn_descent`（图质量略降，需重新评估 recall）。
