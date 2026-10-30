# -*- coding: utf-8 -*-
"""初始化记忆酒馆的依赖数据。

1. 在 seekdb 建 tavern 库与设定表，写入 6 条设定（全文索引）；
2. （可选）给演示玩家「阿狸」预置一条历史记忆——**通过网关接口写**，
   这样 scope 与 seekdb 里的玩家档案保持一致。

用法（顺序建议：先启动网关，再执行本脚本）：
    python seed.py
    python seed.py --with-demo-memory --gateway http://127.0.0.1:9300
"""

import argparse
import pathlib
import re
import sys

import httpx
import pymysql

HERE = pathlib.Path(__file__).parent
DB = dict(host="127.0.0.1", port=2881, user="root", password="",
          charset="utf8mb4", autocommit=True)


def run_sql_file(path: pathlib.Path) -> None:
    sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
    conn = pymysql.connect(**DB)
    with conn.cursor() as cur:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            cur.execute(stmt)
    conn.close()


def verify_lore() -> None:
    conn = pymysql.connect(database="tavern", **DB)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tavern_lore")
        total = cur.fetchone()[0]
        cur.execute("SELECT title FROM tavern_lore WHERE MATCH(content) AGAINST(%s) LIMIT 3", ("规矩",))
        hits = [r[0] for r in cur.fetchall()]
    conn.close()
    print("设定条数:", total)
    print("全文检索『规矩』命中:", hits)


def seed_memory(gateway: str, player: str, text: str) -> None:
    with httpx.Client(base_url=gateway, timeout=20) as c:
        r = c.post("/mem/remember", json={"player": player, "text": text})
        print("写入记忆:", r.status_code, r.json())
        r = c.post("/mem/recall", json={"player": player, "query": "杯子"})
        data = r.json()
        print("回读记忆: count=%s source=%s" % (data.get("count"), data.get("source")))
        print("  text:", data.get("text"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-demo-memory", action="store_true")
    ap.add_argument("--gateway", default="http://127.0.0.1:9300")
    args = ap.parse_args()

    run_sql_file(HERE / "init_lore.sql")
    print("seekdb 初始化完成")
    verify_lore()

    if args.with_demo_memory:
        seed_memory(args.gateway, "阿狸",
                    "阿狸上次来店里，跟隔壁桌打赌输了，摔了一个杯子，答应下次赔一个新的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
