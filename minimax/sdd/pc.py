"""PowerContext client used to persist and reuse task state.

The module talks to a locally running PowerContext server (1.4.x) over HTTP:

* ``POST /v1/scopes`` creates (or reuses) the task Scope;
* ``POST /v1/atomic-memory/list`` lists the Atomic Memory artifacts of that
  Scope (the 1.4.x replacement for the legacy Memory collection read);
* ``GET /v1/scopes/{id}/artifacts/atomic-memory/{artifact}`` reads one revision
  and its ``ETag``;
* ``POST .../artifacts`` writes revision one, ``PUT .../{artifact}`` writes the
  next revision and requires ``If-Match: "revision:N"``.

Only the standard library is used so the module runs on a clean judge machine.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_KIND = "sdd-task-state"
FAMILY = "atomic-memory"
MAX_TEXT = 60000


class PowerContextError(RuntimeError):
    """Raised when the PowerContext server is unreachable or rejects a call."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PowerContext:
    def __init__(self, base_url: str, *, token: str | None = None, log_path: Path | None = None,
                 timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.log_path = log_path
        self.timeout = timeout
        self.requests: list[dict[str, Any]] = []

    # -- transport -------------------------------------------------------
    def _log(self, method: str, path: str, status: int | str, detail: str = "") -> None:
        self.requests.append({"method": method, "path": path, "status": status, "at": _now()})
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = f"  {detail}" if detail else ""
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()}  {method:<6} {path}  -> {status}{suffix}\n")

    def _request(self, method: str, path: str, body: Any | None = None,
                 headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
        url = self.base_url + path
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                # Header names are case-insensitive on the wire; normalise so
                # callers can always read ``etag``.
                return response.status, {key.lower(): value for key, value in response.headers.items()}, (
                    json.loads(payload) if payload else None
                )
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace")
            parsed: Any
            try:
                parsed = json.loads(payload) if payload else None
            except ValueError:
                parsed = None
            return error.code, {key.lower(): value for key, value in (error.headers or {}).items()}, parsed or payload
        except urllib.error.URLError as error:  # pragma: no cover - depends on environment
            raise PowerContextError(
                f"cannot reach PowerContext at {self.base_url} ({error.reason}). "
                "Start it with scripts/start-server.sh (Linux) or scripts/start-server.ps1 (Windows)."
            ) from error

    # -- scopes ----------------------------------------------------------
    def find_scope(self, title: str) -> str | None:
        query = urllib.parse.urlencode({"query": title, "query_field": "title", "limit": 50})
        status, _, payload = self._request("GET", f"/v1/scopes?{query}")
        self._log("GET", f"/v1/scopes?{query}", status)
        if status != 200 or not isinstance(payload, dict):
            return None
        for item in payload.get("items", []):
            if item.get("title") == title:
                return item.get("scope_id")
        return None

    def ensure_scope(self, title: str, summary: str) -> str:
        existing = self.find_scope(title)
        if existing:
            return existing
        body = {"title": title, "summary": summary, "idempotency_key": f"scope:{title}"}
        status, _, payload = self._request("POST", "/v1/scopes", body)
        self._log("POST", "/v1/scopes", status, f"title={title!r}")
        if status in (200, 201) and isinstance(payload, dict) and payload.get("scope_id"):
            return payload["scope_id"]
        if status == 409:
            existing = self.find_scope(title)
            if existing:
                return existing
        raise PowerContextError(f"cannot create scope {title!r}: HTTP {status} {payload}")

    # -- atomic memory artifacts ----------------------------------------
    @staticmethod
    def _collection_path(scope_id: str) -> str:
        return f"/v1/scopes/{urllib.parse.quote(scope_id, safe='')}/artifacts"

    @staticmethod
    def _artifact_path(scope_id: str, artifact_id: str | None = None) -> str:
        base = f"/v1/scopes/{urllib.parse.quote(scope_id, safe='')}/artifacts/{FAMILY}"
        return base if artifact_id is None else f"{base}/{urllib.parse.quote(artifact_id, safe='')}"

    def list_memory(self, scope_id: str) -> list[dict[str, Any]]:
        body = {"scope_id": scope_id, "kind": STATE_KIND, "limit": 50}
        status, _, payload = self._request("POST", "/v1/atomic-memory/list", body)
        self._log("POST", "/v1/atomic-memory/list", status, f"kind={STATE_KIND}")
        if status != 200 or not isinstance(payload, dict):
            raise PowerContextError(f"cannot list Atomic Memory artifacts: HTTP {status} {payload}")
        return list(payload.get("items", []))

    def read_artifact(self, scope_id: str, artifact_id: str) -> dict[str, Any]:
        path = self._artifact_path(scope_id, artifact_id)
        status, headers, payload = self._request("GET", path)
        self._log("GET", path, status)
        if status != 200 or not isinstance(payload, dict):
            raise PowerContextError(f"cannot read Atomic Memory artifact {artifact_id}: HTTP {status} {payload}")
        payload["etag"] = headers.get("etag")
        return payload

    @staticmethod
    def _state_text(payload: dict[str, Any]) -> dict[str, Any] | None:
        content = payload.get("content") or {}
        if content.get("kind") != STATE_KIND:
            return None
        try:
            state = json.loads(content.get("text", ""))
        except ValueError:
            return None
        return state if isinstance(state, dict) else None

    def load_state(self, scope_id: str) -> dict[str, Any] | None:
        """Return the newest active task state, or ``None`` when there is none."""
        candidates: list[dict[str, Any]] = []
        for item in self.list_memory(scope_id):
            if item.get("state") != "active":
                continue
            reference = item.get("artifact") or {}
            artifact_id = reference.get("artifact_id")
            if not artifact_id:
                continue
            payload = self.read_artifact(scope_id, artifact_id)
            state = self._state_text(payload)
            if state is None:
                continue
            revision = payload.get("revision") or reference.get("revision") or 0
            candidates.append({
                "artifact_id": artifact_id,
                "revision": revision,
                "etag": payload.get("etag") or f'"revision:{revision}"',
                "state": state,
            })
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.get("revision") or 0)

    def save_state(self, scope_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """Create or replace the Atomic Memory artifact that stores ``state``."""
        text = json.dumps(state, ensure_ascii=False)
        if len(text) > MAX_TEXT:
            raise PowerContextError(f"task state is {len(text)} characters, over the {MAX_TEXT} limit")
        content = {"kind": STATE_KIND, "text": text}

        existing = self.load_state(scope_id)
        if existing is None:
            status, _, payload = self._request(
                "POST", self._collection_path(scope_id), {"family": FAMILY, "content": content},
            )
            self._log("POST", self._collection_path(scope_id), status, f"family={FAMILY}")
            if status not in (200, 201) or not isinstance(payload, dict):
                raise PowerContextError(f"cannot create task state: HTTP {status} {payload}")
            state.update({"artifact_id": payload.get("artifact_id"), "revision": payload.get("revision"),
                          "scope_id": scope_id})
            return state

        artifact_id = existing["artifact_id"]
        etag = existing["etag"]
        path = self._artifact_path(scope_id, artifact_id)
        status, _, payload = self._request("PUT", path, {"content": content}, {"If-Match": etag})
        self._log("PUT", path, status, f"If-Match={etag}")
        if status not in (200, 201) or not isinstance(payload, dict):
            raise PowerContextError(f"cannot replace task state {artifact_id}: HTTP {status} {payload}")
        state.update({"artifact_id": artifact_id, "revision": payload.get("revision"), "scope_id": scope_id})
        return state
