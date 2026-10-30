#!/usr/bin/env python3
"""把一批向量建成 HNSW 可用的 kNN 图（.kg），供 VSAG/hnswlib 侧装配。

例子
----
    # 本机（无 AMD GPU）：CPU 暴力 KNN 兜底，验证链路
    python3 build_graph.py --vectors data/100k-128.npy --out out/100k.kg --k 32 --M 16

    # AMD Instinct + ROCm 7.2.3：hipVS 在 GPU 上建图
    python3 build_graph.py --vectors data/1m-128.npy --out out/1m.kg --k 64 --M 32 --backend hipvs

产出
----
* ``<out>.kg``        —— 二进制图（见 kgformat.py）
* ``<out>.json``      —— 本次构建的元信息（backend/device/耗时/参数），进 benchmark 表用
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kgformat import KgGraph, assign_levels  # noqa: E402
from knn_backends import build_knn  # noqa: E402


def load_or_make_vectors(path: str | None, n: int, dim: int, seed: int) -> np.ndarray:
    if path and path != "-":
        arr = np.load(path)
        return np.ascontiguousarray(arr, dtype=np.float32)
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, dim), dtype=np.float32)


def main() -> int:
    p = argparse.ArgumentParser(description="用 hipVS/CPU 构建 HNSW 可装配的 kNN 图")
    p.add_argument("--vectors", default=None, help=".npy 向量文件；不传则生成随机向量")
    p.add_argument("--n", type=int, default=20000, help="随机生成时的点数")
    p.add_argument("--dim", type=int, default=128, help="随机生成时的维度")
    p.add_argument("--k", type=int, default=32, help="每点保留的邻居数（建议 2M~4M，通常 <=64）")
    p.add_argument("--M", type=int, default=16, help="HNSW 每层出边上限（与 seekdb 索引参数一致）")
    p.add_argument("--ef", type=int, default=200, help="记入元信息的 ef_construction（seekdb 默认 200）")
    p.add_argument("--metric", default="cosine", choices=["cosine", "l2"])
    p.add_argument("--backend", default="auto", choices=["auto", "hipvs", "cuvs", "cpu"])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", required=True, help="输出 .kg 路径")
    args = p.parse_args()

    vectors = load_or_make_vectors(args.vectors, args.n, args.dim, args.seed)
    n, dim = vectors.shape
    k = min(args.k, n)
    print(f"向量: n={n} dim={dim}  metric={args.metric}  k={k}  M={args.M}")

    t0 = time.perf_counter()
    result = build_knn(vectors, k, metric=args.metric, backend=args.backend)
    levels = assign_levels(n, args.M, seed=args.seed)

    # 入口点选层级最高的点（与 hnswlib 语义一致）
    entry_id = int(np.argmax(levels))

    graph = KgGraph(
        levels=levels,
        neighbors=result.neighbors,
        distances=result.distances,
        dim=dim,
        M=args.M,
        ef_build=args.ef,
        entry_id=entry_id,
        metric=args.metric,
    )
    kg_path = graph.save(args.out)
    total_ms = (time.perf_counter() - t0) * 1000.0

    # 同时导出裸 float32 向量，供 C++ 基准程序直接 mmap 读取（省得在 C++ 里解析 .npy）
    vec_path = kg_path.with_suffix(".vec")
    np.ascontiguousarray(vectors, dtype=np.float32).tofile(vec_path)

    meta = {
        "vectors": args.vectors or f"random(n={n},dim={dim},seed={args.seed})",
        "n": n,
        "dim": dim,
        "k": k,
        "M": args.M,
        "ef_construction": args.ef,
        "metric": args.metric,
        "backend": result.backend,
        "device": result.device,
        "exact_knn": result.exact,
        "knn_ms": round(result.elapsed_ms, 1),
        "total_ms": round(total_ms, 1),
        "levels_hist": {int(l): int(c) for l, c in zip(*np.unique(levels, return_counts=True))},
        "kg_bytes": kg_path.stat().st_size,
        "kg_path": str(kg_path),
        "host": os.uname().nodename if hasattr(os, "uname") else "windows",
    }
    meta_path = kg_path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"建图完成: backend={result.backend} device={result.device} "
        f"knn={result.elapsed_ms:.0f}ms total={total_ms:.0f}ms → {kg_path} "
        f"({meta['kg_bytes'] / 1e6:.1f} MB)"
    )
    if result.backend == "cpu":
        print("提示: 本机走的是 CPU 兜底；AMD GPU 上的数字需按 docs/RUN_ON_AMD_GPU.md 用 --backend hipvs 重跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
