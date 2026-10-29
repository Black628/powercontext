"""检索器：把任务描述变成 seekdb 查询，按 kind 分组返回带来源的命中切片。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from .db import SeekDB
from .indexer import SPEC_ID_RE

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
SPEC_ID_IN_TASK_RE = re.compile(r"SPEC-[0-9]{1,3}")

STOP_IDENTIFIERS = {"the", "and", "for", "with", "def", "import", "assert", "self", "return"}

DEFAULT_LIMITS = {"spec": 5, "code": 6, "test": 6}
HIT_PRIORITY = {"pinned": 0, "symbol": 1, "fulltext": 2, "like": 3}


def extract_terms(task: str) -> Dict[str, object]:
    identifiers = []
    for token in IDENT_RE.findall(task):
        if token.lower() in STOP_IDENTIFIERS:
            continue
        if token not in identifiers:
            identifiers.append(token)
    # min_total 这类下划线标识符，再补上拆开的两段，提升中文/英文混合召回
    like_terms: List[str] = list(identifiers)
    for token in identifiers:
        for part in token.split("_"):
            if len(part) >= 3 and part not in like_terms:
                like_terms.append(part)
    for chunk in CJK_RE.findall(task):
        if chunk not in like_terms:
            like_terms.append(chunk)
    spec_ids = SPEC_ID_IN_TASK_RE.findall(task)
    return {"identifiers": identifiers, "like_terms": like_terms, "spec_ids": spec_ids}


def build_match_query(task: str, terms: Dict[str, object]) -> str:
    parts = [task.strip()]
    for ident in terms["identifiers"]:  # type: ignore[index]
        parts.append(ident)
    for spec_id in terms["spec_ids"]:   # type: ignore[index]
        parts.append(spec_id)
    return " ".join(parts)


def _merge(*groups: Sequence[dict]) -> List[dict]:
    merged: Dict[str, dict] = {}
    for docs in groups:
        for doc in docs:
            key = doc["doc_key"]
            current = merged.get(key)
            if current is None:
                merged[key] = dict(doc)
                continue
            if HIT_PRIORITY.get(doc["hit"], 9) < HIT_PRIORITY.get(current["hit"], 9):
                current["hit"] = doc["hit"]
            current["score"] = max(float(current["score"]), float(doc["score"]))
    return list(merged.values())


def retrieve(db: SeekDB, task: str, limits: Optional[Dict[str, int]] = None,
             prefer_versions: Optional[str] = None,
             pinned_test_paths: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """执行真实 seekdb 查询并返回 {'specs': [...], 'code': [...], 'tests': [...], 'queries': [...]}。

    pinned_test_paths：工程 sdd.json 里显式声明的兼容性测试文件，始终并入 tests，
    保证“回归测试必须通过”这一条在上下文里始终有对应证据。
    """
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    terms = extract_terms(task)
    match_query = build_match_query(task, terms)
    like_terms: List[str] = terms["like_terms"]  # type: ignore[assignment]
    notes: List[str] = []

    result: Dict[str, List[dict]] = {"spec": [], "code": [], "test": []}
    for kind in ("spec", "code", "test"):
        docs: List[dict] = []
        try:
            docs.extend(db.match(match_query, kind, limits[kind]))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"MATCH 检索 {kind} 失败，降级 LIKE：{exc}")
        docs.extend(db.like(like_terms[:8], kind, limits[kind]))
        if kind in ("code", "test"):
            docs.extend(db.find_symbol(terms["identifiers"], kind, limits[kind]))  # type: ignore[arg-type]
        merged = _merge(docs)
        merged.sort(key=lambda d: (HIT_PRIORITY.get(d["hit"], 9), -float(d["score"])))
        result[kind] = merged[: limits[kind]]

    if pinned_test_paths:
        pinned = db.by_path(list(pinned_test_paths), "test", limit=60)
        merged = _merge(result["test"], pinned)
        merged.sort(key=lambda d: (HIT_PRIORITY.get(d["hit"], 9), d["path"], int(d["line_start"])))
        result["test"] = merged[: limits["test"] + len(pinned)]
        if pinned:
            notes.append(f"已按 sdd.json 声明钉住兼容性测试 {len(pinned)} 个切片")

    if prefer_versions and result["spec"]:
        # 只做标注，不参与过滤：让评审能看出命中的是哪一版规格
        for doc in result["spec"]:
            doc["note"] = f"期望规格版本 {prefer_versions}"

    return {
        "task": task,
        "match_query": match_query,
        "identifiers": terms["identifiers"],
        "like_terms": like_terms,
        "specs": result["spec"],
        "code": result["code"],
        "tests": result["test"],
        "notes": notes,
    }
