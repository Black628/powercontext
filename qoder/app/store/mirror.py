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

"""SQLite mirror backend for hosts without the seekdb runtime.

Every ``seekdb`` client library currently targets Linux and macOS. This module
keeps the product honest on a Windows laptop: it implements the *same* store
contract (two independent retrieval channels + reciprocal-rank fusion) over
SQLite, with unit-normalised embeddings compared by cosine and a small BM25
lexical channel written in Python. The UI and the evidence panel label the
backend, so nobody mistakes this for seekdb.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..embedding import Embedder
from ..text import keywords, squeeze, tokenize
from .base import (
    EvidenceLog,
    MaterialRow,
    SearchHit,
    TraceEntry,
    fuse_rrf,
    summarize_vector,
    timed,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY,
    course TEXT NOT NULL,
    chapter TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    difficulty INTEGER NOT NULL DEFAULT 2,
    source TEXT NOT NULL DEFAULT '',
    mastery REAL NOT NULL DEFAULT 0,
    reviews INTEGER NOT NULL DEFAULT 0,
    last_verdict TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL,
    course TEXT NOT NULL,
    verdict TEXT NOT NULL,
    question TEXT NOT NULL,
    context_hit TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

MATERIAL_FIELDS = "id, course, chapter, title, body, keywords, difficulty, source, mastery, reviews, last_verdict, updated_at"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MirrorStore:
    backend = "sqlite-mirror"

    def __init__(self, settings: Settings, embedder: Embedder) -> None:
        self.settings = settings
        self.embedder = embedder
        self.dim = embedder.dim
        self.path = Path(settings.data_dir) / "mirror.sqlite3"
        self.evidence = EvidenceLog()
        self._db: sqlite3.Connection | None = None

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        started = time.perf_counter()
        self._db.executescript(SCHEMA)
        self._db.commit()
        self.evidence.record(
            timed(
                TraceEntry(
                    channel="ddl",
                    sql=squeeze(SCHEMA),
                    params={"path": str(self.path)},
                    rows=0,
                    duration_ms=0.0,
                    note="mirror backend; seekdb runtime unavailable on this host",
                ),
                started,
            )
        )

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.open()
        assert self._db is not None
        return self._db

    def _query(self, sql: str, params: dict[str, Any] | None = None) -> list[sqlite3.Row]:
        started = time.perf_counter()
        cursor = self.db.execute(sql, params or {})
        rows = cursor.fetchall()
        self.evidence.record(
            timed(
                TraceEntry(
                    channel="read",
                    sql=squeeze(sql),
                    params=params or {},
                    rows=len(rows),
                    duration_ms=0.0,
                    note="mirror",
                ),
                started,
            )
        )
        return rows

    # -- writes --------------------------------------------------------------
    def upsert_materials(self, materials: list[dict[str, Any]]) -> int:
        if not materials:
            return 0
        vectors = self.embedder.embed(
            [f"{item['course']} {item['title']}\n{item['body']}" for item in materials]
        )
        started = time.perf_counter()
        written = 0
        for item, vector in zip(materials, vectors, strict=True):
            payload = {
                "id": item["id"],
                "course": item["course"],
                "chapter": item.get("chapter") or "",
                "title": item["title"],
                "body": item["body"],
                "keywords": ",".join(item.get("keywords") or keywords(item["title"] + item["body"])),
                "difficulty": int(item.get("difficulty") or 2),
                "source": item.get("source") or "",
                "mastery": float(item.get("mastery") or 0.0),
                "reviews": int(item.get("reviews") or 0),
                "last_verdict": item.get("last_verdict") or "",
                "updated_at": item.get("updated_at") or _now(),
            }
            blob = array("f", vector).tobytes()
            self.db.execute(
                "INSERT INTO materials (id, course, chapter, title, body, keywords, difficulty, source, mastery, reviews, "
                "last_verdict, updated_at, embedding) VALUES (:id, :course, :chapter, :title, :body, :keywords, :difficulty, "
                ":source, :mastery, :reviews, :last_verdict, :updated_at, :embedding) "
                "ON CONFLICT(id) DO UPDATE SET course=excluded.course, chapter=excluded.chapter, title=excluded.title, "
                "body=excluded.body, keywords=excluded.keywords, difficulty=excluded.difficulty, source=excluded.source, "
                "embedding=excluded.embedding",
                {**payload, "embedding": blob},
            )
            written += 1
        self.db.commit()
        self.evidence.record(
            timed(
                TraceEntry(
                    channel="write",
                    sql="INSERT INTO materials (...) VALUES (...) ON CONFLICT(id) DO UPDATE SET ...",
                    params={"rows": written, "dim": self.dim},
                    rows=written,
                    duration_ms=0.0,
                    note="mirror",
                ),
                started,
            )
        )
        return written

    def record_review(self, review: dict[str, Any]) -> None:
        started = time.perf_counter()
        payload = {
            "id": review["id"],
            "material_id": review["material_id"],
            "course": review.get("course") or "",
            "verdict": review["verdict"],
            "question": squeeze(review.get("question") or "")[:240],
            "context_hit": review.get("context_hit") or "",
            "created_at": review.get("created_at") or _now(),
        }
        self.db.execute(
            "INSERT INTO reviews (id, material_id, course, verdict, question, context_hit, created_at) "
            "VALUES (:id, :material_id, :course, :verdict, :question, :context_hit, :created_at)",
            payload,
        )
        self.db.commit()
        self.evidence.record(
            timed(
                TraceEntry(
                    channel="write",
                    sql="INSERT INTO reviews (...) VALUES (...)",
                    params={"id": payload["id"], "verdict": payload["verdict"]},
                    rows=1,
                    duration_ms=0.0,
                    note="mirror",
                ),
                started,
            )
        )

    def update_mastery(self, material_id: str, mastery: float, reviews: int, verdict: str) -> None:
        started = time.perf_counter()
        self.db.execute(
            "UPDATE materials SET mastery = ?, reviews = ?, last_verdict = ?, updated_at = ? WHERE id = ?",
            (max(0.0, min(1.0, mastery)), reviews, verdict, _now(), material_id),
        )
        self.db.commit()
        self.evidence.record(
            timed(
                TraceEntry(
                    channel="write",
                    sql="UPDATE materials SET mastery = ?, reviews = ?, last_verdict = ?, updated_at = ? WHERE id = ?",
                    params={"id": material_id, "mastery": round(mastery, 3), "verdict": verdict},
                    rows=1,
                    duration_ms=0.0,
                    note="mirror",
                ),
                started,
            )
        )

    # -- reads ---------------------------------------------------------------
    def materials(self, course: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if course:
            rows = self._query(
                f"SELECT {MATERIAL_FIELDS} FROM materials WHERE course = :course ORDER BY course, chapter, title LIMIT :limit",
                {"course": course, "limit": limit},
            )
        else:
            rows = self._query(
                f"SELECT {MATERIAL_FIELDS} FROM materials ORDER BY course, chapter, title LIMIT :limit",
                {"limit": limit},
            )
        return [self._row(row).to_dict() for row in rows]

    def courses(self) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT course, COUNT(*) AS total, AVG(mastery) AS avg_mastery, SUM(reviews) AS reviews "
            "FROM materials GROUP BY course ORDER BY course"
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
        material_rows = self._query(
            f"SELECT {MATERIAL_FIELDS}, embedding FROM materials" + (" WHERE course = :course" if course else ""),
            {"course": course} if course else {},
        )
        materials = [self._row(row).to_dict() for row in material_rows]
        vector_rows = self._vector_channel(query, materials, material_rows, top_k * 3, trace)
        lexical_rows = self._lexical_channel(query, materials, top_k * 3, trace)
        return fuse_rrf(vector_rows, lexical_rows)[:top_k], trace

    def _vector_channel(
        self,
        query: str,
        materials: list[dict[str, Any]],
        rows: list[sqlite3.Row],
        limit: int,
        trace: list[TraceEntry],
    ) -> list[tuple[str, dict[str, Any]]]:
        started = time.perf_counter()
        query_vector = self.embedder.embed([query])[0]
        scored: list[tuple[float, dict[str, Any]]] = []
        for material, row in zip(materials, rows, strict=True):
            vector = array("f")
            vector.frombytes(row["embedding"])
            if len(vector) != self.dim:
                continue
            dot = sum(a * b for a, b in zip(query_vector, vector, strict=True))
            cosine = max(-1.0, min(1.0, dot))
            scored.append((1.0 - cosine, material))
        scored.sort(key=lambda item: item[0])
        entry = timed(
            TraceEntry(
                channel="vector",
                sql="python: ORDER BY cosine_similarity(embedding, query) DESC LIMIT %s" % limit,
                params={"query_vector": summarize_vector(query_vector), "candidates": len(scored)},
                rows=min(limit, len(scored)),
                duration_ms=0.0,
                note="mirror: cosine over unit vectors (seekdb equivalent: l2_distance APPROXIMATE)",
            ),
            started,
        )
        trace.append(entry)
        self.evidence.record(entry)
        return [
            (f"m:{material['id']}", {"material": material, "channel": {"distance": round(distance, 6)}})
            for distance, material in scored[:limit]
        ]

    def _lexical_channel(
        self, query: str, materials: list[dict[str, Any]], limit: int, trace: list[TraceEntry]
    ) -> list[tuple[str, dict[str, Any]]]:
        started = time.perf_counter()
        query_tokens = set(tokenize(query))
        documents = [tokenize(f"{material['title']} {material['chapter']} {material['course']} {material['body']}") for material in materials]
        total = len(documents)
        document_frequency: dict[str, int] = {}
        for tokens in documents:
            for token in set(tokens):
                document_frequency[token] = document_frequency.get(token, 0) + 1
        scored: list[tuple[float, dict[str, Any]]] = []
        for material, tokens in zip(materials, documents, strict=True):
            tf: dict[str, int] = {}
            for token in tokens:
                if token in query_tokens:
                    tf[token] = tf.get(token, 0) + 1
            if not tf:
                continue
            length_norm = 0.75 + 0.25 * (len(tokens) / max(1, sum(len(item) for item in documents) / max(1, total)))
            score = 0.0
            for token, count in tf.items():
                idf = math.log(1.0 + (total - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
                score += idf * (count * 2.2) / (count + 1.2 * length_norm)
            scored.append((score, material))
        scored.sort(key=lambda item: -item[0])
        entry = timed(
            TraceEntry(
                channel="lexical",
                sql="python: BM25 over char-bigrams+words ORDER BY score DESC LIMIT %s" % limit,
                params={"query_tokens": sorted(query_tokens)[:10], "candidates": len(scored)},
                rows=min(limit, len(scored)),
                duration_ms=0.0,
                note="mirror: BM25 (seekdb equivalent: MATCH ... AGAINST)",
            ),
            started,
        )
        trace.append(entry)
        self.evidence.record(entry)
        return [
            (f"l:{material['id']}", {"material": material, "channel": {"score": round(score, 6)}})
            for score, material in scored[:limit]
        ]

    def stats(self) -> dict[str, Any]:
        first = self._query(
            "SELECT COUNT(*) AS total, AVG(mastery) AS avg_mastery, SUM(reviews) AS reviews FROM materials"
        )[0]
        weak = self._query("SELECT COUNT(*) AS weak FROM materials WHERE mastery < 0.5")[0]["weak"]
        verdict_rows = self._query("SELECT verdict, COUNT(*) AS total FROM reviews GROUP BY verdict")
        return {
            "backend": self.backend,
            "mode": "sqlite",
            "materials": int(first["total"] or 0),
            "avg_mastery": round(float(first["avg_mastery"] or 0.0), 3),
            "reviews": int(first["reviews"] or 0),
            "weak": int(weak or 0),
            "verdicts": {row["verdict"]: int(row["total"]) for row in verdict_rows},
            "courses": self.courses(),
            "fts_parser": None,
            "approximate": None,
        }

    def recent_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, material_id, course, verdict, question, context_hit, created_at FROM reviews "
            "ORDER BY created_at DESC LIMIT :limit",
            {"limit": limit},
        )
        return [dict(row) for row in rows]

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "mode": "sqlite",
            "target": str(self.path),
            "dim": self.dim,
            "fts_parser": None,
            "approximate": None,
            "tables": ["materials", "reviews"],
        }

    @staticmethod
    def _row(row: Any) -> MaterialRow:
        keys = str(row["keywords"] or "")
        return MaterialRow(
            id=str(row["id"]),
            course=str(row["course"]),
            chapter=str(row["chapter"] or ""),
            title=str(row["title"]),
            body=str(row["body"]),
            keywords=[part for part in keys.split(",") if part],
            difficulty=int(row["difficulty"] or 2),
            source=str(row["source"] or ""),
            mastery=float(row["mastery"] or 0.0),
            reviews=int(row["reviews"] or 0),
            last_verdict=str(row["last_verdict"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )


def dumps_hint(payload: dict[str, Any]) -> str:
    """Debug helper used by the smoke test."""
    return json.dumps(payload, ensure_ascii=False, default=str)
