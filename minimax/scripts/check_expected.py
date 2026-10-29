#!/usr/bin/env python3
"""按 sample/expected.json 机检上下文与留证文件。

用法：
  python scripts/check_expected.py                      # 检查 .state/context/context.json
  python scripts/check_expected.py --context .state/context/context-v2.json
退出码 0 表示全部 PASS。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MINIMAX = Path(__file__).resolve().parent.parent
SOURCE_RE = re.compile(r"^.+:\d+-\d+$")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", default=str(MINIMAX / ".state" / "context" / "context.json"))
    args = parser.parse_args()

    expected = load_json(MINIMAX / "sample" / "expected.json")
    context_path = Path(args.context)
    if not context_path.exists():
        print(f"FAIL 找不到上下文文件 {context_path}，请先执行 context/run-demo")
        return 2
    context = load_json(context_path)

    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    index = {kind: {doc["doc_key"]: doc for doc in context[kind]}
             for kind in ("specs", "code", "tests")}

    for kind, key in (("specs", "specs_must_contain"), ("code", "code_must_contain"),
                      ("tests", "tests_must_contain")):
        for rule in expected["context"][key]:
            doc = index[kind].get(rule["doc_key"])
            if doc is None:
                check(f"{kind} {rule['doc_key']}", False, "未命中")
                continue
            body = doc.get("content") or ""
            missing = [needle for needle in rule["must_contain"] if needle not in body]
            check(f"{kind} {rule['doc_key']}", not missing,
                  rule["reason"] if not missing else f"缺少 {missing}")

    for name in expected["context"]["checks_all_true"]:
        found = {c["name"]: c["ok"] for c in context.get("checks", [])}
        check(f"check {name}", bool(found.get(name)), "" if found.get(name) else "为 false")

    bad_sources = []
    for kind in ("specs", "code", "tests"):
        for doc in context[kind]:
            if not SOURCE_RE.match(doc.get("source", "")):
                bad_sources.append(doc.get("source"))
    check("所有来源都是 path:line-line", not bad_sources, str(bad_sources[:3]))

    state = context.get("state") or {}
    check("state.backend 来自 PowerContext", state.get("backend") == expected["context"]["state_backend"],
          f"backend={state.get('backend')}")

    # 规格版本对比（需要 context-v1/context-v2 都存在）
    spec_rule = expected["spec_update"]
    v1_path = MINIMAX / ".state" / "context" / "context-v1.json"
    v2_path = MINIMAX / ".state" / "context" / "context-v2.json"
    if v1_path.exists() and v2_path.exists():
        body1 = "\n".join(d["content"] for d in load_json(v1_path)["specs"])
        body2 = "\n".join(d["content"] for d in load_json(v2_path)["specs"])
        check("v1 上下文不含 v2 内容",
              all(n not in body1 for n in spec_rule["v1_must_not_contain"]), "")
        check("v2 上下文含 v2 内容",
              all(n in body2 for n in spec_rule["v2_must_contain"]), "")
        check("v1/v2 正文不同", body1 != body2, "")
    else:
        check("规格版本对比", False, "缺少 .state/context/context-v1.json 或 context-v2.json（请跑 run-demo）")

    # 留证文件
    for rel in expected["artifacts"]["records"]:
        check(f"记录存在 {rel}", (MINIMAX / rel).exists(), "")
    demo = MINIMAX / "records" / "demo-run-latest.json"
    if demo.exists():
        report = load_json(demo)
        step_ok = any(s["name"] == "state-reload-after-restart" and s["status"] == "ok"
                      for s in report.get("steps", []))
        check("状态跨进程复用步骤 ok", step_ok, "")
        check("run-demo 无失败项", report.get("passed") is True, str(report.get("failures")))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
