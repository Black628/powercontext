# 运行证据

这些文件都是在 **2026-10-28**、Windows 11 + Python 3.13.5 上，用 PowerContext 1.2.0（SQLite 后端，`127.0.0.1:8000`）实际跑出来的。
两个 LLM 节点用**罐头判定/叙事**代替（见 `scripts/replay_chatflow.py`），其余节点 —— 包括 DSL 里的 Python 代码、四个 HTTP 调用 —— 都是逐字执行。

## 文件说明

| 文件 | 证明了什么 | 关键观察 |
| --- | --- | --- |
| `dsl-validation.log` | DSL 通过逐项结构校验 | `OK: 0 errors, 0 warning(s)`，12 节点 / 11 条边 |
| `memory-demo.log` | PowerContext 读写链路成立，不需要 LLM 或 Dify | 第一回合 `prepare.status=empty`；写入一笔 `offense` 后，第二回合 `status=ready` 且 `content` 里有「青瓷碗」 |
| `memory-second-visit.log` | 跨进程/跨会话持久化 | **新进程**的第一个 `prepare` 就是 `ready`，同时带出「青瓷碗」和「桂花酿」两条旧账，`content_bytes=1135` |
| `chatflow-replay.log` | DSL 图能按边跑通：`start→prep→pc_scope→pick→pc_prepare→pc_list→panel→narrate→judge→parse→pc_remember→answer` | 首访 `prepare=empty`、面板 `第1次 · 怨气0/100`、落账 `revision` 从 1 起 |
| `chatflow-replay-returning.log` | 同玩家第二次进来，整条看图执行 | `prepare=ready`，面板 `第2次 怨气25/100`，账本里出现青瓷碗那条 |
| `chatflow-replay-favor.log` | 赔罪记为 `favor` | `parse.kind=favor`，正文含原话；下一回合怨气 25+25−18 |
| `chatflow-replay-after-favor.log` | 怨气回收 + 词法检索边界 | 面板 `第4次 怨气32/100`；但本回合 `prepare=empty`，因为「客栈开了多少年」与旧账没有共同字词 |
| `memory-audit.log` | 账本内容与版本历史可对账 | `entries` 里每笔账带 `citation`（entry_id/revision）、`kind`、正文；`changes` 记录每次 add/deactivate 及 reason |

`memory-audit.log` 里的 revision 会是 10 左右而不是 4：`reset_player.py` 的 retire、重复验证都会推进 Memory 的 revision。这正是"不可变历史"的设计 —— 每次变更都留痕，不做原地覆盖。

## 怎么复现

```powershell
# 1. 起 PowerContext（仓库内脚本，或 `powercontext server run`）
.venv\Scripts\python.exe dify\scripts\run_powercontext.py --port 8000

# 2. 清空样例玩家的账本（可跳过；跳过则沿用现有账本）
.venv\Scripts\python.exe dify\scripts\reset_player.py --player cui

# 3. 四回合：首访 = 作恶，回访 = 翻旧账，第三回合 = 赔罪，第四回合 = 闲聊
.venv\Scripts\python.exe dify\scripts\replay_chatflow.py --player cui `
    --query "我把你柜上的青瓷碗摔了，一文不赔" --judge-file dify\samples\judge\offense.json
.venv\Scripts\python.exe dify\scripts\replay_chatflow.py --player cui `
    --query "我又来了，上次的碗钱……" --judge-file dify\samples\judge\offense.json
.venv\Scripts\python.exe dify\scripts\replay_chatflow.py --player cui `
    --query "上次的事是我不对，我赔你三坛酒。" --judge-file dify\samples\judge\favor.json
.venv\Scripts\python.exe dify\scripts\replay_chatflow.py --player cui `
    --query "掌柜的，你这客栈开多少年了？可有旧相好？" --judge-file dify\samples\judge\neutral.json

# 4. 对账
.venv\Scripts\python.exe dify\scripts\memory_audit.py --player cui
```

只想验证"记忆能写能读"、不关心 DSL 时，用更短的：

```powershell
.venv\Scripts\python.exe dify\scripts\pc_http_demo.py --player shen
```

## 关于 `replay_chatflow.py` 的边界

它复刻的是 **Dify 的图执行与 HTTP 调用**，不模拟 Dify 的模型层：

- `code` 节点：直接 `exec` DSL 里的 `def main(...)`，输入按 `value_selector` 解析；
- `http-request` 节点：把 `{{#node.var#}}` 渲染进 JSON body（引号内做 JSON 转义，引号外按原始 JSON 插入），再用 `urllib` 发真实请求；
- `llm` 节点：注入 `samples/judge/*.json` 或内置叙事文本 —— 因此它验证的是「接线和记账」，不是模型文笔。

真实体验以 Dify 里导入 DSL 后的对话为准，步骤见 [`../samples/verification.md`](../samples/verification.md)。
