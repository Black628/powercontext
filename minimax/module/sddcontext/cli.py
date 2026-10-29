"""CLI 入口：python -m sddcontext <command>"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__, pipeline
from .apply import ApplyError, run_apply
from .config import Config, db_summary, load_config
from .context import (build_context, load_last_context, render_prompt, render_text,
                      save_context, task_id_for)
from .db import SeekDB, SeekDBError
from .minimax_client import MiniMaxClient
from .patcher import PatchError
from .state import StateError, open_store
from .util import ensure_dir, now_iso, print_err, write_json
from .verify import verify as run_verify

DEFAULT_TASK_FILE = "sample/task.txt"


# --------------------------------------------------------------------- 工具
def _task_from_args(args, cfg: Config) -> str:
    if getattr(args, "task", None):
        return args.task.strip()
    candidate = cfg.home / DEFAULT_TASK_FILE
    if candidate.exists():
        text = candidate.read_text(encoding="utf-8").strip()
        if text:
            return text
    raise SystemExit("必须提供 --task，或在 sample/task.txt 写入示例任务描述")


def _connect_db(cfg: Config, create_db: bool = True, ensure: bool = False,
                recreate: bool = False) -> SeekDB:
    return pipeline.connect_db(cfg, ensure=ensure, recreate=recreate, create_db=create_db)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _parse_json_arg(value: Optional[str]):
    """--verification 既接受 JSON 字符串，也接受 JSON 文件路径。"""
    if not value:
        return []
    text = value
    candidate = Path(value)
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8-sig")
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SystemExit(f"--verification 需要合法 JSON 或 JSON 文件路径：{exc}") from exc


# --------------------------------------------------------------------- 命令
def cmd_doctor(args, cfg: Config) -> int:
    report: Dict[str, object] = {"version": __version__, "home": str(cfg.home),
                                 "project_root": str(cfg.project_root)}
    try:
        port = cfg.resolve_port()
        report["seekdb"] = {"ok": True, "host": cfg.db_host, "port": port,
                            "note": "端口由实际探测得到，可用 SEEKDB_PORT 覆盖"}
        db = SeekDB(cfg, echo=False)
        db.connect()
        db.execute(f"USE `{cfg.db_name}`")
        report["seekdb"]["database"] = cfg.db_name
        try:
            report["seekdb"]["docs"] = db.total_docs()
            report["seekdb"]["counts_by_kind"] = db.count_by_kind()
        except Exception:  # 表还没建（还没跑 init-db）
            report["seekdb"]["docs"] = None
            report["seekdb"]["note"] = "库可连接，但 sdd_docs 尚未创建，请先执行 init-db"
        db.close()
    except Exception as exc:  # noqa: BLE001
        report["seekdb"] = {"ok": False, "error": str(exc)}
    store = open_store(cfg, quiet=True)
    report["powercontext"] = {"backend": store.backend_name, "ok": store.healthy(),
                              "detail": store.health_detail(), "base_url": cfg.pc_base_url}
    report["minimax"] = {"api_key_present": bool(cfg.minimax_api_key),
                         "base_url": cfg.minimax_base_url, "model": cfg.minimax_model,
                         "mode": "live" if cfg.minimax_api_key else "replay(离线回放)"}
    report["records_dir"] = str(cfg.records_dir)
    _print(report)
    return 0 if report.get("seekdb", {}).get("ok") else 1


def cmd_init_db(args, cfg: Config) -> int:
    db = _connect_db(cfg, ensure=True, recreate=args.recreate)
    try:
        result = _do_index(cfg, db, note="init-db")
        _print(result)
    finally:
        db.close()
    return 0


def cmd_index(args, cfg: Config) -> int:
    db = _connect_db(cfg)
    try:
        result = _do_index(cfg, db, note="index")
        _print(result)
    finally:
        db.close()
    return 0


def _do_index(cfg: Config, db: SeekDB, note: str = "index") -> dict:
    return pipeline.do_index(cfg, db, note=note)


def cmd_context(args, cfg: Config) -> int:
    task = _task_from_args(args, cfg)
    db = _connect_db(cfg)
    try:
        ctx = _build(cfg, db, task, state_required=not args.no_state)
        paths = save_context(cfg, ctx, name=args.name)
        ctx["_files"] = paths
        if args.format in ("json", "both"):
            print(json.dumps(ctx, ensure_ascii=False, indent=2))
        if args.format in ("text", "both"):
            print(render_text(ctx))
        print(f"\n[context] JSON -> {paths['json']}\n[context] TEXT -> {paths['text']}", file=sys.stderr)
    finally:
        db.close()
    return 0


def _build(cfg: Config, db: SeekDB, task: str, state_required: bool = True) -> dict:
    return pipeline.build_context_for(cfg, db, task)


def cmd_state(args, cfg: Config) -> int:
    store = open_store(cfg, quiet=args.quiet)
    if args.action == "save":
        task = _task_from_args(args, cfg)
        task_id = args.task_id or task_id_for(task)
        payload = {
            "task_id": task_id,
            "task": task,
            "completed": args.completed or [],
            "todo": args.todo or [],
            "verification": _parse_json_arg(args.verification),
            "context_files": args.context or [],
            "updated_at": now_iso(),
        }
        record = store.save(payload)
        _print({"backend": store.backend_name, "saved": record})
        return 0
    if args.action == "list":
        _print({"backend": store.backend_name, "items": store.list()})
        return 0
    if args.action == "load":
        if args.task_id:
            task_id = args.task_id
        elif args.task:
            task_id = task_id_for(args.task)
        else:
            items = store.list()
            if not items:
                _print({"backend": store.backend_name, "state": None, "note": "状态库为空"})
                return 0
            task_id = items[-1]["task_id"]
        state = store.load(task_id)
        _print({"backend": store.backend_name, "task_id": task_id, "state": state})
        return 0
    raise SystemExit(f"未知 state 动作：{args.action}")


def cmd_apply(args, cfg: Config) -> int:
    task = _task_from_args(args, cfg)
    db = _connect_db(cfg)
    try:
        ctx = _build(cfg, db, task)
    finally:
        db.close()
    save_context(cfg, ctx, name="apply-context")
    result = run_apply(cfg, ctx, source=args.patch_source, dry_run=args.dry_run,
                       project_root=cfg.project_root)
    _print(result)
    return 0


def cmd_verify(args, cfg: Config) -> int:
    phase = args.phase
    if phase == "auto":
        phase = "before" if args.auto_guess == "before" else "after"
    report = run_verify(cfg, phase)
    store = open_store(cfg, quiet=True)
    try:
        task = _task_from_args(args, cfg)
        task_id = task_id_for(task)
        previous = store.load(task_id) or {}
        verification = list(previous.get("verification") or [])
        verification.append({
            "phase": phase,
            "ts": report["ts"],
            "suites": {k: {"exit_code": v["exit_code"], "result": v["result"], "ran": v["ran"],
                           "ok": v["ok"]} for k, v in report["suites"].items()},
            "summary_file": report["summary_file"],
        })
        store.save({
            "task_id": task_id,
            "task": task,
            "completed": previous.get("completed") or [],
            "todo": previous.get("todo") or [],
            "verification": verification,
            "context_files": previous.get("context_files") or [],
            "updated_at": now_iso(),
        })
    except StateError as exc:
        print_err(f"[state] 保存验证结果失败：{exc}")
    print(json.dumps({k: v for k, v in report.items() if k != "suites"}, ensure_ascii=False, indent=2))
    for name, suite in report["suites"].items():
        print(f"[verify:{phase}] {name}: exit={suite['exit_code']} {suite['result']}")
    return 0 if report["ok"] else 1


def cmd_reset(args, cfg: Config) -> int:
    """把 demo_project 恢复到“修改前”基线，并按需切换规格版本。"""
    copied = pipeline.restore_baseline(cfg, args.spec or "v2")
    print(json.dumps({"restored": copied, "spec_version": args.spec or "v2",
                      "project_root": str(cfg.project_root)}, ensure_ascii=False, indent=2))
    return 0


def cmd_run_demo(args, cfg: Config) -> int:
    from .demo import run_demo
    return run_demo(cfg, source=args.patch_source, spec_version=args.spec or "v2")


def cmd_show(args, cfg: Config) -> int:
    ctx = load_last_context(cfg, name=args.name)
    if ctx is None:
        raise SystemExit("还没有生成过 context，请先执行 context 命令")
    if args.format == "json":
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        print(render_text(ctx))
    return 0


def cmd_prompt(args, cfg: Config) -> int:
    ctx = load_last_context(cfg, name=args.name)
    if ctx is None:
        raise SystemExit("还没有生成过 context，请先执行 context 命令")
    messages = render_prompt(ctx)
    out_dir = ensure_dir(cfg.state_dir / "prompt")
    path = out_dir / "minimax-prompt.md"
    path.write_text("\n\n".join(f"### role={m['role']}\n{m['content']}" for m in messages),
                    encoding="utf-8", newline="\n")
    print(json.dumps({"prompt_file": str(path), "chars": sum(len(m["content"]) for m in messages),
                      "messages": [m["role"] for m in messages]}, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------- 解析
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sddcontext",
        description="SDD 上下文管理模块：seekdb 检索 + PowerContext 状态 + MiniMax 代码修改",
    )
    parser.add_argument("--project", help="工程根目录（默认 <minimax>/demo_project）")
    parser.add_argument("--records", help="records 目录（默认 <minimax>/records）")
    parser.add_argument("--version", action="version", version=f"sddcontext {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="环境自检：seekdb / PowerContext / MiniMax")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("init-db", help="建库表 + FULLTEXT 索引 + 首次全量入库")
    p.add_argument("--recreate", action="store_true", help="先 DROP 再重建 sdd_docs")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("index", help="增量扫描工程目录并入库")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("context", help="按任务描述检索并生成带来源的上下文")
    p.add_argument("--task", help="任务描述；缺省读 sample/task.txt")
    p.add_argument("--format", choices=["json", "text", "both"], default="both")
    p.add_argument("--name", default="context", help="上下文输出文件名（默认 context）")
    p.add_argument("--no-state", action="store_true", help="不读取 PowerContext 状态")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("state", help="任务状态保存/列举/读取（PowerContext）")
    p.add_argument("action", choices=["save", "list", "load"])
    p.add_argument("--task")
    p.add_argument("--task-id")
    p.add_argument("--completed", nargs="*", help="已完成事项")
    p.add_argument("--todo", nargs="*", help="待办事项")
    p.add_argument("--verification", help="验证结果 JSON 字符串")
    p.add_argument("--context", nargs="*", help="上下文文件路径")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("apply", help="把上下文交给 MiniMax 产出 patch 并应用")
    p.add_argument("--task")
    p.add_argument("--patch-source", choices=["auto", "minimax", "replay"], default="auto")
    p.add_argument("--dry-run", action="store_true", help="只生成 patch，不写入工程")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="运行 demo 工程测试并留证")
    p.add_argument("--phase", choices=["before", "after", "auto"], default="after")
    p.add_argument("--auto-guess", choices=["before", "after"], default="after")
    p.add_argument("--task")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("reset", help="恢复 demo_project 修改前基线（可切换规格版本）")
    p.add_argument("--spec", choices=["v1", "v2"])
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("run-demo", help="一键跑完整演示流程并留证")
    p.add_argument("--patch-source", choices=["auto", "minimax", "replay"], default="auto")
    p.add_argument("--spec", choices=["v1", "v2"], default="v2")
    p.set_defaults(func=cmd_run_demo)

    p = sub.add_parser("show", help="打印上一次生成的上下文")
    p.add_argument("--name", default="context")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("prompt", help="渲染将发送给 MiniMax 的 prompt")
    p.add_argument("--name", default="context")
    p.set_defaults(func=cmd_prompt)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.project, args.records)
    try:
        return int(args.func(args, cfg) or 0)
    except (SeekDBError, StateError, ApplyError, PatchError) as exc:
        print_err(f"[error] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
