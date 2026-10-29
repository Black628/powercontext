"""混合检索：向量路 + 全文路（ik 分词）两条独立 SQL，RRF 融合。

硬性要求：两条 SQL 的原文与各自耗时必须打印到日志，
作为"seekdb 技术实际生效"的可核对证据（REQUIREMENTS 3.2 备注）。
"""

import logging
import time

import pymysql

from . import embed
from .store import get_conn

logger = logging.getLogger("recall.search")

# 向量近邻 SQL：COSINE_DISTANCE + APPROX 近似排序
VECTOR_SQL = (
    "SELECT id, doc_id, doc_name, page, offset_in_doc, content,\n"
    "       COSINE_DISTANCE(embedding, %s) AS dist\n"
    "FROM   chunks\n"
    "ORDER  BY dist APPROX\n"
    "LIMIT  %s"
)

# 全文 SQL：MATCH ... AGAINST（依赖 FULLTEXT ... WITH PARSER ik）
FULLTEXT_SQL = (
    "SELECT id, doc_id, doc_name, page, offset_in_doc, content,\n"
    "       MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score\n"
    "FROM   chunks\n"
    "WHERE  MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE)\n"
    "ORDER  BY score DESC\n"
    "LIMIT  %s"
)

# RRF 平滑常数，业界常用 60
RRF_K = 60


def _preview_vec(vec: list, n: int = 4) -> str:
    """日志用：向量只展示前 n 维，避免刷屏。"""
    head = ", ".join(f"{v:.4f}" for v in vec[:n])
    return f"[{head}, ...] (dim={len(vec)})"


def _run(cur, sql: str, params: tuple) -> tuple:
    """执行一条 SQL，返回 (行列表, 耗时毫秒)。"""
    t0 = time.perf_counter()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cost_ms = (time.perf_counter() - t0) * 1000.0
    return rows, cost_ms


def vector_search(qvec: list, k: int) -> list:
    """向量路：按余弦距离取最近 k 条；不支持 APPROX 关键字时退化重试。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            t0 = time.perf_counter()
            try:
                rows, cost = _run(cur, VECTOR_SQL, (embed.vector_to_sql(qvec), k))
            except pymysql.err.ProgrammingError as e:
                # 部分版本不认 APPROX，改用精确排序（语义等价，只是慢）
                logger.warning("[向量路] APPROX 不受支持(%s)，改用精确 ORDER BY", e.args[1] if e.args else e)
                fallback = VECTOR_SQL.replace("dist APPROX", "dist")
                rows, cost = _run(cur, fallback, (embed.vector_to_sql(qvec), k))
            # —— 证据日志：SQL 原文 + 耗时 ——
            logger.info(
                "[向量路] SQL:\n%s\n参数: qvec=%s, k=%d\n耗时: %.1f ms | 命中: %d 条",
                VECTOR_SQL, _preview_vec(qvec), k, cost, len(rows),
            )
            return rows
    finally:
        conn.close()


def fulltext_search(qtext: str, k: int) -> list:
    """全文路：ik 分词 + MATCH AGAINST 取相关度最高 k 条。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # —— 证据日志：SQL 原文 + 耗时 ——
            rows, cost = _run(cur, FULLTEXT_SQL, (qtext, qtext, k))
            logger.info(
                "[全文路] SQL:\n%s\n参数: qtext=%r, k=%d\n耗时: %.1f ms | 命中: %d 条",
                FULLTEXT_SQL, qtext, k, cost, len(rows),
            )
            return rows
    finally:
        conn.close()


def _snippet(row: dict, max_len: int = 300) -> str:
    """截断片段正文，接口返回与日志展示用。"""
    c = row.get("content") or ""
    return c[:max_len] + ("…" if len(c) > max_len else "")


def _to_item(row: dict) -> dict:
    """统一命中行结构：出处（doc/page/offset）+ 两路各自得分位。"""
    return {
        "id": row["id"],
        "doc_id": row["doc_id"],
        "doc_name": row["doc_name"],
        "page": row["page"],
        "offset": row["offset_in_doc"],
        "snippet": _snippet(row),
        "vector_rank": None,
        "vector_dist": None,
        "fulltext_rank": None,
        "fulltext_score": None,
        "rrf_score": 0.0,
    }


def hybrid_search(query: str, k: int = 5) -> dict:
    """混合检索入口：分别执行两路 SQL，再做 RRF 融合。

    返回 {results, vector_hits, fulltext_hits}，results 含出处与两路得分。
    """
    fetch_n = max(k * 2, 10)  # 两路各多取一些，融合后再截断到 k
    qvec = embed.embed_one(query)

    vec_rows = vector_search(qvec, fetch_n)
    try:
        ft_rows = fulltext_search(query, fetch_n)
    except pymysql.err.MySQLError as e:
        # 全文索引缺失/未建 ik 解析器时，降级为纯向量结果，不阻断接口
        logger.warning("[全文路] 执行失败，本次仅返回向量路结果：%s", e.args[1] if e.args else e)
        ft_rows = []

    items = {}
    for rank, row in enumerate(vec_rows, start=1):
        it = items.setdefault(row["id"], _to_item(row))
        it["vector_rank"] = rank
        it["vector_dist"] = float(row["dist"])
        it["rrf_score"] += 1.0 / (RRF_K + rank)
    for rank, row in enumerate(ft_rows, start=1):
        it = items.setdefault(row["id"], _to_item(row))
        it["fulltext_rank"] = rank
        it["fulltext_score"] = float(row["score"])
        it["rrf_score"] += 1.0 / (RRF_K + rank)

    results = sorted(items.values(), key=lambda x: x["rrf_score"], reverse=True)[:k]
    logger.info(
        "[RRF 融合] query=%r 向量路=%d 条, 全文路=%d 条, 融合后返回=%d 条",
        query, len(vec_rows), len(ft_rows), len(results),
    )
    return {
        "results": results,
        "vector_hits": len(vec_rows),
        "fulltext_hits": len(ft_rows),
    }
