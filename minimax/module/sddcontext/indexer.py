"""工程扫描与切片：specs/*.md、源码、测试 -> 带 path:line 溯源的切片。"""

from __future__ import annotations

import ast
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .util import read_json, relpath

DEFAULT_PATTERNS = {
    "specs": ["specs/**/*.md"],
    "code": ["src/**/*.py"],
    "tests": ["tests/**/*.py"],
}

SPEC_ID_RE = re.compile(r"SPEC-[0-9]{1,3}")
SPEC_VERSION_RE = re.compile(r"规格\s*(v[0-9]+(?:\.[0-9]+)?)")
CODE_SUFFIXES = {".py", ".js", ".ts", ".java", ".go"}
SKIP_DIRS = {".git", "__pycache__", ".state", "node_modules", ".venv", "venv", "build", "dist"}


@dataclass
class Chunk:
    kind: str            # spec | code | test
    path: str            # 相对工程根目录
    line_start: int
    line_end: int
    symbol: Optional[str]
    content: str
    spec_version: Optional[str] = None
    meta: Dict[str, str] = field(default_factory=dict)

    @property
    def doc_key(self) -> str:
        if self.symbol:
            return f"{self.kind}:{self.path}#{self.symbol}"
        return f"{self.kind}:{self.path}:{self.line_start}"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def as_row(self) -> dict:
        return {
            "doc_key": self.doc_key,
            "kind": self.kind,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "symbol": self.symbol,
            "content": self.content,
            "content_hash": self.content_hash,
            "spec_version": self.spec_version,
        }


def load_patterns(project_root: Path) -> Dict[str, List[str]]:
    manifest = read_json(project_root / "sdd.json", None)
    patterns = dict(DEFAULT_PATTERNS)
    if isinstance(manifest, dict):
        for key in ("specs", "code", "tests"):
            value = manifest.get(key)
            if isinstance(value, list) and value:
                patterns[key] = [str(v) for v in value]
    return patterns


def pinned_test_paths(project_root: Path) -> List[str]:
    """sdd.json 中 `always_include_tests` 声明的兼容性测试（相对路径），始终并入上下文。"""
    manifest = read_json(project_root / "sdd.json", None)
    if not isinstance(manifest, dict):
        return []
    value = manifest.get("always_include_tests")
    if not isinstance(value, list):
        return []
    return [str(v).replace("\\", "/") for v in value]


def collect_files(project_root: Path, patterns: Sequence[str]) -> List[Path]:
    seen: Dict[str, Path] = {}
    for pattern in patterns:
        for path in sorted(project_root.glob(pattern)):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            seen[path.as_posix()] = path
    return [seen[k] for k in sorted(seen)]


# --------------------------------------------------------------------- specs
def chunk_markdown_spec(project_root: Path, path: Path) -> List[Chunk]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    version = None
    for line in lines:
        m = SPEC_VERSION_RE.search(line)
        if m:
            version = m.group(1)
            break
    rel = relpath(path, project_root)
    # 按 "## " 二级标题切分；首个二级标题之前的内容（含 H1）单独成块
    starts: List[int] = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not starts:
        return [Chunk("spec", rel, 1, max(1, len(lines)), None, text, version)]
    chunks: List[Chunk] = []
    if starts[0] > 0:
        head_end = starts[0]
        chunks.append(Chunk("spec", rel, 1, head_end, None,
                            "\n".join(lines[:head_end]).rstrip(), version))
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        block_end = end
        while block_end > start + 1 and not lines[block_end - 1].strip():
            block_end -= 1
        body = "\n".join(lines[start:block_end]).rstrip()
        heading = lines[start].lstrip("# ").strip()
        m = SPEC_ID_RE.search(body)
        symbol = m.group(0) if m else heading
        chunks.append(Chunk("spec", rel, start + 1, block_end, symbol, body, version))
    return [c for c in chunks if c.content.strip()]


# ---------------------------------------------------------------- 代码 / 测试
def _node_start(node: ast.AST) -> int:
    lineno = getattr(node, "lineno", 1)
    for deco in getattr(node, "decorator_list", []) or []:
        lineno = min(lineno, getattr(deco, "lineno", lineno))
    return lineno


def _segment(source_lines: Sequence[str], start: int, end: int) -> str:
    return "\n".join(source_lines[start - 1:end]).rstrip()


def _looks_like_test(name: str) -> bool:
    return name.startswith("test")


def chunk_python(project_root: Path, path: Path, kind: str) -> List[Chunk]:
    rel = relpath(path, project_root)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [Chunk(kind, rel, 1, max(1, len(lines)), path.stem, text)]
    chunks: List[Chunk] = []
    module_start = _node_start(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _node_start(node), getattr(node, "end_lineno", _node_start(node))
            chunks.append(Chunk(kind, rel, start, end, node.name, _segment(lines, start, end)))
        elif isinstance(node, ast.ClassDef):
            class_start = _node_start(node)
            class_end = getattr(node, "end_lineno", class_start)
            methods = [n for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if kind == "test" and methods:
                for method in methods:
                    if not _looks_like_test(method.name):
                        continue
                    start = _node_start(method)
                    end = getattr(method, "end_lineno", start)
                    symbol = f"{node.name}.{method.name}"
                    chunks.append(Chunk(kind, rel, start, end, symbol, _segment(lines, start, end)))
            else:
                chunks.append(Chunk(kind, rel, class_start, class_end, node.name,
                                    _segment(lines, class_start, class_end)))
    if not chunks:
        chunks.append(Chunk(kind, rel, module_start, max(module_start, len(lines)),
                            path.stem, text))
    return chunks


def chunk_file(project_root: Path, path: Path, kind: str) -> List[Chunk]:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return chunk_markdown_spec(project_root, path)
    if suffix == ".py":
        return chunk_python(project_root, path, kind)
    if suffix in CODE_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        return [Chunk(kind, relpath(path, project_root), 1, max(1, len(lines)), path.stem, text)]
    return []


# ---------------------------------------------------------------- 扫描主入口
def scan_project(project_root: Path) -> Dict[str, List[Chunk]]:
    patterns = load_patterns(project_root)
    result: Dict[str, List[Chunk]] = {"spec": [], "code": [], "test": []}
    for group, kind in (("specs", "spec"), ("code", "code"), ("tests", "test")):
        for path in collect_files(project_root, patterns.get(group, [])):
            try:
                result[kind].extend(chunk_file(project_root, path, kind))
            except Exception as exc:  # noqa: BLE001
                print(f"[index] 跳过 {path}: {exc}")
    return result


def group_paths(chunks: Iterable[Chunk]) -> Dict[str, List[Chunk]]:
    grouped: Dict[str, List[Chunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.path, []).append(chunk)
    return grouped


def make_run_id() -> str:
    return time.strftime("%Y%m%d%H%M%S") + "-" + hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]
