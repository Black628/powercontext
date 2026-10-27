# Recall Coach / 知新 —— 验证样例（stdlib only，无第三方依赖）
#
# 用法：先启动服务，再运行
#   python scripts/smoke.py
#   python scripts/smoke.py --base-url http://127.0.0.1:8848
#
# 它会依次走完「载入样例资料 → 混合检索 → 开始复习 → 答错一张卡 → 记忆写入
# → 记忆快照 → 讲解偏好 → 执行记录」，并在每一步打印可核对的结果。

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(base: str, path: str, payload: dict | None = None, method: str | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_ready(base: str, seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            call(base, "/api/status")
            return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8848")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if not wait_ready(base):
        print(f"[FAIL] 服务 {base} 没有起来（先运行 scripts/start.ps1 或 scripts/start.sh）")
        return 1

    ok = 0
    total = 0
    skipped = 0

    def step(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok, total
        total += 1
        if condition:
            ok += 1
        print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""))

    def skip(name: str, reason: str = "") -> None:
        nonlocal skipped
        skipped += 1
        print(f"[SKIP] {name}" + (f" | {reason}" if reason else ""))

    status = call(base, "/api/status")
    backend = status["backend"]
    print(f"=== 后端：{backend['backend']} / {backend['mode']}｜embedding {status['embedding']['name']}｜记忆 {'在线' if status['memory']['available'] else '离线'}")
    step("status 返回后端信息", bool(backend.get("backend")))

    seeded = call(base, "/api/seed", {})
    stats = seeded["stats"]
    step("载入样例资料", stats["materials"] >= 8, f"materials={stats['materials']} reviews={stats['reviews']}")

    search = call(base, "/api/search", {"query": "为什么哨兵不能真正终止进程", "top_k": 3})
    step("seekdb 混合检索有结果", len(search["hits"]) > 0, f"hit0={search['hits'][0]['material']['title'] if search['hits'] else '-'}")

    session = call(base, "/api/session", {"course": "操作系统", "budget": 3})
    step("创建复习 session", len(session.get("cards", [])) > 0, f"cards={len(session.get('cards') or [])}")
    if session.get("memory", {}).get("available"):
        print(f"--- PowerContext 上下文（{len(session['memory']['context']['content'])} 字节）")
        print("\n".join("    " + line for line in session["memory"]["context"]["content"].splitlines()[:8]))
    else:
        print("--- PowerContext 离线：本轮只走 seekdb 检索（在 WSL/Linux 上可看到记忆复用）")

    if session["cards"]:
        card = session["cards"][0]
        answer = card["options"][0]
        result = call(
            base,
            f"/api/session/{session['session_id']}/grade",
            {"material_id": card["material_id"], "picked": answer},
        )
        step(
            "评分并更新掌握度",
            result.get("verdict") in {"correct", "hard", "again"},
            f"verdict={result['verdict']} answer={result['answer']} mastery={result['mastery']}",
        )
        step(
            "seekdb 相关原文回显",
            len(result.get("related") or []) > 0,
            f"related={len(result.get('related') or [])}",
        )
        if result.get("memory_write"):
            entry = (result.get("memory_write") or {}).get("entry") or {}
            print(f"--- 记忆写入：{entry.get('text', '(仅内存状态)')}")
            step("PowerContext 写入学习记忆", True)
        else:
            print("--- 本轮没有触发记忆写入（模拟后端或连续答对未到里程碑）")

    memory = call(base, "/api/memory")
    if memory.get("available"):
        active = [entry for entry in memory["entries"] if entry["state"] == "active"]
        step("记忆快照可读", len(active) > 0, f"active={len(active)} preference={bool(memory.get('preference'))}")
        pref = call(base, "/api/memory/preference", {"value": "先给反例，再给结论"})
        step("保存讲解偏好", bool(pref.get("ok")), pref.get("memory", {}).get("preference", ""))
    else:
        print(f"--- 记忆不可用：{memory.get('error', '')}")
        skip("记忆快照", "PowerContext 离线（Windows 无 powercontext 服务端）")
        skip("保存讲解偏好", "同上")

    evidence = call(base, "/api/evidence?limit=10")
    step("seekdb 执行记录可核对", len(evidence["store"]) > 0, f"rows={len(evidence['store'])}")
    if evidence["memory"]:
        step("PowerContext 读写记录可核对", True, f"rows={len(evidence['memory'])}")
    else:
        skip("PowerContext 读写记录", "本轮没有记忆调用（离线或尚未触发）")

    print(f"\n=== {ok}/{total} 步通过" + (f"，{skipped} 步跳过（记忆离线）" if skipped else ""))
    if evidence["store"]:
        last = evidence["store"][0]
        print(f"=== 最近一条 seekdb SQL [{last['channel']}] {last['rows']} 行 {last['duration_ms']}ms")
        print("    " + " ".join(last["sql"].split())[:220])
    return 0 if ok == total else 2


if __name__ == "__main__":
    sys.exit(main())
