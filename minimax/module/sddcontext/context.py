"""上下文装配：把 seekdb 检索结果 + PowerContext 状态组装成 JSON / 文本 / LLM prompt。"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Optional

from .config import Config, db_summary
from .util import now_iso, ensure_dir, write_json

KIND_LABEL = {"spec": "规格", "code": "代码", "test": "测试"}


def task_id_for(task: str) -> str:
    digest = hashlib.sha1(task.strip().encode("utf-8")).hexdigest()[:12]
    return f"task-{digest}"


def _fence(content: str, lang: str = "") -> str:
    """选择足够长的围栏，避免规格文本里的 ``` 提前闭合代码块。"""
    longest = 0
    run = 0
    best = 0
    for char in content:
        if char == "`":
            run += 1
            best = max(best, run)
        else:
            run = 0
    size = max(3, best + 1)
    fence = "`" * size
    return f"{fence}{lang}\n{content}\n{fence}"


def _doc_block(doc: dict, lang_hint: str = "") -> str:
    header = f"- {doc['source']}"
    if doc.get("symbol"):
        header += f"  [{doc['symbol']}]"
    if doc.get("spec_version"):
        header += f"  (规格版本 {doc['spec_version']})"
    header += f"  (score={doc['score']:.4f}, hit={doc['hit']})"
    return f"{header}\n{_fence(doc['content'], lang_hint)}"


def build_context(cfg: Config, db, retrieval: dict, state: Optional[dict] = None,
                  db_meta: Optional[dict] = None) -> dict:
    task = retrieval["task"]
    context = {
        "task": task,
        "task_id": task_id_for(task),
        "generated_at": now_iso(),
        "project_root": str(cfg.project_root),
        "db": db_meta or db_summary(cfg),
        "query": {
            "match": retrieval.get("match_query"),
            "identifiers": retrieval.get("identifiers", []),
            "like_terms": retrieval.get("like_terms", []),
        },
        "specs": retrieval["specs"],
        "code": retrieval["code"],
        "tests": retrieval["tests"],
        "state": state or {"backend": "unset", "completed": [], "todo": [], "verification": []},
    }
    context["sources"] = sorted({
        d["source"] for key in ("specs", "code", "tests") for d in context[key]
    })
    context["summary"] = {
        "specs": len(context["specs"]),
        "code": len(context["code"]),
        "tests": len(context["tests"]),
        "notes": retrieval.get("notes", []),
    }

    # ---- 完整性自检：必要规格 / 代码 / 测试是否都命中 ----
    checks: List[dict] = []
    checks.append({
        "name": "specs_hit",
        "ok": bool(context["specs"]),
        "detail": f"{len(context['specs'])} 条规格命中",
    })
    has_apply = any(
        (d.get("symbol") == "apply_discount") or ("apply_discount" in (d["content"] or ""))
        for d in context["code"]
    ) or any("apply_discount" in t for t in retrieval.get("identifiers", []))
    checks.append({
        "name": "code_hit_apply_discount",
        "ok": has_apply,
        "detail": "代码切片中命中 apply_discount" if has_apply else "未命中 apply_discount",
    })
    checks.append({
        "name": "tests_hit",
        "ok": bool(context["tests"]),
        "detail": f"{len(context['tests'])} 条测试命中",
    })
    spec_has_min_total = any("min_total" in (d["content"] or "") for d in context["specs"])
    checks.append({
        "name": "spec_mentions_min_total",
        "ok": spec_has_min_total,
        "detail": "规格文本中出现 min_total" if spec_has_min_total
        else "规格文本未出现 min_total（v1 规格即为该状态）",
    })
    context["checks"] = checks
    return context


def render_text(context: dict) -> str:
    lines: List[str] = []
    lines.append(f"# SDD 任务上下文")
    lines.append("")
    lines.append(f"任务： {context['task']}")
    lines.append(f"工程： {context['project_root']}")
    lines.append(f"生成： {context['generated_at']}")
    db = context["db"]
    lines.append(f"检索库： seekdb {db.get('host')}:{db.get('port')}/{db.get('database')}"
                 f" 表 {db.get('table')}（本次命中 {context['summary']['specs']} 规格 / "
                 f"{context['summary']['code']} 代码 / {context['summary']['tests']} 测试）")
    lines.append("")
    for key, label, lang in (("specs", "规格", "markdown"), ("code", "代码", "python"),
                             ("tests", "测试", "python")):
        lines.append(f"## {label}（{len(context[key])}）")
        if not context[key]:
            lines.append("（无命中）")
        for doc in context[key]:
            lines.append(_doc_block(doc, lang))
        lines.append("")
    state = context.get("state") or {}
    lines.append(f"## 任务状态（来自 {state.get('backend')}）")
    lines.append(f"- 已完成: {state.get('completed') or '（无）'}")
    lines.append(f"- 待办: {state.get('todo') or '（无）'}")
    lines.append(f"- 验证结果: {state.get('verification') or '（无）'}")
    lines.append("")
    lines.append("## 来源清单")
    for source in context["sources"]:
        lines.append(f"- {source}")
    lines.append("")
    lines.append("## 自检")
    for check in context.get("checks", []):
        lines.append(f"- {'PASS' if check['ok'] else 'FAIL'} {check['name']}: {check['detail']}")
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "你是一名严谨的 Python 工程师。你只能输出一个 unified diff（patch），"
    "不要输出任何解释、总结或 markdown 之外的文字。"
    "diff 使用 `--- a/<相对路径>` / `+++ b/<相对路径>` 头部，路径相对工程根目录。"
)


def render_prompt(context: dict) -> List[dict]:
    parts: List[str] = []
    parts.append("## 任务\n" + context["task"])
    parts.append("")
    parts.append("## 必须满足的规格（只改代码，不改规格）")
    for doc in context["specs"]:
        parts.append(_doc_block(doc, "markdown"))
    parts.append("")
    parts.append("## 现有代码")
    for doc in context["code"]:
        parts.append(_doc_block(doc, "python"))
    parts.append("")
    parts.append("## 验证用测试")
    for doc in context["tests"]:
        parts.append(_doc_block(doc, "python"))
    state = context.get("state") or {}
    if state.get("completed") or state.get("todo"):
        parts.append("")
        parts.append("## 已有任务进展（来自 PowerContext）")
        parts.append(f"- 已完成: {state.get('completed')}")
        parts.append(f"- 待办: {state.get('todo')}")
        parts.append(f"- 上次验证: {state.get('verification')}")
    parts.append("")
    parts.append("## 改动要求")
    parts.append("1. 严格按上面的规格实现，不要新增未要求的参数或行为。")
    parts.append("2. 保持现有公共函数签名与返回值兼容（见兼容性规格）。")
    parts.append("3. 只修改必要文件，不要改动测试文件与规格文件。")
    parts.append("4. 只输出 unified diff。")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]


def save_context(cfg: Config, context: dict, name: str = "context") -> dict:
    out_dir = ensure_dir(cfg.state_dir / "context")
    json_path = out_dir / f"{name}.json"
    txt_path = out_dir / f"{name}.txt"
    write_json(json_path, context)
    txt_path.write_text(render_text(context), encoding="utf-8", newline="\n")
    return {"json": str(json_path), "text": str(txt_path)}


def load_last_context(cfg: Config, name: str = "context") -> Optional[dict]:
    path = cfg.state_dir / "context" / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
