# records/ —— 技术使用记录（真实运行产生）

所有文件都是模块**真实运行**时写出来的，不是手写的说明。最后一次生成时间见各文件时间戳。

| 文件 | 内容 | 对应必做项 |
| --- | --- | --- |
| `minimax.jsonl` | 每次 MiniMax 调用的完整记录：`ts`、`mode`（live/replay）、`model`、`endpoint`、`messages` 摘要（role/字数/sha256）、`response`、`usage`、`latency_ms`、`http_status`、`request_id`、`prompt_file` | 2（MiniMax 实际参与） |
| `minimax-summary.md` | 上面 jsonl 的人读摘要（`scripts/render_minimax_summary.py` 生成），含真实调用成功/失败条数 | 2 |
| `minimax-response.json` | 最近一次真实调用的原始响应原文；失败时是原始错误体（本机为 402/404） | 2 |
| `responses/<ts>-response.md`、`responses/<ts>-raw.json` | 每次成功调用的响应文本与原始 JSON | 2 |
| `prompts/<ts>-prompt.md` | 每次调用实际发出去的 prompt 全文（规格 + 代码 + 测试 + 改动要求） | 2、3 |
| `seekdb-queries.md` | 每条执行过的 SQL + 参数 + 返回行数（含 DDL、索引、检索） | 4（数据库实际参与） |
| `seekdb-queries.jsonl` | 同上，机器可读版本 | 4 |
| `powercontext.md` | 每次 `state save/load` 的 HTTP 方法、路径、状态码、scope/artifact_id、读写内容摘要 | 5（状态复用） |
| `applications/<ts>-apply.md` | 单次改码记录：任务、使用的来源清单、prompt 路径、patch 路径、实际写入文件 | 2、6 |
| `code-change.md` | 上面记录的累积版（按时间追加） | 2 |
| `patches/<ts>-<mode>.diff` | 每次产出的 patch 原文（`<mode>` 为 live 或 replay） | 6 |
| `test-logs/before-*.log`、`after-*.log` | 修改前/后的目标测试、回归测试、全量测试原始输出（含命令行、cwd、退出码） | 6（代码修改有效） |
| `test-logs/before-*.md`、`after-*.md` | 上面日志的人读汇总 | 6 |
| `test-logs/index.json` | 每次 verify 的机读索引（phase / suite / exit_code / ran / result） | 6 |
| `demo-run-<ts>.md` | `run-demo` 的 11 步报告（每步 status + 数据） | 1、3、5、6 |
| `demo-run-latest.json` | 最近一次 run-demo 的机读报告（`scripts/check_expected.py` 会读） | 1 |

## 本机实测结论（诚实记录）

- **seekdb**：真实参与。`seekdb-queries.md` 中可看到 `CREATE FULLTEXT INDEX ... WITH PARSER ik`、
  `REPLACE INTO sdd_docs`、`MATCH(content, path, symbol) AGAINST(...)` 及其返回行数。
- **PowerContext**：真实参与（1.4.1，`http://127.0.0.1:8001`，免鉴权）。
  scope `sdd-context:minimax`，写入/读回都是独立进程完成。
- **MiniMax**：真实调用**已发生**，但上游拒绝执行：
  - `MiniMax-M3`、`MiniMax-M3.1-Flash` → **HTTP 402 `insufficient_balance_error`（账户余额不足）**
  - `MiniMax-M2.7` / `MiniMax-M2.5*` / `MiniMax-M2.1*` / `MiniMax-M2` → **HTTP 404 `not_found_error`（模型已下线）**
  - 因此**没有模型生成的 patch**。当前采用的 patch 是 `sample/offline_patch.diff`（离线参照 patch），
    日志里 `mode=replay`、`offline_replay=true`，不冒充模型输出。
  - 账户充值后执行 `.\scripts\sdd.ps1 apply --patch-source minimax` 即可得到真实模型 patch，
    无需改代码。
