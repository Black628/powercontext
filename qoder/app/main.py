"""Recall（知返）FastAPI 服务：REQUIREMENTS 第 4 节的 6 个接口。

  POST /api/docs    上传文档，返回 doc_id，后台切块+建索引
  GET  /api/docs    文档列表与索引状态
  POST /api/search  {query, k} → 片段列表（含出处、两路得分）
  POST /api/ask     {query} → 回答 + 引用 + 命中片段
  GET  /api/review  今日复习清单（来自 PowerContext 记忆）
  GET  /api/stats   导入量、检索次数、估算节省时间
"""

import datetime
import hashlib
import logging
import os
import re

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import embed, memory, search, store

load_dotenv()

# 日志格式：保证两路 SQL 与耗时、记忆读写都可在控制台/文件核对
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logging.getLogger("pymysql").setLevel(logging.WARNING)

# 问答兜底用的 LLM 配置（占位值，真实值放 .env，不要提交密钥）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")       # 如 https://<你的网关>/v1/chat/completions
LLM_API_KEY = os.getenv("LLM_API_KEY", "")         # 占位
LLM_MODEL = os.getenv("LLM_MODEL", "")             # 占位
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30"))

# 切块参数：约 500 字/块（REQUIREMENTS 2.1）
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
# 提效估算：手动翻找中位数默认 0 = 未实测；显式配置 > 0 才估算
MANUAL_SECONDS = int(os.getenv("MANUAL_LOOKUP_SECONDS", "0"))
RECALL_SECONDS = int(os.getenv("RECALL_LOOKUP_SECONDS", "10"))

_CN_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")


def _chunk_text(text: str) -> list:
    """把纯文本切成约 CHUNK_SIZE 字的块，返回 [(offset, content), ...]。"""
    text = text.replace("\r\n", "\n")
    sentences = [s for s in _CN_SENT_SPLIT.split(text) if s]
    chunks, buf, buf_start, pos = [], "", 0, 0
    for sent in sentences:
        if not buf:
            buf_start = pos
        buf += sent
        if len(buf) >= CHUNK_SIZE:
            chunks.append((buf_start, buf.strip()))
            buf = ""
        pos += len(sent)
    if buf.strip():
        chunks.append((buf_start, buf.strip()))
    return chunks


def _extract_file(name: str, raw: bytes) -> list:
    """解析上传文件 → [(page, offset, content), ...]；PDF 取文字层并记录页码。"""
    lower = name.lower()
    if lower.endswith(".pdf"):
        try:
            import io

            from pypdf import PdfReader
        except ImportError:
            raise HTTPException(415, "服务器未安装 pypdf，无法解析 PDF")
        out = []
        reader = PdfReader(io.BytesIO(raw))
        for pno, page in enumerate(reader.pages, start=1):
            ptext = page.extract_text() or ""
            for off, c in _chunk_text(ptext):
                out.append((pno, off, c))
        return out
    # 其余按 UTF-8 文本处理（.md / .txt）
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    return [(0, off, c) for off, c in _chunk_text(text)]


def _build_index(doc_id: str, doc_name: str, pieces: list) -> None:
    """后台任务：切块 → embedding → 写 seekdb，并回写文档状态。"""
    try:
        contents = [c for _, _, c in pieces]
        vectors = embed.embed_texts(contents)
        rows = [
            {
                "doc_id": doc_id,
                "doc_name": doc_name,
                "page": page,
                "offset_in_doc": off,
                "content": c,
                "embedding": vec,
            }
            for (page, off, c), vec in zip(pieces, vectors)
        ]
        n = store.insert_chunks(rows)
        store.finish_doc(doc_id, n, "ready")
    except Exception as e:
        logging.getLogger("recall.main").exception("后台索引失败: %s", e)
        store.finish_doc(doc_id, 0, "failed", str(e)[:500])


# ------------------------------------------------------------
# 请求模型
# ------------------------------------------------------------

class SearchReq(BaseModel):
    query: str
    k: int = Field(5, ge=1, le=50)


class AskReq(BaseModel):
    query: str
    k: int = Field(5, ge=1, le=20)
    uid: str = "guest"           # 记忆按用户隔离
    topic: str = ""              # 不传则用 query 前 20 字作为主题
    understood: bool | None = None  # 用户对回答的反馈：懂/没懂/未反馈


app = FastAPI(title="Recall（知返）", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    """服务启动即保证库表存在（幂等）。"""
    try:
        store.init_schema()
    except Exception as e:
        # 数据库暂时不可用不阻断启动，检索接口会返回明确错误
        logging.getLogger("recall.main").warning("init_schema 失败（稍后可重试）：%s", e)


# ------------------------------------------------------------
# 1. POST /api/docs —— 上传文档
# ------------------------------------------------------------

@app.post("/api/docs")
async def upload_doc(background: BackgroundTasks, file: UploadFile = File(...)):
    name = file.filename or "untitled.txt"
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    pieces = _extract_file(name, raw)
    if not pieces:
        raise HTTPException(422, "未能从文件中切出任何文本块")
    doc_id = hashlib.sha1(f"{name}:{len(raw)}".encode("utf-8")).hexdigest()[:16]
    store.register_doc(doc_id, name, status="indexing")
    background.add_task(_build_index, doc_id, name, pieces)
    return {"doc_id": doc_id, "doc_name": name, "chunks_pending": len(pieces), "status": "indexing"}


# ------------------------------------------------------------
# 2. GET /api/docs —— 文档列表与索引状态
# ------------------------------------------------------------

@app.get("/api/docs")
def docs_list():
    return {"docs": store.list_docs()}


# ------------------------------------------------------------
# 3. POST /api/search —— 混合检索（含出处与两路得分）
# ------------------------------------------------------------

@app.post("/api/search")
def do_search(req: SearchReq):
    try:
        out = search.hybrid_search(req.query, req.k)
    except Exception as e:
        raise HTTPException(503, f"seekdb 检索失败：{e}")
    store.bump_counter("search")
    return {
        "query": req.query,
        "results": out["results"],
        "vector_hits": out["vector_hits"],
        "fulltext_hits": out["fulltext_hits"],
    }


# ------------------------------------------------------------
# 4. POST /api/ask —— 检索增强问答（回答必须带 [n] 引用）
# ------------------------------------------------------------

def _llm_answer(query: str, contexts: list) -> str:
    """优先走可配置的 LLM；未配置或失败则退化为抽取式回答（仍带引用）。"""
    refs = "\n\n".join(
        f"[{i}] {c['doc_name']} 第{c['page']}页 偏移{c['offset']}：{c['snippet']}"
        for i, c in enumerate(contexts, start=1)
    )
    if LLM_BASE_URL and LLM_MODEL:
        try:
            headers = {"Content-Type": "application/json"}
            if LLM_API_KEY:
                headers["Authorization"] = f"Bearer {LLM_API_KEY}"
            resp = requests.post(
                LLM_BASE_URL,
                headers=headers,
                json={
                    "model": LLM_MODEL,
                    "messages": [
                        {"role": "system",
                         "content": "你是复习教练。只根据给定片段回答，答案中必须用 [编号] 标注引用来源。"},
                        {"role": "user", "content": f"问题：{query}\n\n片段：\n{refs}"},
                    ],
                },
                timeout=LLM_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logging.getLogger("recall.main").warning("LLM 调用失败，退化为抽取式回答：%s", e)
    # 抽取式兜底：直接给出片段摘要，保留 [n] 引用可跳原文
    if not contexts:
        return "资料库中未找到相关内容，请先上传文档。"
    lines = [f"根据资料库整理（详见引用出处）："]
    for i, c in enumerate(contexts, start=1):
        lines.append(f"[{i}] {c['snippet'][:120]}")
    return "\n".join(lines)


@app.post("/api/ask")
def do_ask(req: AskReq):
    try:
        out = search.hybrid_search(req.query, req.k)
    except Exception as e:
        raise HTTPException(503, f"seekdb 检索失败：{e}")
    contexts = out["results"]
    answer = _llm_answer(req.query, contexts)
    citations = [
        {
            "n": i,
            "chunk_id": c["id"],
            "doc_id": c["doc_id"],
            "doc_name": c["doc_name"],
            "page": c["page"],
            "offset": c["offset"],
        }
        for i, c in enumerate(contexts, start=1)
    ]
    store.bump_counter("ask")
    # 记忆写入：失败自动降级，不影响响应
    topic = req.topic or req.query[:20]
    memory.record_qa(req.uid, req.query, topic, req.understood, contexts)
    return {
        "query": req.query,
        "answer": answer,
        "citations": citations,
        "results": contexts,  # 命中片段（含出处与两路得分）
    }


# ------------------------------------------------------------
# 5. GET /api/review —— 今日复习清单（来自 PowerContext 记忆）
# ------------------------------------------------------------

@app.get("/api/review")
def review_list(uid: str = "guest"):
    weak = memory.weak_points_summary(uid)
    history = memory.get_memory(uid, "qa_history")
    # 今天问过但没标记"懂"的主题，也算今日待复习
    today = datetime.date.today().isoformat()
    pending = {
        h.get("topic") or h.get("query", "")[:20]
        for h in history
        if h.get("time", "").startswith(today) and h.get("understood") is not True
    }
    items = [{"topic": w["topic"], "count": w["count"], "last_time": w["last_time"],
              "source": "weak_points"} for w in weak]
    seen = {w["topic"] for w in weak}
    for t in pending:
        if t and t not in seen:
            items.append({"topic": t, "count": 1, "last_time": today, "source": "qa_history"})
    return {"uid": uid, "date": today, "items": items}


# ------------------------------------------------------------
# 6. GET /api/stats —— 导入量、检索次数、估算节省时间
# ------------------------------------------------------------

@app.get("/api/stats")
def do_stats():
    s = store.stats()
    lookups = s["search_count"] + s["ask_count"]
    baseline_measured = MANUAL_SECONDS > 0
    saved_minutes = (
        round(lookups * (MANUAL_SECONDS - RECALL_SECONDS) / 60.0, 1)
        if baseline_measured
        else None
    )
    return {
        "doc_count": s["doc_count"],
        "chunk_count": s["chunk_count"],
        "search_count": s["search_count"],
        "ask_count": s["ask_count"],
        "estimated_saved_minutes": saved_minutes,
        "baseline_measured": baseline_measured,
        "assumption": (
            f"每次查资料 {MANUAL_SECONDS}s → {RECALL_SECONDS}s"
            if baseline_measured
            else "手动基线未实测（MANUAL_LOOKUP_SECONDS=0），不估算节省时间"
        ),
    }
