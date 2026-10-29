"""一键演示：规格变更 -> 检索 -> MiniMax/回放改代码 -> 测试前后对比 -> 状态复用。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .context import render_text, save_context, task_id_for
from .db import SeekDB
from .pipeline import build_context_for, connect_db, do_index, restore_baseline
from .state import StateError, open_store
from .util import ensure_dir, now_iso, now_stamp, print_err, write_json

PACKAGE_PARENT = Path(__file__).resolve().parent.parent  # minimax/module


def _read_task(cfg: Config) -> str:
    task_file = cfg.home / "sample" / "task.txt"
    if task_file.exists():
        text = task_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "为 apply_discount 增加 min_total 金额门槛筛选：订单总额低于门槛时不应用折扣。"


def _state_cli(cfg: Config, *args: str) -> dict:
    """用独立子进程调用 state 子命令，证明状态跨进程（模块重启）可读。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(PACKAGE_PARENT), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-m", "sddcontext", "state", *args]
    completed = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env)
    output = (completed.stdout or "") + (completed.stderr or "")
    parsed = None
    try:
        parsed = json.loads(completed.stdout)
    except ValueError:
        parsed = None
    return {"command": " ".join(cmd), "exit_code": completed.returncode,
            "output": output, "json": parsed}


def _spec_signature(context: dict) -> str:
    return " || ".join(f"{d['path']}:{d['line_start']}#{d.get('symbol')}" for d in context["specs"])


def _spec_body(context: dict) -> str:
    return "\n".join(d["content"] for d in context["specs"])


def run_demo(cfg: Config, source: str = "auto", spec_version: str = "v2") -> int:
    task = _read_task(cfg)
    stamp = now_stamp()
    steps: List[dict] = []
    failures: List[str] = []

    def step(name: str, status: str, detail: str, data: Optional[dict] = None) -> None:
        steps.append({"name": name, "status": status, "detail": detail, "data": data or {},
                      "ts": now_iso()})
        print(f"[demo] {status.upper():8s} {name}: {detail}")

    print(f"[demo] 任务：{task}")
    print(f"[demo] 工程：{cfg.project_root}")

    # ---------------------------------------------------------------- 0 基线
    copied = restore_baseline(cfg, "v2")
    step("reset-baseline", "ok", "已恢复修改前快照并写入规格 v2", {"files": copied})

    # ---------------------------------------------------------- 1 建库 + 索引
    db: SeekDB = connect_db(cfg, ensure=True, recreate=True)
    try:
        index_v2 = do_index(cfg, db, note="demo:init")
        step("init-db+index(v2)", "ok",
             f"切片 {index_v2['chunks']} 入库，共 {index_v2['db_total']} 条",
             {"index": index_v2, "db": {"port": cfg.db_port, "database": cfg.db_name}})

        # ------------------------------------------------------ 2 修改前验证
        from .verify import verify as run_verify
        before = run_verify(cfg, "before")
        target_before = before["suites"]["target"]
        regression_before = before["suites"]["regression"]
        before_expected = (not target_before["ok"]) and regression_before["ok"]
        step("verify-before", "ok" if before_expected else "fail",
             f"目标测试 exit={target_before['exit_code']}（应为非 0），"
             f"回归测试 exit={regression_before['exit_code']}（应为 0）",
             {"result": before})
        if not before_expected:
            failures.append("修改前验证不符合预期：目标测试应失败、回归测试应通过")

        # ------------------------------------------------- 3 规格 v2 上下文
        ctx_v2 = build_context_for(cfg, db, task)
        save_context(cfg, ctx_v2, name="context-v2")
        spec_v2_body = _spec_body(ctx_v2)
        step("context-spec-v2", "ok",
             f"specs={len(ctx_v2['specs'])} code={len(ctx_v2['code'])} tests={len(ctx_v2['tests'])}；"
             f"来源：{[d['source'] for d in ctx_v2['specs']]}",
             {"sources": ctx_v2["sources"], "checks": ctx_v2["checks"]})

        # --------------------------------- 4 规格更新 v1 -> 重新 index -> 结果变化
        restore_baseline(cfg, "v1")
        index_v1 = do_index(cfg, db, note="demo:spec-v1")
        ctx_v1 = build_context_for(cfg, db, task)
        save_context(cfg, ctx_v1, name="context-v1")
        spec_v1_body = _spec_body(ctx_v1)
        differs = spec_v1_body != spec_v2_body
        mentions_v2_only = "v2 新增" in spec_v2_body and "v2 新增" not in spec_v1_body
        step("spec-update-v1", "ok" if (differs and spec_v1_body) else "fail",
             f"切到 v1 并重新 index 后检索结果发生变化={differs}，"
             f"v1 命中 {[d['source'] for d in ctx_v1['specs']]}",
             {"differs": differs, "v1_spec_markers": mentions_v2_only,
              "v1_body_head": spec_v1_body[:200], "index": index_v1,
              "db_counts": index_v1["db_counts_by_kind"]})
        if not differs:
            failures.append("规格更新后检索结果没有变化（可能是写死内容或未重新查库）")

        # ------------------------------------- 5 切回 v2 并重建上下文
        restore_baseline(cfg, "v2")
        index_v2b = do_index(cfg, db, note="demo:spec-v2-restore")
        ctx_final = build_context_for(cfg, db, task)
        save_context(cfg, ctx_final, name="context")
        spec_final_body = _spec_body(ctx_final)
        back_to_v2 = spec_final_body == spec_v2_body
        step("spec-restore-v2", "ok" if back_to_v2 else "fail",
             f"切回 v2 后上下文恢复 v2 内容={back_to_v2}，来源 "
             f"{[d['source'] for d in ctx_final['specs']]}",
             {"index": index_v2b})

        # ------------------------------------------- 6 状态（修改前）先落库
        store = open_store(cfg, quiet=True)
        task_id = task_id_for(task)
        try:
            store.save({
                "task_id": task_id,
                "task": task,
                "completed": ["已建立 seekdb 索引（specs/code/tests 切片入库）"],
                "todo": ["按 SPEC-02 实现 min_total 门槛筛选", "跑修改后目标测试与回归测试"],
                "verification": [{
                    "phase": "before",
                    "target_exit": target_before["exit_code"],
                    "regression_exit": regression_before["exit_code"],
                    "summary_file": before["summary_file"],
                }],
                "context_files": [str(cfg.state_dir / "context" / "context.json")],
                "updated_at": now_iso(),
            })
            step("state-save", "ok", f"任务状态已写入 {store.backend_name}", {"backend": store.backend_name})
        except StateError as exc:
            step("state-save", "fail", f"状态写入失败：{exc}")
            failures.append(f"状态写入失败：{exc}")

        # ------------------------------------------------------ 7 应用 patch
        from .apply import run_apply
        apply_result = run_apply(cfg, ctx_final, source=source, project_root=cfg.project_root)
        step("apply", "ok" if apply_result["applied_files"] else "fail",
             f"模式={apply_result['mode']}，写入 {apply_result['applied_files']}，"
             f"patch={apply_result['patch_file']}",
             {"apply": apply_result})

        # ------------------------------------------------------ 8 修改后验证
        after = run_verify(cfg, "after")
        after_ok = after["ok"]
        step("verify-after", "ok" if after_ok else "fail",
             "；".join(f"{k} exit={v['exit_code']}" for k, v in after["suites"].items()),
             {"result": after})
        if not after_ok:
            failures.append("修改后测试未全部通过")

        # --------------------------------- 9 更新状态 + 独立进程读回（重启验证）
        try:
            store.save({
                "task_id": task_id,
                "task": task,
                "completed": [
                    "已建立 seekdb 索引（specs/code/tests 切片入库）",
                    "已按 SPEC-02 实现 min_total 门槛筛选",
                    "修改后目标测试与回归测试全部通过",
                ],
                "todo": ["补充 min_total 的产品文档示例"],
                "verification": [
                    {"phase": "before", "target_exit": target_before["exit_code"],
                     "regression_exit": regression_before["exit_code"],
                     "summary_file": before["summary_file"]},
                    {"phase": "after",
                     "suites": {k: {"exit_code": v["exit_code"], "result": v["result"],
                                    "ran": v["ran"]} for k, v in after["suites"].items()},
                     "summary_file": after["summary_file"]},
                ],
                "context_files": [str(cfg.state_dir / "context" / "context.json"),
                                  apply_result["context_file"]],
                "updated_at": now_iso(),
            })
        except StateError as exc:
            print_err(f"[demo] 状态更新失败：{exc}")

        reload_result = _state_cli(cfg, "load", "--task-id", task_id, "--quiet")
        reloaded = (reload_result.get("json") or {}).get("state") or {}
        reload_ok = bool(reloaded.get("completed"))
        step("state-reload-after-restart", "ok" if reload_ok else "fail",
             f"独立进程 `state load` 读回已完成 {len(reloaded.get('completed') or [])} 项、"
             f"验证记录 {len(reloaded.get('verification') or [])} 条",
             {"command": reload_result["command"], "exit_code": reload_result["exit_code"],
              "state": reloaded, "raw_output": reload_result["output"][:4000]})
        if not reload_ok:
            failures.append("模块重启（独立进程）后未能读回任务状态")

        # ------------------------------------- 10 带状态的上下文再生成一次
        ctx_with_state = build_context_for(cfg, db, task)
        step("context-with-state", "ok",
             f"上下文中 state.backend={ctx_with_state['state'].get('backend')}，"
             f"已完成 {len(ctx_with_state['state'].get('completed') or [])} 项",
             {"state": ctx_with_state["state"]})
    finally:
        db.close()

    # ---------------------------------------------------------------- 报告
    report = {
        "ts": now_iso(),
        "mode": "run-demo",
        "task": task,
        "patch_source": source,
        "project_root": str(cfg.project_root),
        "db": {"host": cfg.db_host, "port": cfg.db_port, "database": cfg.db_name},
        "steps": steps,
        "failures": failures,
        "passed": not failures,
    }
    out_dir = ensure_dir(cfg.records_dir)
    report_path = out_dir / f"demo-run-{stamp}.md"
    report_path.write_text(_render_report(report), encoding="utf-8", newline="\n")
    write_json(out_dir / "demo-run-latest.json", report)
    print(f"\n[demo] 报告：{report_path}")
    if failures:
        print("[demo] 失败项：")
        for item in failures:
            print(f"  - {item}")
        return 3
    print("[demo] 全部步骤通过")
    return 0


def _render_report(report: dict) -> str:
    lines = [f"# 演示运行报告 {report['ts']}", "",
             f"- 任务：{report['task']}",
             f"- patch 来源：{report['patch_source']}",
             f"- seekdb：{report['db']}",
             f"- 结论：{'PASS' if report['passed'] else 'FAIL'}",
             ""]
    for idx, step in enumerate(report["steps"], 1):
        lines.append(f"## {idx}. {step['name']}  [{step['status']}]")
        lines.append("")
        lines.append(step["detail"])
        payload = json.dumps(step.get("data") or {}, ensure_ascii=False, indent=2)
        lines.append("")
        lines.append("```json")
        lines.append(payload[:4000])
        lines.append("```")
        lines.append("")
    if report["failures"]:
        lines.append("## 失败项")
        for failure in report["failures"]:
            lines.append(f"- {failure}")
    return "\n".join(lines) + "\n"
