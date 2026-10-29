"""unified diff 解析与应用（不依赖 patch/git 命令，纯 Python 实现）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(RuntimeError):
    pass


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: List[Tuple[str, str]] = field(default_factory=list)  # (op, text) op in ' ', '-', '+'


@dataclass
class FilePatch:
    old_path: str
    new_path: str
    hunks: List[Hunk] = field(default_factory=list)


def _clean_path(raw: str) -> str:
    path = raw.strip().split("\t")[0].strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    if path == "/dev/null":
        return path
    if path.startswith("/") or ".." in Path(path).parts:
        raise PatchError(f"非法路径（必须是工程内相对路径）：{raw}")
    return path


def extract_diff(text: str) -> str:
    """从模型回复中抽出 diff 本体（容忍 ```diff 围栏与前后说明文字）。"""
    if not text:
        return ""
    fence = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", text, re.S)
    candidate = fence.group(1) if fence else text
    lines = candidate.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("--- ") and idx + 1 < len(lines) and lines[idx + 1].startswith("+++ "):
            start = idx
            break
        if line.startswith("diff --git "):
            start = idx
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(len(lines) - 1, start, -1):
        line = lines[idx]
        if line.strip() in ("", "```"):
            end = idx
            continue
        break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def parse_patch(patch_text: str) -> List[FilePatch]:
    if not patch_text.strip():
        raise PatchError("patch 为空")
    patches: List[FilePatch] = []
    current: Optional[FilePatch] = None
    hunk: Optional[Hunk] = None
    for raw in patch_text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            current = None
            hunk = None
            continue
        if line.startswith("--- "):
            old = _clean_path(line[4:])
            current = FilePatch(old_path=old, new_path=old)
            patches.append(current)
            hunk = None
            continue
        if line.startswith("+++ ") and current is not None:
            current.new_path = _clean_path(line[4:])
            continue
        m = HUNK_RE.match(line)
        if m:
            if current is None:
                raise PatchError("hunk 出现在文件头之前")
            hunk = Hunk(
                old_start=int(m.group(1)),
                old_len=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_len=int(m.group(4) or 1),
            )
            current.hunks.append(hunk)
            continue
        if hunk is None:
            continue
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            hunk.lines.append(("+", line[1:]))
        elif line.startswith("-"):
            hunk.lines.append(("-", line[1:]))
        elif line.startswith(" ") or line == "":
            hunk.lines.append((" ", line[1:] if line.startswith(" ") else ""))
        elif line.startswith("index ") or line.startswith("new file mode"):
            continue
        else:
            # 非 diff 行（模型多写的解释），忽略
            continue
    patches = [p for p in patches if p.hunks]
    if not patches:
        raise PatchError("patch 中没有可应用的 hunk")
    return patches


def apply_file_patch(root: Path, file_patch: FilePatch) -> str:
    target = root / file_patch.new_path
    old_exists = (root / file_patch.old_path).exists()
    if file_patch.old_path == "/dev/null":
        original_lines: List[str] = []
    else:
        if not target.exists():
            raise PatchError(f"目标文件不存在：{file_patch.new_path}")
        original_lines = target.read_text(encoding="utf-8").splitlines()

    lines = list(original_lines)
    offset = 0
    for hunk in file_patch.hunks:
        expected = hunk.old_start - 1 + offset
        removes = [text for op, text in hunk.lines if op in (" ", "-")]
        inserted = [text for op, text in hunk.lines if op in (" ", "+")]
        # 允许少量偏移：上下 20 行内寻找匹配
        position = _locate(lines, removes, expected)
        if position is None:
            raise PatchError(
                f"hunk 上下文不匹配：{file_patch.new_path} @@ -{hunk.old_start},{hunk.old_len} @@"
            )
        lines[position:position + len(removes)] = inserted
        offset += len(inserted) - len(removes)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    return "created" if not old_exists else "modified"


def _locate(lines: List[str], block: List[str], expected: int) -> Optional[int]:
    if not block:
        return max(0, min(expected, len(lines)))
    candidates = [expected]
    for delta in range(1, 21):
        candidates.extend([expected - delta, expected + delta])
    for pos in candidates:
        if pos < 0 or pos + len(block) > len(lines):
            continue
        if lines[pos:pos + len(block)] == block:
            return pos
    return None


def apply_patch(root: Path, patch_text: str) -> Dict[str, str]:
    """应用 patch，返回 {相对路径: modified|created}；任一文件失败则抛出 PatchError。"""
    patches = parse_patch(patch_text)
    result: Dict[str, str] = {}
    for file_patch in patches:
        result[file_patch.new_path] = apply_file_patch(root, file_patch)
    return result


def diff_stats(patch_text: str) -> Dict[str, int]:
    added = removed = 0
    for line in patch_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed, "files": len(parse_patch(patch_text))}
