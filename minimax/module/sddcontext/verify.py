"""测试执行与留证：修改前后分别跑目标测试与回归测试，输出落到 records/test-logs/。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .util import append_jsonl, ensure_dir, now_iso, now_stamp, read_json, write_json

RAN_RE = re.compile(r"^Ran (\d+) tests? in", re.M)
RESULT_RE = re.compile(r"^(OK|FAILED)\b(.*)$", re.M)

TARGET_PATTERN = "test_discount_target.py"
REGRESSION_PATTERN = "test_discount_regression.py"


def _python() -> str:
    return os.environ.get("SDD_PYTHON", sys.executable or "python3")


def run_unittest(project_root: Path, pattern: Optional[str] = None, verbose: bool = True) -> dict:
    cmd = [_python(), "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    if pattern:
        cmd += ["-p", pattern]
    if verbose:
        cmd += ["-v"]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(project_root), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        cmd, cwd=str(project_root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    ran = RAN_RE.search(output)
    result = RESULT_RE.search(output)
    return {
        "command": " ".join(cmd),
        "cwd": str(project_root),
        "pattern": pattern,
        "exit_code": completed.returncode,
        "ran": int(ran.group(1)) if ran else 0,
        "result": (result.group(0).strip() if result else "UNKNOWN"),
        "ok": completed.returncode == 0,
        "output": output,
    }


def verify(project: Config, phase: str) -> dict:
    """phase: before | after。返回结构化结果并写 records/test-logs/。"""
    project_root = project.project_root
    log_dir = ensure_dir(project.test_log_dir)
    stamp = now_stamp()
    suites = {
        "target": run_unittest(project_root, TARGET_PATTERN),
        "regression": run_unittest(project_root, REGRESSION_PATTERN),
        "full": run_unittest(project_root, None),
    }
    report = {
        "phase": phase,
        "ts": now_iso(),
        "project_root": str(project_root),
        "suites": {k: {kk: vv for kk, vv in v.items() if kk != "output"} for k, v in suites.items()},
    }
    lines: List[str] = []
    lines.append(f"# 测试运行记录 phase={phase}")
    lines.append("")
    lines.append(f"- 时间: {report['ts']}")
    lines.append(f"- 工程: {project_root}")
    for name, suite in suites.items():
        log_path = log_dir / f"{phase}-{name}-{stamp}.log"
        header = (
            f"# phase={phase} suite={name}\n"
            f"# command: {suite['command']}\n"
            f"# cwd: {suite['cwd']}\n"
            f"# exit_code: {suite['exit_code']}\n"
            f"# result: {suite['result']}\n\n"
        )
        log_path.write_text(header + suite["output"], encoding="utf-8", newline="\n")
        report["suites"][name]["log"] = str(log_path)
        lines.append("")
        lines.append(f"## suite={name}")
        lines.append(f"命令: `{suite['command']}`")
        lines.append(f"退出码: {suite['exit_code']}  结果: {suite['result']}")
        lines.append(f"日志: `{log_path}`")
        lines.append("")
        lines.append("```text")
        lines.append(suite["output"].strip()[-3000:])
        lines.append("```")
    summary_path = log_dir / f"{phase}-{stamp}.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    report["summary_file"] = str(summary_path)

    index_path = log_dir / "index.json"
    index = read_json(index_path, []) or []
    index.append({
        "ts": report["ts"], "phase": phase, "summary_file": str(summary_path),
        "suites": {
            name: {"exit_code": s["exit_code"], "ran": s["ran"], "result": s["result"], "ok": s["ok"]}
            for name, s in suites.items()
        },
    })
    write_json(index_path, index)
    report["ok"] = all(s["ok"] for s in suites.values())
    return report
