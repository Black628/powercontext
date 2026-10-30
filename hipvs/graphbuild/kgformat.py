"""KG 图文件格式：hipVS/CPU 建出的 kNN 图 → 交给 VSAG/hnswlib 侧做 HNSW 邻接表装配。

设计取舍
--------
* 只存**第 0 层**的 kNN 邻接（每点 k 个最近邻 + 距离）。
  HNSW 的上层是从同一批点里按层级抽稀出来的，装配端按 ``level >= l`` 过滤即可，
  没必要把每层都序列化一遍（体积 ×层数）。
* 定长、小端、纯二进制：C++ 侧一次 mmap / 一次 fread 就够，不引入 json/protobuf 依赖。
* 带 ``k`` 与 ``M`` 两个字段：k 是图里实际保留的邻居数（通常 2M~4M），
  M 是 HNSW 每层每个点的出边上限，装配时再用启发式裁剪到 M。

布局::

    magic       8s   "KGVS1\\0\\0\\0"
    version     int32
    n           int32
    dim         int32
    k           int32
    M           int32
    ef_build    int32
    entry_id    int32
    levels      int32[n]        每点层级（由 build 端按 HNSW 分布抽样，装配端直接用）
    neighbors   int64[n*k]      第 0 层邻居 id，-1 表示空位
    distances   float32[n*k]    对应距离（cosine / l2），装配端做启发式裁剪时复用
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAGIC = b"KGVS1\0\0\0"
VERSION = 1
_HEADER = struct.Struct("<8siiiiiiii")  # magic, version, n, dim, k, M, ef_build, entry_id, reserved


@dataclass
class KgGraph:
    """一张可直接装配成 HNSW 的 kNN 图。"""

    levels: np.ndarray      # int32[n]
    neighbors: np.ndarray   # int64[n, k]
    distances: np.ndarray   # float32[n, k]
    dim: int
    M: int
    ef_build: int
    entry_id: int
    metric: str = "cosine"

    @property
    def n(self) -> int:
        return int(self.levels.shape[0])

    @property
    def k(self) -> int:
        return int(self.neighbors.shape[1])

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            f.write(
                _HEADER.pack(
                    MAGIC,
                    VERSION,
                    self.n,
                    int(self.dim),
                    self.k,
                    int(self.M),
                    int(self.ef_build),
                    int(self.entry_id),
                    0,
                )
            )
            f.write(np.ascontiguousarray(self.levels, dtype=np.int32).tobytes())
            f.write(np.ascontiguousarray(self.neighbors, dtype=np.int64).tobytes())
            f.write(np.ascontiguousarray(self.distances, dtype=np.float32).tobytes())
        return path

    @classmethod
    def load(cls, path: str | Path) -> "KgGraph":
        path = Path(path)
        with path.open("rb") as f:
            head = f.read(_HEADER.size)
            if len(head) != _HEADER.size:
                raise ValueError(f"{path} 太短，不是 KG 图文件")
            magic, version, n, dim, k, m, ef_build, entry_id, _ = _HEADER.unpack(head)
            if magic != MAGIC:
                raise ValueError(f"{path} 魔数不匹配: {magic!r}")
            if version != VERSION:
                raise ValueError(f"{path} 版本不支持: {version}")
            levels = np.frombuffer(f.read(4 * n), dtype=np.int32).copy()
            neighbors = np.frombuffer(f.read(8 * n * k), dtype=np.int64).copy().reshape(n, k)
            distances = np.frombuffer(f.read(4 * n * k), dtype=np.float32).copy().reshape(n, k)
        return cls(
            levels=levels,
            neighbors=neighbors,
            distances=distances,
            dim=dim,
            M=m,
            ef_build=ef_build,
            entry_id=entry_id,
        )


def assign_levels(n: int, M: int, seed: int = 42) -> np.ndarray:
    """按 HNSW 的层级分布给每个点抽样层级（与 hnswlib 的 ``-log(U) * mL`` 一致）。"""
    mL = 1.0 / np.log(M)
    rng = np.random.default_rng(seed)
    u = rng.random(n, dtype=np.float64)
    u = np.clip(u, 1e-12, 1.0)
    return np.floor(-np.log(u) * mL).astype(np.int32)
