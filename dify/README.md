# 记仇客栈 · 会翻旧账的狐狸掌柜

用 Dify Chatflow + PowerContext 做的一家「会记仇」的客栈：掌柜阿箬（修行四百年的赤狐）把客人做过的每一件事记进《恩怨账本》；
换个会话、隔天再来，只要报同一个玩家 ID，她照样跟你一笔一笔地算，怨气值、欠账、旧话一句不落。

- 赛题：Dify × OceanBase《Just for Fun——用 Dify 做一个好玩的智能应用》
- 技术选型：**Dify（必选）+ PowerContext**（题面允许二选一；本作品未使用 seekdb，见「技术分工」）
- 交付目录：`dify/`（DSL、运行说明、验证样例、证据、脚本）

## 1. 玩法与体验

1. 打开应用，开场白会让你填一个「玩家 ID」（例如 `shen`），这是账本上你的名字；留空也行，会自动用 Dify 的会话用户 ID 兜底。
2. 在客栈里自由发挥：摔碗赖账、赔礼道歉、打听掌柜的旧相好、胡说八道都行。
3. 每一回合有两件事同时发生：
   - 柜台后的阿箬检索你以前的账，用古风口吻回应，怨气越高越刻薄；
   - 账簿先生把你这一回合记成一笔账（冒犯 / 人情 / 秘密 / 闲话），写进 PowerContext。
4. 回复末尾附一块**账本面板**：第几次照面、怨气值、最近五条账目。
5. **关键体验**：刷新页面、开一个新对话、报同一个 ID —— 阿箬第一句就会翻旧账。

示例（详见 [`samples/verification.md`](samples/verification.md)）：

> 客人：我把你柜上的青瓷碗摔了，一文不赔。
> 阿箬：（第一次照面，账本空空）……头一回来就这么横？

> —— 新会话，同一 ID ——

> 客人：我又来了，上次的碗钱……
> 阿箬：（把抹布往柜台上一摔）上回你摔碎的那只青瓷碗，碎瓷片还收在柜台底下呢。今日是来赔碗，还是来赔命？

## 2. 目录结构

```
dify/
├── README.md                     # 本文件：创意、运行说明、技术分工、限制
├── dsl/
│   └── grudge-inn.chatflow.yml   # 唯一的 Dify DSL（Chatflow，12 节点，无插件依赖）
├── samples/
│   ├── verification.md           # 五步验证样例：初始状态、输入、预期表现、恢复方法
│   └── judge/*.json              # 回放脚本用的罐头判定结果（offense/favor/neutral）
├── evidence/
│   ├── README.md                 # 每份证据证明什么、怎么复现
│   ├── dsl-validation.log        # 静态校验：0 error
│   ├── memory-demo.log           # 同进程两回合：空账本 → 记一笔 → 召回
│   ├── memory-second-visit.log   # 新进程首回合就召回两条旧账
│   ├── chatflow-replay*.log      # 按 DSL 图真实执行 12 节点（首访/回访/赔罪/闲聊）
│   └── memory-audit.log          # entries + changes：账本内容与版本历史
└── scripts/
    ├── run_powercontext.py       # 用已安装的 PowerContext 或在仓库里起服务（127.0.0.1:8000）
    ├── validate_dsl.py           # DSL 静态校验（PyYAML；结构对照 0.4.0 样例，字段对照 Dify 1.9.2 实体）
    ├── replay_chatflow.py        # 不依赖 Dify 的图执行器：真实 HTTP + code 节点，LLM 用罐头输出
    ├── pc_http_demo.py           # 最小记忆链路演示（无 LLM、无 Dify 也能证明读写）
    ├── memory_audit.py           # 打印某位客人的账本与 revision 历史
    └── reset_player.py           # 清空某位客人的账本，用于重放验证样例
```

## 3. 技术分工

| 组件 | 承担的事 | 在本作品中的具体证据 |
| --- | --- | --- |
| **Dify Chatflow** | 应用入口、会话、全部编排：身份整理 → 开账/取账本号 → 检索旧事 → 取账本 → 算怨气 → LLM 叙事 → LLM 判定 → 解析落账 → 渲染回答 | `dsl/grudge-inn.chatflow.yml`（12 节点、11 条边）；`evidence/chatflow-replay*.log` 按该 DSL 逐节点执行 |
| **PowerContext** | 按玩家分区的长期记忆：`/v1/scopes` 开账本、`/v1/context/prepare` 检索旧事、`/v1/memory/entries/list` 提供账本数据、`/v1/memory/remember` 落账、`/v1/memory/changes` 审计 | `evidence/memory-*.log`、`memory-audit.log`：能看到每笔账的 `kind`、正文、`citation` 与 revision 递增 |
| seekdb | **未使用**。题面要求 seekdb、PowerContext 至少一种；本作品选择 PowerContext，PowerContext 默认使用 SQLite + FTS5，无需向量库即可运行 | 说明见「已知限制」：若想换成 seekdb，只需替换 PowerContext 的持久化后端（`seekdb` extra，仅 Linux/macOS），DSL 不用改 |

记忆如何被真正使用（而不是"装了个组件"）：

- **写**：`judge` LLM 输出 `{"kind","deed","quote"}` → `parse` 代码节点把 `deed` 与玩家原话拼成账目正文 → `pc_remember` 调 `/v1/memory/remember`。原话一定要进正文，因为检索是词法的。
- **读**：下一回合 `pc_prepare` 调 `/v1/context/prepare`，把命中的旧账原文注入 `narrate` 的提示词（「旧事摘录」），`panel` 再统计账本算怨气。
- **跨会话**：PowerContext 的 scope 由 `idempotency_key = grudge-inn-<player_key>` 唯一确定，与 Dify 会话无关；同一 ID 在新会话里拿到同一个 `scope_id`，于是旧账复现。
- **可审计**：每次写入返回 `citation`（entry_id + revision），`/v1/memory/changes` 记录 add/deactivate 及 reason。

## 4. 运行环境

| 项 | 版本 / 说明 |
| --- | --- |
| Dify | **1.9.2**（`CURRENT_DSL_VERSION = 0.4.0`，与 DSL 完全匹配）。在 1.17.1（DSL 0.7.0）导入会提示「版本较旧」，属预期，功能不受影响 |
| DSL | `version: 0.4.0`，`mode: advanced-chat`，`dependencies: []`（无插件依赖） |
| PowerContext | **1.2.0**（PyPI：`powercontext[server,cli]`；`cli` 提供 `powercontext` 命令所需的 typer，只跑随附脚本则可省），SQLite + FTS5 持久化，无需 Embedding / Rerank |
| Python | 3.11+（PowerContext 与随附脚本；3.13.5 实测） |
| 模型 | 任意 OpenAI 兼容 chat 模型；DSL 默认 `langgenius/openai/openai` + `gpt-4o-mini`，导入后按第 5.3 步改成你账号下可用的模型 |
| 网络 | Dify 需要能访问 PowerContext 的 8000 端口；两个 LLM 节点需要能访问你配置的模型服务。无其他外网依赖 |
| 资源 | 2 vCPU / 2 GB 内存足够跑 Dify + PowerContext（PowerContext 空载常驻约 100 MB） |

不使用的组件：自定义插件、额外后端、知识库、Embedding、Rerank。

DSL 与仓库里不含任何 API Key、密码或真实凭据：模型 key 由评审在自己的 Dify 工作区配置，PowerContext 地址只以环境变量 `pc_base_url` 出现（默认 `http://127.0.0.1:8000`，属占位值）。

## 5. 启动步骤

### 5.1 启动 PowerContext（必须先于对话）

方式 A：已装好 Python 的机器（推荐，评审最省事）

```powershell
# Windows
python -m venv .venv-pc
.venv-pc\Scripts\python.exe -m pip install "powercontext[server,cli]==1.2.0"
.venv-pc\Scripts\powercontext.exe server run --host 127.0.0.1 --port 8000 --no-env-file
```

```bash
# macOS / Linux
python -m venv .venv-pc
.venv-pc/bin/pip install "powercontext[server,cli]==1.2.0"
.venv-pc/bin/powercontext server run --host 127.0.0.1 --port 8000 --no-env-file
```

> 必须带 `cli` extra：`powercontext` 命令的入口在导入时就需要 `typer`，只装 `[server]` 会在启动时报 `ModuleNotFoundError: typer`（只跑方式 B 的脚本则不需要）。

> `--no-env-file` 表示不读取当前目录的 `.env`，避免环境里已有配置干扰；国内网络下载慢时可给 pip 加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

方式 B：使用本目录脚本（已装好 PowerContext 的 `server` 依赖，或就在 PowerContext 仓库里以 `pip install -e ".[server]"` 装过依赖）

```powershell
.venv\Scripts\python.exe dify\scripts\run_powercontext.py --port 8000
```

启动后自检：

```powershell
curl.exe http://127.0.0.1:8000/health/ready
# {"status":"ready","checks":{...}}
```

数据默认落在 `dify/.data/`（方式 B）或 `%USERPROFILE%\.powercontext`（方式 A 的 CLI 默认值），删掉目录即回到全新账本。

### 5.2 导入 DSL

Dify → 工作室 → 创建/导入 → **导入 DSL 文件** → 选择 `dify/dsl/grudge-inn.chatflow.yml`。

导入后确认：应用类型为 Chatflow，12 个节点齐全，环境变量里有 `pc_base_url`。

### 5.3 绑定模型

打开两个 LLM 节点，各点一次「模型」选择器，换成你 Dify 里已配置的模型：

- `掌柜开口`（叙事）：建议温度 0.8–0.9，要求有角色感、会翻旧账；
- `判这一笔`（记账）：建议温度 0.1–0.3，要求稳定输出 JSON。

> 若你的 Dify 没有 OpenAI 供应商，这两个节点会显示模型不可用；换成本地/国产模型即可，提示词与 JSON 约定不用改。
> `judge` 节点必须使用支持「按指令输出 JSON」的模型；`parse` 节点对多余文字、代码块围栏都做了兜底。

### 5.4 配置 PowerContext 地址

应用「环境变量」里把 `pc_base_url` 改成 Dify 实际能访问到的地址：

| Dify 部署方式 | 应填的值 |
| --- | --- |
| 本机源码 / 本机 Docker 且用 host 网络 | `http://127.0.0.1:8000`（默认值） |
| Docker Desktop（Windows/macOS） | `http://host.docker.internal:8000` |
| Linux Docker bridge | 宿主机内网 IP，如 `http://172.17.0.1:8000` |
| 与 PowerContext 同一 compose 网络 | `http://powercontext:8000` |

### 5.5 开始体验

发布应用（或直接在「调试与预览」里），填一个玩家 ID，开始对话。验证步骤见 [`samples/verification.md`](samples/verification.md)。

## 6. 工作流说明（12 节点）

| # | 节点 | 类型 | 作用 |
| --- | --- | --- | --- |
| 1 | `start` 报上名号 | start | 变量 `player_id`（可空：空则取 Dify 用户 ID 前 8 位加 `dx-` 前缀，仍空则 `guest`） |
| 2 | `prep` 身份与话术整理 | code | 清洗 ID；把本回合原话转成可安全拼进 JSON 的 `query_json`；生成 `reason_json` |
| 3 | `pc_scope` 开一本账 | http | `POST /v1/scopes`，`idempotency_key=grudge-inn-<id>`；返回服务端生成的 `scope_id` |
| 4 | `pick` 记下账本号 | code | 从 `body` 中取 `scope_id`，供后续节点使用 |
| 5 | `pc_prepare` 检索旧事 | http | `POST /v1/context/prepare`，拿回这位客人的旧账原文 |
| 6 | `pc_list` 取账本 | http | `POST /v1/memory/entries/list`，拿全部账目 |
| 7 | `panel` 算账 | code | 按 kind 统计（冒犯 +25 / 人情 −18 / 秘密 +12 / 闲话 0），clamp 0–100；生成账本面板文本 |
| 8 | `narrate` 掌柜开口 | llm | 把「账本 + 旧事摘录 + 怨气 + 本回合原话」交给掌柜，古风回应 + 三个选项 |
| 9 | `judge` 判这一笔 | llm | 只输出 `{"kind","deed","quote","delta"}` |
| 10 | `parse` 誊账 | code | 解析 JSON，把 `deed` 与玩家原话拼成账目正文（原话入正文才能被下次检索命中） |
| 11 | `pc_remember` 落账 | http | `POST /v1/memory/remember`，`kind` + 正文 + reason |
| 12 | `answer` 回话 | answer | 掌柜的话 + 账本面板 |

两个设计细节：

- **JSON 安全**：用户原话不能直接拼进 HTTP body，`prep` 先用 `json.dumps` 生成字面量，body 里再用 `{{#prep.query_json#}}` 插值；`parse` 同理生成 `ledger_json`。
- **scope id 是服务端生成的**：`/v1/scopes` 的响应里才有 `scope_id`（形如 `scp_...`），所以必须有 `pc_scope → pick` 两步；幂等键保证同一玩家永远落到同一本账。

## 7. 验证样例与证据

- 五步体验脚本：[`samples/verification.md`](samples/verification.md)（含初始状态、每步输入、预期表现、重置方法）
- 运行证据与复现命令：[`evidence/README.md`](evidence/README.md)
- 静态校验：`python dify/scripts/validate_dsl.py dify/dsl/grudge-inn.chatflow.yml` → `OK: 0 errors`（该脚本额外需要 PyYAML：`python -m pip install pyyaml`；其余脚本只用标准库）

无 Dify 环境时也可以验证记忆链路：

```powershell
# 真实读写、无 LLM
python dify/scripts/pc_http_demo.py --player shen
# 按 DSL 图完整回放（LLM 用罐头输出，HTTP 与 code 节点真实执行）
python dify/scripts/replay_chatflow.py --player shen --judge-file dify/samples/judge/offense.json
# 查账本与版本历史
python dify/scripts/memory_audit.py --player shen
```

## 8. 已知限制

1. **词法检索**：PowerContext 的 SQLite 后端用 FTS5（中文按单字/双字 bigram 建索引），检索依赖字面重合。作品已把玩家原话写进账目正文来提高命中率，但若新话题与旧账毫无共同字词，`prepare` 会返回 `empty`（日志 `chatflow-replay-after-favor.log` 就是这种情况），此时掌柜只按账本面板反应。想要语义召回可把 PowerContext 后端换成 seekdb（向量检索）或配置 Embedding。
2. **需要模型 key**：两个 LLM 节点必须绑定可用模型；`replay_chatflow.py` 的罐头输出只用于验证接线，不代表真实叙事质量。
3. **无鉴权演示**：PowerContext 默认仅 loopback、无鉴权；玩家隔离靠玩家 ID（`player_id`，空则由 Dify 用户 ID 兜底）分区，没有身份校验，属于单机演示定位。
4. **seekdb 未使用**：题面允许 "seekdb、PowerContext 至少一种"。seekdb 的 Python 依赖仅在 Linux/macOS 提供，本作品在 Windows 上开发与验证，因此选择 PowerContext；若要加 seekdb，只需按 PowerContext 文档把持久化后端切换为 seekdb，DSL 无需改动。
5. **Dify 版本**：DSL 按 1.9.2 导出（0.4.0）。在更新版本（如 1.17.1 / 0.7.0）导入会提示版本较旧，导入与运行正常；若评审环境为更老的 1.4 及以下，可能缺少部分节点字段，请以 1.9.2+ 为准。

## 9. 来源声明

- Dify DSL 结构参考 Dify 官方仓库与公开导出样例（`dify-for-dsl` 的 0.4.0 Chatflow 样例）的字段组织方式，业务编排、提示词与全部代码均为本作品原创。
- 未复用第三方代码、数据或插件；DSL 的 `dependencies` 为空，不需要安装任何插件即可导入。
- PowerContext 使用官方 HTTP API（版本 1.2.0），未使用其 Dify 插件。
