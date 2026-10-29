"""任务状态持久化：PowerContext（优先）与本地文件（离线兜底）两种后端。

状态记录结构（CLI `state` 子命令与 demo 共用）：

    {
      "task_id": "task-xxxxxxxxxxxx",
      "task": "任务描述",
      "completed": ["..."],
      "todo": ["..."],
      "verification": [{"phase": "before", "suites": {...}}, ...],
      "context_files": ["..."],
      "updated_at": "2026-..."
    }

PowerContext 后端使用 1.4.x HTTP API（与仓库内 pc-qoder 参考实现一致的调用方式）：

    GET  /v1/scopes                     按 title 复用或创建 scope
    POST /v1/scopes                     {title, summary, idempotency_key}
    POST /v1/memory/remember            {scope_id, kind, text, reason}
    POST /v1/memory/entries/list        {scope_id, limit, cursor}

整条状态以一条 `kind=sdd_task_state` 的显式记忆写入服务端，读取时按 task_id 取最新一条，
因此模块进程重启（甚至 Server 重启）后仍能读回。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

from .config import Config
from .util import append_jsonl, append_text, ensure_dir, now_iso, read_json, write_json

STATE_KIND = "sdd_task_state"
SCOPE_TITLE = "sdd-context:minimax"
MAX_STATE_CHARS = 12000


class StateError(RuntimeError):
    pass


class StateStore:
    backend_name = "unknown"

    def save(self, payload: dict) -> dict:
        raise NotImplementedError

    def list(self) -> List[dict]:
        raise NotImplementedError

    def load(self, task_id: str) -> Optional[dict]:
        raise NotImplementedError

    def healthy(self) -> bool:
        return True

    def health_detail(self) -> str:
        return ""


# --------------------------------------------------------------------- 文件后端
class FileStateStore(StateStore):
    backend_name = "file"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = ensure_dir(cfg.state_dir) / "powercontext-fallback.jsonl"

    def save(self, payload: dict) -> dict:
        record = {**payload, "backend": self.backend_name, "saved_at": now_iso(),
                  "saved_at_ms": int(time.time() * 1000)}
        append_jsonl(self.path, record)
        append_text(self.cfg.pc_log, self._log_block("save", record))
        return record

    def list(self) -> List[dict]:
        latest: Dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                task_id = record.get("task_id", "")
                current = latest.get(task_id)
                if current is None or _newer(record, current):
                    latest[task_id] = record
        return sorted(latest.values(), key=lambda r: r.get("saved_at", ""))

    def load(self, task_id: str) -> Optional[dict]:
        for record in reversed(self.list()):
            if record.get("task_id") == task_id:
                append_text(self.cfg.pc_log, self._log_block("load", record))
                return record
        return None

    def health_detail(self) -> str:
        return f"本地文件后端：{self.path}"

    def _log_block(self, action: str, record: dict) -> str:
        return (
            f"\n### {action} @ {now_iso()}  backend=file（降级）\n\n"
            f"- task_id: `{record.get('task_id')}`\n"
            f"- 文件: `{self.path}`\n"
            f"- 已完成: {record.get('completed')}\n"
            f"- 待办: {record.get('todo')}\n"
            f"- 验证: {len(record.get('verification') or [])} 条\n"
        )


# --------------------------------------------------------------- PowerContext
class PowerContextStore(StateStore):
    """PowerContext Server（1.4.x HTTP API）后端。"""

    backend_name = "powercontext"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base_url = (cfg.pc_base_url or "http://127.0.0.1:8001").rstrip("/")
        self.scope_id: Optional[str] = cfg.pc_scope_id
        self.token = cfg.pc_token
        self._scope_cache = cfg.state_dir / "pc-scope.json"
        self._last_http: List[dict] = []

    # ----------------------------------------------------------- HTTP helpers
    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, payload: Optional[dict] = None,
                 timeout: int = 10) -> Tuple[int, dict]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                body = json.loads(raw) if raw.strip() else {}
                self._record_http(method, path, payload, response.status, body)
                return response.status, body
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            self._record_http(method, path, payload, exc.code, {"error": detail[:600]})
            return exc.code, {"error": detail[:600]}
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise StateError(f"连接 PowerContext 失败 {url}: {exc}") from exc

    def _record_http(self, method: str, path: str, payload, status: int, body: dict) -> None:
        self._last_http.append({
            "ts": now_iso(), "method": method, "path": path, "status": status,
            "request": payload, "response": _shrink(body),
        })
        if len(self._last_http) > 50:
            self._last_http = self._last_http[-50:]

    # ------------------------------------------------------------- scope
    def _find_scope(self) -> Optional[str]:
        cached = read_json(self._scope_cache, {}) or {}
        if cached.get("base_url") == self.base_url and cached.get("scope_id"):
            self.scope_id = cached["scope_id"]
            return self.scope_id
        cursor = None
        while True:
            query = "limit=100" + (f"&cursor={cursor}" if cursor else "")
            code, body = self._request("GET", f"/v1/scopes?{query}")
            if code != 200:
                return None
            for item in body.get("items") or []:
                if item.get("title") == SCOPE_TITLE:
                    self._remember_scope(item.get("scope_id"))
                    return item.get("scope_id")
            cursor = body.get("next_cursor")
            if not cursor:
                return None

    def _remember_scope(self, scope_id: Optional[str]) -> None:
        self.scope_id = scope_id
        self.cfg.pc_scope_id = scope_id
        if scope_id:
            write_json(self._scope_cache, {"base_url": self.base_url, "scope_id": scope_id})

    def ensure_scope(self) -> str:
        if self.scope_id:
            return self.scope_id
        found = self._find_scope()
        if found:
            return found
        code, body = self._request("POST", "/v1/scopes", {
            "title": SCOPE_TITLE,
            "summary": "SDD 上下文管理模块：任务状态与验证结果",
            "idempotency_key": SCOPE_TITLE,
        })
        scope_id = body.get("scope_id")
        if not scope_id:
            raise StateError(f"创建 PowerContext Scope 失败 HTTP {code}: {body}")
        self._remember_scope(scope_id)
        return scope_id

    # ------------------------------------------------------------- 读写
    def save(self, payload: dict) -> dict:
        scope_id = self.ensure_scope()
        record = {**payload, "backend": self.backend_name, "saved_at": now_iso(),
                  "saved_at_ms": int(time.time() * 1000)}
        text = self._encode(record)
        code, body = self._request("POST", "/v1/memory/remember", {
            "scope_id": scope_id,
            "kind": STATE_KIND,
            "text": text,
            "reason": "sddcontext: state save",
        })
        if code not in (200, 201):
            raise StateError(f"PowerContext 写入失败 HTTP {code}: {body}")
        refs = (body.get("records") or [{}])[0].get("artifact") or {}
        record["pc_scope_id"] = scope_id
        record["pc_artifact_id"] = refs.get("artifact_id")
        record["pc_revision"] = refs.get("revision")
        append_text(self.cfg.pc_log, self._log_block("save", record, code, body))
        return record

    def _entries(self) -> List[dict]:
        scope_id = self.ensure_scope()
        entries: List[dict] = []
        cursor = None
        page = 0
        while True:
            payload = {"scope_id": scope_id, "limit": 100}
            if cursor:
                payload["cursor"] = cursor
            code, body = self._request("POST", "/v1/memory/entries/list", payload)
            if code != 200:
                raise StateError(f"PowerContext 读取失败 HTTP {code}: {body}")
            entries.extend(body.get("entries") or [])
            cursor = body.get("next_cursor")
            page += 1
            if not cursor or page > 20:
                return entries

    def list(self) -> List[dict]:
        latest: Dict[str, dict] = {}
        for entry in self._entries():
            if entry.get("kind") != STATE_KIND:
                continue
            record = self._decode(entry.get("text"))
            if not record or not record.get("task_id"):
                continue
            record.setdefault("pc_artifact_id",
                             (entry.get("artifact") or {}).get("artifact_id"))
            task_id = record["task_id"]
            current = latest.get(task_id)
            # 服务端返回顺序不保证，统一按 saved_at_ms 取最新
            if current is None or _newer(record, current):
                latest[task_id] = record
        return sorted(latest.values(), key=lambda r: r.get("saved_at", ""))

    def load(self, task_id: str) -> Optional[dict]:
        found: Optional[dict] = None
        for entry in self._entries():
            if entry.get("kind") != STATE_KIND:
                continue
            record = self._decode(entry.get("text"))
            if not record or record.get("task_id") != task_id:
                continue
            record.setdefault("pc_artifact_id", (entry.get("artifact") or {}).get("artifact_id"))
            record.setdefault("pc_revision", (entry.get("artifact") or {}).get("revision"))
            record.setdefault("pc_scope_id", self.scope_id)
            record["backend"] = self.backend_name
            if found is None or _newer(record, found):
                found = record
        if found:
            append_text(self.cfg.pc_log, self._log_block("load", found, 200, {}))
        return found

    # ------------------------------------------------------------- 健康检查
    def healthy(self) -> bool:
        for path in ("/health/live", "/v1/server-info"):
            try:
                code, _ = self._request("GET", path, timeout=3)
                if code == 200:
                    return True
            except StateError:
                return False
        return False

    def health_detail(self) -> str:
        try:
            code, body = self._request("GET", "/v1/server-info", timeout=4)
            if code == 200:
                return (f"{self.base_url} PowerContext {body.get('package_version')} "
                        f"(api {body.get('api_contract_version')})")
            return f"{self.base_url} 不可用（HTTP {code}）"
        except StateError as exc:
            return str(exc)

    # ------------------------------------------------------------- 编解码
    def _encode(self, record: dict) -> str:
        text = json.dumps(record, ensure_ascii=False)
        if len(text) <= MAX_STATE_CHARS:
            return text
        trimmed = dict(record)
        trimmed["verification"] = (record.get("verification") or [])[-3:]
        trimmed["_trimmed"] = True
        return json.dumps(trimmed, ensure_ascii=False)[:MAX_STATE_CHARS]

    @staticmethod
    def _decode(text) -> Optional[dict]:
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            record = json.loads(text)
        except ValueError:
            return None
        return record if isinstance(record, dict) else None

    def _log_block(self, action: str, record: dict, code: int, raw: dict) -> str:
        http_lines = []
        for item in self._last_http[-6:]:
            http_lines.append(
                f"  - `{item['method']} {item['path']}` -> HTTP {item['status']}"
            )
        return (
            f"\n### {action} @ {now_iso()}  backend=powercontext\n\n"
            f"- Server: `{self.base_url}`\n"
            f"- Scope: `{record.get('pc_scope_id') or self.scope_id}`（title `{SCOPE_TITLE}`）\n"
            f"- kind: `{STATE_KIND}`  task_id: `{record.get('task_id')}`\n"
            f"- artifact_id: `{record.get('pc_artifact_id')}`  revision: `{record.get('pc_revision')}`\n"
            f"- 已完成: {record.get('completed')}\n"
            f"- 待办: {record.get('todo')}\n"
            f"- 验证记录: {len(record.get('verification') or [])} 条\n"
            f"- 最近 HTTP 往返：\n" + "\n".join(http_lines) + "\n"
        )


def _newer(candidate: dict, current: dict) -> bool:
    """按保存时间戳判断候选记录是否更新（毫秒级优先，其次 ISO 字符串）。"""
    for field in ("saved_at_ms", "saved_at", "updated_at"):
        left, right = candidate.get(field), current.get(field)
        if left is None or right is None:
            continue
        if left != right:
            return left > right
    return False


def _shrink(body: dict, limit: int = 500) -> dict:
    try:
        text = json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"raw": str(body)[:limit]}
    if len(text) <= limit:
        return body
    return {"truncated": text[:limit]}


def open_store(cfg: Config, quiet: bool = False) -> StateStore:
    backend = (cfg.pc_backend or "auto").lower()
    if backend == "file":
        return FileStateStore(cfg)
    pc = PowerContextStore(cfg)
    if backend == "powercontext":
        return pc
    if pc.healthy():
        return pc
    if not quiet:
        print(f"[state] PowerContext 不可用（{pc.health_detail()}），回落本地文件后端 "
              f"（可用 POWERCONTEXT_BASE_URL 指定 Server 地址）")
    append_text(cfg.pc_log,
                f"\n### fallback @ {now_iso()}\n\nPowerContext 不可用：{pc.health_detail()}\n"
                f"本次使用本地文件后端。\n")
    return FileStateStore(cfg)
