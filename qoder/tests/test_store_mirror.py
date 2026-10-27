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

from __future__ import annotations

from app.store.base import fuse_rrf


def _material(material_id: str, course: str, title: str, body: str, mastery: float = 0.0, reviews: int = 0) -> dict:
    return {
        "id": material_id,
        "course": course,
        "chapter": "",
        "title": title,
        "body": body,
        "keywords": [],
        "difficulty": 2,
        "source": "tests",
        "mastery": mastery,
        "reviews": reviews,
        "last_verdict": "",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def test_upsert_and_query_materials(store) -> None:
    written = store.upsert_materials(
        [
            _material("m_1", "操作系统", "虚拟内存与页面置换", "虚拟内存用页表把虚拟地址映射到物理页框，缺页时按页面置换算法换出。"),
            _material("m_2", "操作系统", "进程与线程", "进程是资源分配单位，线程是 CPU 调度单位，线程共享地址空间。"),
        ]
    )
    assert written == 2
    rows = store.materials("操作系统")
    assert [row["id"] for row in rows] == ["m_1", "m_2"]
    assert rows[0]["keywords"]


def test_courses_aggregate(store) -> None:
    store.upsert_materials(
        [
            _material("m_1", "操作系统", "虚拟内存", "虚拟内存 缺页异常", mastery=0.2),
            _material("m_2", "机器学习", "正则化", "L2 正则化抑制过拟合", mastery=0.6),
        ]
    )
    rows = {row["course"]: row for row in store.courses()}
    assert rows["操作系统"]["total"] == 1
    assert rows["操作系统"]["avg_mastery"] == 0.2
    assert rows["机器学习"]["avg_mastery"] == 0.6


def test_hybrid_search_uses_both_channels(store) -> None:
    store.upsert_materials(
        [
            _material("m_1", "操作系统", "虚拟内存与页面置换", "虚拟内存用页表映射物理页框，缺页时按页面置换算法换出。"),
            _material("m_2", "操作系统", "进程与线程", "进程是资源分配单位，线程是 CPU 调度单位。"),
        ]
    )
    hits, trace = store.hybrid_search("虚拟内存 缺页", None, top_k=3)
    assert hits
    assert hits[0].material["id"] == "m_1"
    assert hits[0].vector is not None
    assert hits[0].lexical is not None
    channels = {entry.channel for entry in trace}
    assert {"vector", "lexical"} <= channels


def test_hybrid_search_empty_store(store) -> None:
    hits, trace = store.hybrid_search("任意查询", None, top_k=3)
    assert hits == []
    assert isinstance(trace, list)


def test_update_mastery_clamps(store) -> None:
    store.upsert_materials([_material("m_1", "操作系统", "虚拟内存", "缺页异常")])
    store.update_mastery("m_1", 1.8, 3, "correct")
    assert store.materials("操作系统")[0]["mastery"] == 1.0
    store.update_mastery("m_1", -0.4, 4, "again")
    assert store.materials("操作系统")[0]["mastery"] == 0.0
    assert store.materials("操作系统")[0]["reviews"] == 4


def test_reviews_and_stats(store) -> None:
    store.upsert_materials([_material("m_1", "操作系统", "虚拟内存", "缺页异常")])
    store.record_review(
        {
            "id": "rv_1",
            "material_id": "m_1",
            "course": "操作系统",
            "verdict": "again",
            "question": "虚拟内存 ___",
            "context_hit": "",
        }
    )
    reviews = store.recent_reviews(5)
    assert reviews[0]["verdict"] == "again"
    assert store.stats()["verdicts"]["again"] == 1


def test_fuse_rrf_deduplicates_channels() -> None:
    material = {"id": "m_1", "title": "虚拟内存"}
    vector_rows = [("vector:m_1", {"material": material, "channel": {"rank": 1, "distance": 0.3}})]
    lexical_rows = [("lexical:m_1", {"material": material, "channel": {"rank": 1, "score": 3.2}})]
    hits = fuse_rrf(vector_rows, lexical_rows)
    assert len(hits) == 1
    assert hits[0].vector is not None and hits[0].lexical is not None
    assert hits[0].fused > 1.0 / 60.0
