#!/usr/bin/env python3
"""从 snapshots/before 与 snapshots/after 生成 unified diff（git 风格，a/ b/ 前缀）。

用途：
  1. 生成 sample/offline_patch.diff —— 离线回放用的参照 patch；
  2. 生成 demo_project/patch/fix.diff —— 提交里的“修改后”快照差异，便于评审对照。

用法：
  python scripts/make_patch.py            # 打印并写入两个文件
  python scripts/make_patch.py --check    # 只校验两个文件是否与快照一致
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

MINIMAX = Path(__file__).resolve().parent.parent
BEFORE = MINIMAX / "demo_project" / "snapshots" / "before"
AFTER = MINIMAX / "demo_project" / "snapshots" / "after"
TARGETS = [MINIMAX / "sample" / "offline_patch.diff",
           MINIMAX / "demo_project" / "patch" / "fix.diff"]


def build_diff() -> str:
    chunks = []
    files = sorted(p.relative_to(BEFORE).as_posix() for p in BEFORE.rglob("*") if p.is_file())
    for rel in files:
        before_path = BEFORE / rel
        after_path = AFTER / rel
        old = before_path.read_text(encoding="utf-8").splitlines(keepends=True) if before_path.exists() else []
        new = after_path.read_text(encoding="utf-8").splitlines(keepends=True) if after_path.exists() else []
        if old == new:
            continue
        diff = difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3)
        text = "".join(diff)
        if not text.endswith("\n"):
            text += "\n"
        chunks.append(text)
    return "".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查已提交的 patch 是否与快照一致")
    args = parser.parse_args()
    diff = build_diff()
    if not diff:
        print("before/after 快照无差异，无需 patch", file=sys.stderr)
        return 1
    if args.check:
        ok = True
        for target in TARGETS:
            if not target.exists():
                print(f"MISSING {target}", file=sys.stderr)
                ok = False
                continue
            if target.read_text(encoding="utf-8") != diff:
                print(f"STALE   {target}", file=sys.stderr)
                ok = False
            else:
                print(f"OK      {target}")
        return 0 if ok else 1
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(diff, encoding="utf-8", newline="\n")
        print(f"写入 {target}")
    print(diff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
