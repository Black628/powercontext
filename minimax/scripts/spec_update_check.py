#!/usr/bin/env python3
"""Prove that an updated spec is used after re-import.

Copies ``workspace/`` to a temporary directory, rewrites the S-04 free-shipping
threshold from 99.00 to 120.00, re-runs ``sdd ingest`` against the copy, then
``sdd select`` for the same task and asserts that the rendered context quotes the
new 120.00 wording instead of the old 99.00 one.

The real workspace and the real material index are never touched.

    python scripts/spec_update_check.py --backend seekdb
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
TASK = "订单满 99 元免运费；会员折扣后金额不足 99 元时应按折后金额判断，金卡会员始终免运费。"
CLAUSE = re.compile(r"(?ms)^##\s+S-04\s+.*?$(.*?)(?=^##\s+|\Z)")


def run(command: list[str]) -> str:
    print("[spec-update] $ " + " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True)
    if completed.returncode != 0:
        print((completed.stdout or "") + (completed.stderr or ""), file=sys.stderr)
        raise SystemExit(f"command failed with {completed.returncode}")
    return completed.stdout


def sdd(*args: str) -> str:
    return run([sys.executable, "-m", "sdd", *args])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["auto", "seekdb", "sqlite"],
                        default="seekdb" if sys.platform.startswith("linux") else "sqlite")
    parser.add_argument("--db", default=None, help="temporary index location (defaults to the temp dir)")
    args = parser.parse_args()
    backend = args.backend
    if backend == "auto":
        backend = "seekdb" if sys.platform.startswith("linux") else "sqlite"

    with tempfile.TemporaryDirectory(prefix="sdd-spec-update-") as temp:
        temp_dir = Path(temp)
        workspace = temp_dir / "workspace"
        shutil.copytree(WORKSPACE, workspace)
        spec_path = workspace / "specs" / "pricing_rules.md"
        original = spec_path.read_text(encoding="utf-8")
        match = CLAUSE.search(original)
        if match is None:
            return fail("S-04 section not found in the spec")
        body = match.group(1)
        updated_body = body.replace("99.00", "120.00")
        if updated_body == body:
            return fail("S-04 body does not contain the 99.00 threshold")
        spec_path.write_text(original[: match.start(1)] + updated_body + original[match.end(1):], encoding="utf-8")
        print("[spec-update] S-04 now reads: " + " ".join(updated_body.split())[:120], flush=True)

        db = Path(args.db) if args.db else temp_dir / "index"
        ingested = json.loads(sdd("ingest", "--workspace", str(workspace), "--backend", backend, "--db", str(db)))
        out = temp_dir / "context"
        sdd(
            "select", "--workspace", str(workspace), "--task", TASK, "--no-state",
            "--backend", backend, "--db", str(db), "--out", str(out),
        )
        context = json.loads((Path(str(out) + ".json")).read_text(encoding="utf-8"))
        clause = next((item for item in context["spec_clauses"] if item["clause_id"] == "S-04"), None)
        if clause is None:
            return fail("S-04 was not selected for the task")
        if "120.00" not in clause["body"] or "99.00" in clause["body"]:
            return fail("the selected S-04 body still uses the stale threshold")
        markdown = Path(str(out) + ".md").read_text(encoding="utf-8")
        if "120.00" not in markdown:
            return fail("the rendered Markdown does not quote the updated clause")

        print(json.dumps({
            "ok": True,
            "backend": ingested["backend"],
            "spec_counts": ingested["counts"],
            "primary_clauses": context["primary_clauses"],
            "clause_body": " ".join(clause["body"].split()),
            "digest": clause["digest"],
        }, ensure_ascii=False, indent=2))
        print("[spec-update] OK: the re-imported spec is what the context quotes")
        return 0


def fail(message: str) -> int:
    print(f"[spec-update] FAIL: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
