"""通用工具：路径、日志、JSON 读写、时间戳。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
#: minimax/ 根目录（module/sddcontext/util.py -> module/sddcontext -> module -> minimax）
PACKAGE_ROOT = MODULE_DIR.parent          # minimax/module
DEFAULT_HOME = PACKAGE_ROOT.parent        # minimax/


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default=None):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        return json.loads(text)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    ensure_dir(Path(path).parent)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    ensure_dir(Path(path).parent)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def append_jsonl(path: Path, record: dict) -> None:
    ensure_dir(Path(path).parent)
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def relpath(path, base) -> str:
    """尽量输出工程根目录相对路径（用于来源标注）。"""
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [截断，原长 {len(text)} 字符]"


def env(name: str, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def print_err(msg: str) -> None:
    import sys
    print(msg, file=sys.stderr)
