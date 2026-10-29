"""seekdb 访问层：建表、全文索引、增量写入、检索，并记录每一条真实 SQL。"""

from __future__ import annotations

import re
import time
from typing import Dict, Iterable, List, Optional, Sequence

from .config import Config
from .util import append_jsonl, append_text, now_iso, read_json

try:  # pymysql 只在需要连库时才要求安装
    import pymysql
except ImportError:  # pragma: no cover
    pymysql = None


DDL_DOCS = """
CREATE TABLE IF NOT EXISTS sdd_docs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  doc_key VARCHAR(400) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  path VARCHAR(500) NOT NULL,
  line_start INT NOT NULL,
  line_end INT NOT NULL,
  symbol VARCHAR(200) DEFAULT NULL,
  content MEDIUMTEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  spec_version VARCHAR(64) DEFAULT NULL,
  run_id VARCHAR(64) DEFAULT NULL,
  indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_doc_key (doc_key),
  KEY idx_kind (kind),
  KEY idx_path (path)
) DEFAULT CHARSET = utf8mb4
""".strip()

DDL_RUNS = """
CREATE TABLE IF NOT EXISTS sdd_runs (
  run_id VARCHAR(64) PRIMARY KEY,
  project_root VARCHAR(1000) NOT NULL,
  spec_count INT NOT NULL,
  code_count INT NOT NULL,
  test_count INT NOT NULL,
  doc_count BIGINT NOT NULL,
  started_at VARCHAR(32) NOT NULL,
  finished_at VARCHAR(32) NOT NULL,
  note VARCHAR(500) DEFAULT NULL
) DEFAULT CHARSET = utf8mb4
""".strip()

DDL_STATE_INDEX = """
CREATE TABLE IF NOT EXISTS sdd_state_refs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(200) NOT NULL,
  backend VARCHAR(32) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  saved_at VARCHAR(32) NOT NULL,
  KEY idx_task (task_id)
) DEFAULT CHARSET = utf8mb4
""".strip()

FULLTEXT_COLUMNS = "content, path, symbol"


class SeekDBError(RuntimeError):
    pass


class SeekDB:
    """一个很薄的 seekdb 客户端，所有执行过的 SQL 都会落到 records/seekdb-queries.md。"""

    def __init__(self, cfg: Config, echo: bool = True):
        self.cfg = cfg
        self.echo = echo
        self.conn = None
        self.parser = None
        self._query_count = 0

    # ------------------------------------------------------------------ 连接
    def _candidate_ports(self) -> List[int]:
        """候选端口：先配置/缓存端口，再按默认顺序探测（换机器换端口时无需改配置）。"""
        from .config import DEFAULT_PORT_CANDIDATES
        cached = read_json(self.cfg.state_dir / "db-port.json", {}) or {}
        ordered: List[int] = []
        for port in ([self.cfg.db_port] + [int(cached["port"]) if cached.get("port") else None]
                     + DEFAULT_PORT_CANDIDATES):
            if port and port not in ordered:
                ordered.append(port)
        return ordered

    def connect(self, create_db: bool = True) -> None:
        if pymysql is None:
            raise SeekDBError(
                "缺少 pymysql，请先执行: pip3 install pymysql "
                "(或 pip install 'sddcontext[db]')"
            )
        last_error = None
        configured = self.cfg.db_port
        for attempt_port in self._candidate_ports():
            try:
                self.conn = pymysql.connect(
                    host=self.cfg.db_host,
                    port=attempt_port,
                    user=self.cfg.db_user,
                    password=self.cfg.db_password,
                    charset="utf8mb4",
                    autocommit=True,
                    connect_timeout=5,
                    read_timeout=60,
                    write_timeout=60,
                )
                if attempt_port != configured:
                    print(f"[seekdb] 配置端口 {configured} 不可用，自动改用 {attempt_port}"
                          f"（可用 SEEKDB_PORT 显式指定）")
                self.cfg.db_port = attempt_port
                write_json(self.cfg.state_dir / "db-port.json",
                           {"host": self.cfg.db_host, "port": attempt_port})
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if self.conn is None:
            raise SeekDBError(
                f"无法连接 seekdb {self.cfg.db_host}（候选端口 {self._candidate_ports()}）-> {last_error}\n"
                "请确认 seekdb 已启动，或用环境变量 SEEKDB_HOST/SEEKDB_PORT/SEEKDB_USER/SEEKDB_PASSWORD 指定。"
            )
        if create_db:
            self.execute(f"CREATE DATABASE IF NOT EXISTS `{self.cfg.db_name}` "
                         f"DEFAULT CHARACTER SET utf8mb4", note="init-db: 建库")
        self.execute(f"USE `{self.cfg.db_name}`", note="init-db: 切换库")

    # ------------------------------------------------------------------ SQL
    def _log(self, sql: str, params, rowcount: Optional[int], note: str = "") -> None:
        self._query_count += 1
        single = " ".join(sql.split())
        param_text = ""
        if params:
            param_text = "  -- params: " + repr(params)
        record = {
            "ts": now_iso(),
            "seq": self._query_count,
            "note": note,
            "sql": single,
            "params": params if params else None,
            "rows": rowcount,
        }
        append_jsonl(self.cfg.records_dir / "seekdb-queries.jsonl", record)
        block = (
            f"\n### [{self._query_count}] {now_iso()}  {note or ''}\n"
            f"\n```sql\n{single}{param_text}\n```\n"
            f"\n返回行数: {rowcount}\n"
        )
        append_text(self.cfg.seekdb_log, block)
        if self.echo:
            print(f"[seekdb] {note or 'query'} -> {rowcount} rows")

    def execute(self, sql: str, params=None, note: str = "") -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.rowcount
        self._log(sql, params, rows, note)
        return rows

    def fetch(self, sql: str, params=None, note: str = "") -> List[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        self._log(sql, params, len(rows), note)
        return list(rows)

    # ------------------------------------------------------------------ 建表
    def ensure_schema(self, recreate: bool = False) -> None:
        self.connect()
        if recreate:
            self.execute("DROP TABLE IF EXISTS sdd_docs", note="init-db: 重建（drop）")
        self.execute(DDL_DOCS, note="init-db: 建 sdd_docs")
        self.execute(DDL_RUNS, note="init-db: 建 sdd_runs")
        self.execute(DDL_STATE_INDEX, note="init-db: 建 sdd_state_refs")
        self._ensure_fulltext()

    def _ensure_fulltext(self) -> None:
        rows = self.fetch(
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'sdd_docs' AND INDEX_TYPE = 'FULLTEXT'",
            (self.cfg.db_name,),
            note="init-db: 检查 FULLTEXT 索引",
        )
        names = {r[0] for r in rows}
        if names:
            self.parser = "existing:" + ",".join(sorted(names))
            return
        for parser in ("ik", "beng", None):
            sql = (f"CREATE FULLTEXT INDEX ft_sdd_docs ON sdd_docs({FULLTEXT_COLUMNS})"
                   + (f" WITH PARSER {parser}" if parser else ""))
            try:
                self.execute(sql, note=f"init-db: 建 FULLTEXT 索引 parser={parser or 'default'}")
                self.parser = parser or "default"
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[seekdb] parser={parser} 不可用（{exc}），尝试下一个")
        raise SeekDBError("无法创建 FULLTEXT 索引")

    # ------------------------------------------------------------------ 写入
    def upsert_docs(self, docs: Sequence[dict], run_id: str) -> int:
        if not docs:
            return 0
        sql = (
            "REPLACE INTO sdd_docs"
            "(doc_key, kind, path, line_start, line_end, symbol, content, content_hash, spec_version, run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        payload = [
            (
                d["doc_key"], d["kind"], d["path"], d["line_start"], d["line_end"],
                d.get("symbol"), d["content"], d["content_hash"], d.get("spec_version"), run_id,
            )
            for d in docs
        ]
        with self.conn.cursor() as cur:
            cur.executemany(sql, payload)
            affected = cur.rowcount
        self._log(sql, f"executemany({len(payload)} 行)", affected, note="index: 写入切片")
        return affected

    def delete_stale(self, kind: str, path: str, keep_keys: Sequence[str]) -> int:
        if keep_keys:
            placeholders = ",".join(["%s"] * len(keep_keys))
            sql = (f"DELETE FROM sdd_docs WHERE kind=%s AND path=%s "
                   f"AND doc_key NOT IN ({placeholders})")
            params = [kind, path, *keep_keys]
        else:
            sql = "DELETE FROM sdd_docs WHERE kind=%s AND path=%s"
            params = [kind, path]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            affected = cur.rowcount
        self._log(sql, params, affected, note="index: 清理同文件旧切片")
        return affected

    def record_run(self, run: dict) -> None:
        self.execute(
            "REPLACE INTO sdd_runs(run_id, project_root, spec_count, code_count, test_count, "
            "doc_count, started_at, finished_at, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run["run_id"], run["project_root"], run["spec_count"], run["code_count"],
             run["test_count"], run["doc_count"], run["started_at"], run["finished_at"],
             run.get("note")),
            note="index: 记录本次索引 run",
        )

    # ------------------------------------------------------------------ 检索
    def count_by_kind(self) -> Dict[str, int]:
        rows = self.fetch("SELECT kind, COUNT(*) FROM sdd_docs GROUP BY kind",
                          note="context: 统计各 kind 切片数")
        return {k: int(v) for k, v in rows}

    def match(self, query: str, kind: str, limit: int) -> List[dict]:
        """FULLTEXT MATCH 检索；失败时抛异常由上层降级。"""
        sql = (
            "SELECT doc_key, kind, path, line_start, line_end, symbol, content, "
            f"MATCH({FULLTEXT_COLUMNS}) AGAINST(%s) AS score "
            f"FROM sdd_docs WHERE MATCH({FULLTEXT_COLUMNS}) AGAINST(%s) AND kind = %s "
            "ORDER BY score DESC LIMIT %s"
        )
        with self.conn.cursor() as cur:
            cur.execute(sql, (query, query, kind, limit))
            rows = cur.fetchall()
        self._log(sql, (query, query, kind, limit), len(rows),
                  note=f"context: FULLTEXT 检索 kind={kind}")
        return [self._row_to_doc(r, hit="fulltext") for r in rows]

    def like(self, terms: Iterable[str], kind: str, limit: int) -> List[dict]:
        clauses, params = [], []
        for term in terms:
            clauses.append("(content LIKE %s OR path LIKE %s OR symbol LIKE %s)")
            params.extend([f"%{term}%"] * 3)
        if not clauses:
            return []
        sql = (
            "SELECT doc_key, kind, path, line_start, line_end, symbol, content, 0 AS score "
            f"FROM sdd_docs WHERE kind = %s AND ({' OR '.join(clauses)}) LIMIT %s"
        )
        params = [kind, *params, limit]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        self._log(sql, params, len(rows), note=f"context: LIKE 兜底检索 kind={kind}")
        return [self._row_to_doc(r, hit="like") for r in rows]

    def by_path(self, paths: Sequence[str], kind: str, limit: int = 50) -> List[dict]:
        """按工程内相对路径取切片（用于配置里显式钉住的兼容性测试）。"""
        if not paths:
            return []
        placeholders = ",".join(["%s"] * len(paths))
        sql = (
            "SELECT doc_key, kind, path, line_start, line_end, symbol, content, 1.0 AS score "
            f"FROM sdd_docs WHERE kind = %s AND path IN ({placeholders}) "
            "ORDER BY path, line_start LIMIT %s"
        )
        params = [kind, *paths, limit]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        self._log(sql, params, len(rows), note=f"context: 按路径钉住 kind={kind}")
        return [self._row_to_doc(r, hit="pinned") for r in rows]

    def find_symbol(self, symbols: Sequence[str], kind: str, limit: int) -> List[dict]:
        if not symbols:
            return []
        placeholders = ",".join(["%s"] * len(symbols))
        sql = (
            "SELECT doc_key, kind, path, line_start, line_end, symbol, content, 1.0 AS score "
            f"FROM sdd_docs WHERE kind = %s AND symbol IN ({placeholders}) LIMIT %s"
        )
        params = [kind, *symbols, limit]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        self._log(sql, params, len(rows), note=f"context: 符号精确命中 kind={kind}")
        return [self._row_to_doc(r, hit="symbol") for r in rows]

    @staticmethod
    def _row_to_doc(row, hit: str) -> dict:
        doc_key, kind, path, line_start, line_end, symbol, content, score = row
        return {
            "doc_key": doc_key,
            "kind": kind,
            "path": path,
            "line_start": int(line_start),
            "line_end": int(line_end),
            "symbol": symbol,
            "content": content,
            "score": float(score or 0.0),
            "hit": hit,
            "source": f"{path}:{line_start}-{line_end}",
        }

    def total_docs(self) -> int:
        rows = self.fetch("SELECT COUNT(*) FROM sdd_docs", note="context: 统计切片总数")
        return int(rows[0][0]) if rows else 0

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None


def is_identifier(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token))
