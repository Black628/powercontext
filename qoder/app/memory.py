"""PowerContext 记忆客户端（HTTP API）。

记忆键约定（REQUIREMENTS 3.3）：
  user:{uid}:weak_points  薄弱知识点列表（带次数与最近时间）
  user:{uid}:progress     每门课复习进度
  user:{uid}:qa_history   最近 N 次问答摘要

硬性要求：服务不可用时降级——读返回空、写直接放弃，只打 warning，
绝不抛异常阻断问答主流程。
"""

import datetime
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("recall.memory")

POWERCONTEXT_BASE_URL = os.getenv("POWERCONTEXT_BASE_URL", "")  # 占位：.env 中配置真实地址
POWERCONTEXT_API_KEY = os.getenv("POWERCONTEXT_API_KEY", "")   # 占位：不要提交真实密钥
POWERCONTEXT_TIMEOUT = float(os.getenv("POWERCONTEXT_TIMEOUT", "3"))

# qa_history 只保留最近 N 条，避免无限增长
QA_HISTORY_LIMIT = 20


def _key(uid: str, slot: str) -> str:
    """拼出记忆键，如 user:alice:weak_points。"""
    return f"user:{uid}:{slot}"


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


def get_memory(uid: str, slot: str) -> list:
    """读取一个记忆槽位（值为 JSON 列表）；任何异常都返回空列表。"""
    if not _enabled():
        return []
    url = f"{POWERCONTEXT_BASE_URL.rstrip('/')}/v1/memories"
    try:
        resp = requests.get(
            url, headers=_headers(),
            params={"user_id": uid, "key": _key(uid, slot)},
            timeout=POWERCONTEXT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", data if isinstance(data, list) else [])
        logger.info("[memory] 读取 %s 命中 %d 条", _key(uid, slot), len(items))
        return items
    except Exception as e:  # 降级：只警告，不阻断
        logger.warning("[memory] 读取 %s 失败（降级为空）：%s", _key(uid, slot), e)
        return []


def append_memory(uid: str, slot: str, item: dict) -> bool:
    """向记忆槽位追加一条记录（读-改-写）；失败返回 False，不抛异常。"""
    if not _enabled():
        return False
    key = _key(uid, slot)
    # qa_history 做长度裁剪；weak_points 的合并计数在读取侧完成
    items = get_memory(uid, slot)
    items.append(item)
    if slot == "qa_history":
        items = items[-QA_HISTORY_LIMIT:]
    url = f"{POWERCONTEXT_BASE_URL.rstrip('/')}/v1/memories"
    try:
        resp = requests.post(
            url, headers=_headers(),
            json={"user_id": uid, "key": key, "items": items},
            timeout=POWERCONTEXT_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("[memory] 写入 %s 成功（共 %d 条）", key, len(items))
        return True
    except Exception as e:  # 降级：只警告，不阻断
        logger.warning("[memory] 写入 %s 失败（忽略）：%s", key, e)
        return False


def record_qa(uid: str, query: str, topic: str, understood, sources: list) -> None:
    """问答结束后落记忆：qa_history 必写；understood=False 时追加 weak_points。

    understood: True/False/None（None 表示用户未反馈）。
    """
    now = datetime.datetime.now().isoformat(timespec="seconds")
    append_memory(uid, "qa_history", {
        "query": query,
        "topic": topic,
        "understood": understood,
        "sources": [
            {"doc_name": s.get("doc_name"), "page": s.get("page"), "offset": s.get("offset")}
            for s in sources[:3]
        ],
        "time": now,
    })
    if understood is False:
        append_memory(uid, "weak_points", {"topic": topic, "time": now})


def weak_points_summary(uid: str) -> list:
    """把 weak_points 原始记录聚合为「主题 + 出现次数 + 最近时间」，按次数倒序。"""
    agg = {}
    for it in get_memory(uid, "weak_points"):
        topic = (it or {}).get("topic") or "未知主题"
        cur = agg.setdefault(topic, {"topic": topic, "count": 0, "last_time": ""})
        cur["count"] += 1
        cur["last_time"] = max(cur["last_time"], it.get("time", ""))
    return sorted(agg.values(), key=lambda x: (-x["count"], x["last_time"]))
