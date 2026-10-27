#!/usr/bin/env python3
"""End-to-end demonstration of the minimax SDD submission.

The script drives the whole story required by the competition brief:

1. start a local PowerContext Server (SQLite storage, no external database),
2. reset the demo project to its *before* state,
3. ingest specs/code/tests into the material index (seekdb on Linux, SQLite on Windows),
4. select the development context for one task and remember it in PowerContext,
5. show that the target tests fail before the change,
6. apply the change produced by MiniMax and verify target + regression tests,
7. restart PowerContext, then read the task state back to prove it is reusable,
8. rebuild the development context so it shows the completed items and verification records.

Run ``python scripts/demo.py`` (Linux) or ``python scripts\\demo.py`` (Windows) from
``minimax/``.  Use ``--backend sqlite`` on Windows (seekdb only ships Linux/macOS wheels)
and ``--backend seekdb`` on Linux.

Exit code 0 means every check passed; any failed check stops the run with exit code 1.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
EVIDENCE = ROOT / "evidence"
RUN = ROOT / "run"
TASK = "订单满 99 元免运费；会员折扣后金额不足 99 元时应按折后金额判断，金卡会员始终免运费。"
SCOPE_TITLE = "minimax-sdd-pricing"
SERVER_PYTHON = os.environ.get("POWERCONTEXT_PYTHON", sys.executable)
SERVER_LAUNCHER = ROOT / "scripts" / "powercontext_launcher.py"


def log(message: str) -> None:
    print(f"[demo] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[demo] FAIL: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def free_port(preferred: int) -> int:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
        check: bool = True, capture: bool = True) -> str:
    log("$ " + " ".join(command))
    completed = subprocess.run(
        command, cwd=str(cwd), env=env, text=True,
        capture_output=capture,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if check and completed.returncode != 0:
        print(output)
        fail(f"command exited with {completed.returncode}")
    return output


def sdd(backend: str, db: Path, *args: str, check: bool = True) -> str:
    return run(
        [sys.executable, "-m", "sdd", *args, "--backend", backend, "--db", str(db)],
        check=check,
    )


def pytest_counts(output: str) -> tuple[int, int]:
    import re

    failed = passed = 0
    for line in output.splitlines():
        failed_match = re.search(r"(\d+) failed", line) if "failed" in line else None
        passed_match = re.search(r"(\d+) passed", line) if "passed" in line else None
        if failed_match:
            failed = int(failed_match.group(1))
        if passed_match:
            passed = int(passed_match.group(1))
    return passed, failed


def wait_ready(base: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health/ready", timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    fail(f"PowerContext Server did not become ready within {timeout:.0f}s")


def start_server(port: int, home: Path, log_path: Path) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["POWERCONTEXT_HOME"] = str(home)
    env.pop("POWERCONTEXT_SERVER_AUTH_ENABLED", None)
    env.pop("POWERCONTEXT_SERVER_AUTH_TOKEN", None)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [SERVER_PYTHON, str(SERVER_LAUNCHER),
         "server", "run", "--no-env-file", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT, text=True,
    )
    handle.close()
    return process


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["auto", "seekdb", "sqlite"],
                        default="seekdb" if sys.platform.startswith("linux") else "sqlite")
    parser.add_argument("--db", default=None, help="material index location (seekdb dir or sqlite prefix)")
    parser.add_argument("--port", type=int, default=8137)
    parser.add_argument("--keep-run", action="store_true", help="keep run/powercontext data from a previous run")
    args = parser.parse_args()

    if args.backend == "auto":
        args.backend = "seekdb" if sys.platform.startswith("linux") else "sqlite"
    if args.db:
        db = Path(args.db)
    elif args.backend == "seekdb":
        db = Path.home() / ".local" / "share" / "minimax-sdd" / "index"
    else:
        db = ROOT / ".data" / "index"

    if not (EVIDENCE / "before" / "engine.py").is_file():
        fail("evidence/before/engine.py is missing; run from the minimax directory")
    if not (EVIDENCE / "after" / "engine.py").is_file():
        fail("evidence/after/engine.py is missing")

    port = free_port(args.port)
    base = f"http://127.0.0.1:{port}"
    home = RUN / "powercontext"
    if not args.keep_run and home.exists():
        shutil.rmtree(home)
    for name in ("sql.log", "powercontext.log"):
        path = ROOT / ".data" / "logs" / name
        if path.exists():
            path.unlink()

    server: subprocess.Popen[str] | None = None
    summary: dict[str, object] = {"backend": args.backend, "db": str(db), "port": port}
    try:
        log("checking the PowerContext installation")
        run([SERVER_PYTHON, "-c",
             "import importlib.metadata as m; print('powercontext', m.version('powercontext'))"])

        log("starting PowerContext Server")
        server = start_server(port, home, EVIDENCE / "powercontext-server.log")
        wait_ready(base, timeout=120)
        log(f"PowerContext Server ready at {base}")

        log("resetting the demo project to the before state")
        shutil.copyfile(EVIDENCE / "before" / "engine.py", WORKSPACE / "src" / "pricing" / "engine.py")

        log("ingesting specs, code and tests into the material index")
        ingested = json.loads(sdd(args.backend, db, "ingest"))
        summary["ingest"] = ingested
        if ingested["counts"]["spec_clauses"] != 5 or ingested["counts"]["tests"] != 11:
            fail(f"unexpected index counts: {ingested['counts']}")
        if ingested["backend"] != args.backend:
            fail(f"requested backend {args.backend} but store reported {ingested['backend']}")

        log("selecting the development context and remembering it in PowerContext")
        selected = json.loads(sdd(
            args.backend, db, "select", "--task", TASK, "--remember",
            "--out", str(EVIDENCE / "context-latest"), "--powercontext-url", base,
        ))
        summary["select"] = {
            "run_id": selected["run_id"],
            "primary_clauses": selected["primary_clauses"],
            "clauses": selected["clauses"],
            "symbols": selected["symbols"],
            "target_tests": selected["target_tests"],
            "regression_tests": selected["regression_tests"],
        }
        if selected["primary_clauses"] != ["S-04"]:
            fail(f"expected primary clause S-04, got {selected['primary_clauses']}")
        if len(selected["target_tests"]) != 3 or len(selected["regression_tests"]) != 8:
            fail("expected 3 target tests and 8 regression tests")
        if "engine.py::shipping" not in selected["symbols"][0]:
            fail(f"expected the shipping symbol, got {selected['symbols']}")
        if selected["state_saved"] is None:
            fail("PowerContext task state was not saved")

        log("running the target tests before the change (expected: 3 failed, 8 passed)")
        before = run([sys.executable, "-m", "pytest", "-v", "-rA", "tests"], cwd=WORKSPACE, check=False)
        (EVIDENCE / "pytest-before.log").write_text(before, encoding="utf-8")
        passed, failed = pytest_counts(before)
        summary["before"] = {"passed": passed, "failed": failed}
        if (passed, failed) != (8, 3):
            fail(f"expected 8 passed / 3 failed before the change, got {passed} passed / {failed} failed")

        log("applying the change produced by MiniMax (evidence/after/engine.py)")
        shutil.copyfile(EVIDENCE / "after" / "engine.py", WORKSPACE / "src" / "pricing" / "engine.py")

        log("verifying target + regression tests through sdd verify")
        verified = json.loads(sdd(
            args.backend, db, "verify", "--task", TASK,
            "--log", str(EVIDENCE / "pytest-after.log"),
            "--require-state", "--powercontext-url", base,
        ))
        summary["verify"] = verified
        if verified["status"] != "verified" or verified["pytest_exit_code"] != 0:
            fail(f"verification did not succeed: {verified}")

        log("restarting PowerContext Server")
        stop_server(server)
        server = start_server(port, home, EVIDENCE / "powercontext-server-restart.log")
        wait_ready(base, timeout=120)

        log("reading the task state back after the restart")
        state_payload = json.loads(run(
            [sys.executable, "-m", "sdd", "state", "--backend", args.backend, "--db", str(db),
             "--powercontext-url", base],
        ))
        state = state_payload["state"]
        summary["state_after_restart"] = {
            "scope_id": state_payload["scope_id"],
            "artifact_id": state["artifact_id"],
            "revision": state["revision"],
            "status": state["state"]["status"],
            "verification_entries": len(state["state"]["verification"]),
        }
        if state["state"]["status"] != "verified":
            fail(f"state after restart is {state['state']['status']!r}, expected 'verified'")
        if not state["state"]["verification"]:
            fail("state after restart has no verification records")
        checks = [entry["check"] for entry in state["state"]["verification"]]
        if not any(check.startswith("target tests") for check in checks) or not any(
            check.startswith("regression tests") for check in checks
        ):
            fail(f"unexpected verification records: {checks}")

        log("rebuilding the context with the remembered task state")
        sdd(
            args.backend, db, "select", "--task", TASK,
            "--out", str(EVIDENCE / "context-after-verify"),
            "--powercontext-url", base,
        )
        with_state = json.loads(
            (EVIDENCE / "context-after-verify.json").read_text(encoding="utf-8")
        )
        summary["select_with_state"] = {
            "run_id": with_state["run_id"],
            "task_state": {
                "source": with_state["task_state"]["source"],
                "status": with_state["task_state"]["status"],
                "completed": len(with_state["task_state"]["completed"]),
                "pending": len(with_state["task_state"]["pending"]),
                "verification": len(with_state["task_state"]["verification"]),
            },
        }
        if with_state["task_state"]["status"] != "verified":
            fail("the rebuilt context did not load the remembered task state")
        if len(with_state["task_state"]["verification"]) < 2:
            fail("the remembered task state lost its verification records")

        summary["ok"] = True
        log("all checks passed")
        return 0
    finally:
        stop_server(server)
        (EVIDENCE / "demo-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        for name in ("sql.log", "powercontext.log"):
            source = ROOT / ".data" / "logs" / name
            if source.exists():
                shutil.copyfile(source, EVIDENCE / name)


if __name__ == "__main__":
    raise SystemExit(main())
