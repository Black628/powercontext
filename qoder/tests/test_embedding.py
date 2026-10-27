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

import math

from app.embedding import HashEmbedder, OpenAIEmbedder, build_embedder


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def test_hash_embedder_is_unit_length_and_stable() -> None:
    first = HashEmbedder(64)
    second = HashEmbedder(64)
    [vector] = first.embed(["虚拟内存 缺页异常"])
    assert len(vector) == 64
    assert abs(_norm(vector) - 1.0) < 1e-6
    assert vector == second.embed(["虚拟内存 缺页异常"])[0]


def test_hash_embedder_separates_different_text() -> None:
    embedder = HashEmbedder(64)
    a, b = embedder.embed(["进程调度", "缺页异常"])
    assert a != b


def test_hash_embedder_handles_empty_text() -> None:
    embedder = HashEmbedder(16)
    [vector] = embedder.embed(["的 了 在"])
    assert vector == [0.0] * 16


def test_build_embedder_defaults_to_hash(settings) -> None:
    embedder = build_embedder(settings)
    assert isinstance(embedder, HashEmbedder)
    assert embedder.name == "builtin-hash-64"


def test_openai_embedder_normalises() -> None:
    vector = OpenAIEmbedder._normalise([2.0, 0.0])
    assert vector == [1.0, 0.0]
