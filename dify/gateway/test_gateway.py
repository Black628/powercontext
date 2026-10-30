# -*- coding: utf-8 -*-
"""记忆酒馆网关自检：验证 seekdb 设定检索 + PowerContext 记忆读写 + 跨会话复用。"""

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9300"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")[:300]


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main():
    print("== health ==")
    print(get("/health"))

    print("\n== 设定检索（seekdb 全文）==")
    code, lore = post("/lore/search", {"query": "规矩", "limit": 2})
    print(code, "命中", lore.get("count"))
    for it in lore.get("items", []):
        print("  -", it["title"], "|", it["content"][:40])

    player = "测试玩家-01"
    print("\n== 第一轮：无历史记忆 ==")
    code, rec = post("/mem/recall", {"player": player, "query": "我来过吗"})
    print(code, "记忆条数", rec.get("count"), "text=", repr(rec.get("text")))

    print("\n== 写入一条互动记忆 ==")
    print(post("/mem/remember", {"player": player, "text": "测试玩家-01 点了招牌酒三更醒，说下次带朋友来。"}))

    print("\n== 第二轮：同一玩家，应能召回 ==")
    code, rec2 = post("/mem/recall", {"player": player, "query": "我上次点了什么"})
    print(code, "记忆条数", rec2.get("count"))
    print("  text:", rec2.get("text"))

    print("\n== 预置玩家「阿狸」的记忆（seed 写入）==")
    code, rec3 = post("/mem/recall", {"player": "阿狸", "query": "杯子"})
    print(code, "记忆条数", rec3.get("count"))
    print("  text:", rec3.get("text"))


if __name__ == "__main__":
    main()
