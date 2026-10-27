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

"""Learner memory backed by PowerContext.

PowerContext owns the *durable facts about the learner*: which topics are
weak, what kind of hints work, how far a course has progressed. seekdb owns
the *study material* and answers "which passages are relevant to this query".
The two never trade places: retrieval results become memory only after the
learner answers a card, and memory only steers the next retrieval.

Two deployment shapes are supported:

``embedded`` (default)
    A PowerContext Server is started on a loopback socket inside the demo
    process, persisted to ``<data_dir>/powercontext/powercontext.db``. This is
    the zero-setup path for judges.

``external``
    ``COACH_PC_URL`` points at a running Server (docker, remote host, ...).
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .config import Settings
from .text import squeeze

LOGGER = logging.getLogger(__name__)

# PowerContext's SQLite profile needs SQLite >= 3.38 (for its retrieval schema).
# Ubuntu 22.04 ships 3.37.2; when the application host has the pysqlite3-binary
# wheel available we transparently swap the stdlib module before PowerContext
# imports it, so the embedded Server still works on older distros.
MIN_SQLITE = (3, 38, 0)


def ensure_modern_sqlite() -> str:
    """Return the module name PowerContext will see (``sqlite3`` or ``pysqlite3``)."""
    import sqlite3

    if sqlite3.sqlite_version_info >= MIN_SQLITE:
        return "sqlite3"
    try:
        import pysqlite3
    except ImportError:
        LOGGER.warning(
            "SQLite %s is older than 3.38 and pysqlite3 is not installed; "
            "embedded PowerContext will refuse to start",
            sqlite3.sqlite_version,
        )
        return "sqlite3"
    sys.modules["sqlite3"] = pysqlite3
    for module in list(sys.modules.values()):
        if getattr(module, "sqlite3", None) is sqlite3:
            module.sqlite3 = pysqlite3  # type: ignore[attr-defined]
    LOGGER.info("swapped stdlib sqlite3 for pysqlite3 %s", pysqlite3.sqlite_version)
    return "pysqlite3"

MEMORY_KINDS = {
    "weakness": "薄弱点",
    "misconception": "常见误区",
    "preference": "偏好",
    "progress": "进度",
    "goal": "目标",
}

SCOPE_TITLE = "知新 · 学习教练"
SCOPE_SUMMARY = (
    "一名学生的学习记忆：薄弱知识点、反复出错的概念、偏好的讲解方式、各门课程的复习进度，"
    "以及每次复习后的自我判断。用于让复习教练在后续会话里继续使用既有印象。"
)


class MemoryUnavailable(RuntimeError):
    """Raised when the PowerContext server cannot serve memory operations."""


class LearnerMemory:
    """Thin HTTP client over the PowerContext Server plus an optional embedded Server."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mode = settings.powercontext_mode
        self.base_url = settings.powercontext_url
        self.workspace = Path(settings.powercontext_workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.scope_file = self.workspace / "scope.json"
        self.scope_id: str | None = None
        self.available = False
        self.error: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._evidence: list[dict[str, Any]] = []
        self.sqlite_module: str | None = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        try:
            if self.mode == "external":
                if not self.base_url:
                    raise MemoryUnavailable("COACH_PC_MODE=external requires COACH_PC_URL")
                await asyncio.to_thread(self._probe_external)
            else:
                await asyncio.to_thread(self._start_embedded)
            assert self.base_url is not None
            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)
            await self.ensure_scope()
            self.available = True
        except Exception as error:  # noqa: BLE001 - demo keeps running without memory
            self.error = f"{type(error).__name__}: {error}"
            LOGGER.warning("PowerContext unavailable: %s", self.error)
            self.available = False
            await self.stop()

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def _probe_external(self) -> None:
        assert self.base_url
        response = httpx.get(f"{self.base_url.rstrip('/')}/v1/capabilities", timeout=10.0)
        response.raise_for_status()

    def _start_embedded(self) -> None:
        import uvicorn

        self.sqlite_module = ensure_modern_sqlite()
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime.config import ExternalSkillsConfig, InferenceConfig, RuntimeConfig
        from powercontext.server.factory import create_server_app
        from powercontext.server.settings import (
            AccessControlConfig,
            BearerAuthConfig,
            HttpConfig,
            McpConfig,
            MetricsConfig,
            ServerSettings,
            TracingConfig,
        )

        directory = self.workspace.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        database_path = directory / "powercontext.db"
        settings = ServerSettings(
            workspace=directory,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path.as_posix()}"),
            http=HttpConfig(host="127.0.0.1"),
            auth=BearerAuthConfig(enabled=False),
            access=AccessControlConfig(mode="disabled"),
            inference=InferenceConfig(),
            # The coach only writes explicit memory entries, so background
            # schedulers stay off and the demo never needs an LLM.
            runtime=RuntimeConfig(schedule_seconds=None, experience_schedule_seconds=None),
            external_skills=ExternalSkillsConfig(),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            tracing=TracingConfig(enabled=False),
        )
        app = create_server_app(settings=settings, scheduler_path=directory / "scheduler.db")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self._listener = listener
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on"))
        self._server = server
        self._thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 45
        while self._thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise MemoryUnavailable("embedded PowerContext server did not start within 45s")

    # -- transport -----------------------------------------------------------
    async def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._http is None:
            raise MemoryUnavailable("PowerContext client is not started")
        started = time.perf_counter()
        try:
            response = await self._http.request(method, path, json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception as error:  # noqa: BLE001 - evidence records failures too
            self._record(method, path, payload, status=f"error: {type(error).__name__}: {str(error)[:200]}", started=started)
            raise
        self._record(method, path, payload, status=f"{response.status_code} {response.reason_phrase}", started=started, response=body)
        return body

    def _record(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        status: str,
        started: float,
        response: dict[str, Any] | None = None,
    ) -> None:
        self._evidence.append(
            {
                "at": time.strftime("%H:%M:%S"),
                "method": method,
                "path": path,
                "request": _redact(payload or {}),
                "status": status,
                "response": _redact(response or {}),
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            }
        )
        del self._evidence[:-80]

    def log(self, limit: int = 30) -> list[dict[str, Any]]:
        return list(reversed(self._evidence[-limit:]))

    # -- scope ---------------------------------------------------------------
    async def ensure_scope(self) -> str:
        if self.scope_id:
            return self.scope_id
        stored = _read_scope_id(self.scope_file)
        if stored:
            try:
                body = await self._call("GET", f"/v1/scopes/{stored}")
            except Exception:  # noqa: BLE001 - stale id, create a new scope
                body = None
            if body and body.get("scope_id"):
                self.scope_id = str(body["scope_id"])
                return self.scope_id
        body = await self._call(
            "POST",
            "/v1/scopes",
            {
                "title": SCOPE_TITLE,
                "summary": SCOPE_SUMMARY,
                "idempotency_key": f"recall-coach-{uuid4().hex[:16]}",
            },
        )
        self.scope_id = str(body["scope_id"])
        self.scope_file.write_text(json.dumps({"scope_id": self.scope_id, "base_url": self.base_url}), encoding="utf-8")
        return self.scope_id

    # -- memory operations ---------------------------------------------------
    async def remember(self, kind: str, text: str, reason: str = "") -> dict[str, Any]:
        scope_id = await self.ensure_scope()
        payload = {"scope_id": scope_id, "kind": kind, "text": squeeze(text)[:4000]}
        if reason:
            payload["reason"] = squeeze(reason)[:400]
        result = await self._call("POST", "/v1/memory/remember", payload)
        records = result.get("records") if isinstance(result, dict) else None
        if records:
            record = records[0]
            # Flatten the Atomic Memory record so callers (smoke, UI) can show the
            # confirmed entry text without knowing the lifecycle envelope.
            entry = {key: record[key] for key in ("kind", "text", "state", "artifact", "state_version") if key in record}
            result = {**result, "entry": entry}
        return result

    async def forget(self, artifact: dict[str, Any], state_version: int) -> dict[str, Any]:
        """Retire one Atomic Memory entry (PowerContext 1.4.x lifecycle API)."""
        scope_id = await self.ensure_scope()
        return await self._call(
            "POST",
            "/v1/atomic-memory/lifecycle",
            {"scope_id": scope_id, "target": {"artifact": artifact, "state_version": int(state_version)}, "state": "forgotten"},
        )

    async def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        scope_id = await self.ensure_scope()
        body = await self._call(
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": squeeze(query)[:2000], "limit": limit, "mode": "auto"},
        )
        return list(body.get("hits") or [])

    async def entries(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        scope_id = await self.ensure_scope()
        body = await self._call(
            "POST",
            "/v1/memory/entries/list",
            {"scope_id": scope_id, "include_inactive": include_inactive},
        )
        return list(body.get("entries") or [])

    async def prepare(self, query: str, max_bytes: int = 6000) -> dict[str, Any]:
        scope_id = await self.ensure_scope()
        return await self._call(
            "POST",
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": squeeze(query)[:2000], "max_bytes": max_bytes},
        )

    def describe(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "mode": self.mode,
            "base_url": self.base_url,
            "scope_id": self.scope_id,
            "database": str(self.workspace / "powercontext.db") if self.mode != "external" else None,
            "error": self.error,
            "sqlite_module": self.sqlite_module,
        }


def _read_scope_id(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scope_id = payload.get("scope_id")
    return str(scope_id) if scope_id else None


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep evidence readable: truncate long text, never expose tokens."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in {"token", "authorization", "api_key"}:
            cleaned[key] = "***"
        elif isinstance(value, str) and len(value) > 240:
            cleaned[key] = value[:240] + "…"
        elif isinstance(value, list):
            cleaned[key] = f"<{len(value)} items>"
        elif isinstance(value, dict):
            cleaned[key] = {inner: value[inner] for inner in list(value)[:4]}
        else:
            cleaned[key] = value
    return cleaned
