# minimax — 面向软件持续演进的 SDD 上下文管理演示

本目录是「规有所依 —— 面向软件持续演进的 SDD 上下文管理系统」大赛 MiniMax 赛道的完整提交物。

演示项目是一个可运行的结算服务 `pricing`：它按一份带编号条款的结算规范（`S-01`～`S-05`）
计算订单小计、会员折扣、优惠券、运费和应付总额。当前任务要求修复「包邮门槛」问题：
规范（S-04）规定按**会员折扣后金额**判断是否满 99 元免运费、等于 99 元也应包邮、金卡会员
始终免运费，而现有实现按折前金额且用严格大于判断，并且没有处理金卡会员。

围绕这次修改，`sdd` 模块提供了完整的 SDD 上下文闭环：

1. **导入**：解析规范条款、代码符号、测试函数，写入 seekdb（Linux）或 SQLite（Windows 备选）材料索引；
2. **选择**：按任务描述检索出「适用的规范条款 → 需要修改的代码位置 → 目标测试 → 兼容性回归测试」，
   并附上任务名、上下文运行号、引用位置（`path:line`），生成 JSON / Markdown / 提示词三份上下文；
3. **落盘**：把任务目标、已完成项、待办项、验证记录写入 PowerContext 的 Atomic Memory Artifact；
4. **执行**：把上下文交给 MiniMax（`minimax-cn` / `MiniMax-M3`）生成代码修改；
5. **验证**：运行目标测试与回归测试，`sdd verify` 把验证结果写回 PowerContext；
6. **复用**：重启 PowerContext Server 后再次读取任务状态，验证状态仍可复用。

## 提交物对照

| 赛题要求 | 位置 |
| --- | --- |
| 完整源码 | `sdd/`、`workspace/`、`scripts/` |
| 运行说明（依赖、配置、初始化、入口、测试、限制） | `README.md`、`requirements.txt` |
| 验证样例（任务、适用规范、初始数据、步骤、预期结果、重置） | `docs/验证样例.md` |
| 规范更新后重新导入、上下文随之更新 | `scripts/spec_update_check.py`、`evidence/spec-update-check.log` |
| 改动前/后代码、diff、新增目标测试、回归测试、运行日志 | `evidence/before/`、`evidence/after/`、`evidence/diff.patch`、`workspace/tests/`、`evidence/pytest-*.log` |
| 技术使用记录（MiniMax、seekdb、PowerContext） | `docs/技术使用记录.md` |
| 一页技术说明 | `docs/技术说明.md` |

## 目录结构

```
minimax/
├── sdd/                 SDD 上下文模块（CLI 入口）
│   ├── cli.py           ingest / select / verify / state / info 五个命令
│   ├── parser.py        规范条款、代码符号、测试解析（ast）
│   ├── keywords.py      中英文关键词抽取（CJK bigram）
│   ├── store.py         seekdb / SQLite 材料索引与 SQL 日志
│   ├── select.py        上下文选择与 Markdown/JSON/提示词渲染
│   └── pc.py            PowerContext HTTP 客户端（Atomic Memory 状态）
├── workspace/           演示业务项目
│   ├── specs/pricing_rules.md   带条款编号的结算规范（S-01～S-05）
│   ├── src/pricing/engine.py    业务实现（提交时为修复后状态）
│   └── tests/                   8 个回归测试 + 3 个目标测试
├── scripts/
│   ├── demo.py          一键端到端复现（含 PowerContext 启动/重启）
│   ├── powercontext_launcher.py  兼容旧 SQLite 的 Server 启动器
│   ├── spec_update_check.py  规范更新后重新导入的验证
│   ├── reset.py         恢复改动前状态、清理本地数据
│   └── start-server.*   手动启动 PowerContext Server
├── evidence/            改动前/后快照、diff、pytest 日志、上下文输出、服务日志
└── docs/                验证样例、技术使用记录、一页技术说明
```

## 环境要求

- Python **3.11+**（`sdd` 模块本身只用标准库）。
- 材料索引后端二选一：
  - **seekdb（评分环境推荐）**：`pylibseekdb==1.4.0.post1` 只提供 Linux（x86_64/aarch64，
    glibc ≥ 2.28）与 macOS arm64 轮子，Windows 上无法安装；seekdb 数据库目录必须放在原生
    文件系统上（WSL 中不要放在 `/mnt/e` 这类 9p 挂载下）。
  - **SQLite（Windows 备选）**：Python 自带，无需额外依赖。
- PowerContext **1.4.1**：`pip install "powercontext[cli,server]==1.4.1"`。Server 使用 SQLite
  存储，端口默认 `8000`，演示脚本会自动选择空闲端口并在隔离的 `POWERCONTEXT_HOME` 下运行。
  Server 要求 SQLite **≥ 3.38**；`requirements.txt` 会在 Linux/x86_64 上安装
  `pysqlite3-binary`，`scripts/powercontext_launcher.py` 在解释器自带 SQLite 过旧时自动替换
  （例如 Ubuntu 22.04 的 3.37.2），无需手工处理。
- 测试依赖：`pytest`。

### Linux 安装

```bash
cd minimax
python3.12 -m venv .venv        # 3.11–3.13 均可；无 3.12 时可用 conda-forge Python 或 pyenv
. .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

### Windows 安装

```powershell
cd minimax
python -m venv .venv
.\.venv\Scripts\activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

`requirements.txt` 中的环境标记会在 Windows 上跳过 `pylibseekdb`，请使用 `--backend sqlite`。

## 一键复现

```bash
# Linux：seekdb 索引 + PowerContext 状态写入/重启复用
python scripts/demo.py --backend seekdb

# Windows：SQLite 索引（seekdb 无 Windows 轮子）
python scripts\demo.py --backend sqlite
```

两个平台都在全新 venv 中从零复核过（含 Ubuntu 22.04 系统 SQLite 3.37.2 的兼容路径），
Linux 复核日志见 `evidence/cleanroom-demo-seekdb.log`。

脚本按顺序执行以下检查，任意一步不符预期都会以退出码 1 失败：

1. 启动 PowerContext Server（`POWERCONTEXT_HOME=run/powercontext`）并等待 `/health/ready`；
2. 把 `workspace/src/pricing/engine.py` 恢复为改动前版本；
3. `sdd ingest`：期望索引 5 条规范条款、7 个代码符号、11 个测试；
4. `sdd select --remember`：期望主条款 `S-04`，目标测试 3 个，回归测试 8 个，并写入任务状态；
5. 运行 `pytest`：期望 **3 failed, 8 passed**（改动前，目标测试失败）；
6. 应用 MiniMax 生成的修复（`evidence/after/engine.py`）；
7. `sdd verify --require-state`：期望目标测试 3 个、回归测试 8 个全部通过，状态为 `verified`；
8. 重启 PowerContext Server，再读取任务状态：期望状态仍为 `verified`，且验证记录存在；
9. 带任务状态重新生成上下文：期望 `verified`、3 个已完成项、2 条验证记录（`evidence/context-after-verify.*`）。

所有输出写入 `evidence/`：`demo-summary.json`、`pytest-before.log`、`pytest-after.log`、
`context-latest.{json,md,prompt.txt}`、`context-after-verify.{json,md,prompt.txt}`、
`powercontext-server*.log`。

规范更新后的行为单独验证：

```bash
python scripts/spec_update_check.py --backend seekdb
```

脚本在临时目录里把 S-04 的 `99.00` 改成 `120.00`，重新 `ingest` 后再 `select`，断言输出引用的是
**更新后的条款正文**（`evidence/spec-update-check.log`）。真实工作区与索引不会被修改。

## 上下文模块入口

入口为 CLI：`python -m sdd <command> [options]`。公共参数：

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--workspace` | 业务项目目录 | `minimax/workspace` |
| `--backend` | `auto` / `seekdb` / `sqlite` | `auto`（能导入 `pylibseekdb` 则 seekdb） |
| `--db` | 索引位置：seekdb 目录或 SQLite 文件前缀 | `.data/index` |
| `--powercontext-url` | PowerContext 服务地址 | `http://127.0.0.1:8000` |

### `ingest` — 导入工程材料

```bash
python -m sdd ingest --backend seekdb
```

输入：`workspace/specs/*.md`、`workspace/src/**/*.py`、`workspace/tests/**/*.py`。
输出（stdout JSON）：`run_id`、后端、数据库位置、`spec_clauses` / `code_symbols` / `tests` 计数、
`sql_log` 路径。规范条款以 `## S-XX 标题` 编号；测试用 `Covers: S-XX` 标注覆盖条款。

### `select` — 生成开发上下文

```bash
python -m sdd select --backend seekdb \
  --task "订单满 99 元免运费；会员折扣后金额不足 99 元时应按折后金额判断，金卡会员始终免运费。" \
  --remember --out evidence/context-latest
```

输出三份文件：

- `.json`：结构化上下文（任务、运行号、主条款、条款全文与 `path:line` 引用、代码符号与签名、
  目标测试、回归测试、任务状态、警告）；
- `.md`：面向人的中文上下文，分「适用规范条款 / 需要修改的代码 / 功能测试 / 兼容性回归测试 /
  任务状态（PowerContext）/ 警告」六节；
- `.prompt.txt`：交给 MiniMax 的紧凑提示词。

`--remember` 会把任务目标、主条款、已完成项（材料导入、上下文生成）、待办项（修改哪些符号、
运行目标/回归测试）写入 PowerContext；`--no-state` 则完全离线运行。

### `verify` — 运行测试并回写验证结果

```bash
python -m sdd verify --backend seekdb \
  --task "（同 select）" --log evidence/pytest-after.log --require-state
```

按 `select` 的同一份上下文把 pytest 结果分成「目标测试」和「回归测试」两组，写入 PowerContext
的验证记录（命令、通过/失败名单、时间）。退出码：0 全部通过；1 有失败；3 PowerContext 不可用
且指定了 `--require-state`。

### `state` / `info`

- `python -m sdd state`：读取 PowerContext 中本任务的最新状态（`status`、`completed`、`pending`、
  `verification`、`artifact_id`、`revision`）。
- `python -m sdd info`：查看索引计数与最近一次导入运行号。

## 材料索引（seekdb / SQLite）

`store.py` 使用同一套可移植 SQL，表结构如下：

| 表 | 内容 |
| --- | --- |
| `spec_clauses` | 条款号、标题、正文、`path`、`line_start`、`line_end`、摘要 |
| `spec_keywords` | 条款关键词权重 |
| `code_symbols` | 符号 ID、限定名、签名、docstring、路径、行号、docstring 引用的条款 |
| `symbol_keywords` | 符号关键词权重 |
| `tests` | 测试 ID、名称、docstring、覆盖条款、路径、行号 |
| `test_keywords` | 测试关键词权重 |
| `ingest_runs` / `context_runs` | 导入与上下文选择运行记录 |

每次读写都会向 `.data/logs/sql.log` 追加一条带时间戳的 SQL 记录（含参数），可直接作为
“数据库真实使用”的证据。`docs/技术使用记录.md` 收录了实际执行日志摘录。

## PowerContext 任务状态

- Scope 标题：`minimax-sdd-pricing`（`POST /v1/scopes` 幂等创建/复用）；
- 状态 Artifact：`atomic-memory` 家族，`kind = sdd-task-state`，正文为 JSON：
  `objective`、`status`（`in-progress` / `verified`）、`primary_clauses`、`selected`（条款/符号/目标
  测试/回归测试 ID）、`completed`、`pending`、`verification`（每次验证的命令与通过名单）；
- 首次写入 `POST /v1/scopes/{scope_id}/artifacts`，之后 `PUT .../artifacts/atomic-memory/{id}`，
  带 `If-Match: "revision:N"` 做版本保护；
- 读取使用 `POST /v1/atomic-memory/list`（1.4.x 的 Atomic Memory 接口；旧版 Memory collection
  读取接口在 1.4.1 已停用）。

## MiniMax 参与记录

代码修改由 `minimax-cn` 提供方的 `MiniMax-M3` 模型完成，原始回复、提示词与 diff 均保存在
`evidence/`，详见 `docs/技术使用记录.md`。仓库中的 `workspace/src/pricing/engine.py` 是应用
MiniMax 输出后的最终状态；`evidence/before/engine.py` 是修改前快照。

## 重置

```bash
python scripts/reset.py            # 恢复改动前代码，删除 .data/ 与 run/
python scripts/reset.py --keep-data  # 只恢复代码
```

## 已知限制

- seekdb 轮子只支持 Linux（x86_64/aarch64，glibc ≥ 2.28）与 macOS arm64；Windows 使用 SQLite 后端。
- seekdb 不能运行在 WSL 的 `/mnt/*` 挂载目录下，请把 `--db` 指到 Linux 原生路径。
- PowerContext 1.4.x 停用了旧版 Memory collection 读取，改用 Atomic Memory；本目录按 1.4.1 实现。
- PowerContext Server 需要 SQLite ≥ 3.38：Linux/x86_64 由 `pysqlite3-binary` +
  `scripts/powercontext_launcher.py` 自动兼容（Ubuntu 22.04 的 3.37.2 已实测）；其他平台若解释器
  SQLite 过旧，请改用较新的 Python（如 conda-forge `python=3.12`）。
- 演示任务只涉及一个符号（`shipping`）；上下文选择按关键词 + 条款引用打分，规模较大的项目需要
  替换 `keywords.py` 的切词/权重策略。
- 演示不依赖任何在线大模型 API：MiniMax 的调用记录已固化在 `evidence/minimax-raw-reply.md`，
  重跑 `scripts/demo.py` 不会再次调用模型。
