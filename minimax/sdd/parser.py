"""Parsers that turn specs, source files and tests into index rows.

The parsers deliberately keep the source location (path plus line range) of
every row so the context output can cite real files, and so an ingest can be
checked against the working tree later.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SPEC_HEADING = re.compile(r"^##\s+(S-\d{2})\s+(.+?)\s*$")
COVERS = re.compile(r"[Cc]overs?:\s*([^\n]+)")
TEST_NAME = re.compile(r"^test_")


@dataclass(frozen=True)
class Clause:
    clause_id: str
    title: str
    body: str
    path: str
    line_start: int
    line_end: int
    digest: str


@dataclass(frozen=True)
class Symbol:
    symbol_id: str
    kind: str
    qualname: str
    path: str
    line_start: int
    line_end: int
    signature: str
    docstring: str
    clause_refs: str


@dataclass(frozen=True)
class TestFunction:
    test_id: str
    name: str
    path: str
    line_start: int
    line_end: int
    docstring: str
    covers: str


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(path: Path, root: Path) -> str:
    """Return a root-relative POSIX path, or the absolute path for external workspaces."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def parse_specs(workspace: Path, root: Path) -> list[Clause]:
    """Parse ``## S-XX title`` clauses out of every Markdown file under ``specs/``."""
    clauses: list[Clause] = []
    for spec_path in sorted((workspace / "specs").glob("*.md")):
        lines = spec_path.read_text(encoding="utf-8").splitlines()
        headings: list[tuple[int, str, str]] = []
        for index, line in enumerate(lines, start=1):
            match = SPEC_HEADING.match(line)
            if match:
                headings.append((index, match.group(1), match.group(2)))
        for position, (line_start, clause_id, title) in enumerate(headings):
            body_start = line_start  # first line after the heading (1-based)
            body_end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
            body_lines = lines[body_start:body_end]
            while body_lines and not body_lines[-1].strip():
                body_lines.pop()
            body = "\n".join(body_lines).strip()
            clauses.append(
                Clause(
                    clause_id=clause_id,
                    title=title.strip(),
                    body=body,
                    path=_relative(spec_path, root),
                    line_start=line_start,
                    line_end=body_start + len(body_lines) if body_lines else line_start,
                    digest=_digest(body),
                )
            )
    return clauses


def clause_digest_at(path: Path, line_start: int, line_end: int) -> str:
    """Recompute the digest of a clause from the current file content."""
    lines = path.read_text(encoding="utf-8").splitlines()
    body_lines = lines[line_start:line_end]
    body = "\n".join(body_lines).strip()
    return _digest(body)


def parse_code(workspace: Path, root: Path) -> list[Symbol]:
    """Parse top-level functions, classes and class methods with their docstrings."""
    symbols: list[Symbol] = []
    for source_path in sorted((workspace / "src").rglob("*.py")):
        if "__pycache__" in source_path.parts:
            continue
        text = source_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        relative = _relative(source_path, root)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(_function_symbol(node, relative, prefix=""))
            elif isinstance(node, ast.ClassDef):
                symbols.append(_class_symbol(node, relative))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(_function_symbol(child, relative, prefix=f"{node.name}."))
    return symbols


def _function_symbol(node: ast.FunctionDef | ast.AsyncFunctionDef, path: str, *, prefix: str) -> Symbol:
    qualname = f"{prefix}{node.name}"
    return Symbol(
        symbol_id=f"{path}::{qualname}",
        kind="function",
        qualname=qualname,
        path=path,
        line_start=node.lineno,
        line_end=node.end_lineno or node.lineno,
        signature=f"{node.name}({ast.unparse(node.args)})",
        docstring=ast.get_docstring(node, clean=True) or "",
        clause_refs=",".join(sorted(set(re.findall(r"S-\d{2}", ast.get_docstring(node) or "")))),
    )


def _class_symbol(node: ast.ClassDef, path: str) -> Symbol:
    return Symbol(
        symbol_id=f"{path}::{node.name}",
        kind="class",
        qualname=node.name,
        path=path,
        line_start=node.lineno,
        line_end=node.end_lineno or node.lineno,
        signature=f"class {node.name}",
        docstring=ast.get_docstring(node, clean=True) or "",
        clause_refs=",".join(sorted(set(re.findall(r"S-\d{2}", ast.get_docstring(node) or "")))),
    )


def parse_tests(workspace: Path, root: Path) -> list[TestFunction]:
    """Parse ``test_*`` functions, their docstrings and their ``Covers:`` tags."""
    tests: list[TestFunction] = []
    for test_path in sorted((workspace / "tests").glob("test_*.py")):
        text = test_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        relative = _relative(test_path, root)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not TEST_NAME.match(node.name):
                continue
            docstring = ast.get_docstring(node, clean=True) or ""
            match = COVERS.search(docstring)
            covers = ",".join(sorted(set(re.findall(r"S-\d{2}", match.group(1))))) if match else ""
            tests.append(
                TestFunction(
                    test_id=f"{relative}::{node.name}",
                    name=node.name,
                    path=relative,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    docstring=docstring,
                    covers=covers,
                )
            )
    return tests
