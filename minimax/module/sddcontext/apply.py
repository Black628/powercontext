"""apply：把上下文交给 MiniMax（或离线参照 patch）产出 diff，并写入工程。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .context import render_prompt, save_context
from .minimax_client import MiniMaxClient, MiniMaxError
from .patcher import PatchError, apply_patch, diff_stats, extract_diff
from .util import append_text, ensure_dir, now_iso, now_stamp, write_json


class ApplyError(RuntimeError):
    pass


def _context_meta(context: dict, ctx_path: Optional[str]) -> dict:
    return {
        "task_id": context.get("task_id"),
        "context_file": ctx_path,
        "project_root": context.get("project_root"),
        "sources": context.get("sources", []),
        "spec_refs": [d["source"] for d in context.get("specs", [])],
        "code_refs": [d["source"] for d in context.get("code", [])],
        "test_refs": [d["source"] for d in context.get("tests", [])],
        "db": context.get("db"),
    }


def _chat_with_fallback(client, cfg: Config, messages, task: str, meta: dict,
                        attempts: List[dict]) -> dict:
    """按 MINIMAX_MODEL → 备选模型 依次尝试；每次尝试都会写一条 records/minimax.jsonl。"""
    models = [cfg.minimax_model] + [m for m in cfg.minimax_fallbacks if m != cfg.minimax_model]
    last_error: Optional[Exception] = None
    for index, model in enumerate(models):
        try:
            result = client.chat(messages, task, context_meta={**meta, "model_attempt": model},
                                 model=model)
            attempts.append({"model": model, "status": "ok",
                             "http_status": result.get("http_status"),
                             "usage": result.get("usage"),
                             "latency_ms": result.get("latency_ms")})
            return result
        except MiniMaxError as exc:
            attempts.append({"model": model, "status": "error", "error": str(exc)})
            print(f"[apply] 模型 {model} 调用失败：{exc}")
            last_error = exc
            if index + 1 < len(models):
                print(f"[apply] 回退到下一个模型 {models[index + 1]}")
    raise ApplyError(f"所有模型调用都失败，最后一次错误：{last_error}")


def run_apply(cfg: Config, context: dict, source: str = "auto", dry_run: bool = False,
              project_root: Optional[Path] = None) -> dict:
    project_root = Path(project_root or context["project_root"] or cfg.project_root)
    stamp = now_stamp()
    messages = render_prompt(context)
    prompt_dir = ensure_dir(cfg.state_dir / "prompt")
    prompt_path = prompt_dir / f"{stamp}-minimax-prompt.md"
    prompt_path.write_text("\n\n".join(f"### role={m['role']}\n{m['content']}" for m in messages),
                           encoding="utf-8", newline="\n")
    ctx_files = save_context(cfg, context, name="apply-context")
    meta = _context_meta(context, ctx_files["json"])

    client = MiniMaxClient(cfg)
    mode: str
    attempts: List[dict] = []
    if source == "replay":
        result = client.replay(context["task"], cfg.sample_dir / "offline_patch.diff",
                              context_meta=meta, messages=messages,
                              reason="显式指定 --patch-source replay")
        mode = "replay"
    elif source == "minimax":
        if not client.has_key:
            raise ApplyError("指定了 --patch-source minimax，但环境变量 MINIMAX_API_KEY 为空")
        result = _chat_with_fallback(client, cfg, messages, context["task"], meta, attempts)
        mode = "live"
    else:  # auto：有 key 先试真实调用，失败则显式标注原因后回放参照 patch
        if client.has_key:
            try:
                result = _chat_with_fallback(client, cfg, messages, context["task"], meta, attempts)
                mode = "live"
            except ApplyError as exc:
                print(f"[apply] 真实调用失败，回退离线回放（原因已写入日志）：{exc}")
                result = client.replay(
                    context["task"], cfg.sample_dir / "offline_patch.diff",
                    context_meta=meta, messages=messages,
                    reason=f"实时调用失败（{exc}），回退 sample/offline_patch.diff",
                )
                mode = "replay"
        else:
            result = client.replay(context["task"], cfg.sample_dir / "offline_patch.diff",
                                   context_meta=meta, messages=messages,
                                   reason="未检测到 MINIMAX_API_KEY，自动回放 sample/offline_patch.diff")
            mode = "replay"

    patch_text = result.get("patch") or extract_diff(result.get("text", ""))
    if not patch_text.strip():
        raise ApplyError("模型输出中没有可解析的 unified diff；详见 records/ 下的 response 文件")

    patch_dir = ensure_dir(cfg.records_dir / "patches")
    patch_path = patch_dir / f"{stamp}-{mode}.diff"
    patch_path.write_text(patch_text, encoding="utf-8", newline="\n")
    latest = ensure_dir(cfg.state_dir / "patch") / "latest.diff"
    latest.write_text(patch_text, encoding="utf-8", newline="\n")

    stats = diff_stats(patch_text)
    applied: Dict[str, str] = {}
    if not dry_run:
        try:
            applied = apply_patch(project_root, patch_text)
        except PatchError as exc:
            raise ApplyError(f"patch 应用失败：{exc}（patch 已保存到 {patch_path}）") from exc

    summary = {
        "ts": now_iso(),
        "mode": mode,
        "task": context["task"],
        "project_root": str(project_root),
        "prompt_file": str(prompt_path),
        "context_file": ctx_files["json"],
        "context_text_file": ctx_files["text"],
        "patch_file": str(patch_path),
        "patch_stats": stats,
        "applied_files": applied,
        "dry_run": dry_run,
        "usage": result.get("usage"),
        "latency_ms": result.get("latency_ms"),
        "model": (result.get("record") or {}).get("model"),
        "attempts": attempts,
        "minimax_log_record_ts": (result.get("record") or {}).get("ts"),
        "sources_used": meta["sources"],
    }
    write_json(cfg.state_dir / "last-apply.json", summary)

    # 开发记录：任务 -> 上下文 -> 补丁 -> 实际改动，一一对应
    record_dir = ensure_dir(cfg.records_dir / "applications")
    (record_dir / f"{stamp}-apply.md").write_text(_render_apply_record(summary, patch_text),
                                                  encoding="utf-8", newline="\n")
    append_text(cfg.records_dir / "code-change.md",
                _render_apply_record(summary, patch_text) + "\n\n---\n")
    return summary


def _render_apply_record(summary: dict, patch_text: str) -> str:
    lines = [
        f"## 代码修改记录 {summary['ts']}",
        "",
        f"- 任务：{summary['task']}",
        f"- 产出模式：`{summary['mode']}`"
        + ("（离线回放，未调用 MiniMax）" if summary["mode"] == "replay" else "（MiniMax 真实调用）"),
        f"- 工程：`{summary['project_root']}`",
        f"- Prompt：`{summary['prompt_file']}`",
        f"- 上下文 JSON：`{summary['context_file']}`",
        f"- Patch：`{summary['patch_file']}`（+{summary['patch_stats'].get('added', 0)}"
        f"/-{summary['patch_stats'].get('removed', 0)}，"
        f"{summary['patch_stats'].get('files', 0)} 个文件）",
        f"- 实际写入：{summary['applied_files'] or '（dry-run，未写盘）'}",
        f"- token 用量：{summary['usage']}",
        f"- 耗时：{summary['latency_ms']} ms",
        "",
        "### 使用的上下文来源",
    ]
    for source in summary["sources_used"]:
        lines.append(f"- {source}")
    lines += ["", "### patch 全文", "", "```diff", patch_text.rstrip(), "```"]
    return "\n".join(lines) + "\n"
