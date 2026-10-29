"""MiniMax（OpenAI 兼容）客户端：真实调用 + 完整日志；无 key 时进入离线回放模式。"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .patcher import extract_diff
from .util import append_jsonl, ensure_dir, now_iso, now_stamp, truncate, write_json


class MiniMaxError(RuntimeError):
    """MiniMax 调用失败；携带 HTTP 状态码与响应体，便于原样留证。"""

    def __init__(self, message: str, http_status: Optional[int] = None,
                 body: Optional[str] = None):
        super().__init__(message)
        self.http_status = http_status
        self.body = body


def _post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int) -> tuple:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:  # 4xx / 5xx
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise MiniMaxError(f"HTTP {exc.code} <- {url}: {detail}",
                           http_status=exc.code, body=detail) from exc
    except urllib.error.URLError as exc:
        raise MiniMaxError(f"网络错误 {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise MiniMaxError(f"请求失败 {url}: {exc}") from exc


def _messages_digest(messages: List[dict]) -> List[dict]:
    summary = []
    for message in messages:
        content = message.get("content", "")
        summary.append({
            "role": message.get("role"),
            "chars": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        })
    return summary


class MiniMaxClient:
    def __init__(self, cfg: Config, log_path: Optional[Path] = None):
        self.cfg = cfg
        self.log_path = log_path or cfg.minimax_log

    @property
    def has_key(self) -> bool:
        return bool(self.cfg.minimax_api_key)

    def chat(self, messages: List[dict], task: str, context_meta: Optional[dict] = None,
             temperature: float = 0.1, max_tokens: int = 2048,
             model: Optional[str] = None) -> dict:
        """真实调用 MiniMax；把 prompt / response / usage / 耗时写入 records/minimax.jsonl。"""
        if not self.has_key:
            raise MiniMaxError("未设置 MINIMAX_API_KEY")
        model = model or self.cfg.minimax_model
        endpoint = f"{self.cfg.minimax_base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.minimax_api_key}",
            "Content-Type": "application/json",
        }
        started = time.time()
        stamp = now_stamp()
        prompt_path = self._dump_prompt(messages, stamp)
        record = {
            "ts": now_iso(),
            "mode": "live",
            "task": task,
            "model": model,
            "endpoint": endpoint,
            "messages": _messages_digest(messages),
            "prompt_file": str(prompt_path),
            "context": context_meta or {},
        }
        try:
            status, data = _post_json(endpoint, payload, headers, self.cfg.minimax_timeout)
        except MiniMaxError as exc:
            record.update({
                "status": "error",
                "http_status": getattr(exc, "http_status", None),
                "error": str(exc),
                "latency_ms": int((time.time() - started) * 1000),
                "response": None,
            })
            append_jsonl(self.log_path, record)
            # 失败也留原文，便于复核（明确标注 status=error）
            write_json(self.cfg.records_dir / "minimax-response.json", {
                "ts": record["ts"], "status": "error",
                "http_status": getattr(exc, "http_status", None),
                "model": model, "endpoint": endpoint,
                "error": str(exc), "raw_error_body": getattr(exc, "body", None),
                "note": "本次调用被上游拒绝，没有模型输出；不是 patch",
            })
            raise
        latency_ms = int((time.time() - started) * 1000)
        choices = data.get("choices") or []
        message = (choices[0] or {}).get("message") or {} if choices else {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        response_path = self._dump_response(content, stamp)
        raw_path = self._dump_raw(data, stamp)
        record.update({
            "status": "ok",
            "http_status": status,
            "latency_ms": latency_ms,
            "usage": data.get("usage"),
            "finish_reason": (choices[0] or {}).get("finish_reason") if choices else None,
            "response": content,
            "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
            "response_file": str(response_path),
            "raw_response_file": str(raw_path),
            "patch_detected": bool(extract_diff(content)),
            "request_id": data.get("id"),
            "base_resp": data.get("base_resp"),
        })
        append_jsonl(self.log_path, record)
        # 便于复核：最新一次真实响应原文
        latest = self.cfg.records_dir / "minimax-response.json"
        write_json(latest, {
            "ts": record["ts"], "model": model, "http_status": status,
            "endpoint": endpoint, "usage": data.get("usage"),
            "latency_ms": latency_ms, "finish_reason": record["finish_reason"],
            "request_id": data.get("id"),
            "response_text": content,
            "patch_extracted": extract_diff(content),
            "raw": data,
        })
        return {
            "text": content,
            "usage": data.get("usage"),
            "latency_ms": latency_ms,
            "record": record,
            "patch": extract_diff(content),
            "http_status": status,
        }

    def replay(self, task: str, patch_path: Path, context_meta: Optional[dict] = None,
               messages: Optional[List[dict]] = None, reason: str = "") -> dict:
        """离线回放：复用 sample/ 中预置的参照 patch，并在日志中明确标注。"""
        if not patch_path.exists():
            raise MiniMaxError(f"离线回放依赖的参照 patch 不存在：{patch_path}")
        patch_text = patch_path.read_text(encoding="utf-8")
        record = {
            "ts": now_iso(),
            "mode": "replay",
            "offline_replay": True,
            "task": task,
            "model": None,
            "endpoint": None,
            "reason": reason or "未设置 MINIMAX_API_KEY，使用 sample/ 预置参照 patch 回放",
            "messages": _messages_digest(messages or []),
            "context": context_meta or {},
            "patch_source": str(patch_path),
            "response": patch_text,
            "usage": None,
            "latency_ms": 0,
            "status": "replay",
            "note": "离线回放模式：本条记录不是 MiniMax 的真实响应，仅用于证明流程可复现。"
                    "设置 MINIMAX_API_KEY 后执行 `apply --patch-source minimax` 得到真实调用记录。",
        }
        append_jsonl(self.log_path, record)
        return {"text": patch_text, "usage": None, "latency_ms": 0, "record": record,
                "patch": extract_diff(patch_text)}

    def _dump_prompt(self, messages: List[dict], stamp: str) -> Path:
        out_dir = ensure_dir(self.cfg.records_dir / "prompts")
        path = out_dir / f"{stamp}-prompt.md"
        text = []
        for message in messages:
            text.append(f"### role={message.get('role')}\n{message.get('content', '')}\n")
        path.write_text("\n".join(text), encoding="utf-8", newline="\n")
        return path

    def _dump_response(self, content: str, stamp: str) -> Path:
        out_dir = ensure_dir(self.cfg.records_dir / "responses")
        path = out_dir / f"{stamp}-response.md"
        path.write_text(truncate(content, 20000), encoding="utf-8", newline="\n")
        return path

    def _dump_raw(self, data: dict, stamp: str) -> Path:
        out_dir = ensure_dir(self.cfg.records_dir / "responses")
        path = out_dir / f"{stamp}-raw.json"
        write_json(path, data)
        return path
