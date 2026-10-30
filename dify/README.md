# 记忆酒馆 · 老陈（Dify × OceanBase 赛题）

> 一个**记得你上次干了什么**的酒馆 NPC。你每次进门，老板老陈都会翻出你过去的旧账；
> 你在店里说的话，会成为他下次见到你时的谈资。

- 赛道：Dify × OceanBase —— Just for Fun
- 提交目录：`dify/`
- 交付物：DSL（`dsl/tavern-memory.yml`）＋ 运行说明（本文件）＋ 验证样例（`verification.md`）＋ 配套网关源码（`gateway/`）

---

## 一、玩什么

老陈是「醉三更」酒馆的老板：嘴硬、记性好、爱翻旧账。

- 第一次进门，他敷衍你；
- 你说"上次我把杯子摔了"，他会接"账还没赔"；
- 你点了"三更醒"，下次他会先提这一杯；
- 他不知道的事（排队、天气、别人隐私）会直接说"我哪知道"，**不会编**。

一句话：**一个会记仇、会调侃、但说话有依据的酒馆 NPC。**

## 二、技术分工（两项都用上）

| 组件 | 承担什么 | 可核对证据 |
| --- | --- | --- |
| **Dify** | 编排整条链路：开始 → 召回记忆 → 检索设定 → LLM 生成 → 写回记忆 → 回复 | `dsl/tavern-memory.yml` 的 5 个节点与连线 |
| **seekdb** | ① 酒馆设定库 `tavern_lore`（全文索引，NPC 说话的依据）② 玩家档案 `tavern_players`（player → 记忆 scope 映射） | 网关日志 `[lore] query=... hits=N`；`tavern_lore` 表与 `MATCH ... AGAINST` 语句 |
| **PowerContext** | 保存与召回玩家记忆 | 网关日志 `[recall] ... source=search`、`[remember] ok=True`；对应 `/v1/memory/remember`、`/v1/memory/search` 调用 |

> 为什么不让 Dify 直连数据库：Dify 的 HTTP Request 节点只做编排，数据库访问与 scope 解析放在网关里，
> 好处是 **Dify 侧零插件依赖**，评审只要起两个服务即可复现。

## 三、目录结构

```
dify/
├── dsl/tavern-memory.yml     # Dify 导入用的 DSL（Chatflow）
├── gateway/
│   ├── server.py             # 记忆网关（FastAPI，3 个接口）
│   ├── seed.py               # 初始化设定库 + 预置演示记忆
│   ├── init_lore.sql         # 建表与 6 条设定数据
│   ├── test_gateway.py       # 网关自检（不需要 Dify 也能跑）
│   └── requirements.txt
├── verification.md           # 验证样例（两轮对话 + 跨会话记忆复用）
└── README.md
```

## 四、运行环境与版本

| 项 | 版本 / 要求 |
| --- | --- |
| Dify | v1.x（DSL `version: 0.1.5`，`advanced-chat` 模式）；本 DSL 在 **未指定具体小版本** 下编写 |
| seekdb | v1.4.0.0（MySQL 协议 2881；`FULLTEXT ... WITH PARSER ik`） |
| PowerContext | 1.4.1（HTTP 8001） |
| Python | 3.11+（网关依赖 `fastapi` `uvicorn` `httpx` `pymysql`） |
| 模型 | 任意 Chat 模型皆可；DSL 里默认写 `qwen2.5:7b`（Ollama provider），**导入后请改成你环境里的模型** |

## 五、从零跑起来

### 1. 起 seekdb 与 PowerContext

```bash
# seekdb（任意 Linux / Docker）
docker run -d -p 2881:2881 oceanbase/seekdb

# PowerContext（pip 安装后无模型也能启动，只做记忆读写）
pip install "powercontext[server]"
python -c "from powercontext.server.cli import app; app()" run --host 127.0.0.1 --port 8001 --no-env-file
```

### 2. 起记忆网关

```bash
cd dify/gateway
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 9300
```

自检（不需要 Dify）：

```bash
python test_gateway.py
# 期望：health 双 true；设定检索命中 2 条；写入后再次召回能看到新记忆
```

### 3. 初始化设定库与演示记忆

```bash
python seed.py --with-demo-memory
# 会建 tavern 库、写入 6 条设定、为玩家「阿狸」预置一条"摔杯子"的记忆
```

### 4. 导入 DSL 并绑定

1. Dify 控制台 → 创建应用 → 导入 DSL → 选 `dsl/tavern-memory.yml`
2. **改两个 HTTP 节点的地址**：`http://host.docker.internal:9300` →
   - Dify 用 Docker 部署：保持 `host.docker.internal`（Linux 上需 `extra_hosts: host-gateway`）
   - Dify 与应用同机非 Docker：改成 `http://127.0.0.1:9300`
3. **改 LLM 节点的模型**：选中你环境里已有的 Chat 模型
4. 发布应用（Chatflow 自动有对话界面）

### 5. 体验

在 Dify 应用对话页，先填「玩家」= `阿狸`，再说一句话，例如：

```
上次我是不是把杯子摔了？
```

老陈会翻出"摔杯子、答应赔"的旧账。

## 六、接口约定（网关）

| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| POST | `/mem/recall` | `{player, query}` | `{memories[], text, count, source}`，`source=search/recent/none` |
| POST | `/mem/remember` | `{player, text}` | `{ok, scope_id}` |
| POST | `/lore/search` | `{query, limit}` | `{items[], text, count}` |
| GET | `/health` | — | `{ok, powercontext, seekdb}` |

## 七、已知限制

1. **DSL 未在真实 Dify 中导入验证**：本机无法访问 Docker Hub（沙箱网络限制），拉不起 Dify。
   DSL 按 0.1.5 结构编写，若导入报错，优先检查：`version` 字段（可改成你实例导出的版本号）、
   LLM 节点的 `model.provider/name`（必须换成你环境里的模型）。
2. 记忆检索在索引未就绪时会退化为"最近记忆"（`source=recent`），不影响体验，但排序可能不按相关度。
3. 预置数据只有 6 条设定 + 1 条演示记忆，属于最小可玩集；扩展只需往 `tavern_lore` 加行。
4. 网关未做鉴权，仅用于本地/内网演示。

## 八、数据与来源

- 6 条设定全部为本作品原创，无第三方数据；
- 未复用任何公开 DSL 或代码；
- 网关为本作品原创代码。
