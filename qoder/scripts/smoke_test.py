"""按 REQUIREMENTS 第 5 节依次验证 4 个样例，打印 PASS/FAIL 与可核对证据。

样例 1：检索 + 出处          —— 倒排索引为什么比顺序扫描快
样例 2：混合检索的必要性      —— B+ 树和 LSM 树在写放大上的区别
样例 3：记忆复用              —— 聚簇索引是什么（标记没懂 → 重开 review）
样例 4：提效对比              —— 同一问题的项目耗时（3 次中位数）

任一 FAIL 以非 0 退出。前置条件：已运行 scripts/seed_docs.py，seekdb 可连接；
样例 3 还要求 PowerContext 已配置且可写。

用法：
    python scripts/smoke_test.py
    python scripts/smoke_test.py --k 5 --manual-seconds 95   # 已实测手动中位数时
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "app" / ".env")

import pymysql  # noqa: E402

from app import embed, memory, search, store  # noqa: E402
from app import main as app_main  # noqa: E402

REQUIRED_DOCS = [
    "算法导论-第3章.md",
    "全文索引与倒排表.md",
    "实验报告-索引性能.md",
]
TARGET_DOC = "全文索引与倒排表.md"
KEYWORDS_SAMPLE1 = ["倒排表", "词项", "文档频率"]
WIDTH = 84

# 手动翻 PDF 找答案的中位数（秒）。0 表示尚未实测，不计算提效倍数。
MANUAL_LOOKUP_SECONDS = float(os.getenv("MANUAL_LOOKUP_SECONDS") or 0)

_COST_RE = re.compile(r"耗时:\s*([0-9.]+)\s*ms")
_HITS_RE = re.compile(r"命中:\s*(\d+)\s*条")


# ------------------------------------------------------------
# 基础工具
# ------------------------------------------------------------

class _Capture(logging.Handler):
    """收集 recall.search / recall.memory 的日志，作为冒烟测试证据。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def clear(self) -> None:
        self.messages.clear()


_CAPTURE = _Capture()


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


def _install_capture() -> None:
    for name in ("recall.search", "recall.memory"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        if _CAPTURE not in lg.handlers:
            lg.addHandler(_CAPTURE)
        # 证据由本脚本分节打印，避免 SQL 日志刷屏
        lg.propagate = False


def _run_capture(fn, *args, **kwargs):
    """清空捕获 → 执行 → 返回 (结果, 日志, 墙钟毫秒)。"""
    _CAPTURE.clear()
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    return result, list(_CAPTURE.messages), wall_ms


def _path_evidence(messages: list, tag: str) -> dict:
    """从日志中抽出某一路的 SQL、耗时、命中数。"""
    for msg in messages:
        if tag not in msg:
            continue
        cost = _COST_RE.search(msg)
        hits = _HITS_RE.search(msg)
        sql = ""
        if "SQL:" in msg:
            sql = msg.split("SQL:", 1)[1].split("参数:", 1)[0]
            sql = " ".join(sql.split())
        return {
            "cost_ms": float(cost.group(1)) if cost else None,
            "hits": int(hits.group(1)) if hits else None,
            "sql": sql,
            "raw": msg,
        }
    return {"cost_ms": None, "hits": None, "sql": "", "raw": ""}


def _print_paths(vec: dict, ft: dict, indent: str = "  ") -> None:
    for label, ev in (("向量路", vec), ("全文路", ft)):
        cost = f"{ev['cost_ms']:.1f} ms" if ev["cost_ms"] is not None else "未捕获"
        hits = ev["hits"] if ev["hits"] is not None else "?"
        print(f"{indent}{label}：耗时 {cost} | 命中 {hits} 条")
        if ev["sql"]:
            print(f"{indent}  SQL: {ev['sql']}")


def _bar(char: str = "-") -> None:
    print(char * WIDTH)


def _fmt_rank(value) -> str:
    return "-" if value is None else str(value)


def _fmt_score(value) -> str:
    return "-" if value is None else f"{value:.4f}"


def _print_hits(results: list, limit: int = 5) -> None:
    for i, r in enumerate(results[:limit], start=1):
        print(
            f"  [{i}] {r['doc_name']}  page={r['page']} offset={r['offset']}  "
            f"vec#{_fmt_rank(r['vector_rank'])}/{_fmt_score(r['vector_dist'])}  "
            f"ft#{_fmt_rank(r['fulltext_rank'])}/{_fmt_score(r['fulltext_score'])}  "
            f"rrf={_fmt_score(r['rrf_score'])}"
        )
        snippet = (r.get("snippet") or "").replace("\n", " ")
        print(f"      {snippet[:160]}{'…' if len(snippet) > 160 else ''}")


# ------------------------------------------------------------
# 前置检查
# ------------------------------------------------------------

def preflight() -> bool:
    print("=" * WIDTH)
    print("Recall（知返）· 冒烟测试（REQUIREMENTS 第 5 节）")
    print(f"seekdb       : {store.SEEKDB_HOST}:{store.SEEKDB_PORT}/{store.SEEKDB_DATABASE}")
    print(f"embedder     : {embed.get_embedder().name} (dim={embed.get_embedder().dim})")
    print(f"PowerContext : {memory.POWERCONTEXT_BASE_URL or '(未配置，样例 3 将无法通过)'}")
    print("=" * WIDTH)

    try:
        conn = store.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM chunks")
                chunk_count = int(cur.fetchone()["n"])
                cur.execute("SELECT doc_name, chunk_count, status FROM docs ORDER BY doc_name")
                docs = cur.fetchall()
        finally:
            conn.close()
    except pymysql.err.MySQLError as e:
        print(f"[FAIL] 无法连接 seekdb：{e}")
        print("       请先启动 seekdb，并检查 .env 中的 SEEKDB_* 配置。")
        return False

    print(f"库内文档 {len(docs)} 篇 / 切片 {chunk_count} 块：")
    for d in docs:
        print(f"  - {d['doc_name']:<24} status={d['status']:<8} chunks={d['chunk_count']}")
    if chunk_count == 0:
        print("[FAIL] 库内没有切片，请先运行：python scripts/seed_docs.py")
        return False

    names = {d["doc_name"] for d in docs}
    missing = [n for n in REQUIRED_DOCS if n not in names]
    if missing:
        print(f"[FAIL] 缺少样例文档：{missing}")
        print("       请运行：python scripts/seed_docs.py")
        return False
    return True


# ------------------------------------------------------------
# 样例 1：检索 + 出处
# ------------------------------------------------------------

def sample1(k: int) -> tuple[bool, str]:
    query = "倒排索引为什么比顺序扫描快"
    print("\n" + "=" * WIDTH)
    print("样例 1/4 · 检索 + 出处")
    print(f"输入：{query}")
    _bar()

    try:
        out, messages, wall_ms = _run_capture(search.hybrid_search, query, k)
    except Exception as e:  # noqa: BLE001  —— 冒烟测试需要把异常转成 FAIL
        print(f"检索异常：{e}")
        return False, f"检索异常：{e}"

    vec = _path_evidence(messages, "[向量路]")
    ft = _path_evidence(messages, "[全文路]")
    _print_paths(vec, ft)
    print(f"  融合检索总耗时：{wall_ms:.1f} ms | 返回 {len(out['results'])} 条")

    target_hits = [(i, r) for i, r in enumerate(out["results"], 1) if r["doc_name"] == TARGET_DOC]
    print(f"\n命中 {TARGET_DOC} 的片段：{len(target_hits)} 个（要求 >= 2）")
    _print_hits(out["results"])

    print("\n关键字段证据（片段前 300 字内）：")
    kw_evidence = {}
    for kw in KEYWORDS_SAMPLE1:
        where = [(i, r["offset"]) for i, r in target_hits if kw in (r.get("snippet") or "")]
        kw_evidence[kw] = where
        print(f"  {kw}: {'命中 ' + str(where) if where else '未出现'}")

    ok = (
        len(target_hits) >= 2
        and all(kw_evidence.values())
        and vec["cost_ms"] is not None
        and ft["cost_ms"] is not None
    )
    reason = (
        f"{TARGET_DOC} 命中 {len(target_hits)} 个片段；"
        f"关键词覆盖 {[k for k, v in kw_evidence.items() if v]}/{KEYWORDS_SAMPLE1}；"
        f"两路耗时 向量={vec['cost_ms']}ms 全文={ft['cost_ms']}ms"
    )
    return ok, reason


# ------------------------------------------------------------
# 样例 2：混合检索的必要性
# ------------------------------------------------------------

def sample2(k: int) -> tuple[bool, str]:
    query = "B+ 树和 LSM 树在写放大上的区别"
    print("\n" + "=" * WIDTH)
    print("样例 2/4 · 混合检索的必要性")
    print(f"输入：{query}")
    _bar()

    try:
        conn = store.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, doc_name, offset_in_doc FROM chunks "
                    "WHERE content LIKE %s ORDER BY id",
                    ("%log-structured%",),
                )
                targets = cur.fetchall()
                cur.execute("SELECT COUNT(*) AS n FROM chunks")
                total = int(cur.fetchone()["n"])
        finally:
            conn.close()
    except pymysql.err.MySQLError as e:
        print(f"读取目标块失败：{e}")
        return False, f"读取目标块失败：{e}"

    if not targets:
        print("未在库中找到含 'log-structured' 的块，请重新运行 seed_docs.py")
        return False, "未找到含 log-structured 的块"

    target_ids = [int(t["id"]) for t in targets]
    print("目标块（仅写 log-structured、不含 B+树/LSM/写放大 字样）：")
    for t in targets:
        print(f"  id={t['id']} {t['doc_name']} offset={t['offset_in_doc']}")

    # 1) 纯关键词检索：用户可见的 top-k（这就是“只用倒排表”的效果）
    try:
        ft_top, ft_msgs, ft_wall = _run_capture(search.fulltext_search, query, k)
    except Exception as e:  # noqa: BLE001
        print(f"纯关键词检索异常：{e}")
        return False, f"纯关键词检索异常：{e}"

    ft_top_ids = {int(r["id"]) for r in ft_top}
    keyword_missed = all(tid not in ft_top_ids for tid in target_ids)
    ft_top_ev = _path_evidence(ft_msgs, "[全文路]")
    print(f"\n纯关键词检索（仅全文路，k={k}，耗时 {ft_wall:.1f} ms）：")
    for i, r in enumerate(ft_top, start=1):
        mark = "  <-- 目标块" if int(r["id"]) in target_ids else ""
        print(f"  [{i}] {r['doc_name']} offset={r['offset_in_doc']} score={_fmt_score(float(r['score']))}{mark}")
    print(f"  目标块是否命中：{'是' if not keyword_missed else '否（关键词 top-k 漏召回）'}")

    # 2) 混合检索：取全部融合结果，便于展示目标块落在两路的什么位置
    k_all = max(total, k)
    try:
        out, messages, wall_ms = _run_capture(search.hybrid_search, query, k_all)
    except Exception as e:  # noqa: BLE001
        print(f"检索异常：{e}")
        return False, f"检索异常：{e}"

    vec = _path_evidence(messages, "[向量路]")
    ft = _path_evidence(messages, "[全文路]")
    print(f"\n混合检索（RRF 融合，k={k_all}）：")
    _print_paths(vec, ft)
    print(f"  融合总耗时：{wall_ms:.1f} ms | 向量候选 {out['vector_hits']} 条, "
          f"全文候选 {out['fulltext_hits']} 条")

    by_id = {int(r["id"]): r for r in out["results"]}
    fused_rank = {
        int(r["id"]): i for i, r in enumerate(out["results"], start=1)
    }
    target_rows = [(tid, by_id.get(tid)) for tid in target_ids]
    vector_recalled = all(r is not None and r["vector_rank"] is not None for _, r in target_rows)
    fused_included = all(r is not None for _, r in target_rows)

    print("\n两路召回对比（目标块）：")
    for tid, r in target_rows:
        if r is None:
            print(f"  id={tid}: 未出现在融合结果中")
            continue
        ft_rank = r["fulltext_rank"]
        ft_view = "未进候选" if ft_rank is None else f"rank={ft_rank}"
        print(
            f"  id={tid}: 向量路 rank={_fmt_rank(r['vector_rank'])} dist={_fmt_score(r['vector_dist'])}"
            f" | 全文路 {ft_view} score={_fmt_score(r['fulltext_score'])}"
            f" | 融合 rank={fused_rank.get(tid)} rrf={_fmt_score(r['rrf_score'])}"
        )
        snippet = (r.get("snippet") or "").replace("\n", " ")
        print(f"      片段：{snippet[:220]}")

    print("\n融合结果前 5 条（同时展示两路得分）：")
    _print_hits(out["results"], limit=5)

    ok = (
        keyword_missed
        and vector_recalled
        and fused_included
        and out["vector_hits"] > 0
        and out["fulltext_hits"] > 0
        and vec["cost_ms"] is not None
        and ft["cost_ms"] is not None
    )
    reason = (
        f"纯关键词 top-{k} {'未命中' if keyword_missed else '命中了'}目标块；"
        f"向量候选 rank={_fmt_rank(target_rows[0][1]['vector_rank']) if target_rows[0][1] else '-'}"
        f"（已召回）；融合 rank={fused_rank.get(target_ids[0])}；"
        f"耗时 关键词={ft_top_ev['cost_ms']}ms 向量={vec['cost_ms']}ms 全文={ft['cost_ms']}ms"
    )
    return ok, reason


# ------------------------------------------------------------
# 样例 3：记忆复用（PowerContext）
# ------------------------------------------------------------

def sample3(uid: str) -> tuple[bool, str]:
    query = "聚簇索引是什么"
    topic = "聚簇索引"
    print("\n" + "=" * WIDTH)
    print("样例 3/4 · 记忆复用（PowerContext）")
    print(f"步骤：提问「{query}」并标记「没懂」→ 重新打开 /api/review")
    print(f"uid：{uid}")
    _bar()

    try:
        out, _, _ = _run_capture(search.hybrid_search, query, 3)
    except Exception as e:  # noqa: BLE001
        print(f"检索异常，无法生成记忆来源：{e}")
        return False, f"检索异常：{e}"

    sources = out.get("results", [])[:3]
    print(f"检索到 {len(sources)} 个来源片段，写入记忆时一并保存。")

    try:
        _, write_msgs, _ = _run_capture(
            memory.record_qa, uid, query, topic, False, out.get("results", [])
        )
    except Exception as e:  # noqa: BLE001
        print(f"记忆写入异常：{e}")
        return False, f"记忆写入异常：{e}"

    try:
        review, read_msgs, _ = _run_capture(app_main.review_list, uid)
    except Exception as e:  # noqa: BLE001
        print(f"记忆读取异常：{e}")
        return False, f"记忆读取异常：{e}"

    write_ok = any(f"写入 user:{uid}:weak_points 成功" in m for m in write_msgs)
    read_ok = any(f"读取 user:{uid}:weak_points" in m for m in read_msgs)
    item = next((i for i in review.get("items", []) if i.get("topic") == topic), None)

    print("\nPowerContext 写入日志：")
    for m in write_msgs:
        print(f"  {m}")
    print("\nPowerContext 读取日志（模拟关闭页面后重新打开 /api/review）：")
    for m in read_msgs:
        print(f"  {m}")
    print(f"\n/api/review 返回：{json.dumps(review, ensure_ascii=False)}")
    if item:
        print(f"复习项：topic={item['topic']} count={item['count']} "
              f"source={item['source']} last_time={item['last_time']}")
    else:
        print(f"复习项：未找到 topic={topic}")

    ok = bool(item) and item.get("count") == 1 and write_ok and read_ok
    if not write_ok:
        reason = "PowerContext 写入未成功（检查 POWERCONTEXT_BASE_URL 与服务状态）"
    elif not read_ok:
        reason = "PowerContext 读取未成功"
    elif not item:
        reason = "复习清单里没有出现「聚簇索引」"
    elif item.get("count") != 1:
        reason = f"「聚簇索引」出现次数为 {item.get('count')}，期望 1"
    else:
        reason = "写入成功、重读命中、复习清单出现「聚簇索引」且次数=1"
    return ok, reason


# ------------------------------------------------------------
# 样例 4：提效对比
# ------------------------------------------------------------

def sample4(manual_seconds: float, k: int) -> tuple[bool, str]:
    query = "倒排索引为什么比顺序扫描快"
    print("\n" + "=" * WIDTH)
    print("样例 4/4 · 提效对比")
    print(f"输入：{query}")
    if manual_seconds > 0:
        print(f"手动基线：{manual_seconds:.1f} s（实测 3 次中位数）")
    else:
        print("手动基线：未实测（MANUAL_LOOKUP_SECONDS=0）")
    _bar()

    try:
        _run_capture(search.hybrid_search, query, k)  # 预热，不计入
        times = []
        for i in range(3):
            _, _, wall_ms = _run_capture(search.hybrid_search, query, k)
            times.append(wall_ms)
            print(f"  第 {i + 1} 次本项目耗时：{wall_ms:.1f} ms")
    except Exception as e:  # noqa: BLE001
        print(f"检索异常：{e}")
        return False, f"检索异常：{e}"

    median_ms = statistics.median(times)
    runs_ok = len(times) == 3 and all(t > 0 for t in times)
    print(f"\n本项目 3 次中位数：{median_ms:.1f} ms")

    if manual_seconds > 0:
        manual_ms = manual_seconds * 1000.0
        speedup = manual_ms / median_ms if median_ms > 0 else 0.0
        saved_s = (manual_ms - median_ms) / 1000.0
        print(f"手动翻 PDF 中位数：{manual_ms:.0f} ms（{manual_seconds:.1f} s）")
        print(f"提效倍数：{speedup:.1f}x | 单次节省约 {saved_s:.1f} s")
        ok = runs_ok and speedup > 1.0
        reason = (
            f"本项目中位数 {median_ms:.1f}ms vs 手动 {manual_ms:.0f}ms，"
            f"提效 {speedup:.1f}x"
        )
    else:
        print("手动基线未实测，不计算提效倍数")
        ok = runs_ok and median_ms > 0
        reason = (
            f"本项目 3 次均成功，中位数 {median_ms:.1f}ms；"
            "手动基线未实测，不计算提效倍数"
        )
    return ok, reason


# ------------------------------------------------------------
# 入口
# ------------------------------------------------------------

def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description="Recall（知返）4 个验证样例冒烟测试")
    parser.add_argument("--k", type=int, default=5, help="检索返回条数（默认 5）")
    parser.add_argument(
        "--manual-seconds",
        type=float,
        default=MANUAL_LOOKUP_SECONDS,
        help=f"实测手动翻 PDF 中位数秒数；0 表示未实测、不计算倍数（默认 {MANUAL_LOOKUP_SECONDS:g}）",
    )
    parser.add_argument(
        "--uid",
        default="",
        help="样例 3 使用的用户 ID；默认每次生成唯一 ID，保证「出现 1 次」可复现",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pymysql").setLevel(logging.WARNING)
    _install_capture()

    if not preflight():
        return 1

    uid = args.uid or f"smoke-{uuid.uuid4().hex[:8]}"
    if args.uid:
        print(f"[WARN] 指定了 --uid={uid}，若该用户之前已问过「聚簇索引」，次数可能大于 1。")

    results = []
    for name, fn, fn_args in (
        ("样例 1 检索+出处", sample1, (args.k,)),
        ("样例 2 混合检索", sample2, (args.k,)),
        ("样例 3 记忆复用", sample3, (uid,)),
        ("样例 4 提效对比", sample4, (args.manual_seconds, args.k)),
    ):
        try:
            ok, reason = fn(*fn_args)
        except Exception as e:  # noqa: BLE001  —— 单项异常不阻断其余样例
            ok, reason = False, f"未捕获异常：{e}"
        results.append((name, ok, reason))
        print(f"\n>>> {name}: {'PASS' if ok else 'FAIL'} —— {reason}")

    print("\n" + "=" * WIDTH)
    print("冒烟测试汇总")
    _bar()
    for name, ok, reason in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<14} {reason}")
    failed = [r for r in results if not r[1]]
    print("=" * WIDTH)
    if failed:
        print(f"结果：{len(failed)}/{len(results)} 项 FAIL，退出码 1")
        return 1
    print(f"结果：{len(results)}/{len(results)} 项 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
