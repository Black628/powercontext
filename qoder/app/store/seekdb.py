# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""seekdb backend: vector (HNSW + L2) and full-text channels fused with RRF.

The SQL below is deliberately ordinary MySQL/OceanBase SQL so the evidence
panel can show exactly what ran:

* vector channel  ``ORDER BY l2_distance(embedding, :q) APPROXIMATE LIMIT n``
* lexical channel ``MATCH(search_text) AGAINST(:q)``

The demo prefers this backend whenever ``SEEKDB_PATH`` (embedded) or
``SEEKDB_URI`` (server / docker) is configured. When neither is set the
factory falls back to :mod:`app.store.mirror`, which keeps the same contract
on a stock Windows machine where the seekdb runtime is unavailable.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..embedding import Embedder
from ..text import keywords, squeeze
from .base import (
    EvidenceLog,
    MaterialRow,
    SearchHit,
    TraceEntry,
    fuse_rrf,
    summarize_vector,
    timed,
)

LOGGER = logging.getLogger(__name__)

MATERIALS_TABLE = "coach_materials"
REVIEWS_TABLE = "coach_reviews"

MATERIAL_COLUMNS = (
    "id",
    "course",
    "chapter",
    "title",
    "body",
    "search_text",
    "keywords",
    "difficulty",
    "source",
    "mastery",
    "reviews",
    "last_verdict",
    "updated_at",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_dict(row: dict[str, Any]) -> MaterialRow:
    keys = str(row.get("keywords") or "")
    return MaterialRow(
        id=str(row["id"]),
        course=str(row["course"]),
        chapter=str(row.get("chapter") or ""),
        title=str(row["title"]),
        body=str(row["body"]),
        keywords=[part for part in keys.split(",") if part],
        difficulty=int(row.get("difficulty") or 2),
        source=str(row.get("source") or ""),
        mastery=float(row.get("mastery") or 0.0),
        reviews=int(row.get("reviews") or 0),
        last_verdict=str(row.get("last_verdict") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


class SeekdbStore:
    backend = "seekdb"

    def __init__(self, settings: Settings, embedder: Embedder) -> None:
        self.settings = settings
        self.embedder = embedder
        self.dim = embedder.dim
        self.evidence = EvidenceLog()
        self.client: Any = None
        self.engine: Any = None
        self.mode = "embedded" if settings.seekdb_path else "server"
        self.approximate_supported: bool | None = None
        self.fts_parser = ""

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        from pyobvector import ObVecClient

        settings = self.settings
        if settings.seekdb_path:
            self._ensure_embedded_database(str(settings.seekdb_path), settings.seekdb_db)
            self.client = ObVecClient(path=str(settings.seekdb_path), db_name=settings.seekdb_db)
        else:
            self.client = ObVecClient(
                uri=settings.seekdb_uri,
                user=settings.seekdb_user,
                password=settings.seekdb_password,
                db_name=settings.seekdb_db,
            )
        self.engine = self.client.engine
        self._ensure_schema()

    @staticmethod
    def _ensure_embedded_database(path: str, database: str) -> None:
        """An embedded engine only ships the ``test`` catalog; create ours first."""
        if not database.replace("_", "").isalnum():
            raise ValueError(f"invalid seekdb database name: {database!r}")
        from pyobvector.client.seekdb_engine import create_embedded_engine

        started = time.perf_counter()
        engine = create_embedded_engine(path, database="test")
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
                connection.commit()
        finally:
            engine.dispose()
        LOGGER.info("seekdb embedded database %r ensured in %.0f ms", database, (time.perf_counter() - started) * 1000)

    def close(self) -> None:
        engine = self.engine
        self.engine = None
        self.client = None
        if engine is not None:
            engine.dispose()

    # -- schema --------------------------------------------------------------
    def _ensure_schema(self) -> None:
        from pyobvector import VECTOR

        from sqlalchemy import Column, String, Text

        if not self.client.check_table_exists(MATERIALS_TABLE):
            ddl_started = time.perf_counter()

            def build_columns() -> list[Any]:
                return [
                    Column("id", String(64), primary_key=True),
                    Column("course", String(64), nullable=False),
                    Column("chapter", String(128), nullable=False, default=""),
                    Column("title", String(255), nullable=False),
                    Column("body", Text, nullable=False),
                    Column("search_text", Text, nullable=False),
                    Column("keywords", String(512), nullable=False, default=""),
                    Column("difficulty", String(4), nullable=False, default="2"),
                    Column("source", String(255), nullable=False, default=""),
                    Column("mastery", String(16), nullable=False, default="0"),
                    Column("reviews", String(16), nullable=False, default="0"),
                    Column("last_verdict", String(16), nullable=False, default=""),
                    Column("updated_at", String(32), nullable=False, default=""),
                    Column("embedding", VECTOR(self.dim), nullable=False),
                ]

            # Scalar/vector index params; HNSW + L2 matches the query SQL below.
            from pyobvector import IndexParams, VecIndexType

            vidxs = IndexParams()
            vidxs.add_index(
                field_name="embedding",
                index_type=VecIndexType.HNSW,
                index_name=f"vidx_{MATERIALS_TABLE}_embedding",
                metric_type="l2",
            )
            self.client.create_table_with_index_params(
                table_name=MATERIALS_TABLE,
                columns=build_columns(),
                vidxs=vidxs,
            )
            self._record(
                TraceEntry(
                    channel="ddl",
                    sql=(
                        f"CREATE TABLE {MATERIALS_TABLE} (... embedding VECTOR({self.dim}) ..., "
                        f"VECTOR INDEX vidx_{MATERIALS_TABLE}_embedding (embedding) WITH (distance=l2, type=hnsw))"
                    ),
                    params={"dim": self.dim},
                    rows=0,
                    duration_ms=(time.perf_counter() - ddl_started) * 1000.0,
                )
            )

        self._ensure_fts_index()
        self._execute(
            channel="ddl",
            sql=(
                f"CREATE TABLE IF NOT EXISTS {REVIEWS_TABLE} ("
                "id VARCHAR(64) PRIMARY KEY, material_id VARCHAR(64) NOT NULL, course VARCHAR(64) NOT NULL, "
                "verdict VARCHAR(16) NOT NULL, question VARCHAR(255) NOT NULL, context_hit TEXT, created_at VARCHAR(32) NOT NULL)"
            ),
            params={},
        )

    def _ensure_fts_index(self) -> None:
        # seekdb ships the MySQL-compatible ``ngram`` parser, which handles the
        # mixed CJK/English passages in the sample library. If a deployment
        # rejects it we degrade to ``space`` and finally to the default parser.
        # The index is created once per database, so a restart must recognise
        # the existing definition instead of trying (and failing) to add it again.
        existing = self._existing_fts_parser()
        if existing:
            self.fts_parser = existing
            self._record(
                TraceEntry(
                    channel="ddl",
                    sql=f"SHOW CREATE TABLE {MATERIALS_TABLE}",
                    params={},
                    rows=1,
                    duration_ms=0.0,
                    note=f"reused existing full-text index (parser={existing})",
                )
            )
            return

        from sqlalchemy import text

        for parser in ("ngram", "space", ""):
            started = time.perf_counter()
            with_parser = f" WITH PARSER {parser}" if parser else ""
            ddl = (
                f"ALTER TABLE {MATERIALS_TABLE} ADD FULLTEXT INDEX ftidx_{MATERIALS_TABLE}_search_text "
                f"(search_text){with_parser}"
            )
            try:
                with self.engine.connect() as connection:
                    connection.execute(text(ddl))
                    connection.commit()
            except Exception as error:  # noqa: BLE001 - dialect probing
                message = str(error)
                if "Duplicate key name" in message or "1061" in message:
                    # Another process (or a previous boot) already created it.
                    self.fts_parser = parser or "default"
                    return
                self._record(
                    TraceEntry(
                        channel="ddl",
                        sql=ddl,
                        params={"parser": parser or "default"},
                        rows=0,
                        duration_ms=(time.perf_counter() - started) * 1000.0,
                        note=f"rejected: {type(error).__name__}: {message[:160]}",
                    )
                )
                continue
            self.fts_parser = parser or "default"
            self._record(
                TraceEntry(
                    channel="ddl",
                    sql=ddl,
                    params={"parser": parser or "default"},
                    rows=0,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            )
            return
        LOGGER.warning("no full-text parser accepted; lexical channel disabled")
        self.fts_parser = ""

    def _existing_fts_parser(self) -> str | None:
        try:
            rows = self._execute(
                channel="ddl", sql=f"SHOW CREATE TABLE {MATERIALS_TABLE}", params={}
            )
        except Exception:  # noqa: BLE001 - metadata probe
            return None
        ddl = " ".join(str(value) for row in rows for value in row.values())
        if f"ftidx_{MATERIALS_TABLE}_search_text" not in ddl:
            return None
        match = re.search(r"WITH PARSER\s+(\w+)", ddl)
        return (match.group(1).lower() if match else "default")

    # -- helpers -------------------------------------------------------------
    def _record(self, entry: TraceEntry) -> None:
        self.evidence.record(entry)

    def _execute(self, *, channel: str, sql: str, params: dict[str, Any], show_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        from sqlalchemy import text

        started = time.perf_counter()
        with self.engine.connect() as connection:
            result = connection.execute(text(sql), params) if params else connection.execute(text(sql))
            rows = [dict(row._mapping) for row in result] if result.returns_rows else []
            connection.commit()
        self._record(
            timed(
                TraceEntry(
                    channel=channel,
                    sql=squeeze(sql),
                    params=show_params if show_params is not None else params,
                    rows=len(rows),
                    duration_ms=0.0,
                ),
                started,
            )
        )
        return rows

    def _vector_param(self, vector: list[float]) -> Any:
        from sqlalchemy import bindparam

        from pyobvector import VECTOR

        return bindparam("query_vector", value=vector, type_=VECTOR(self.dim))

    # -- writes --------------------------------------------------------------
    def upsert_materials(self, materials: list[dict[str, Any]]) -> int:
        if not materials:
            return 0
        vectors = self.embedder.embed(
            [f"{item['course']} {item['title']}\n{item['body']}" for item in materials]
        )
        written = 0
        for item, vector in zip(materials, vectors, strict=True):
            payload = {
                "id": item["id"],
                "course": item["course"],
                "chapter": item.get("chapter") or "",
                "title": item["title"],
                "body": item["body"],
                "search_text": f"{item['title']} {item.get('chapter') or ''} {item['course']} {item['body']}",
                "keywords": ",".join(item.get("keywords") or keywords(item["title"] + item["body"])),
                "difficulty": str(int(item.get("difficulty") or 2)),
                "source": item.get("source") or "",
                "mastery": str(round(float(item.get("mastery") or 0.0), 4)),
                "reviews": str(int(item.get("reviews") or 0)),
                "last_verdict": item.get("last_verdict") or "",
                "updated_at": item.get("updated_at") or _now(),
            }
            existing = self._execute(
                channel="write",
                sql=f"SELECT id FROM {MATERIALS_TABLE} WHERE id = :id",
                params={"id": payload["id"]},
            )
            if existing:
                assignments = ", ".join(f"{name} = :{name}" for name in payload if name != "id")
                self._execute(
                    channel="write",
                    sql=f"UPDATE {MATERIALS_TABLE} SET {assignments}, embedding = :embedding WHERE id = :id",
                    params={**payload, "embedding": vector},
                    show_params={**payload, "embedding": summarize_vector(vector)},
                )
            else:
                self.client.insert(MATERIALS_TABLE, data=[{**payload, "embedding": vector}])
                self._record(
                    TraceEntry(
                        channel="write",
                        sql=f"INSERT INTO {MATERIALS_TABLE} (...) VALUES (...)",
                        params={"id": payload["id"], "embedding": summarize_vector(vector)},
                        rows=1,
                        duration_ms=0.0,
                    )
                )
            written += 1
        return written

    def record_review(self, review: dict[str, Any]) -> None:
        payload = {
            "id": review["id"],
            "material_id": review["material_id"],
            "course": review.get("course") or "",
            "verdict": review["verdict"],
            "question": squeeze(review.get("question") or "")[:240],
            "context_hit": review.get("context_hit") or "",
            "created_at": review.get("created_at") or _now(),
        }
        self._execute(
            channel="write",
            sql=(
                f"INSERT INTO {REVIEWS_TABLE} (id, material_id, course, verdict, question, context_hit, created_at) "
                "VALUES (:id, :material_id, :course, :verdict, :question, :context_hit, :created_at)"
            ),
            params=payload,
        )

    def update_mastery(self, material_id: str, mastery: float, reviews: int, verdict: str) -> None:
        self._execute(
            channel="write",
            sql=(
                f"UPDATE {MATERIALS_TABLE} SET mastery = :mastery, reviews = :reviews, "
                "last_verdict = :verdict, updated_at = :updated_at WHERE id = :id"
            ),
            params={
                "id": material_id,
                "mastery": f"{max(0.0, min(1.0, mastery)):.4f}",
                "reviews": str(reviews),
                "verdict": verdict,
                "updated_at": _now(),
            },
        )

    # -- reads ---------------------------------------------------------------
    def _list_materials(self, course: str | None, limit: int) -> list[dict[str, Any]]:
        where = "WHERE course = :course " if course else ""
        params: dict[str, Any] = {"limit": limit}
        if course:
            params["course"] = course
        rows = self._execute(
            channel="read",
            sql=(
                f"SELECT id, course, chapter, title, body, keywords, difficulty, source, mastery, reviews, "
                f"last_verdict, updated_at FROM {MATERIALS_TABLE} {where}ORDER BY course, chapter, title LIMIT :limit"
            ),
            params=params,
        )
        return [_row_to_dict(row).to_dict() for row in rows]

    def materials(self, course: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return self._list_materials(course, limit)

    def courses(self) -> list[dict[str, Any]]:
        rows = self._execute(
            channel="read",
            sql=(
                "SELECT course, COUNT(*) AS total, AVG(m) AS avg_mastery, SUM(r) AS reviews "
                f"FROM (SELECT course, CAST(mastery AS DOUBLE) AS m, CAST(reviews AS SIGNED) AS r "
                f"FROM {MATERIALS_TABLE}) t GROUP BY course ORDER BY course"
            ),
            params={},
        )
        return [
            {
                "course": row["course"],
                "total": int(row["total"]),
                "avg_mastery": round(float(row["avg_mastery"] or 0.0), 3),
                "reviews": int(row["reviews"] or 0),
            }
            for row in rows
        ]

    def hybrid_search(
        self, query: str, course: str | None, top_k: int = 6
    ) -> tuple[list[SearchHit], list[TraceEntry]]:
        trace: list[TraceEntry] = []
        vector_rows = self._vector_channel(query, course, top_k * 3, trace)
        lexical_rows = self._lexical_channel(query, course, top_k * 3, trace) if self.fts_parser else []
        return fuse_rrf(vector_rows, lexical_rows)[:top_k], trace

    def _vector_channel(
        self, query: str, course: str, limit: int, trace: list[TraceEntry]
    ) -> list[tuple[str, dict[str, Any]]]:
        from sqlalchemy import text

        query_vector = self.embedder.embed([query])[0]
        where = "WHERE course = :course " if course else ""
        params: dict[str, Any] = {"limit": limit}
        if course:
            params["course"] = course
        for approximate in ((True, False) if self.approximate_supported is not False else (False,)):
            suffix = " APPROXIMATE" if approximate else ""
            sql = (
                "SELECT id, course, chapter, title, body, keywords, difficulty, source, mastery, reviews, "
                "last_verdict, updated_at, l2_distance(embedding, :query_vector) AS distance "
                f"FROM {MATERIALS_TABLE} {where}ORDER BY l2_distance(embedding, :query_vector){suffix} LIMIT :limit"
            )
            started = time.perf_counter()
            try:
                with self.engine.connect() as connection:
                    result = connection.execute(
                        text(sql).bindparams(self._vector_param(query_vector)), params
                    )
                    rows = [dict(row._mapping) for row in result]
            except Exception as error:  # noqa: BLE001 - dialect probing
                trace.append(
                    timed(
                        TraceEntry(
                            channel="vector",
                            sql=squeeze(sql),
                            params={"query_vector": summarize_vector(query_vector), **params},
                            rows=0,
                            duration_ms=0.0,
                            note=f"failed: {type(error).__name__}: {str(error)[:160]}",
                        ),
                        started,
                    )
                )
                if approximate:
                    continue
                raise
            self.approximate_supported = approximate
            entry = timed(
                TraceEntry(
                    channel="vector",
                    sql=squeeze(sql),
                    params={"query_vector": summarize_vector(query_vector), **params},
                    rows=len(rows),
                    duration_ms=0.0,
                ),
                started,
            )
            trace.append(entry)
            self._record(entry)
            results = []
            for row in rows:
                material = _row_to_dict(row).to_dict()
                distance = float(row.get("distance") or 0.0)
                results.append((f"m:{material['id']}", {"material": material, "channel": {"distance": round(distance, 6)}}))
            return results
        return []

    def _lexical_channel(
        self, query: str, course: str | None, limit: int, trace: list[TraceEntry]
    ) -> list[tuple[str, dict[str, Any]]]:
        from sqlalchemy import text

        sql = (
            "SELECT id, course, chapter, title, body, keywords, difficulty, source, mastery, reviews, "
            "last_verdict, updated_at, MATCH(search_text) AGAINST(:query_text) AS lexical_score "
            f"FROM {MATERIALS_TABLE} WHERE MATCH(search_text) AGAINST(:query_text)"
        )
        params: dict[str, Any] = {"query_text": query, "limit": limit}
        if course:
            sql += " AND course = :course"
            params["course"] = course
        sql += " ORDER BY lexical_score DESC LIMIT :limit"
        started = time.perf_counter()
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(sql), params)
                rows = [dict(row._mapping) for row in result]
        except Exception as error:  # noqa: BLE001 - dialect probing
            self.fts_parser = ""
            entry = timed(
                TraceEntry(
                    channel="lexical",
                    sql=squeeze(sql),
                    params=params,
                    rows=0,
                    duration_ms=0.0,
                    note=f"disabled after failure: {type(error).__name__}: {str(error)[:160]}",
                ),
                started,
            )
            trace.append(entry)
            self._record(entry)
            return []
        entry = timed(
            TraceEntry(channel="lexical", sql=squeeze(sql), params=params, rows=len(rows), duration_ms=0.0),
            started,
        )
        trace.append(entry)
        self._record(entry)
        results = []
        for row in rows:
            material = _row_to_dict(row).to_dict()
            score = float(row.get("lexical_score") or 0.0)
            results.append((f"l:{material['id']}", {"material": material, "channel": {"score": round(score, 6)}}))
        return results

    def stats(self) -> dict[str, Any]:
        # seekdb mislabels ``CAST(...) AS <alias>`` columns (the cursor reports
        # the cast type, e.g. "DOUBLE"), so aggregate the already-cast values in
        # an outer query instead of aliasing the CAST itself.
        materials = self._execute(
            channel="read",
            sql=(
                "SELECT COUNT(*) AS total, AVG(m) AS avg_mastery, SUM(r) AS reviews "
                f"FROM (SELECT CAST(mastery AS DOUBLE) AS m, CAST(reviews AS SIGNED) AS r FROM {MATERIALS_TABLE}) t"
            ),
            params={},
        )[0]
        weak = self._execute(
            channel="read",
            sql=(
                f"SELECT COUNT(*) AS weak FROM {MATERIALS_TABLE} WHERE CAST(mastery AS DOUBLE) < 0.5"
            ),
            params={},
        )[0]
        verdicts = self._execute(
            channel="read",
            sql=f"SELECT verdict, COUNT(*) AS total FROM {REVIEWS_TABLE} GROUP BY verdict",
            params={},
        )
        return {
            "backend": self.backend,
            "mode": self.mode,
            "materials": int(materials["total"] or 0),
            "avg_mastery": round(float(materials["avg_mastery"] or 0.0), 3),
            "reviews": int(materials["reviews"] or 0),
            "weak": int(weak["weak"] or 0),
            "verdicts": {row["verdict"]: int(row["total"]) for row in verdicts},
            "courses": self.courses(),
            "fts_parser": self.fts_parser or None,
            "approximate": self.approximate_supported,
        }

    def recent_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._execute(
            channel="read",
            sql=(
                f"SELECT id, material_id, course, verdict, question, context_hit, created_at "
                f"FROM {REVIEWS_TABLE} ORDER BY created_at DESC LIMIT :limit"
            ),
            params={"limit": limit},
        )
        return rows

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "mode": self.mode,
            "target": str(self.settings.seekdb_path) if self.settings.seekdb_path else self.settings.seekdb_uri,
            "dim": self.dim,
            "fts_parser": self.fts_parser or None,
            "approximate": self.approximate_supported,
            "tables": [MATERIALS_TABLE, REVIEWS_TABLE],
        }
