"""seekdb 存储层：通过 MySQL 协议（默认端口 2881）访问。

职责：建库建表（init_schema）、写入 chunk、文档登记与统计函数。
检索 SQL 在 search.py 中执行，本模块只负责连接与写入。
"""

import logging
import os

import pymysql
from dotenv import load_dotenv

from . import embed

load_dotenv()

logger = logging.getLogger("recall.store")

# 连接参数全部来自环境变量；.env.example 中均为占位值，不要提交真实密钥
SEEKDB_HOST = os.getenv("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.getenv("SEEKDB_PASSWORD", "")  # 占位：从 .env 注入
SEEKDB_DATABASE = os.getenv("SEEKDB_DATABASE", "recall")


def get_conn(database: str = None):
    """创建一个 seekdb 连接（utf8mb4、自动提交）。

    database=None 使用 SEEKDB_DATABASE；传空串表示不指定库（建库前需要）。
    """
    db = SEEKDB_DATABASE if database is None else database
    return pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        database=db or None,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


_VECTOR_INDEX_DDL = (
    ",\n  VECTOR INDEX vidx(embedding) WITH (distance=cosine, type=hnsw, lib=vsag)"
)

# chunks 表 DDL：与 scripts/init_seekdb.sql 保持一致，维度跟随 EMBEDDING_DIM
_CHUNKS_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id       VARCHAR(64)  NOT NULL,
  doc_name     VARCHAR(256) NOT NULL,
  page         INT          NOT NULL DEFAULT 0,
  offset_in_doc INT         NOT NULL DEFAULT 0,
  content      TEXT         NOT NULL,
  embedding    VECTOR({dim}),
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FULLTEXT INDEX ft_content(content) WITH PARSER ik""" + _VECTOR_INDEX_DDL + """
) DEFAULT CHARSET = utf8mb4
"""

# 降级 DDL：部分 seekdb 版本不支持表内 VECTOR INDEX 声明
_CHUNKS_DDL_NO_VEC = _CHUNKS_DDL.replace(_VECTOR_INDEX_DDL, "")

_DOCS_DDL = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id      VARCHAR(64) PRIMARY KEY,
  doc_name    VARCHAR(256) NOT NULL,
  chunk_count INT DEFAULT 0,
  status      VARCHAR(32) DEFAULT 'pending',
  error       TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET = utf8mb4
"""

_COUNTERS_DDL = """
CREATE TABLE IF NOT EXISTS counters (
  name  VARCHAR(64) PRIMARY KEY,
  value BIGINT DEFAULT 0
) DEFAULT CHARSET = utf8mb4
"""


def _exec(cur, sql: str, args=None):
    """执行单条语句并打印日志（作为"技术实际生效"的证据）。"""
    logger.info("[store] SQL: %s", " ".join(sql.split()))
    return cur.execute(sql, args or [])


def init_schema() -> None:
    """建库 + 建表；向量/全文索引语法不被支持时降级并告警。"""
    conn = get_conn(database="")
    try:
        with conn.cursor() as cur:
            _exec(cur, f"CREATE DATABASE IF NOT EXISTS {SEEKDB_DATABASE}")
            cur.execute(f"USE {SEEKDB_DATABASE}")
            dim = embed.get_embedder().dim
            try:
                # 首选：带 ik 分词全文索引 + 向量索引的完整 DDL
                _exec(cur, _CHUNKS_DDL.format(dim=dim))
            except pymysql.err.MySQLError:
                # 老版本 seekdb 不支持 VECTOR INDEX 声明时，索引可事后单独创建
                logger.warning("[store] 完整 DDL 失败，尝试去掉 VECTOR INDEX 建表（向量检索仍可用，近似索引退化）")
                _exec(cur, _CHUNKS_DDL_NO_VEC.format(dim=dim))
            _exec(cur, _DOCS_DDL)
            _exec(cur, _COUNTERS_DDL)
    finally:
        conn.close()


def insert_chunks(rows: list) -> int:
    """批量写入 chunk。

    rows: [{doc_id, doc_name, page, offset_in_doc, content, embedding(list[float])}]
    """
    if not rows:
        return 0
    sql = (
        "INSERT INTO chunks (doc_id, doc_name, page, offset_in_doc, content, embedding) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    params = [
        (
            r["doc_id"], r["doc_name"], r.get("page", 0),
            r.get("offset_in_doc", 0), r["content"], embed.vector_to_sql(r["embedding"]),
        )
        for r in rows
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            n = cur.executemany(sql, params)
        logger.info("[store] 写入 chunks 完成：%d 条", n)
        return n
    finally:
        conn.close()


def register_doc(doc_id: str, doc_name: str, status: str = "pending") -> None:
    """登记一个上传文档（后台索引前先行落库，供 GET /api/docs 展示）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "REPLACE INTO docs (doc_id, doc_name, chunk_count, status) VALUES (%s, %s, 0, %s)",
                (doc_id, doc_name, status),
            )
    finally:
        conn.close()


def finish_doc(doc_id: str, chunk_count: int, status: str = "ready", error: str = "") -> None:
    """更新文档索引状态（ready/failed）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE docs SET chunk_count=%s, status=%s, error=%s WHERE doc_id=%s",
                (chunk_count, status, error, doc_id),
            )
    finally:
        conn.close()


def list_docs() -> list:
    """文档列表与索引状态。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, doc_name, chunk_count, status, created_at "
                "FROM docs ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
        for r in rows:
            if r.get("created_at") is not None:
                r["created_at"] = str(r["created_at"])
        return rows
    finally:
        conn.close()


def bump_counter(name: str, delta: int = 1) -> None:
    """运行计数 +1（search/ask 次数），失败只告警不阻断主流程。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO counters (name, value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE value = value + %s",
                (name, delta, delta),
            )
    except pymysql.err.MySQLError:
        logger.warning("[store] 计数失败（忽略）：%s", name)
    finally:
        conn.close()


def stats() -> dict:
    """统计函数：导入量、chunk 总数、检索/问答次数。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM docs")
            doc_count = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM chunks")
            chunk_count = cur.fetchone()["n"]
            cur.execute("SELECT name, value FROM counters")
            counters = {r["name"]: r["value"] for r in cur.fetchall()}
        return {
            "doc_count": doc_count,
            "chunk_count": chunk_count,
            "search_count": int(counters.get("search", 0)),
            "ask_count": int(counters.get("ask", 0)),
        }
    finally:
        conn.close()
