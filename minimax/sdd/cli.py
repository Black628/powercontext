"""Command line entry point of the SDD context module.

Commands::

    python -m sdd ingest     parse specs/code/tests into the material index
    python -m sdd select     select the context for one task (and optionally remember it)
    python -m sdd verify     run target + regression tests and store the result
    python -m sdd state      show the task state stored in PowerContext
    python -m sdd info       show index counts and the last ingest run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import keywords, parser
from .config import Config
from .pc import PowerContext, PowerContextError
from .select import build_context, write_selection
from .store import StoreError, open_store

SCOPE_SUMMARY = "minimax SDD demo: task progress and verification for the pricing settlement change"
PYTEST_RESULT = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(tests/[^\s:]+::\S+)\s*$")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _sql_log(cfg: Config) -> Path:
    configured = os.environ.get("SDD_SQL_LOG")
    return Path(configured) if configured else cfg.data_dir / "logs" / "sql.log"


def _open(cfg: Config):
    return open_store(cfg.backend, cfg.db_target, sql_log=_sql_log(cfg))


def _pc(cfg: Config) -> PowerContext:
    return PowerContext(
        cfg.powercontext_url,
        token=cfg.powercontext_token,
        log_path=cfg.data_dir / "logs" / "powercontext.log",
    )


def _relative_root(cfg: Config) -> str:
    try:
        return cfg.workspace.relative_to(cfg.root).as_posix()
    except ValueError:
        return str(cfg.workspace)


# -- ingest --------------------------------------------------------------
def cmd_ingest(cfg: Config, args: argparse.Namespace) -> int:
    clauses = parser.parse_specs(cfg.workspace, cfg.root)
    symbols = parser.parse_code(cfg.workspace, cfg.root)
    tests = parser.parse_tests(cfg.workspace, cfg.root)
    store = _open(cfg)
    try:
        run_id = store.ingest(clauses, symbols, tests, _relative_root(cfg))
        for clause in clauses:
            store.replace_keywords(
                "spec_keywords", "clause_id", clause.clause_id,
                keywords.keyword_weights(clause.body, title=clause.title),
            )
        for symbol in symbols:
            text = " ".join([symbol.qualname, symbol.signature, symbol.docstring])
            store.replace_keywords("symbol_keywords", "symbol_id", symbol.symbol_id, keywords.keyword_weights(text))
        for test in tests:
            text = test.name.replace("_", " ") + " " + test.docstring
            store.replace_keywords("test_keywords", "test_id", test.test_id, keywords.keyword_weights(text))
        summary = {
            "run_id": run_id,
            "backend": store.backend,
            "db": store.describe,
            "workspace": str(cfg.workspace),
            "counts": store.counts(),
            "sql_log": str(_sql_log(cfg)),
        }
    finally:
        store.close()
    _print(summary)
    return 0


# -- select --------------------------------------------------------------
def _stored_state(loaded: dict[str, Any] | None) -> dict[str, Any]:
    """Return the task-state document out of a ``load_state`` result."""
    if not loaded:
        return {}
    inner = loaded.get("state")
    return inner if isinstance(inner, dict) else {}


def _remember_state(context: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    previous = previous or {}
    symbols = [f"{symbol['qualname']} ({symbol['path']}:{symbol['line_start']})"
               for symbol in context["code_symbols"]]
    state = {
        "schema": "sdd.task-state.v1",
        "objective": context["task"],
        "status": "in-progress",
        "updated_at": _now(),
        "context_run": context["run_id"],
        "primary_clauses": context["primary_clauses"],
        "selected": {
            "clauses": [clause["clause_id"] for clause in context["spec_clauses"]],
            "symbols": [symbol["symbol_id"] for symbol in context["code_symbols"]],
            "target_tests": [test["test_id"] for test in context["tests"]["target"]],
            "regression_tests": [test["test_id"] for test in context["tests"]["regression"]],
        },
        "completed": ["材料已导入，任务上下文已生成（" + context["run_id"] + "）"],
        "pending": [f"修改 {name}" for name in symbols] + ["运行目标测试并记录结果", "运行回归测试并记录结果"],
        "verification": list(previous.get("verification", []))[-10:],
    }
    return state


def cmd_select(cfg: Config, args: argparse.Namespace) -> int:
    state: dict[str, Any] | None = None
    scope_id: str | None = None
    pc: PowerContext | None = None
    if not args.no_state:
        pc = _pc(cfg)
        try:
            scope_id = pc.ensure_scope(cfg.scope_title, SCOPE_SUMMARY)
            loaded = pc.load_state(scope_id)
            if loaded is not None:
                state = _stored_state(loaded)
                state["scope_id"] = scope_id
                state["artifact_id"] = loaded.get("artifact_id")
                state["revision"] = loaded.get("revision")
        except PowerContextError as exc:
            if args.remember:
                print(f"sdd: cannot use PowerContext: {exc}", file=sys.stderr)
                return 3
            print(f"sdd: warning: {exc}", file=sys.stderr)

    store = _open(cfg)
    try:
        context = build_context(
            cfg, store, args.task, state=state,
            limit_clauses=args.limit_clauses, limit_symbols=args.limit_symbols,
        )
    finally:
        store.close()

    base = Path(args.out).resolve() if args.out else cfg.data_dir / "context" / context["run_id"]
    write_selection(context, base.with_suffix(".json"), base.with_suffix(".md"), base.with_suffix(".prompt.txt"))

    saved: dict[str, Any] | None = None
    if args.remember:
        assert pc is not None and scope_id is not None
        try:
            saved = pc.save_state(scope_id, _remember_state(context, state))
        except PowerContextError as exc:
            print(f"sdd: cannot save task state: {exc}", file=sys.stderr)
            return 3

    _print(
        {
            "run_id": context["run_id"],
            "backend": context["backend"],
            "primary_clauses": context["primary_clauses"],
            "clauses": [clause["clause_id"] for clause in context["spec_clauses"]],
            "symbols": [symbol["symbol_id"] for symbol in context["code_symbols"]],
            "target_tests": [test["test_id"] for test in context["tests"]["target"]],
            "regression_tests": [test["test_id"] for test in context["tests"]["regression"]],
            "warnings": context["warnings"],
            "state_before": None
            if state is None
            else {"artifact_id": state.get("artifact_id"), "revision": state.get("revision"), "status": state.get("status")},
            "state_saved": None
            if saved is None
            else {"artifact_id": saved.get("artifact_id"), "revision": saved.get("revision")},
            "files": {"json": str(base.with_suffix(".json")), "markdown": str(base.with_suffix(".md")),
                      "prompt": str(base.with_suffix(".prompt.txt"))},
        }
    )
    return 0


# -- verify --------------------------------------------------------------
def _run_pytest(workspace: Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-v", "-rA", "tests"]
    try:
        completed = subprocess.run(command, cwd=str(workspace), capture_output=True, text=True, timeout=600)
    except FileNotFoundError as error:  # pragma: no cover - environment dependent
        return {"exit_code": 127, "stdout": "", "stderr": str(error), "results": {}}
    results: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        match = PYTEST_RESULT.match(line.strip())
        if match:
            results[match.group(2)] = match.group(1)
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "results": results,
        "command": " ".join(command),
    }


def _split_results(tests: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    target = {test["name"]: test["test_id"] for test in tests}
    return target, {}


def _verified_state(
    context: dict[str, Any], previous: dict[str, Any] | None, runs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    previous = _stored_state(previous)
    target_names = {test["name"] for test in context["tests"]["target"]}
    regression_names = {test["name"] for test in context["tests"]["regression"]}

    def name_of(pytest_id: str) -> str:
        return pytest_id.split("::")[-1]

    records: list[dict[str, Any]] = []
    for label, names, run in (("target", target_names, runs["target"]), ("regression", regression_names, runs["regression"])):
        passed = sorted(name_of(item) for item, status in run["results"].items() if status == "PASSED" and name_of(item) in names)
        failed = sorted(name_of(item) for item, status in run["results"].items() if status != "PASSED" and name_of(item) in names)
        records.append(
            {
                "check": f"{label} tests ({len(names)} expected)",
                "command": run["command"],
                "result": f"{len(passed)} passed, {len(failed)} failed",
                "at": _now(),
                "passed": passed,
                "failed": failed,
            }
        )

    ok = all(not record["failed"] for record in records) and all(
        len(record["passed"]) == len(names) for record, names in ((records[0], target_names), (records[1], regression_names))
    )
    completed = ["材料已导入并完成上下文选择（" + context["run_id"] + "）"]
    if ok:
        completed += [
            f"目标测试全部通过（{len(records[0]['passed'])} 个）",
            f"回归测试全部通过（{len(records[1]['passed'])} 个）",
        ]
    return {
        "schema": "sdd.task-state.v1",
        "objective": context["task"],
        "status": "verified" if ok else "in-progress",
        "updated_at": _now(),
        "context_run": context["run_id"],
        "primary_clauses": context["primary_clauses"],
        "selected": {
            "clauses": [clause["clause_id"] for clause in context["spec_clauses"]],
            "symbols": [symbol["symbol_id"] for symbol in context["code_symbols"]],
            "target_tests": [test["test_id"] for test in context["tests"]["target"]],
            "regression_tests": [test["test_id"] for test in context["tests"]["regression"]],
        },
        "completed": completed,
        "pending": [] if ok else ["仍有失败的测试，需要继续修改后再验证"],
        "verification": (list(previous.get("verification", [])) + records)[-10:],
    }


def cmd_verify(cfg: Config, args: argparse.Namespace) -> int:
    state: dict[str, Any] | None = None
    scope_id: str | None = None
    pc: PowerContext | None = None
    if not args.no_state:
        pc = _pc(cfg)
        try:
            scope_id = pc.ensure_scope(cfg.scope_title, SCOPE_SUMMARY)
            state = pc.load_state(scope_id)
        except PowerContextError as exc:
            if args.require_state:
                print(f"sdd: cannot use PowerContext: {exc}", file=sys.stderr)
                return 3
            print(f"sdd: warning: {exc}", file=sys.stderr)

    store = _open(cfg)
    try:
        context = build_context(cfg, store, args.task, state=state)
    finally:
        store.close()

    overall = _run_pytest(cfg.workspace)
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        Path(args.log).write_text(overall["stdout"] + "\n" + overall["stderr"], encoding="utf-8")

    target_names = {test["name"] for test in context["tests"]["target"]}
    regression_names = {test["name"] for test in context["tests"]["regression"]}
    target_run = {"command": overall["command"], "results": {
        item: status for item, status in overall["results"].items() if item.split("::")[-1] in target_names}}
    regression_run = {"command": overall["command"], "results": {
        item: status for item, status in overall["results"].items() if item.split("::")[-1] in regression_names}}
    next_state = _verified_state(context, state, {"target": target_run, "regression": regression_run})

    saved: dict[str, Any] | None = None
    if not args.no_state:
        assert pc is not None and scope_id is not None
        try:
            saved = pc.save_state(scope_id, next_state)
        except PowerContextError as exc:
            print(f"sdd: warning: cannot save task state: {exc}", file=sys.stderr)
            if args.require_state:
                return 3

    ok = next_state["status"] == "verified" and overall["exit_code"] == 0
    _print(
        {
            "status": next_state["status"],
            "pytest_exit_code": overall["exit_code"],
            "target": target_run["results"],
            "regression": regression_run["results"],
            "verification": next_state["verification"],
            "state_saved": None if saved is None else {"artifact_id": saved.get("artifact_id"),
                                                       "revision": saved.get("revision")},
            "log": str(args.log) if args.log else None,
            "context_run": context["run_id"],
        }
    )
    return 0 if ok else 1


# -- state / info --------------------------------------------------------
def cmd_state(cfg: Config, args: argparse.Namespace) -> int:
    pc = _pc(cfg)
    try:
        scope_id = pc.ensure_scope(cfg.scope_title, SCOPE_SUMMARY)
        state = pc.load_state(scope_id)
    except PowerContextError as exc:
        print(f"sdd: {exc}", file=sys.stderr)
        return 3
    _print({"scope_id": scope_id, "state": state})
    return 0 if state is not None else 2


def cmd_info(cfg: Config, args: argparse.Namespace) -> int:
    store = _open(cfg)
    try:
        payload = {
            "backend": store.backend,
            "db": store.describe,
            "counts": store.counts(),
            "last_ingest": store.last_ingest(),
            "sql_log": str(_sql_log(cfg)),
        }
    finally:
        store.close()
    _print(payload)
    return 0


# -- argument parsing ----------------------------------------------------
def _common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=None, help="业务项目目录（默认 minimax/workspace）")
    common.add_argument("--backend", default=None, choices=["auto", "seekdb", "sqlite"], help="材料索引后端")
    common.add_argument("--db", default=None, help="索引数据目录/文件前缀")
    common.add_argument("--powercontext-url", default=None, help="PowerContext 服务地址")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser_ = argparse.ArgumentParser(prog="sdd", description="SDD 上下文管理演示模块")
    sub = parser_.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", parents=[common], help="导入规范、代码与测试")
    ingest.set_defaults(func=cmd_ingest)

    select = sub.add_parser("select", parents=[common], help="为一则任务选择开发上下文")
    select.add_argument("--task", required=True, help="任务描述，例如“订单满 99 元免运费”")
    select.add_argument("--out", default=None, help="输出文件前缀（生成 .json/.md/.prompt.txt）")
    select.add_argument("--limit-clauses", type=int, default=8)
    select.add_argument("--limit-symbols", type=int, default=10)
    select.add_argument("--no-state", action="store_true", help="不读写 PowerContext")
    select.add_argument("--remember", action="store_true", help="把本次上下文写入 PowerContext 任务状态")
    select.set_defaults(func=cmd_select)

    verify = sub.add_parser("verify", parents=[common], help="运行目标测试与回归测试并保存结果")
    verify.add_argument("--task", required=True)
    verify.add_argument("--log", default=None, help="保存 pytest 原始输出")
    verify.add_argument("--no-state", action="store_true")
    verify.add_argument("--require-state", action="store_true", help="PowerContext 不可用时以退出码 3 失败")
    verify.set_defaults(func=cmd_verify)

    state = sub.add_parser("state", parents=[common], help="读取 PowerContext 中的任务状态")
    state.set_defaults(func=cmd_state)

    info = sub.add_parser("info", parents=[common], help="查看索引统计")
    info.set_defaults(func=cmd_info)
    return parser_


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(
        workspace=args.workspace,
        db=args.db,
        backend=args.backend,
        powercontext_url=args.powercontext_url,
    )
    try:
        return int(args.func(cfg, args))
    except StoreError as exc:
        print(f"sdd: {exc}", file=sys.stderr)
        return 2
