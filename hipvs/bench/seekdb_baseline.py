#!/usr/bin/env python3
"""seekdb 侧基线：真实 SQL 建 HNSW 向量索引的耗时。

用途：给出「seekdb 自己的构建耗时」，与 bench/hnsw_bench（VSAG 同源算法）的构建耗时应处于同一量级，
      这样"图装配能省多少"才有参照；也是本题"构建耗时影响导入与检索准备效率"的直接证据。

用法:
    python3 seekdb_baseline.py --n 20000 --dim 128 --M 16 --ef 200
产出:
    results/seekdb-n<N>.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pymysql

TABLE = "hipvs_bench"


def vec_literal(row: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in row) + "]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2881)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="")
    ap.add_argument("--database", default="test")
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--M", type=int, default=16)
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--keep", action="store_true", help="保留表，不删")
    args = ap.parse_args()

    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    vectors = rng.standard_normal((args.n, args.dim), dtype=np.float32)

    conn = pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.database, autocommit=True, local_infile=False,
    )
    cur = conn.cursor()
    cur.execute(f"drop table if exists {TABLE}")
    cur.execute(f"create table {TABLE} (id bigint primary key, v vector({args.dim}))")

    print(f"插入 {args.n} 行（batch={args.batch}）...")
    t0 = time.perf_counter()
    rows_total = 0
    for start in range(0, args.n, args.batch):
        stop = min(start + args.batch, args.n)
        values = ",".join(
            f"({i},'{vec_literal(vectors[i])}')" for i in range(start, stop)
        )
        cur.execute(f"insert into {TABLE} (id, v) values {values}")
        rows_total += stop - start
    insert_ms = (time.perf_counter() - t0) * 1000.0
    print(f"插入完成 {rows_total} 行，用时 {insert_ms:.0f} ms")

    print("创建 HNSW 向量索引（计时）...")
    t1 = time.perf_counter()
    cur.execute(
        f"create vector index idx_{TABLE} on {TABLE}(v) "
        f"with (distance=cosine, type=HNSW, lib=vsag, m={args.M}, ef_construction={args.ef})"
    )
    build_ms = (time.perf_counter() - t1) * 1000.0
    print(f"建索引完成，用时 {build_ms:.0f} ms")

    cur.execute(f"show create table {TABLE}")
    ddl = cur.fetchone()[1]

    result = {
        "engine": "seekdb + VSAG",
        "n": args.n,
        "dim": args.dim,
        "M": args.M,
        "ef_construction": args.ef,
        "rows": rows_total,
        "insert_ms": round(insert_ms, 1),
        "build_index_ms": round(build_ms, 1),
        "insert_rows_per_s": round(rows_total / (insert_ms / 1000.0), 1),
        "index_ddl_contains_vsag": "LIB=VSAG" in ddl,
    }
    path = out_dir / f"seekdb-n{args.n}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"→ {path}")

    if not args.keep:
        cur.execute(f"drop table if exists {TABLE}")
        print("已删除测试表")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
