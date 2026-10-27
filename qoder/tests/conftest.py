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

"""Shared fixtures for the offline unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.embedding import HashEmbedder  # noqa: E402
from app.store.mirror import MirrorStore  # noqa: E402


def make_settings(data_dir: Path, **overrides: Any) -> Settings:
    base = dict(
        data_dir=data_dir,
        embedding_dim=64,
        embedding_kind="hash",
        embedding_api_base="",
        embedding_api_key="",
        embedding_model="",
        seekdb_path=None,
        seekdb_uri="",
        seekdb_user="root",
        seekdb_password="",
        seekdb_db="recall_coach",
        powercontext_mode="embedded",
        powercontext_url="",
        powercontext_workspace=data_dir / "powercontext",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        learner_name="测试同学",
        backend_preference="auto",
        http_host="127.0.0.1",
        http_port=0,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def store(settings: Settings):
    instance = MirrorStore(settings, HashEmbedder(settings.embedding_dim))
    instance.open()
    try:
        yield instance
    finally:
        instance.close()


class FakeMemory:
    """Duck-typed stand-in for ``LearnerMemory`` without an HTTP server."""

    available = True
    error = ""
    scope_id = "scp_test"
    base_url = "embedded"

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._evidence: list[dict[str, Any]] = []
        self._seq = 0

    def describe(self) -> dict[str, Any]:
        return {
            "available": True,
            "mode": "fake",
            "base_url": self.base_url,
            "scope_id": self.scope_id,
            "sqlite_module": "test",
            "error": "",
            "evidence": [],
        }

    def log(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._evidence[-limit:]

    async def prepare(self, query: str, max_bytes: int = 6000) -> dict[str, Any]:
        return {"status": "assembled", "content": "注入的上下文", "content_bytes": 18}

    async def entries(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        rows = self._entries if include_inactive else [row for row in self._entries if row["state"] == "active"]
        return [dict(row) for row in rows]

    async def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        return [
            {"text": row["text"], "score": 0.5, "matched_by": ["fts"]}
            for row in await self.entries()
        ][:limit]

    async def remember(self, kind: str, text: str, reason: str = "") -> dict[str, Any]:
        self._seq += 1
        entry = {
            "artifact": {"family": "atomic_memory", "artifact_id": f"am_{self._seq}"},
            "kind": kind,
            "text": text,
            "state": "active",
            "state_version": 1,
        }
        self._entries.append(entry)
        return {"entry": dict(entry)}

    async def forget(self, artifact: dict[str, Any], state_version: int) -> dict[str, Any]:
        for row in self._entries:
            if row["artifact"] == artifact:
                row["state"] = "forgotten"
                row["state_version"] = int(row.get("state_version") or 0) + 1
        return {"state": "forgotten"}


@pytest.fixture()
def memory() -> FakeMemory:
    return FakeMemory()


@pytest.fixture()
def offline_memory():
    class Offline:
        available = False
        error = "PowerContext 不可用（单元测试）"
        scope_id = ""
        base_url = "embedded"

        def describe(self) -> dict[str, Any]:
            return {"available": False, "error": self.error}

        def log(self, limit: int = 30) -> list[dict[str, Any]]:
            return []

        async def entries(self, include_inactive: bool = False) -> list[dict[str, Any]]:
            return []

        async def prepare(self, query: str, max_bytes: int = 6000) -> dict[str, Any]:
            raise RuntimeError("offline")

        async def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
            return []

        async def remember(self, kind: str, text: str, reason: str = "") -> dict[str, Any]:
            raise RuntimeError("offline")

        async def forget(self, artifact: dict[str, Any], state_version: int) -> dict[str, Any]:
            raise RuntimeError("offline")

    return Offline()
