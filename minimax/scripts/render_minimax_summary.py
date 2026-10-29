"""把 records/minimax.jsonl 渲染成人读摘要（records/minimax-summary.md）。

用法：python scripts/render_minimax_summary.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MINIMAX = Path(__file__).resolve().parent.parent
LOG = MINIMAX / "records" / "minimax.jsonl"
OUT = MINIMAX / "records" / "minimax-summary.md"


def main() -> int:
    if not LOG.exists():
        print(f"没有 {LOG}，先跑一次 apply/run-demo")
        return 1
    records = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    live_ok = [r for r in records if r.get("mode") == "live" and r.get("status") == "ok"]
    live_err = [r for r in records if r.get("mode") == "live" and r.get("status") == "error"]
    replay = [r for r in records if r.get("mode") == "replay"]

    lines = ["# MiniMax 调用记录摘要", "",
             f"- 日志文件：`records/minimax.jsonl`（共 {len(records)} 条）",
             f"- 真实调用成功：{len(live_ok)} 条；真实调用失败：{len(live_err)} 条；离线回放：{len(replay)} 条",
             ""]
    lines.append("## 真实调用（mode=live）")
    if not records:
        lines.append("（无）")
    for record in records:
        if record.get("mode") != "live":
            continue
        usage = record.get("usage") or {}
        lines += [
            f"### {record.get('ts')}  {record.get('model')}  status={record.get('status')}"
            + (f"  http={record.get('http_status')}" if record.get("http_status") else ""),
            f"- endpoint: `{record.get('endpoint')}`",
            f"- 耗时: {record.get('latency_ms')} ms；usage: prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}",
            f"- messages: " + ", ".join(
                f"{m['role']}({m['chars']}字,sha={m['sha256']})" for m in record.get("messages", [])),
            f"- prompt 全文: `{record.get('prompt_file')}`",
        ]
        if record.get("status") == "error":
            lines.append(f"- 错误：`{str(record.get('error'))[:400]}`")
        else:
            lines.append(f"- 响应文件: `{record.get('response_file')}`；"
                         f"原始 JSON: `{record.get('raw_response_file')}`；"
                         f"patch 抽取成功={record.get('patch_detected')}")
        lines.append("")
    lines.append("## 离线回放（mode=replay，不是模型响应）")
    for record in replay:
        lines += [
            f"- {record.get('ts')}  原因：{record.get('reason')}",
            f"  - patch 来源：`{record.get('patch_source')}`；note：{record.get('note')}",
        ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
