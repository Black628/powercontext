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

"""Storage contract shared by the seekdb backend and the SQLite mirror."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

RRF_K = 60


@dataclass(slots=True)
class SearchHit:
    """One fused retrieval result plus the per-channel evidence behind it."""

    material: dict[str, Any]
    fused: float
    vector: dict[str, Any] | None = None
    lexical: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "fused": round(self.fused, 6),
            "vector": self.vector,
            "lexical": self.lexical,
        }


@dataclass(slots=True)
class TraceEntry:
    channel: str
    sql: str
    params: dict[str, Any]
    rows: int
    duration_ms: float
    note: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "sql": self.sql,
            "params": self.params,
            "rows": self.rows,
            "duration_ms": round(self.duration_ms, 3),
            "note": self.note,
        }


def summarize_vector(values: list[float], keep: int = 4) -> list[float]:
    """Compact a vector for evidence display without dumping 1024 floats."""
    return [round(value, 4) for value in values[:keep]]


def fuse_rrf(
    vector_rows: list[tuple[str, dict[str, Any]]],
    lexical_rows: list[tuple[str, dict[str, Any]]],
    k: int = RRF_K,
) -> list[SearchHit]:
    """Reciprocal-rank fusion over the two independent channels.

    RRF is scale-free, so the vector channel's L2 distance (lower is better)
    and the lexical channel's BM25-ish score (higher is better) can be fused
    without normalising either one.
    """
    fused: dict[str, SearchHit] = {}
    for rank, (_channel_key, detail) in enumerate(vector_rows, start=1):
        material_id = str(detail["material"]["id"])
        hit = fused.setdefault(material_id, SearchHit(material=detail["material"], fused=0.0))
        hit.vector = {"rank": rank, **detail["channel"]}
        hit.fused += 1.0 / (k + rank)
    for rank, (_channel_key, detail) in enumerate(lexical_rows, start=1):
        material_id = str(detail["material"]["id"])
        hit = fused.setdefault(material_id, SearchHit(material=detail["material"], fused=0.0))
        hit.lexical = {"rank": rank, **detail["channel"]}
        hit.fused += 1.0 / (k + rank)
    return sorted(fused.values(), key=lambda hit: (-hit.fused, hit.material["id"]))


class MaterialStore(Protocol):
    """Port implemented by each backend; the coach only talks to this surface."""

    backend: str

    def open(self) -> None: ...

    def close(self) -> None: ...

    def upsert_materials(self, materials: list[dict[str, Any]]) -> int: ...

    def courses(self) -> list[dict[str, Any]]: ...

    def materials(self, course: str | None = None, limit: int = 200) -> list[dict[str, Any]]: ...

    def hybrid_search(self, query: str, course: str | None, top_k: int) -> tuple[list[SearchHit], list[TraceEntry]]: ...

    def record_review(self, review: dict[str, Any]) -> None: ...

    def update_mastery(self, material_id: str, mastery: float, reviews: int, verdict: str) -> None: ...

    def stats(self) -> dict[str, Any]: ...

    def recent_reviews(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def describe(self) -> dict[str, Any]: ...


class EvidenceLog:
    """Ring buffer of executed SQL, used by the evidence panel and the smoke test."""

    def __init__(self, capacity: int = 200) -> None:
        self._entries: deque[TraceEntry] = deque(maxlen=capacity)

    def record(self, entry: TraceEntry) -> TraceEntry:
        self._entries.append(entry)
        return entry

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        return [entry.to_payload() for entry in list(self._entries)[-limit:][::-1]]

    def clear(self) -> None:
        self._entries.clear()


def timed(entry: TraceEntry, started: float) -> TraceEntry:
    entry.duration_ms = (time.perf_counter() - started) * 1000.0
    return entry


@dataclass(slots=True)
class MaterialRow:
    """Typed view over a stored material row."""

    id: str
    course: str
    chapter: str
    title: str
    body: str
    keywords: list[str] = field(default_factory=list)
    difficulty: int = 2
    source: str = ""
    mastery: float = 0.0
    reviews: int = 0
    last_verdict: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "course": self.course,
            "chapter": self.chapter,
            "title": self.title,
            "body": self.body,
            "keywords": list(self.keywords),
            "difficulty": self.difficulty,
            "source": self.source,
            "mastery": round(self.mastery, 3),
            "reviews": self.reviews,
            "last_verdict": self.last_verdict,
            "updated_at": self.updated_at,
        }
