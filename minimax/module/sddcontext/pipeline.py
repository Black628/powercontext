"""可复用的流程片段：建库、索引、上下文构建、基线恢复（CLI 与 demo 共用）。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config, db_summary
from .context import build_context, task_id_for
from .db import SeekDB
from .indexer import group_paths, make_run_id, scan_project
from .state import StateError, open_store
from .util import ensure_dir, now_iso, print_err, write_json


def connect_db(cfg: Config, ensure: bool = False, recreate: bool = False,
               create_db: bool = True, echo: bool = True) -> SeekDB:
    db = SeekDB(cfg, echo=echo)
    if ensure:
        db.ensure_schema(recreate=recreate)
    else:
        db.connect(create_db=create_db)
    return db


def existing_hashes(db: SeekDB) -> Dict[str, str]:
    rows = db.fetch("SELECT doc_key, content_hash FROM sdd_docs",
                    note="index: 读取已有切片指纹")
    return {r[0]: r[1] for r in rows}


def do_index(cfg: Config, db: SeekDB, note: str = "index") -> dict:
    started = now_iso()
    run_id = make_run_id()
    chunks = scan_project(cfg.project_root)
    existing = existing_hashes(db)
    inserted = updated = unchanged = deleted = 0
    for kind, items in chunks.items():
        for path, file_chunks in group_paths(items).items():
            rows, keys = [], []
            for chunk in file_chunks:
                row = chunk.as_row()
                keys.append(row["doc_key"])
                if existing.get(row["doc_key"]) == row["content_hash"]:
                    unchanged += 1
                    continue
                rows.append(row)
            if rows:
                db.upsert_docs(rows, run_id)
                inserted += sum(1 for r in rows if existing.get(r["doc_key"]) is None)
                updated += sum(1 for r in rows if existing.get(r["doc_key"]) is not None)
            deleted += db.delete_stale(kind, path, keys)
    counts = db.count_by_kind()
    total = db.total_docs()
    db.record_run({
        "run_id": run_id,
        "project_root": str(cfg.project_root),
        "spec_count": len({c.path for c in chunks["spec"]}),
        "code_count": len({c.path for c in chunks["code"]}),
        "test_count": len({c.path for c in chunks["test"]}),
        "doc_count": total,
        "started_at": started,
        "finished_at": now_iso(),
        "note": note,
    })
    result = {
        "run_id": run_id,
        "project_root": str(cfg.project_root),
        "files": {
            "specs": sorted({c.path for c in chunks["spec"]}),
            "code": sorted({c.path for c in chunks["code"]}),
            "tests": sorted({c.path for c in chunks["test"]}),
        },
        "chunks": {k: len(v) for k, v in chunks.items()},
        "write": {"inserted": inserted, "updated": updated, "unchanged": unchanged,
                  "deleted_stale": deleted},
        "db_counts_by_kind": counts,
        "db_total": total,
        "spec_version_seen": sorted({c.spec_version for c in chunks["spec"] if c.spec_version}),
    }
    write_json(cfg.state_dir / "last-index.json", result)
    return result


def build_context_for(cfg: Config, db: SeekDB, task: str) -> dict:
    from .indexer import pinned_test_paths
    from .retriever import retrieve
    retrieval = retrieve(db, task, pinned_test_paths=pinned_test_paths(cfg.project_root))
    meta = db_summary(cfg)
    meta["total_docs"] = db.total_docs()
    meta["counts_by_kind"] = db.count_by_kind()
    meta["fulltext_parser"] = db.parser
    store = open_store(cfg, quiet=True)
    state = None
    try:
        state = store.load(task_id_for(task))
    except StateError as exc:
        print_err(f"[state] 读取失败：{exc}")
    ctx = build_context(cfg, db, retrieval, state=state, db_meta=meta)
    if state is None:
        ctx["state"]["backend"] = store.backend_name
    return ctx


def restore_baseline(cfg: Config, spec_version: str = "v2") -> List[str]:
    """把 demo_project 恢复到“修改前”快照，并把规格切换到指定版本。"""
    root = cfg.project_root
    snapshots = root / "snapshots" / "before"
    if not snapshots.exists():
        raise FileNotFoundError(f"缺少基线快照目录：{snapshots}")
    copied: List[str] = []
    for src in sorted(snapshots.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(snapshots)
        dst = root / rel
        ensure_dir(dst.parent)
        shutil.copyfile(src, dst)
        copied.append(rel.as_posix())
    spec_src = cfg.sample_dir / "specs" / f"discount.{spec_version}.md"
    if spec_src.exists():
        ensure_dir(root / "specs")
        shutil.copyfile(spec_src, root / "specs" / "discount.md")
        copied.append(f"specs/discount.md <- sample/specs/discount.{spec_version}.md")
    return copied
