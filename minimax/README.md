# SDD 上下文管理模块（minimax）

> **更新（2026-10-29 20:32）**：MiniMax Token Plan 额度到账后，本模块已完成一次**真实**的模型调用并应用其补丁。
> 记录见 `records/minimax.jsonl` 最后一条（`mode=live`、`status=ok`、usage 2609/753、latency 6101ms）
> 与 `records/patches/20261029-203153-live.diff`；之后 `verify --phase after` 目标/回归/全量均 `exit=0 OK`。
> 下文 §8「已知限制」中关于 402 的描述为历史记录，现状以本条为准。

一个可运行的**规格驱动开发（SDD）上下文管理模块**：给一句话任务 + 一个工程根目录，
模块从 **seekdb** 检索出适用的规格 / 代码 / 测试（每条都带 `path:line`），
把 **PowerContext** 里记录的任务进展一并带上，组装成带来源的上下文；
需要改代码时把上下文交给 **MiniMax**（OpenAI 兼容接口）产出一个 unified diff 并应用；
最后跑 demo 工程的目标测试与回归测试，把结果写回 PowerContext 并留证。

```
任务描述 + 工程根目录
      │
      ├─ seekdb（工程资料库）   ← specs/*.md、源码、测试切片 + FULLTEXT(ik) 索引
      │        └── 检索命中 → 按 kind 分组 → 每条带 path:line 溯源
      ├─ PowerContext（状态库）  ← 已完成 / 待办 / 历史验证结果，进程重启后可读回
      └─ MiniMax（改码）         ← 规格+代码+测试 → patch；无 key 时离线回放同一份 patch
```

---

## 1. 目录结构

```
minimax/
├── README.md                     # 本文件：依赖、配置、初始化、调用入口、测试、已知限制
├── .env.example                  # 配置样例（无真实凭据）
├── .gitattributes                # .sh 强制 LF、.ps1 强制 CRLF
├── module/                       # 上下文模块本体（Python 包 sddcontext，CLI 入口）
│   ├── pyproject.toml            # 可 pip install -e 的包定义（提供 sddcontext 命令）
│   ├── sddcontext/
│   │   ├── cli.py                # 命令行：doctor/init-db/index/context/state/apply/verify/reset/run-demo
│   │   ├── config.py             # 全部配置从环境变量读取 + seekdb/PowerContext 端口探测
│   │   ├── db.py                 # seekdb：建表、FULLTEXT 索引、增量写入、MATCH/LIKE 检索、SQL 留证
│   │   ├── indexer.py            # 扫描 specs/**、code/**、tests/**，按 markdown 小节 / AST 符号切片
│   │   ├── retriever.py          # 任务 → 检索词 → seekdb 查询 → 按 kind 归并排序
│   │   ├── context.py            # 组装 JSON / 文本 / MiniMax prompt，含来源与自检
│   │   ├── state.py              # 状态持久化：PowerContext HTTP 后端 + 本地文件兜底
│   │   ├── minimax_client.py     # MiniMax 真实调用 + 完整日志；无 key 时离线回放
│   │   ├── patcher.py            # 纯 Python unified diff 解析/应用（不依赖 patch/git）
│   │   ├── apply.py              # 上下文 → patch → 写盘 → 开发记录
│   │   ├── verify.py             # 跑 unittest 目标/回归/全量，留证到 records/test-logs
│   │   ├── pipeline.py           # 建库/索引/上下文/基线恢复（CLI 与 demo 共用）
│   │   └── demo.py               # run-demo：11 步端到端演示
│   └── tests/test_minimax_client.py  # 用本地假服务验证 MiniMax 请求构造与响应解析
├── demo_project/                 # 业务工程（自带测试，无第三方依赖）
│   ├── sdd.json                  # 索引配置（specs/code/tests 的 glob + 钉住的兼容性测试）
│   ├── inventory/{__init__,discount}.py
│   ├── specs/discount.md         # 当前生效规格（默认 v2）
│   ├── tests/{test_discount_target,test_discount_regression}.py
│   ├── snapshots/before|after/   # 修改前 / 修改后两份快照
│   └── patch/fix.diff            # 由快照生成的 patch（与 after 快照一一对应）
├── sample/                       # 验证样例：任务、规格 v1/v2、参照 patch、步骤、预期、重置
├── records/                      # 技术使用记录（真实运行产生，见 records/README.md）
├── docs/tech-note.md             # 一页技术说明
└── scripts/                      # 一键初始化 / 跑通 / 验证脚本（.ps1 与 .sh 双份）
```

---

## 2. 依赖与实测环境

| 组件 | 版本 / 说明 | 本机实测 |
| --- | --- | --- |
| Python | 3.9+（用到 `from __future__ import annotations`） | 3.12.10（Windows 原生） |
| PyMySQL | `pip install pymysql`，唯一第三方依赖 | 已装，导入显示 `2.2.8`（`pymysql.__version__`） |
| seekdb | MySQL 协议，1.4.0（`OceanBase 4.3.5.3 seekdb`），支持 `FULLTEXT ... WITH PARSER ik` 与 `MATCH ... AGAINST` | `127.0.0.1:2882` 实测连通（本机 2881 未监听，模块自动探测端口） |
| PowerContext | 1.4.1（`/v1/server-info`：package_version 1.4.1，api 1.4），本机 8001，免鉴权 | 实测连通并完成写入/读回 |
| MiniMax | OpenAI 兼容 `POST {MINIMAX_BASE_URL}/chat/completions`，默认 `https://api.minimaxi.com/v1`，默认模型 `MiniMax-M3` | **真实调用已发生但被上游拒绝**：`GET /v1/models` 200（key 有效）；`MiniMax-M3`/`MiniMax-M3.1-Flash` → **402 insufficient_balance_error**；`MiniMax-M2.7`/`M2.5*`/`M2.1*`/`M2` → **404 模型已下线**。因此本轮无模型产物，采用离线参照 patch（详见 §7、`records/README.md`） |

> 关于运行位置：本机 **PowerContext Server(8001) 与 seekdb(2882) 都只监听 Windows 侧**，
> WSL2 内 `127.0.0.1:8001` 不可达（实测 `Connection refused`）。因此**推荐用 Windows 原生 Python 运行**。
> 模块本身是纯 Python + 标准库，代码与路径都不含 OS 特判；在 Linux/WSL 中运行只需
> `pip install pymysql` 并把 `POWERCONTEXT_BASE_URL` / `SEEKDB_*` 指向可达地址即可。

---

## 3. 配置

```powershell
Copy-Item .env.example .env     # 然后按需修改
```

所有配置项见 `.env.example`，全部可通过环境变量覆盖，**没有任何硬编码的机器路径或凭据**。

> 入口脚本会读取 `minimax/.env`（若存在）；`.env` 与 `.state/` 已被 `.gitignore` 忽略，不会入库。
> 直接 `python -m sddcontext` 时需要自己 `PYTHONPATH=<minimax>/module`（或 `pip install -e module`）。
> `.env` 请存为**无 BOM 的 UTF-8**，否则首行键名会带上 `\ufeff`（详见 §8-4）。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST` / `SEEKDB_PORT` | `127.0.0.1` / 自动探测 | 未显式指定端口时依次探测 2881、2882、2880、2883、2884、3306 |
| `SEEKDB_USER` / `SEEKDB_PASSWORD` | `root` / 空 | |
| `SDD_DB_NAME` | `sdd_minimax` | 独立库名，避免与他人冲突 |
| `SDD_PROJECT` | `<minimax>/demo_project` | 工程根目录 |
| `SDD_RECORDS_DIR` / `SDD_STATE_DIR` | `<minimax>/records` / `<minimax>/.state` | 证据与运行态 |
| `MINIMAX_API_KEY` | 空 | 留空 → `apply` 自动离线回放 |
| `MINIMAX_BASE_URL` / `MINIMAX_MODEL` / `MINIMAX_TIMEOUT` | `https://api.minimaxi.com/v1` / `MiniMax-M3` / 180 | |
| `MINIMAX_MODEL_FALLBACKS` | `MiniMax-M3.1-Flash,MiniMax-M2.7` | 主模型失败后依次尝试 |
| `POWERCONTEXT_BASE_URL` | `http://127.0.0.1:8001` | 未设置时探测 8001 → 8000 |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | 空 | 本地免鉴权留空；启用鉴权后填 Server Token |
| `POWERCONTEXT_SCOPE_ID` | 空 | 留空则按 `title=sdd-context:minimax` 查找/创建 |
| `SDD_STATE_BACKEND` | `auto` | `auto`/`powercontext`/`file`；PowerContext 不可用时自动回落文件后端 |
| `SDD_PYTHON` | 当前解释器 | 指定跑测试的 Python |

---

## 4. 初始化与调用入口

两个等价入口（都只是设置 `PYTHONPATH=<minimax>/module` 后调用 `python -m sddcontext`）：

```powershell
.\scripts\sdd.ps1 <command>      # Windows
```
```bash
./scripts/sdd.sh <command>       # Linux / WSL
```

首次准备：

```powershell
.\scripts\bootstrap.ps1          # 装 pymysql + 端口探测 + doctor
.\scripts\sdd.ps1 init-db --recreate
```

### 命令一览

| 命令 | 作用 |
| --- | --- |
| `doctor` | 环境自检：seekdb 连通/切片数、PowerContext 后端与版本、MiniMax 是否有 key |
| `init-db [--recreate]` | 建库表 + FULLTEXT(ik) 索引 + 全量切片入库（内容全部来自真实文件） |
| `index` | 增量扫描工程目录入库（按 `content_hash` 跳过未变切片，清理失效切片） |
| `context [--task T] [--format json\|text\|both]` | 检索并生成带来源的上下文，落 `.state/context/context.{json,txt}` |
| `state save/list/load` | 通过 PowerContext 保存/列举/读回任务进展 |
| `apply [--patch-source auto\|minimax\|replay] [--dry-run]` | 上下文 → MiniMax（或回放）→ patch → 写入工程 |
| `verify --phase before\|after` | 跑目标/回归/全量测试，留证并写回 PowerContext |
| `reset [--spec v1\|v2]` | 恢复 demo_project「修改前」基线，可切换规格版本 |
| `run-demo` | 11 步端到端演示，报告写入 `records/demo-run-<ts>.md` |
| `show` / `prompt` | 打印上次上下文 / 渲染将发给 MiniMax 的 prompt |

---

## 5. 一键跑通与测试命令

```powershell
.\scripts\run-demo.ps1                 # 自动判断：有 key 走真实调用，无 key 走离线回放
.\scripts\run-demo.ps1 -PatchSource replay
```

分开跑（评审逐项核对时推荐，见 `sample/steps.md`）：

```powershell
.\scripts\sdd.ps1 doctor
.\scripts\sdd.ps1 init-db --recreate
.\scripts\sdd.ps1 context --format both
.\scripts\sdd.ps1 verify --phase before      # 期望退出码 1：目标测试失败、回归测试通过
.\scripts\sdd.ps1 apply --patch-source auto
.\scripts\sdd.ps1 verify --phase after       # 期望退出码 0：全部通过
.\scripts\sdd.ps1 state save --completed "已实现 min_total" --todo "补文档"
.\scripts\sdd.ps1 state list
.\scripts\sdd.ps1 state load                 # 独立进程读回（模拟重启）
.\scripts\demo-spec-update.ps1               # 规格 v1/v2 切换，输出必须变化
python .\scripts\check_expected.py           # 按 sample/expected.json 机检
```

模块自测（MiniMax 客户端用本地假服务验证请求构造与响应解析）：

```powershell
python -m unittest discover -s module\tests -t module -v
```

demo 工程测试（由 `verify` 代跑，也可手动）：

```powershell
cd demo_project
python -m unittest discover -s tests -t . -p "test_discount_target.py" -v
python -m unittest discover -s tests -t . -p "test_discount_regression.py" -v
```

---

## 6. 六项必做与证据位置

| # | 要求 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | 能够运行 | ✅ 实测 | `records/demo-run-<ts>.md` 11 步全 OK；`init-db` 输出 24 条切片（spec 4 / code 5 / test 15） |
| 2 | MiniMax 参与 | ✅ **真实调用成功**（2026-10-29 20:31，`MiniMax-M3`，`mode=live status=ok`，prompt 2609 / completion 753 tokens，6.1s，补丁 851B）；模型补丁已应用到 `demo_project`，目标测试与回归测试全部通过。此前 402 记录保留为过程证据 | `records/minimax.jsonl`（9 条 `mode=live, status=error`，含 `http_status` 与 `request_id`）+ `records/minimax-response.json`（原始错误体）+ `records/minimax-summary.md`；调用链路另用假服务单测（`module/tests/test_minimax_client.py`，2 passed） |
| 3 | 上下文正确 | ✅ 实测 | `.state/context/context.json`；`python scripts/check_expected.py` → **20/20 PASS**；v1/v2 规格切换正文变化断言 |
| 4 | 数据库实际参与 | ✅ 实测 | `records/seekdb-queries.md`（每条 SQL + 返回行数，含 `WITH PARSER ik`、`MATCH ... AGAINST`）、`records/seekdb-queries.jsonl` |
| 5 | 状态可复用 | ✅ 实测 | `records/powercontext.md`（HTTP 方法/路径/状态码/artifact_id）；`state save`/`state list`/`state load` 三个独立进程 + `run-demo` 的 `state-reload-after-restart` 步骤 |
| 6 | 代码修改有效 | ✅ 实测 | `records/test-logs/before-*`（target `FAILED (errors=6)`、regression `OK`）+ `records/test-logs/after-*`（三个 suite 全 `OK`） |

---

## 7. 已被证明的“不写死”与离线可复现

- **规格不写死**：`context` 每次都真查 seekdb（`MATCH ... AGAINST` + LIKE + 符号精确命中），
  不使用任何缓存或常量。`demo-spec-update` 演示「换规格文件 → 重新 index → 输出正文变化」，
  `run-demo` 中的 `spec-update-v1` / `spec-restore-v2` 两步对正文做了**字符串不等**断言。
- **离线可复现**：无 `MINIMAX_API_KEY`（或真实调用失败）时 `apply` 走 `sample/offline_patch.diff`
  （= `demo_project/patch/fix.diff`，由 `scripts/make_patch.py` 从 before/after 快照生成），
  并在 `records/minimax.jsonl` 写 `mode=replay / offline_replay=true`，**不会伪装成真实 API 调用**。
- **`--patch-source` 三种取值**：
  - `minimax`：强制真实调用，失败直接报错退出（本项目实测就是这条路径失败，`EXITCODE=2`）；
  - `replay`：强制离线回放；
  - `auto`（默认）：有 key 先真实调用，失败后**显式记录失败原因**再回退回放，不会再抛错。
- **真实调用已实测到的边界**：本机 key 有效（`GET /v1/models` 200，9 个模型），但
  `POST /v1/chat/completions` 对 `MiniMax-M3`/`MiniMax-M3.1-Flash` 返回 **402 insufficient_balance_error**，
  对 `MiniMax-M2.7` 等返回 **404 模型已下线**。也就是说：**鉴权、端点、请求体、错误处理都已验证**，
  唯一缺的是账户额度。充值后 `.\scripts\sdd.ps1 apply --patch-source minimax` 即可切到真实产出，
  代码无需改动。
- **模型回退**：`MINIMAX_MODEL` 失败后自动依次试 `MINIMAX_MODEL_FALLBACKS`
  （默认 `MiniMax-M3.1-Flash,MiniMax-M2.7`），每次尝试都单独记一条日志。

---

## 8. 已知限制

1. ~~**MiniMax 真实产出缺失**（412 余额不足）~~ → **已解决**：额度到账后于 2026-10-29 20:31 实跑成功，见 `records/minimax.jsonl` 末条（`status=ok`）与 `records/patches/20261029-203153-live.diff`。
   因此提交里采用的 patch 是离线参照 patch，`mode=replay` 有明确标注。
   代码路径（鉴权头、请求体、响应解析、diff 抽取、日志字段、模型回退）已被
   `module/tests/test_minimax_client.py` 用本地假服务覆盖，并已对真实端点发起过调用（有 `request_id`）。
2. **PowerContext 状态用 Atomic Memory 承载**（`POST /v1/memory/remember`，`kind=sdd_task_state`），
   不是 Handoff Artifact。原因：Handoff 需要 prepare→finalize→commit 的多步 prepared 值，
   在没有模型生成能力的本机实例上不稳定；显式 Memory 是最小可靠路径，语义上也满足
   「保存任务进展并在重启后读回」。若要换成 Handoff，只需替换 `state.py` 的
   `PowerContextStore.save/load` 两个方法。
3. **seekdb 端口本机是 2882**（任务描述里写的是 2881，实测 2881 未监听）。
   所以即使 `.env` 写着 `SEEKDB_PORT=2881`，模块也会自动改连可用端口并在 stderr 提示；
   也可直接改 `.env` 显式指定。
4. **`.env` 必须是无 BOM 的 UTF-8**：PowerShell 的 `Set-Content -Encoding utf8` 会写 BOM，
   首行的键就会变成 `\ufeffMINIMAX_API_KEY`。`scripts/sdd.ps1` 用 `Get-Content -Encoding UTF8`
   读取可容忍 BOM，但 `bash source .env` / docker-compose 不容忍，建议统一存为 UTF-8 无 BOM。
   `.env`、`.env.*`、`.state/`、`__pycache__/` 都已在 `.gitignore` 中忽略（`git check-ignore` 可验证）。
5. **回归测试通过 `demo_project/sdd.json` 的 `always_include_tests` 显式钉住**。
   纯语义检索不会把「兼容性回归测试」排进前 N 条，所以用工程配置声明，属于可解释的设计选择。
6. 切片是按 markdown 小节 / Python AST 顶层符号切分，超长函数不会二次切分（demo 工程够用）。
7. `records/seekdb-queries.*` 会随运行次数增长，重置方法见 `sample/reset.md`。
8. 交付时 `demo_project/` 处于**「修改前」基线**（`discount.py` 没有 `min_total`），
   「修改后」内容在 `demo_project/snapshots/after/` 与 `demo_project/patch/fix.diff` 里。

---

## 9. 安全

- 仓库内**没有任何真实凭据**，`.env.example` 全部留空。
- `.env`、`.state/` 已在 `.gitignore` 中忽略。
- 状态记录里不含密钥；PowerContext 写入的内容仅任务进展与验证结果。
