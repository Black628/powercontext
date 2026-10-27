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

"""Embedding providers.

Two providers ship with the demo:

``hash``
    A deterministic hashed-feature embedder over CJK bigrams and words. No key,
    no network, unit length. Lexical overlap between query and passage still
    produces meaningful cosine similarity, which keeps the whole demo runnable
    on a laptop and on the CI machine.

``openai``
    Any OpenAI-compatible ``/embeddings`` endpoint. Set the base URL, key and
    model; the dimension is derived once from a probe call.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx

from .config import Settings
from .text import tokenize


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Hashed n-gram features, L2 normalised, stable across processes."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.name = f"builtin-hash-{dim}"

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = tokenize(text)
        if not tokens:
            return vector
        weights: dict[str, float] = {}
        for token in tokens:
            weights[token] = weights.get(token, 0.0) + 1.0
        for token, weight in weights.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            position = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            # Sub-linear term frequency keeps long passages from dominating.
            vector[position] += sign * (1.0 + math.log(weight))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class OpenAIEmbedder:
    """OpenAI-compatible embeddings client with batching and unit normalisation."""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int, batch: int = 16) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.batch = batch
        self.name = f"openai:{model}"

    def _post(self, inputs: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": inputs, "encoding_format": "float"},
            timeout=60.0,
        )
        response.raise_for_status()
        rows = response.json()["data"]
        vectors = [row["embedding"] for row in sorted(rows, key=lambda row: row["index"])]
        if vectors and len(vectors[0]) != self.dim:
            raise RuntimeError(f"embedding dimension mismatch: expected {self.dim}, got {len(vectors[0])}")
        return [self._normalise(vector) for vector in vectors]

    @staticmethod
    def _normalise(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return list(vector)
        return [value / norm for value in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch):
            vectors.extend(self._post(texts[start : start + self.batch]))
        return vectors


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_kind == "openai" and settings.embedding_api_key and settings.embedding_model:
        return OpenAIEmbedder(
            base_url=settings.embedding_api_base or "https://api.openai.com/v1",
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )
    return HashEmbedder(settings.embedding_dim)
