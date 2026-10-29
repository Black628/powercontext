"""PowerContext 记忆客户端（1.4.x HTTP API）。

记忆约定（REQUIREMENTS 3.3）：
  - 每个 uid 一个 scope，title 固定 recall:<uid>；scope 已存在则复用。
  - 薄弱知识点写成 kind=weak_point，text 为「<topic>｜<状态>」，reason 记录来源。
  - 问答历史写成 kind=qa_history，text 为「<query>｜<topic>｜<状态>｜<ISO 时间>」。

硬性要求：服务不可用时降级——读返回空、写直接放弃，只打 warning，
绝不抛异常阻断问答主流程。
"""

import datetime
import logging
import os
import threading

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("recall.memory")

POWERCONTEXT_BASE_URL = os.getenv("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8001").strip()
POWERCONTEXT_API_KEY = os.getenv("POWERCONTEXT_API_KEY", "")   # 占位：不要提交真实密钥
POWERCONTEXT_TIMEOUT = float(os.getenv("POWERCONTEXT_TIMEOUT", "3"))

# 全角竖线作为字段分隔符，避免与 topic/query 里的半角字符冲突
SEP = "｜"

WEAK_KIND = "weak_point"
HISTORY_KIND = "qa_history"

# uid -> scope_id；进程内缓存，避免反复查 scope
_scope_cache: dict[str, str] = {}
_scope_lock = threading.Lock()


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if POWERCONTEXT_API_KEY:
        h["Authorization"] = f"Bearer {POWERCONTEXT_API_KEY}"
    return h


def _enabled() -> bool:
    if not POWERCONTEXT_BASE_URL:
        logger.warning("[memory] 未配置 POWERCONTEXT_BASE_URL，记忆功能降级为空操作")
        return False
    return True


def _post(path: str, payload: dict) -> dict:
    url = f"{POWERCONTEXT_BASE_URL.rstrip('/')}{path}"
    resp = requests.post(url, headers=_headers(), json=payload, timeout=POWERCONTEXT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{POWERCONTEXT_BASE_URL.rstrip('/')}{path}"
    resp = requests.get(url, headers=_headers(), params=params, timeout=POWERCONTEXT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _find_scope_id(title: str) -> str | None:
    """分页遍历 GET /v1/scopes，按 title 精确匹配。"""
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = _get("/v1/scopes", params)
        for item in data.get("items") or []:
            if item.get("title") == title:
                return item.get("scope_id")
        cursor = data.get("next_cursor")
        if not cursor:
            return None


def _scope_id(uid: str) -> str | None:
    """取（必要时创建）uid 对应的 scope；失败返回 None。"""
    with _scope_lock:
        cached = _scope_cache.get(uid)
        if cached:
            return cached
        title = f"recall:{uid}"
        try:
            scope_id = _find_scope_id(title)
            if not scope_id:
                data = _post("/v1/scopes", {
                    "title": title,
                    "summary": f"Recall 用户 {uid} 的薄弱点与问答记忆",
                    "idempotency_key": f"recall-scope:{uid}",
                })
                scope_id = data.get("scope_id")
                logger.info("[memory] 创建 scope %s（%s）", title, scope_id)
            if not scope_id:
                return None
            _scope_cache[uid] = scope_id
            return scope_id
        except Exception as e:  # 降级：只警告，不阻断
            logger.warning("[memory] 解析 scope %s 失败（降级为空操作）：%s", title, e)
            return None


def _remember(uid: str, kind: str, text: str, reason: str) -> bool:
    """写一条记忆；失败返回 False，不抛异常。"""
    if not _enabled():
        return False
    scope_id = _scope_id(uid)
    if not scope_id:
        return False
    try:
        _post("/v1/memory/remember", {
            "scope_id": scope_id,
            "kind": kind,
            "text": text,
            "reason": reason,
        })
        logger.info("[memory] 写入 user:%s:%s 成功（共 1 条）", uid, kind)
        return True
    except Exception as e:  # 降级：只警告，不阻断
        logger.warning("[memory] 写入 user:%s:%s 失败（忽略）：%s", uid, kind, e)
        return False


def append_memory(uid: str, records: list) -> int:
    """批量写薄弱点：每个 weak_point 写一条 kind=weak_point，返回成功条数。

    records 元素形如 {"topic": str, "status": str, "source": str}；
    text 统一为「<topic>｜<状态>」，reason 记录来源（如 ask）。
    """
    if not records:
        return 0
    if not _enabled():
        return 0
    scope_id = _scope_id(uid)
    if not scope_id:
        return 0
    written = 0
    for rec in records:
        rec = rec or {}
        topic = str(rec.get("topic") or "未知主题").strip() or "未知主题"
        status = str(rec.get("status") or "没懂").strip() or "没懂"
        reason = str(rec.get("source") or rec.get("reason") or "ask").strip() or "ask"
        try:
            _post("/v1/memory/remember", {
                "scope_id": scope_id,
                "kind": WEAK_KIND,
                "text": f"{topic}{SEP}{status}",
                "reason": reason,
            })
            written += 1
        except Exception as e:  # 降级：只警告，不阻断
            logger.warning("[memory] 写入 user:%s:weak_points 失败（忽略）：%s", uid, e)
    if written:
        logger.info("[memory] 写入 user:%s:weak_points 成功（共 %d 条）", uid, written)
    return written


def _list_entries(scope_id: str) -> list:
    """分页取回 scope 内全部记忆条目。"""
    entries = []
    cursor = None
    while True:
        payload = {"scope_id": scope_id, "limit": 100}
        if cursor:
            payload["cursor"] = cursor
        data = _post("/v1/memory/entries/list", payload)
        entries.extend(data.get("entries") or [])
        cursor = data.get("next_cursor")
        if not cursor:
            return entries


def _split_text(text: str, parts: int) -> list:
    return [p.strip() for p in (text or "").split(SEP, parts - 1)]


def _aggregate_weak(entries: list) -> list:
    agg: dict = {}
    for entry in entries:
        if (entry or {}).get("kind") != WEAK_KIND:
            continue
        fields = _split_text(entry.get("text") or "", 2)
        topic = (fields[0] if fields else "").strip() or "未知主题"
        status = fields[1] if len(fields) > 1 else ""
        cur = agg.setdefault(topic, {
            "topic": topic,
            "count": 0,
            "last_time": "",
            "status": status,
            "source": "weak_points",
        })
        cur["count"] += 1
        if status:
            cur["status"] = status
    return sorted(agg.values(), key=lambda x: (-x["count"], x["topic"]))


def _parse_history(entries: list) -> list:
    items = []
    for entry in entries:
        if (entry or {}).get("kind") != HISTORY_KIND:
            continue
        fields = _split_text(entry.get("text") or "", 4)
        query = fields[0] if fields else ""
        topic = fields[1] if len(fields) > 1 else ""
        status = fields[2] if len(fields) > 2 else ""
        time_str = fields[3] if len(fields) > 3 else ""
        items.append({
            "query": query,
            "topic": topic or query[:20],
            "understood": {"已懂": True, "没懂": False}.get(status),
            "time": time_str,
            "source": "qa_history",
        })
    return items


def get_memory(uid: str, slot: str = "weak_points") -> list:
    """读取记忆；任何异常或解析失败都降级为空列表。

    slot="weak_points"：按 topic 聚合为 [{topic, count, status, source, last_time}]；
    slot="qa_history"：返回 [{query, topic, understood, time, source}]。
    """
    if not _enabled():
        return []
    if slot not in ("weak_points", HISTORY_KIND):
        slot = "weak_points"
    scope_id = _scope_id(uid)
    if not scope_id:
        return []
    try:
        entries = _list_entries(scope_id)
    except Exception as e:  # 降级：只警告，不阻断
        logger.warning("[memory] 读取 user:%s:%s 失败（降级为空）：%s", uid, slot, e)
        return []
    try:
        if slot == HISTORY_KIND:
            return _parse_history(entries)
        items = _aggregate_weak(entries)
    except Exception as e:  # 解析失败同样降级为空
        logger.warning("[memory] 解析 user:%s:%s 失败（降级为空）：%s", uid, slot, e)
        return []
    logger.info("[memory] 读取 user:%s:weak_points 命中 %d 条", uid, len(items))
    return items


def record_qa(uid: str, query: str, topic: str, understood, sources: list) -> None:
    """问答结束后落记忆：qa_history 必写；understood=False 时追加 weak_point。

    understood: True/False/None（None 表示用户未反馈）。
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")
    status = {True: "已懂", False: "没懂"}.get(understood, "未反馈")
    _remember(uid, HISTORY_KIND, f"{query}{SEP}{topic}{SEP}{status}{SEP}{now}", "ask")
    if understood is False:
        append_memory(uid, [{"topic": topic, "status": status, "source": "ask"}])


def weak_points_summary(uid: str) -> list:
    """把 weak_point 条目聚合为「主题 + 出现次数」，按次数倒序。"""
    return get_memory(uid, "weak_points")
