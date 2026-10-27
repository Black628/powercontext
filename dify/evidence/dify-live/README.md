# Dify 1.9.2 实机运行证据

这一组文件来自一次**真实 Dify 1.9.2 部署**：不是回放脚本，也不是用 Dify 的导入代码模拟的。

- 环境：WSL2 Ubuntu 22.04 / Docker 29.1.3 / Docker Compose v2 / Dify 1.9.2 官方 compose
- 起用的服务：`api`、`db`(postgres:15)、`redis:6`、`weaviate:1.27`、`sandbox:0.2.12`、`plugin_daemon:0.3.3`、`ssrf_proxy`
- 模型：本机 9100 端口的 **OpenAI 兼容 stub**（见下），通过市场插件 `langgenius/openai:1.0.8` 接入
- 记忆：同一台 WSL 里的 PowerContext 1.2.0，`http://0.0.0.0:8000`，数据目录 `/root/pc-home`
- 导入的 DSL：`grudge-inn.docker.chatflow.yml`，与 [`../../dsl/grudge-inn.chatflow.yml`](../../dsl/grudge-inn.chatflow.yml) 只差一处 —— 环境变量 `pc_base_url` 从 `http://127.0.0.1:8000` 改为 `http://host.docker.internal:8000`（容器访问宿主机）

## 结果

| 动作 | 结果 |
| --- | --- |
| 导入 DSL | `status=completed`，`current_dsl_version=0.4.0`，`imported_dsl_version=0.4.0`，`app_mode=advanced-chat` |
| 发布 + 建 API Key | 200 / 201 |
| 第一轮 `POST /v1/chat-messages`（新会话，`player_id=docker-final`） | 面板「第 1 次照面 · 怨气 0/100」「共 0 条」，落账 1 笔 |
| 第二轮 `POST /v1/chat-messages`（**另一个新会话**，同一 `player_id`） | 面板「第 2 次照面 · 怨气 25/100」「共 1 条：冒犯1」，旧账正文带出上一轮的原话 |

两轮会话 ID 不同（`7c180eff-…` 与 `241e126f-…`），但 `pc_scope` 的 `idempotency_key=grudge-inn-docker-final` 让两次都落到同一个 PowerContext scope，所以第二轮 `pc_prepare` 直接召回了第一轮的账。这是作品「换会话照样翻旧账」的核心链路，在真实 Dify 里成立。

## 文件

| 文件 | 内容 |
| --- | --- |
| `dify-app.json` | 导入后的应用信息：名称、`mode=advanced-chat`、`api_base_url`、站点信息（本地测试 token 已打码） |
| `dify-workflow-draft.json` | 发布后的 draft workflow（graph / features / environment_variables），可与 DSL 对照 |
| `dify-model-providers.json` | 工作区已安装的模型供应商（`langgenius/openai/openai`） |
| `dify-chat-turn1.json` | 第一轮完整响应（含 answer、conversation_id、message_id） |
| `dify-chat-turn2.json` | 第二轮完整响应（另一个 conversation_id，同一个玩家账本） |
| `dify-powercontext-audit.log` | 直接用 `scripts/memory_audit.py` 从 PowerContext 拉出的 scope / entries / changes |

## 关于 stub 模型

为了让这轮证据可以**确定性复现**，两个 LLM 节点没有接真实商用模型，而是接了一个本地 OpenAI 兼容 stub：它按提示词内容返回固定的掌柜叙事，或在「账簿先生」提示词下返回固定的记账 JSON。它只替换了模型层；Dify 的 12 个节点、4 次真实 HTTP 调 PowerContext、3 个 Python code 节点、sandbox 执行、答案拼接全部是真实发生的。

换成真实模型只需把供应商凭据里的 Base URL 改回你的模型服务；DSL、提示词、PowerContext 都不需要动。

## 复现路径（概要）

1. 按 Dify 官方 `docker/docker-compose.yaml` 起 `api`、`db`、`redis`、`weaviate`、`sandbox`、`plugin_daemon`、`ssrf_proxy`；给 `api` 加 `extra_hosts: host.docker.internal:host-gateway`。
2. 控制台依次：`POST /console/api/init` → `/setup` → `/login`。
3. 市场安装 `langgenius/openai`，`POST /workspaces/current/model-providers/langgenius/openai/openai/credentials` 指向你的模型服务。
4. `POST /console/api/apps/imports`（`mode=yaml-content`）导入 DSL，`POST /apps/<id>/workflows/publish`，再建 API Key。
5. 用同一个 `inputs.player_id` 连续调两次 `POST /v1/chat-messages`（第二次不需要带 `conversation_id`），对比两次回答里的账本面板。

> 上面的控制台自动化与 stub 是本轮取证的临时工装，不属于作品运行依赖，因此没有放进交付目录；评审按 [`../../README.md`](../../README.md) 用真实模型体验即可。
