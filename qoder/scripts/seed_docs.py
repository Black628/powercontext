"""导入 samples/ 下的样例文档到 seekdb。

流程：读取文档 → 按约 500 字切块（与 /api/docs 使用同一套切块器）→
embedding → 写入 recall.chunks，并登记 recall.docs。

用法（在 qoder/ 目录或任意目录均可）：
    python scripts/seed_docs.py
    python scripts/seed_docs.py --chunk-size 500 --samples-dir samples

同一篇文档重复导入时会先清掉旧块，保证样例数据可重复复现。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

# 配置必须在 import app.* 之前加载：app/embed.py、app/store.py 在导入时读环境变量
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "app" / ".env")

import pymysql  # noqa: E402

from app import embed, store  # noqa: E402
from app import main as app_main  # noqa: E402

SUPPORTED_SUFFIXES = {".md", ".txt"}


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


def _purge_doc(doc_id: str, doc_name: str) -> tuple:
    """删除同名文档的旧块与登记行，避免重复导入产生脏数据。"""
    conn = store.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chunks WHERE doc_id=%s OR doc_name=%s", (doc_id, doc_name)
            )
            chunks = cur.rowcount
            cur.execute("DELETE FROM docs WHERE doc_id=%s OR doc_name=%s", (doc_id, doc_name))
            docs = cur.rowcount
        return chunks, docs
    finally:
        conn.close()


def _count_chunks(doc_id: str) -> int:
    conn = store.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM chunks WHERE doc_id=%s", (doc_id,))
            return int(cur.fetchone()["n"])
    finally:
        conn.close()


def _iter_sample_files(samples_dir: Path) -> list:
    if not samples_dir.is_dir():
        raise SystemExit(f"[seed] 样例目录不存在：{samples_dir}")
    files = [p for p in samples_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES]
    if not files:
        raise SystemExit(f"[seed] {samples_dir} 下没有 .md/.txt 文档")
    return sorted(files, key=lambda p: p.name)


def _seed_one(path: Path, chunk_size: int) -> dict:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    name = path.name
    doc_id = hashlib.sha1(f"{name}:{len(raw)}".encode("utf-8")).hexdigest()[:16]

    removed_chunks, removed_docs = _purge_doc(doc_id, name)
    store.register_doc(doc_id, name, status="indexing")

    t0 = time.perf_counter()
    pieces = app_main._chunk_text(text)
    vectors = embed.embed_texts([c for _, c in pieces])
    rows = [
        {
            "doc_id": doc_id,
            "doc_name": name,
            "page": 0,
            "offset_in_doc": off,
            "content": content,
            "embedding": vec,
        }
        for (off, content), vec in zip(pieces, vectors)
    ]
    store.insert_chunks(rows)
    store.finish_doc(doc_id, len(rows), "ready")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    stored = _count_chunks(doc_id)
    if stored != len(rows):
        store.finish_doc(doc_id, stored, "failed", f"写入数量不一致：期望 {len(rows)}，实际 {stored}")
        raise SystemExit(f"[seed] {name} 写入校验失败：期望 {len(rows)} 块，实际 {stored} 块")

    return {
        "doc_id": doc_id,
        "doc_name": name,
        "chars": len(text),
        "chunks": len(rows),
        "elapsed_ms": elapsed_ms,
        "offsets": [off for off, _ in pieces],
        "removed_chunks": removed_chunks,
        "removed_docs": removed_docs,
    }


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description="导入 samples/ 文档到 seekdb")
    parser.add_argument(
        "--samples-dir",
        default=str(ROOT / "samples"),
        help="样例文档目录（默认 qoder/samples）",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=app_main.CHUNK_SIZE,
        help=f"切块字符数（默认 {app_main.CHUNK_SIZE}，与 CHUNK_SIZE 环境变量一致）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pymysql").setLevel(logging.WARNING)

    # /api/docs 的后台索引使用 app.main.CHUNK_SIZE；这里同步覆盖，保证两条导入路径切块一致
    app_main.CHUNK_SIZE = args.chunk_size

    samples_dir = Path(args.samples_dir).expanduser().resolve()
    files = _iter_sample_files(samples_dir)

    print("=" * 78)
    print("Recall（知返）· 样例导入")
    print(f"seekdb : {store.SEEKDB_HOST}:{store.SEEKDB_PORT}/{store.SEEKDB_DATABASE}")
    print(f"embed  : {embed.get_embedder().name} (dim={embed.get_embedder().dim})")
    print(f"samples: {samples_dir}  ({len(files)} 篇)")
    print(f"chunk  : 约 {args.chunk_size} 字/块")
    print("=" * 78)

    try:
        store.init_schema()
    except pymysql.err.MySQLError as e:
        print(f"\n[FAIL] 无法初始化 seekdb：{e}")
        print("       请确认 seekdb 已启动（默认 127.0.0.1:2881），并检查 .env 中的连接配置。")
        return 1

    total_chunks = 0
    total_ms = 0.0
    try:
        for path in files:
            info = _seed_one(path, args.chunk_size)
            total_chunks += info["chunks"]
            total_ms += info["elapsed_ms"]
            print(
                f"\n[OK] {info['doc_name']}\n"
                f"     doc_id={info['doc_id']}  字数={info['chars']}  "
                f"块数={info['chunks']}  耗时={info['elapsed_ms']:.1f} ms\n"
                f"     块偏移={info['offsets']}\n"
                f"     清理旧数据：chunks={info['removed_chunks']} docs={info['removed_docs']}"
            )
    except pymysql.err.MySQLError as e:
        print(f"\n[FAIL] 写入 seekdb 失败：{e}")
        return 1

    print("\n" + "=" * 78)
    print(f"导入完成：{len(files)} 篇，共 {total_chunks} 块，总耗时 {total_ms:.1f} ms")
    try:
        for d in store.list_docs():
            print(
                f"  - {d['doc_name']:<24} status={d['status']:<8} "
                f"chunks={d['chunk_count']} doc_id={d['doc_id']}"
            )
        s = store.stats()
        print(f"库内合计：docs={s['doc_count']} chunks={s['chunk_count']}")
    except pymysql.err.MySQLError as e:
        print(f"[WARN] 读取导入结果失败：{e}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
