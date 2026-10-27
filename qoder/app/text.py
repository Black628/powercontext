# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Small text helpers shared by both storage backends.

The same tokenisation feeds three consumers:

* the builtin hash embedder (lexical similarity without an API key),
* the SQLite mirror backend's lexical channel,
* query highlighting on the client.
"""

from __future__ import annotations

import re

_CN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.-]*|\d+(?:\.\d+)?")
_SPACE = re.compile(r"\s+")

STOP_WORDS = frozenset(
    "的 了 和 是 在 就 都 而 及 与 着 或 一个 我们 他们 它 这 那 你 我 他 她 呀 吧 吗 呢 啊 么 之 其 也 并 很 更".split()
)


def squeeze(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def tokenize(text: str) -> list[str]:
    """Split mixed Chinese/English text into overlapping CJK bigrams and words."""
    tokens: list[str] = []
    for chunk in _CN.findall(text):
        if len(chunk) == 1:
            tokens.append(chunk)
            continue
        tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    for word in _WORD.findall(text):
        token = word.lower()
        if token not in STOP_WORDS:
            tokens.append(token)
    return [token for token in tokens if token not in STOP_WORDS]


def keywords(text: str, limit: int = 8) -> list[str]:
    """Cheap keyword extraction: frequency over tokens, Chinese runs kept whole."""
    counts: dict[str, int] = {}
    for chunk in _CN.findall(text):
        if 2 <= len(chunk) <= 8:
            counts[chunk] = counts.get(chunk, 0) + 3
    for token in tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0])))
    return [token for token, _ in ranked[:limit]]


def snippet(text: str, query: str, size: int = 180) -> str:
    """Return a window of ``text`` centred on the first query hit."""
    plain = squeeze(text)
    if len(plain) <= size:
        return plain
    lowered = plain.lower()
    best = -1
    for token in tokenize(query):
        position = lowered.find(token)
        if position >= 0 and (best < 0 or position < best):
            best = position
    if best < 0:
        return plain[:size] + "…"
    start = max(0, best - size // 3)
    end = min(len(plain), start + size)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(plain) else ""
    return f"{head}{plain[start:end]}{tail}"


def chunk_text(text: str, size: int = 420, overlap: int = 80) -> list[str]:
    """Split long material bodies into overlapping passages for retrieval."""
    plain = squeeze(text)
    if len(plain) <= size:
        return [plain] if plain else []
    chunks: list[str] = []
    step = max(1, size - overlap)
    for start in range(0, len(plain), step):
        piece = plain[start : start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(plain):
            break
    return chunks
