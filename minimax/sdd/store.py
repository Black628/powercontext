"""Storage layer: ingest parsed materials and select task context.

The same SQL runs on two backends:

* ``seekdb``  - the embedded seekdb engine shipped with OceanBase, accessed via
  ``pylibseekdb`` plus a PyMySQL connection (Linux/macOS only);
* ``sqlite``  - a development fallback that needs no extra service.

Both are driven through a tiny adapter so the selection logic is written once.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

DATABASE_NAME = "sdd"

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS ingest_runs (
        run_id VARCHAR(64) PRIMARY KEY,
        created_at VARCHAR(64) NOT NULL,
        backend VARCHAR(32) NOT NULL,
        source_root VARCHAR(512) NOT NULL,
        spec_count INTEGER NOT NULL,
        symbol_count INTEGER NOT NULL,
        test_count INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS spec_clauses (
        clause_id VARCHAR(32) PRIMARY KEY,
        title VARCHAR(512) NOT NULL,
        body TEXT NOT NULL,
        digest VARCHAR(80) NOT NULL,
        path VARCHAR(512) NOT NULL,
        line_start INTEGER NOT NULL,
        line_end INTEGER NOT NULL,
        ingest_run VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS spec_keywords (
        clause_id VARCHAR(32) NOT NULL,
        keyword VARCHAR(128) NOT NULL,
        weight INTEGER NOT NULL,
        PRIMARY KEY (clause_id, keyword)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS code_symbols (
        symbol_id VARCHAR(200) PRIMARY KEY,
        kind VARCHAR(16) NOT NULL,
        qualname VARCHAR(256) NOT NULL,
        path VARCHAR(512) NOT NULL,
        line_start INTEGER NOT NULL,
        line_end INTEGER NOT NULL,
        signature VARCHAR(512) NOT NULL,
        docstring TEXT NOT NULL,
        clause_refs VARCHAR(128) NOT NULL,
        ingest_run VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS symbol_keywords (
        symbol_id VARCHAR(200) NOT NULL,
        keyword VARCHAR(128) NOT NULL,
        weight INTEGER NOT NULL,
        PRIMARY KEY (symbol_id, keyword)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tests (
        test_id VARCHAR(320) PRIMARY KEY,
        name VARCHAR(256) NOT NULL,
        path VARCHAR(512) NOT NULL,
        line_start INTEGER NOT NULL,
        line_end INTEGER NOT NULL,
        docstring TEXT NOT NULL,
        covers VARCHAR(128) NOT NULL,
        ingest_run VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS test_keywords (
        test_id VARCHAR(320) NOT NULL,
        keyword VARCHAR(128) NOT NULL,
        weight INTEGER NOT NULL,
        PRIMARY KEY (test_id, keyword)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS context_runs (
        run_id VARCHAR(64) PRIMARY KEY,
        created_at VARCHAR(64) NOT NULL,
        task TEXT NOT NULL,
        backend VARCHAR(32) NOT NULL,
        clause_ids VARCHAR(512) NOT NULL,
        symbol_ids TEXT NOT NULL,
        target_tests TEXT NOT NULL,
        regression_tests TEXT NOT NULL,
        selection_json TEXT NOT NULL
    )
    """,
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clamp(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


class StoreError(RuntimeError):
    """Raised when the configured storage backend cannot be opened."""


class Store:
    """Thin adapter over seekdb/MySQL and SQLite."""

    def __init__(self, backend: str, target: Path, *, sql_log: Path | None = None) -> None:
        self.backend = backend
        self.target = target
        self.sql_log = sql_log
        self._placeholder = "%s"
        self._conn: Any = None
        self._instance: Any = None
        self._statements: list[str] = []

    # -- lifecycle -------------------------------------------------------
    @classmethod
    def open(cls, backend: str, target: Path, *, sql_log: Path | None = None) -> "Store":
        store = cls(backend, target, sql_log=sql_log)
        store._connect()
        return store

    def _connect(self) -> None:
        if self.backend == "seekdb":
            self._connect_seekdb()
        else:
            self._connect_sqlite()

    def _connect_sqlite(self) -> None:
        path = Path(str(self.target) + ".sqlite3") if self.target.suffix != ".sqlite3" else self.target
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._placeholder = "?"
        self.describe = str(path)

    def _connect_seekdb(self) -> None:
        try:
            import pymysql
            import pylibseekdb
        except ImportError as exc:  # pragma: no cover - depends on host platform
            raise StoreError(
                "seekdb backend needs the 'pylibseekdb' and 'pymysql' packages "
                "(pip install -r requirements.txt; Linux or macOS only). "
                "Use --backend sqlite to run on Windows."
            ) from exc

        self.target.mkdir(parents=True, exist_ok=True)
        try:
            self._instance = pylibseekdb.open(db_dir=str(self.target))
        except Exception as exc:  # pragma: no cover - host specific
            raise StoreError(f"cannot start embedded seekdb in {self.target}: {exc}") from exc

        options = dict(self._instance.connection_options())
        options.pop("autocommit", None)
        try:
            self._conn = pymysql.connect(database=DATABASE_NAME, autocommit=True, **options)
        except Exception:
            self._conn = pymysql.connect(autocommit=True, **options)
            with self._conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE_NAME}")
            self._conn.select_db(DATABASE_NAME)
        self._conn.autocommit(True)
        self._placeholder = "%s"
        self.describe = f"seekdb:{self.target}/{DATABASE_NAME}"

    def close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        finally:
            self._conn = None
            if self._instance is not None:
                try:
                    self._instance.close()
                except Exception:  # pragma: no cover - best effort
                    pass
                self._instance = None

    # -- sql helpers -----------------------------------------------------
    def _normalise(self, sql: str) -> str:
        return " ".join(sql.split())

    def _log_sql(self, sql: str, params: Sequence[Any]) -> None:
        self._statements.append(self._normalise(sql))
        if self.sql_log is None:
            return
        self.sql_log.parent.mkdir(parents=True, exist_ok=True)
        rendered = self._normalise(sql)
        if params:
            rendered += "  -- params: " + json.dumps(list(params), ensure_ascii=False, default=str)
        with self.sql_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()}  {rendered}\n")

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        params = list(params or [])
        self._log_sql(sql, params)
        if self.backend == "seekdb":
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params or None)
        else:
            self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        materialised = [list(row) for row in rows]
        if not materialised:
            return
        self._log_sql(sql, [f"{len(materialised)} rows"])
        if self.backend == "seekdb":
            with self._conn.cursor() as cursor:
                cursor.executemany(sql, materialised)
        else:
            self._conn.executemany(sql, materialised)

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        params = list(params or [])
        self._log_sql(sql, params)
        if self.backend == "seekdb":
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params or None)
                rows = cursor.fetchall()
                columns = [item[0] for item in cursor.description]
        else:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        rows = self.fetchall(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def ph(self) -> str:
        return self._placeholder

    def placeholders(self, count: int) -> str:
        return ",".join(self._placeholder for _ in range(count))

    def initialise(self) -> None:
        for statement in SCHEMA:
            self.execute(statement)

    # -- ingest ----------------------------------------------------------
    def ingest(self, clauses: Sequence[Any], symbols: Sequence[Any], tests: Sequence[Any], source_root: str) -> str:
        run_id = "ingest-" + datetime.now().strftime("%Y%m%d%H%M%S")
        ph = self.ph()
        for table in ("spec_keywords", "symbol_keywords", "test_keywords", "spec_clauses", "code_symbols", "tests"):
            self.execute(f"DELETE FROM {table}")
        self.executemany(
            f"REPLACE INTO spec_clauses (clause_id, title, body, digest, path, line_start, line_end, ingest_run)"
            f" VALUES ({self.placeholders(8)})",
            [
                (c.clause_id, _clamp(c.title, 500), c.body, c.digest, c.path, c.line_start, c.line_end, run_id)
                for c in clauses
            ],
        )
        self.executemany(
            f"REPLACE INTO code_symbols (symbol_id, kind, qualname, path, line_start, line_end, signature,"
            f" docstring, clause_refs, ingest_run) VALUES ({self.placeholders(10)})",
            [
                (
                    s.symbol_id,
                    s.kind,
                    s.qualname,
                    s.path,
                    s.line_start,
                    s.line_end,
                    _clamp(s.signature, 500),
                    s.docstring,
                    s.clause_refs,
                    run_id,
                )
                for s in symbols
            ],
        )
        self.executemany(
            f"REPLACE INTO tests (test_id, name, path, line_start, line_end, docstring, covers, ingest_run)"
            f" VALUES ({self.placeholders(8)})",
            [
                (t.test_id, t.name, t.path, t.line_start, t.line_end, t.docstring, t.covers, run_id)
                for t in tests
            ],
        )
        self.execute(
            f"REPLACE INTO ingest_runs (run_id, created_at, backend, source_root, spec_count, symbol_count, test_count)"
            f" VALUES ({self.placeholders(7)})",
            (run_id, _now(), self.backend, source_root, len(clauses), len(symbols), len(tests)),
        )
        return run_id

    def replace_keywords(self, table: str, key_column: str, key: str, weights: dict[str, int]) -> None:
        self.execute(f"DELETE FROM {table} WHERE {key_column} = {self.ph()}", (key,))
        self.executemany(
            f"REPLACE INTO {table} ({key_column}, keyword, weight) VALUES ({self.placeholders(3)})",
            [(key, keyword, weight) for keyword, weight in weights.items()],
        )

    # -- selection -------------------------------------------------------
    def clauses_for_keywords(self, keywords: Sequence[str], limit: int) -> list[dict[str, Any]]:
        if not keywords:
            return []
        rows = self.fetchall(
            f"""
            SELECT c.clause_id, c.title, c.body, c.path, c.line_start, c.line_end, c.digest, s.score
            FROM spec_clauses c
            JOIN (
                SELECT clause_id, SUM(weight) AS score
                FROM spec_keywords
                WHERE keyword IN ({self.placeholders(len(keywords))})
                GROUP BY clause_id
            ) s ON s.clause_id = c.clause_id
            ORDER BY s.score DESC, c.clause_id
            LIMIT {self.ph()}
            """,
            list(keywords) + [limit],
        )
        for row in rows:
            row["score"] = int(row["score"])
        return rows

    def clauses_by_id(self, clause_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not clause_ids:
            return []
        return self.fetchall(
            f"""
            SELECT clause_id, title, body, path, line_start, line_end, digest
            FROM spec_clauses
            WHERE clause_id IN ({self.placeholders(len(clause_ids))})
            """,
            list(clause_ids),
        )

    def symbols_for_keywords(self, keywords: Sequence[str], limit: int) -> list[dict[str, Any]]:
        if not keywords:
            return []
        rows = self.fetchall(
            f"""
            SELECT c.symbol_id, c.kind, c.qualname, c.path, c.line_start, c.line_end, c.signature,
                   c.docstring, c.clause_refs, s.score
            FROM code_symbols c
            JOIN (
                SELECT symbol_id, SUM(weight) AS score
                FROM symbol_keywords
                WHERE keyword IN ({self.placeholders(len(keywords))})
                GROUP BY symbol_id
            ) s ON s.symbol_id = c.symbol_id
            ORDER BY s.score DESC, c.symbol_id
            LIMIT {self.ph()}
            """,
            list(keywords) + [limit],
        )
        for row in rows:
            row["score"] = int(row["score"])
        return rows

    def symbols_for_clauses(self, clause_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not clause_ids:
            return []
        conditions = " OR ".join(f"clause_refs LIKE {self.ph()}" for _ in clause_ids)
        rows = self.fetchall(
            f"""
            SELECT symbol_id, kind, qualname, path, line_start, line_end, signature, docstring, clause_refs
            FROM code_symbols
            WHERE {conditions}
            """,
            [f"%{clause_id}%" for clause_id in clause_ids],
        )
        return rows

    def all_tests(self) -> list[dict[str, Any]]:
        return self.fetchall(
            "SELECT test_id, name, path, line_start, line_end, docstring, covers FROM tests ORDER BY path, line_start"
        )

    def record_context_run(self, run_id: str, task: str, clause_ids: Sequence[str], symbol_ids: Sequence[str],
                           target_tests: Sequence[str], regression_tests: Sequence[str], selection: dict[str, Any]) -> None:
        self.execute(
            f"""
            REPLACE INTO context_runs (run_id, created_at, task, backend, clause_ids, symbol_ids,
                                       target_tests, regression_tests, selection_json)
            VALUES ({self.placeholders(9)})
            """,
            (
                run_id,
                _now(),
                task,
                self.backend,
                ",".join(clause_ids),
                json.dumps(list(symbol_ids), ensure_ascii=False),
                json.dumps(list(target_tests), ensure_ascii=False),
                json.dumps(list(regression_tests), ensure_ascii=False),
                json.dumps(selection, ensure_ascii=False, default=str),
            ),
        )

    # -- introspection ---------------------------------------------------
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in ("spec_clauses", "code_symbols", "tests", "context_runs"):
            value = self.scalar(f"SELECT COUNT(*) FROM {table}")
            counts[table] = int(value or 0)
        return counts

    def last_ingest(self) -> dict[str, Any] | None:
        rows = self.fetchall(
            "SELECT run_id, created_at, backend, spec_count, symbol_count, test_count FROM ingest_runs"
            " ORDER BY created_at DESC LIMIT 1"
        )
        return rows[0] if rows else None


def open_store(backend: str, target: Path, *, sql_log: Path | None = None) -> Store:
    """Open a store, resolving ``auto`` to seekdb when the binding is available."""
    resolved = backend
    if resolved == "auto":
        try:
            import pylibseekdb  # noqa: F401

            resolved = "seekdb"
        except Exception:
            resolved = "sqlite"
    store = Store.open(resolved, target, sql_log=sql_log)
    store.initialise()
    return store
