"""Context selection: turn a task description into the materials a coder needs.

Selection is a SQL join over the keyword index (specs / code / tests) plus the
``Covers: S-XX`` tags on tests, then a small amount of Python for merging,
citation checking and classification of functional vs. regression tests.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import keywords, parser
from .config import Config
from .store import Store

PRIMARY_BOOST = 100
CLAUSE_REF_BOOST = 5


def _merge(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row[key]
        if identifier in merged:
            merged[identifier]["score"] = merged[identifier].get("score", 0) + row.get("score", 0)
        else:
            merged[identifier] = dict(row)
    return list(merged.values())


def _primary_clause_ids(clauses: list[dict[str, Any]]) -> list[str]:
    if not clauses:
        return []
    best = max(clause["score"] for clause in clauses)
    return [clause["clause_id"] for clause in clauses if clause["score"] == best]


def build_context(
    cfg: Config,
    store: Store,
    task: str,
    *,
    state: dict[str, Any] | None = None,
    limit_clauses: int = 8,
    limit_symbols: int = 10,
) -> dict[str, Any]:
    """Return the selected materials for ``task`` (also persisted as a context run)."""
    query = keywords.query_tokens(task)[:40]
    direct_ids = keywords.clause_ids(task)

    clause_rows = store.clauses_for_keywords(query, limit_clauses)
    for row in store.clauses_by_id(direct_ids):
        row["score"] = PRIMARY_BOOST
        clause_rows.append(row)
    clauses = sorted(_merge(clause_rows, "clause_id"), key=lambda row: (-row["score"], row["clause_id"]))

    primary = _primary_clause_ids(clauses)

    symbol_rows = [dict(row, reason="keyword match") for row in store.symbols_for_keywords(query, limit_symbols)]
    for row in store.symbols_for_clauses(primary):
        row["score"] = CLAUSE_REF_BOOST
        row["reason"] = "docstring references " + row["clause_refs"]
        symbol_rows.append(row)
    symbols = sorted(_merge(symbol_rows, "symbol_id"), key=lambda row: (-row["score"], row["path"], row["line_start"]))

    target_tests: list[dict[str, Any]] = []
    regression_tests: list[dict[str, Any]] = []
    for test in store.all_tests():
        covered = [item for item in test["covers"].split(",") if item]
        if primary and set(covered) & set(primary):
            test["reason"] = "covers " + ",".join(covered)
            target_tests.append(test)
        else:
            test["reason"] = ("covers " + test["covers"]) if test["covers"] else "no clause tag"
            regression_tests.append(test)

    warnings: list[str] = []
    if not clauses:
        warnings.append("no spec clause matched the task description")
    if not primary:
        warnings.append("no primary clause could be derived; target tests are empty")
    if not state:
        warnings.append("no PowerContext task state was loaded; completed/pending items are unavailable")

    for clause in clauses:
        try:
            path = Path(clause["path"])
            if not path.is_absolute():
                path = cfg.root / path
            fresh = parser.clause_digest_at(
                path, int(clause["line_start"]), int(clause["line_end"])
            )
            clause["stale"] = fresh != clause["digest"]
        except (OSError, IndexError):
            clause["stale"] = True
        if clause.get("stale"):
            warnings.append(f"{clause['clause_id']} changed on disk after the last ingest")

    run_id = "ctx-" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    context = {
        "schema": "sdd.context.v1",
        "run_id": run_id,
        "task": task,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "backend": store.backend,
        "workspace": str(cfg.workspace),
        "query_tokens": query,
        "primary_clauses": primary,
        "spec_clauses": clauses,
        "code_symbols": symbols,
        "tests": {"target": target_tests, "regression": regression_tests},
        "task_state": {
            "source": "powercontext" if state else "unavailable",
            "scope_id": (state or {}).get("scope_id"),
            "artifact_id": (state or {}).get("artifact_id"),
            "revision": (state or {}).get("revision"),
            "status": (state or {}).get("status"),
            "completed": (state or {}).get("completed", []),
            "pending": (state or {}).get("pending", []),
            "verification": (state or {}).get("verification", []),
        },
        "warnings": warnings,
    }
    store.record_context_run(
        run_id,
        task,
        [clause["clause_id"] for clause in clauses],
        [symbol["symbol_id"] for symbol in symbols],
        [test["test_id"] for test in target_tests],
        [test["test_id"] for test in regression_tests],
        context,
    )
    return context


def _cite(row: dict[str, Any]) -> str:
    return f"{row['path']}:{row['line_start']}-{row['line_end']}"


def render_markdown(context: dict[str, Any]) -> str:
    """Render the selection as the Markdown document handed to the coding session."""
    lines: list[str] = [
        "# SDD 开发上下文",
        "",
        f"- 任务：{context['task']}",
        f"- 上下文运行：`{context['run_id']}`（{context['generated_at']}）",
        f"- 材料索引后端：`{context['backend']}`",
        f"- 主条款：{', '.join(context['primary_clauses']) or '无'}",
        "",
        "## 适用规范条款",
        "",
    ]
    if not context["spec_clauses"]:
        lines.append("_没有匹配的条款。_")
    for clause in context["spec_clauses"]:
        flag = "  ⚠️ 与索引不一致" if clause.get("stale") else ""
        lines += [
            f"### {clause['clause_id']} {clause['title']}{flag}",
            "",
            f"来源：`{_cite(clause)}`　匹配分：{clause['score']}",
            "",
            clause["body"],
            "",
        ]

    lines += ["## 需要修改的代码", ""]
    if not context["code_symbols"]:
        lines.append("_没有匹配的代码符号。_")
    for symbol in context["code_symbols"]:
        lines += [
            f"- `{symbol['qualname']}`（{symbol['kind']}）— `{_cite(symbol)}`  "
            f"匹配分 {symbol['score']}，{symbol['reason']}",
            f"  - 签名：`{symbol['signature']}`",
        ]
        if symbol["docstring"]:
            lines.append(f"  - 说明：{symbol['docstring'].splitlines()[0]}")
    lines.append("")

    lines += ["## 功能测试（必须由本次变更转为通过）", ""]
    if not context["tests"]["target"]:
        lines.append("_没有找到覆盖主条款的测试。_")
    for test in context["tests"]["target"]:
        lines.append(f"- `{test['name']}` — `{_cite(test)}`（{test['reason']}）")
    lines.append("")

    lines += ["## 兼容性 / 回归测试（变更后必须保持通过）", ""]
    for test in context["tests"]["regression"]:
        lines.append(f"- `{test['name']}` — `{_cite(test)}`（{test['reason']}）")
    lines.append("")

    state = context["task_state"]
    lines += ["## 任务状态（PowerContext）", ""]
    if state["source"] != "powercontext":
        lines.append("_未从 PowerContext 读取到任务状态。_")
    else:
        lines += [
            f"- Scope：`{state['scope_id']}`；Artifact：`{state['artifact_id']}`；版本：{state['revision']}",
            f"- 状态：`{state['status']}`",
            "",
            "已完成：",
            "",
        ]
        lines += [f"- {item}" for item in state["completed"]] or ["- （无）"]
        lines += ["", "待完成：", ""]
        lines += [f"- {item}" for item in state["pending"]] or ["- （无）"]
        lines += ["", "已有验证结果：", ""]
        if state["verification"]:
            for record in state["verification"]:
                lines.append(
                    f"- {record.get('check', '')}：{record.get('result', '')}"
                    f"（`{record.get('command', '')}`，{record.get('at', '')}）"
                )
        else:
            lines.append("- （无）")
    lines.append("")

    if context["warnings"]:
        lines += ["## 警告", ""]
        lines += [f"- {warning}" for warning in context["warnings"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_prompt(context: dict[str, Any]) -> str:
    """Render a compact hand-off prompt for the coding assistant."""
    lines = [
        "你是本项目的实现工程师。请只依据下面的规范条款修改代码，不要改动测试断言。",
        "",
        f"任务：{context['task']}",
        "",
        "必须满足的条款：",
    ]
    for clause in context["spec_clauses"]:
        marker = "（主条款）" if clause["clause_id"] in context["primary_clauses"] else ""
        lines += ["", f"[{clause['clause_id']}] {clause['title']}{marker}", clause["body"]]
    lines += ["", "涉及代码："]
    for symbol in context["code_symbols"]:
        lines.append(f"- {symbol['path']}:{symbol['line_start']}-{symbol['line_end']} `{symbol['signature']}`")
    lines += ["", "新增/目标测试（当前失败）："]
    lines += [f"- {test['path']}::{test['name']}" for test in context["tests"]["target"]]
    lines += ["", "回归测试（必须保持通过）："]
    lines += [f"- {test['path']}::{test['name']}" for test in context["tests"]["regression"]]
    lines += ["", "输出要求：给出修改后的完整函数实现，并说明为什么满足条款。"]
    return "\n".join(lines) + "\n"


def write_selection(context: dict[str, Any], json_path: Path | None, markdown_path: Path | None,
                    prompt_path: Path | None = None) -> None:
    """Write the context to disk in the requested formats."""
    import json

    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(context, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(context), encoding="utf-8")
    if prompt_path is not None:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(render_prompt(context), encoding="utf-8")
