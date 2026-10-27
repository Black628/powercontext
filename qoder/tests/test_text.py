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

from __future__ import annotations

from app.text import chunk_text, keywords, snippet, squeeze, tokenize


def test_squeeze_collapses_whitespace() -> None:
    assert squeeze("  虚拟内存\n\n与页面置换\t") == "虚拟内存 与页面置换"


def test_tokenize_mixes_cjk_bigrams_and_words() -> None:
    tokens = tokenize("进程与线程 context switch")
    assert "进程" in tokens
    assert "线程" in tokens
    assert "context" in tokens
    assert "switch" in tokens
    assert "与" not in tokens


def test_keywords_prefer_whole_runs() -> None:
    words = keywords("虚拟内存与页面置换 缺页异常")
    assert "虚拟内存" in words or "内存" in words
    assert len(words) <= 8


def test_chunk_text_overlaps_and_bounds() -> None:
    body = "".join(f"第{i}段讲进程调度与内存管理。" for i in range(60))
    chunks = chunk_text(body, size=120, overlap=30)
    assert len(chunks) > 2
    assert all(len(chunk) <= 120 for chunk in chunks)
    # Adjacent chunks overlap by exactly ``overlap`` characters.
    assert chunks[0][-30:] == chunks[1][:30]


def test_chunk_text_short_body_single_chunk() -> None:
    assert chunk_text("只有一个短句。") == ["只有一个短句。"]


def test_snippet_centres_on_query() -> None:
    text = "前文" * 100 + "虚拟内存缺页异常" + "后文" * 100
    out = snippet(text, "缺页异常", size=60)
    assert "缺页异常" in out
    assert out.startswith("…") and out.endswith("…")


def test_snippet_returns_short_text_unchanged() -> None:
    assert snippet("短笔记", "笔记") == "短笔记"
