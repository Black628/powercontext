# -*- coding: utf-8 -*-
"""记忆酒馆 · 网关服务（Dify 通过 HTTP Request 节点调用）

接口：
  POST /mem/recall   {player, query}  → 召回该玩家记忆（PowerContext），失败降级为最近记忆
  POST /mem/remember {player, text}   → 写入一条新记忆（PowerContext）
  POST /lore/search  {query, limit}   → 酒馆设定检索（seekdb 全文索引）
  GET  /health

技术分工：
- seekdb：① 酒馆设定库 tavern_lore（全文索引，供 NPC 说话有依据）
          ② 玩家档案 tavern_players（player → PowerContext scope_id 映射）
- PowerContext：记忆条目（/v1/scopes、/v1/memory/remember、/v1/memory/search、/v1/memory/entries/list）
- Dify：编排（召回 → LLM 生成 → 写回记忆）

所有外部依赖失败都降级为空结果并记日志，不让 Dify 侧报错中断对话。
"""

import logging
import os
import uuid

import httpx
import pymysql
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tavern")

PC_BASE = os.getenv("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PC_TIMEOUT = float(os.getenv("POWERCONTEXT_TIMEOUT", "8"))

DB = dict(
    host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
    port=int(os.getenv("SEEKDB_PORT", "2881")),
    user=os.getenv("SEEKDB_USER", "root"),
    password=os.getenv("SEEKDB_PASSWORD", ""),
    charset="utf8mb4",
    database=os.getenv("SEEKDB_DATABASE", "tavern"),
    autocommit=True,
)

app = FastAPI(title="Tavern Memory Gateway", version="1.1.0")


class RecallReq(BaseModel):
    player: str
    query: str = ""


class RememberReq(BaseModel):
    player: str
    text: str


class LoreReq(BaseModel):
    query: str
    limit: int = 2


def db():
    return pymysql.connect(**DB)


def pc():
    return httpx.Client(base_url=PC_BASE, timeout=PC_TIMEOUT)


def ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS tavern_players ("
            " player VARCHAR(96) NOT NULL PRIMARY KEY,"
            " scope_id VARCHAR(64) NOT NULL,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ") DEFAULT CHARSET = utf8mb4"
        )


def lookup_scope(conn, player: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT scope_id FROM tavern_players WHERE player=%s", (player,))
        row = cur.fetchone()
    return row[0] if row else ""


def save_scope(conn, player: str, scope_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tavern_players(player, scope_id) VALUES(%s, %s) "
            "ON DUPLICATE KEY UPDATE scope_id=VALUES(scope_id)",
            (player, scope_id),
        )


def create_scope(player: str) -> str:
    """创建玩家 scope。若 idempotency_key 已用过（409），换一个新 key 重建，
    避免依赖列表分页去翻一个可能翻不到的旧 scope。"""
    for attempt in range(2):
        key = "tavern-scope:%s" % player if attempt == 0 else "tavern-scope:%s:%s" % (player, uuid.uuid4().hex[:6])
        with pc() as c:
            r = c.post("/v1/scopes", json={
                "title": "tavern:%s" % player,
                "summary": "记忆酒馆中玩家 %s 的互动记忆" % player,
                "idempotency_key": key,
            })
        if r.status_code in (200, 201):
            data = r.json() or {}
            sid = data.get("scope_id") or data.get("id") or ""
            logger.info("[scope] 新建 player=%s scope=%s", player, sid)
            return sid
        if r.status_code == 409:
            logger.info("[scope] idempotency_key 冲突，换新 key 重试 player=%s", player)
            continue
        logger.warning("[scope] 创建失败 code=%s body=%s", r.status_code, r.text[:200])
        return ""
    return ""


def find_scope_by_title(player: str) -> str:
    """按 title 在已有 scope 中查找（注意返回字段是 items）。"""
    title = "tavern:%s" % player
    try:
        with pc() as c:
            r = c.get("/v1/scopes", params={"limit": 200})
        if r.status_code != 200:
            return ""
        items = (r.json() or {}).get("items") or (r.json() or {}).get("scopes") or []
        for it in items:
            if it.get("title") == title:
                return it.get("scope_id") or it.get("id") or ""
    except Exception as e:
        logger.warning("[scope] 查标题异常 %s", e)
    return ""


def resolve_scope(player: str) -> str:
    """优先从 seekdb 档案取 scope_id；没有则查/建 scope 并登记。"""
    conn = db()
    try:
        ensure_tables(conn)
        sid = lookup_scope(conn, player)
        if sid:
            return sid
        sid = find_scope_by_title(player) or create_scope(player)
        if sid:
            save_scope(conn, player, sid)
        return sid
    finally:
        conn.close()


@app.get("/health")
def health() -> dict:
    pc_ok = False
    try:
        # PowerContext 没有 /health，用 OpenAPI 探测存活
        with pc() as c:
            pc_ok = c.get("/openapi.json").status_code == 200
    except Exception:
        pass
    db_ok = False
    try:
        conn = db()
        conn.close()
        db_ok = True
    except Exception:
        pass
    return {"ok": pc_ok and db_ok, "powercontext": pc_ok, "seekdb": db_ok}


def _entries(scope_id: str, limit: int = 5) -> list:
    """兜底：直接列最近记忆（检索索引未就绪时用）。"""
    try:
        with pc() as c:
            r = c.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        if r.status_code != 200:
            return []
        items = r.json().get("entries", []) or []
        texts = []
        for e in items:
            mem = e.get("memory") or e
            t = mem.get("text", "")
            if t:
                texts.append(t)
        return texts[-limit:]
    except Exception as e:
        logger.warning("[entries] 异常 %s", e)
        return []


@app.post("/mem/recall")
def mem_recall(req: RecallReq) -> dict:
    try:
        sid = resolve_scope(req.player)
        if not sid:
            return {"memories": [], "text": "", "count": 0, "source": "none"}

        texts = []
        source = "search"
        try:
            with pc() as c:
                r = c.post("/v1/memory/search", json={
                    "scope_id": sid, "query": req.query or "互动记录", "limit": 5})
            if r.status_code == 200:
                for h in (r.json().get("hits") or []):
                    t = ((h.get("memory") or {}).get("text") or "").strip()
                    if t:
                        texts.append(t)
        except Exception as e:
            logger.warning("[recall] 检索异常 %s", e)

        if not texts:  # 检索未命中或索引未就绪 → 用最近记忆兜底
            texts = _entries(sid)
            source = "recent"

        logger.info("[recall] player=%s scope=%s source=%s count=%d", req.player, sid, source, len(texts))
        return {"memories": texts, "text": "\n".join("- " + t for t in texts),
                "count": len(texts), "scope_id": sid, "source": source}
    except Exception as e:
        logger.warning("[recall] 异常 %s", e)
        return {"memories": [], "text": "", "count": 0, "source": "error"}


@app.post("/mem/remember")
def mem_remember(req: RememberReq) -> dict:
    try:
        sid = resolve_scope(req.player)
        if not sid:
            return {"ok": False, "reason": "scope_unavailable"}
        with pc() as c:
            r = c.post("/v1/memory/remember", json={
                "scope_id": sid, "kind": "event", "text": req.text, "reason": "tavern_turn"})
        ok = r.status_code == 200
        logger.info("[remember] player=%s ok=%s", req.player, ok)
        return {"ok": ok, "scope_id": sid}
    except Exception as e:
        logger.warning("[remember] 异常 %s", e)
        return {"ok": False, "reason": str(e)}


@app.post("/lore/search")
def lore_search(req: LoreReq) -> dict:
    try:
        conn = db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, content FROM tavern_lore "
                "WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) LIMIT %s",
                (req.query, req.limit),
            )
            rows = cur.fetchall()
        conn.close()
        items = [{"title": r[0], "content": r[1]} for r in rows]
        logger.info("[lore] query=%s hits=%d", req.query, len(items))
        return {"items": items, "count": len(items),
                "text": "\n".join("- %s：%s" % (i["title"], i["content"]) for i in items)}
    except Exception as e:
        logger.warning("[lore] 异常 %s", e)
        return {"items": [], "count": 0, "text": ""}
