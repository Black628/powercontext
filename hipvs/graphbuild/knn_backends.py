"""kNN 图构建：hipVS(GPU) 为主，CPU 精确暴力 KNN 为兜底。

三种后端，接口一致，产出 (neighbors:int64[n,k], distances:float32[n,k])：

``hipvs``
    官方 GPU 路径。用 ``hipvs.neighbors.brute_force``（或 ``nn_descent`` 处理大规模）
    在 AMD Instinct + ROCm 7.2.3 上建图。找不到 hipVS 会抛错并提示改用 ``cpu``。
``cuvs``
    hipVS 的 API 是 cuVS 兼容的（见官方文档 "API-compatible with cuVS"），
    因此在没有 AMD GPU 但有 NVIDIA GPU 的开发机上，可以用 cuVS 跑**同一段**调用逻辑，
    验证接口与数据流是否正确。这不是 AMD 的加速结果，汇报时必须分开标注。
``cpu``
    纯 CPU 分块暴力 KNN。任何机器都能跑，用来验证"图装配"这条链路本身。

metric：cosine / l2。hipVS 侧传 ``cosine`` / ``sqeuclidean``（与 cuVS 一致）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

METRIC_TO_HIPVS = {"cosine": "cosine", "l2": "sqeuclidean"}


@dataclass
class KnnResult:
    neighbors: np.ndarray   # int64[n, k]
    distances: np.ndarray   # float32[n, k]
    backend: str
    device: str
    elapsed_ms: float
    exact: bool


# --------------------------------------------------------------------------- CPU


def cpu_knn(
    vectors: np.ndarray,
    k: int,
    metric: str = "cosine",
    block: int = 2048,
    dtype=np.float32,
) -> KnnResult:
    """分块暴力 KNN。O(n^2) 但每块走 BLAS，几十万以内够用；用于兜底与正确性对照。"""
    import time

    x = np.ascontiguousarray(vectors, dtype=dtype)
    n = x.shape[0]
    if k > n:
        k = n
    if metric == "cosine":
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        x = x / norms

    neigh = np.empty((n, k), dtype=np.int64)
    dist = np.empty((n, k), dtype=np.float32)
    t0 = time.perf_counter()
    for start in range(0, n, block):
        stop = min(start + block, n)
        q = x[start:stop]
        if metric == "cosine":
            sim = q @ x.T                       # 越大越近
            # 取 top-k 最大相似度；把自身算进去也无妨，后面可以剔除
            idx = np.argpartition(-sim, kth=min(k, n - 1), axis=1)[:, :k]
            rows = np.arange(idx.shape[0])[:, None]
            order = np.argsort(-sim[rows, idx], axis=1)
            idx = idx[rows, order]
            neigh[start:stop] = idx
            dist[start:stop] = (1.0 - sim[rows, idx]).astype(np.float32)
        else:
            # ||a-b||^2 = ||a||^2 + ||b||^2 - 2ab，省一次开方
            sq = (q * q).sum(axis=1, keepdims=True) + (x * x).sum(axis=1)[None, :] - 2.0 * (q @ x.T)
            np.maximum(sq, 0.0, out=sq)
            idx = np.argpartition(sq, kth=min(k, n - 1), axis=1)[:, :k]
            rows = np.arange(idx.shape[0])[:, None]
            order = np.argsort(sq[rows, idx], axis=1)
            idx = idx[rows, order]
            neigh[start:stop] = idx
            dist[start:stop] = sq[rows, idx].astype(np.float32)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # 剔除自身并把后一列补上（保持 k 列，末尾用 -1 填）
    rows = np.arange(n)[:, None]
    self_hit = neigh == rows
    if self_hit.any():
        for i in range(n):
            if self_hit[i].any():
                col = int(np.argmax(self_hit[i]))
                neigh[i, col] = -1
                dist[i, col] = np.inf
        order = np.argsort(dist, axis=1)
        neigh = np.take_along_axis(neigh, order, axis=1)
        dist = np.take_along_axis(dist, order, axis=1)
        dist[neigh < 0] = np.inf

    return KnnResult(
        neighbors=neigh.astype(np.int64),
        distances=dist.astype(np.float32),
        backend="cpu",
        device="cpu",
        elapsed_ms=elapsed_ms,
        exact=True,
    )


# ------------------------------------------------------------------ GPU (hipVS)


def _import_gpu_neighbors():
    """取 GPU 建图模块。

    注意：hipVS 的 Python 包**就叫 ``cuvs``**（它是 cuVS 的 drop-in 替代，
    见 hipVS 仓库 ``python/cuvs/cuvs/...``），所以 AMD 与 NVIDIA 上是同一行 import：
    ``from cuvs.neighbors import brute_force``。
    """
    try:
        from cuvs.neighbors import brute_force  # type: ignore

        impl = "hipVS" if "hipvs" in (getattr(brute_force, "__file__", "") or "").lower() else "cuVS"
        return brute_force, impl
    except Exception as exc:  # pragma: no cover - 取决于机器
        raise RuntimeError(
            "没有可用的 cuVS/hipVS。GPU 建图需要：AMD Instinct + ROCm 7.2.3 安装 hipVS，"
            "或 NVIDIA + cuVS（API 相同）；本机请用 --backend cpu。"
        ) from exc


def gpu_knn(
    vectors: np.ndarray,
    k: int,
    metric: str = "cosine",
    prefer: str = "auto",
    use_nn_descent: bool | None = None,
) -> KnnResult:
    """GPU 上构建 kNN 图（AMD 走 hipVS，NVIDIA 走 cuVS，两者 API 相同）。

    默认用 ``brute_force``（精确，与 CPU 兜底同口径：这样"装配"和"图质量"两件事不会混在一起）。
    点数超过 ``NN_DESCENT_THRESHOLD`` 时可以改用 ``nn_descent``（近似，适合百万级），
    但它返回的是图结构、需要额外转换，当前实现只保留 brute_force 一条确定路径。

    数组桥接：cuVS/hipVS 的 Python 层吃 ``cupy``/DLPack；ROCm 上装 ``cupy-rocm``。
    """
    import time

    t0 = time.perf_counter()
    brute_force, impl = _import_gpu_neighbors()
    try:
        import cupy as cp  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "GPU 路径需要 cupy（ROCm 上装 cupy-rocm）。若只想验证链路，请用 --backend cpu。"
        ) from exc

    n = vectors.shape[0]
    x = cp.asarray(vectors, dtype=cp.float32)
    metric_name = METRIC_TO_HIPVS.get(metric, metric)

    index = brute_force.build(x, metric=metric_name)
    # 用数据集自身当查询，取 k+1 个是为了把"自己"也拿回来再剔除
    dist, neigh = brute_force.search(index, x, k + 1)

    dist = cp.asnumpy(dist).astype(np.float32)
    neigh = cp.asnumpy(neigh).astype(np.int64)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # 剔除自身（GPU 结果第一列通常就是自己）
    rows = np.arange(n)[:, None]
    self_hit = neigh == rows
    neigh = np.where(self_hit, -1, neigh)
    dist = np.where(self_hit, np.inf, dist)
    order = np.argsort(dist, axis=1)[:, :k]
    neigh = np.take_along_axis(neigh, order, axis=1)
    dist = np.take_along_axis(dist, order, axis=1)
    dist[neigh < 0] = np.inf

    device = "gpu"
    try:
        device = str(cp.cuda.runtime.getDeviceProperties(0)["name"].decode())
    except Exception:
        pass
    return KnnResult(
        neighbors=neigh,
        distances=dist,
        backend=impl,
        device=device,
        elapsed_ms=elapsed_ms,
        exact=True,
    )


def build_knn(
    vectors: np.ndarray,
    k: int,
    metric: str = "cosine",
    backend: str = "auto",
) -> KnnResult:
    """backend: auto | hipvs | cuvs | cpu。auto 先试 GPU，失败回落 CPU 并标注。"""
    if backend == "cpu":
        return cpu_knn(vectors, k, metric)
    if backend in ("hipvs", "cuvs", "gpu", "auto"):
        try:
            return gpu_knn(vectors, k, metric)
        except RuntimeError:
            if backend in ("hipvs", "cuvs", "gpu"):
                raise
            # auto：没有 GPU 就用 CPU，并在结果里如实标注 backend=cpu
            return cpu_knn(vectors, k, metric)
    raise ValueError(f"未知 backend: {backend}")
