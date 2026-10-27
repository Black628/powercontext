---
status: community
title: Pi Coding Agent
description: 安装 PowerContext 原生 Pi package，并控制召回、采集和持久化工具写入。
---

# Pi Coding Agent

`community`

## 安装或刷新 package

先安装 Pi，再从与 PowerContext CLI 相同的 ref 安装 package：

```bash
powercontext setup pi
```

也可以使用本地 checkout：

```bash
powercontext setup pi --source .
```

`setup pi` 会调用 Pi 的原生 package 安装器，并创建 PowerContext 数据目录；它不会启动 Server。启动 Server 后，
在项目目录中开启新的 Pi 会话：

```bash
powercontext server run
pi
```

## 了解 package 的行为

Pi 开始 agent turn 前，package 会以默认 8000-byte 预算调用一次 `POST /v1/context/prepare`。它只严格接受
`powercontext.prepared-context.v1`，并把结果标记为不可信历史证据。当前 system instruction、仓库规范和用户请求
始终优先。

符合条件的用户提示词会被独立采集为 Content Source。package 不会同步完整 Pi transcript。Server 不可用、超时、
重定向或响应不符合契约时，召回、采集和边界 flush 都会正常降级：Pi 的 prompt 不变，普通工作不会被阻塞。

package 按 `POWERCONTEXT_PI_SCOPE_ID`、workspace 持久 binding、Server 默认 Scope 的顺序解析一个由 Server
管理的 Scope。workspace 路径只会哈希为外部 binding key，不会成为 Scope ID。仅在宿主必须固定到某个已有
Scope 时设置显式变量。

启用这些能力后，Pi 已满足仓库定义的 Full 核心接入 profile。

## 导入离线会话分支

Python 模块 `powercontext.sources.pi_session` 可从 Pi v3 JSONL 文件采集原始历史。这是显式的离线采集入口；
安装 Pi package 不会自动启动完整会话采集。

传入完整 UTF-8 字节内容，可带文件开头的 UTF-8 BOM。不传 `leaf_id` 时选择文件中的最后一个节点：

```python
from powercontext.sources import SourceDefinitionRegistry
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionInput,
    parse_pi_session,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


async def inspect_history(content: bytes, leaf_id: str | None = None):
    events = parse_pi_session(PiSessionInput(content=content, leaf_id=leaf_id))
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    sources = [await registry.resolve(event) for event in events]
    return tuple(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY) for source in sources)
```

解析器先验证完整文件，再返回事件。首条记录必须是 v3 session header；其余节点 ID 唯一，且显式提供 `parentId`，
值为 null 或之前出现的节点 ID。编码、JSON、图连接不合法或指定 leaf 不存在时，均抛出 `ValueError`，不返回部分结果。
控制节点保留图上的连接关系，但不生成文本证据。返回事件沿完整根到叶路径排列；压缩、分支摘要和上下文编辑不会删除此前的原始证据。

text 块保留原字符串，并以换行连接。工具调用格式为 `[tool_call NAME ID] COMPACT_SORTED_JSON_ARGUMENTS`，
工具结果格式为 `[tool_result NAME ID] TEXT`。Bash 消息保留 `$ COMMAND`、换行和完整输出。摘要与 custom message
保留可见文本。thinking、图片、system message 和控制节点不进入文本投影。每个可见事件仍保存完整原始 `record`；
采集过程不会执行记录里的工具参数或命令。

每个事件形成定义版本为 `1` 的 captured `pi-event` Source。其名称由 `pi:` 加上紧凑 Unicode JSON 数组
`[session_id,event_id]` 的 SHA-256 构成，共 67 个字符；身份只取决于两个原始 ID，保留其中有意义的空白。
`registry.read(source)` 返回完整 `PiEventInput`。标准 `powercontext.text-evidence/1` 投影保留完整 `ROLE: BODY`
正文，以及 `session_id`、`event_id`、`parent_id`、`event_type`、`role` 五个 metadata 键。
Source 不会为了适配 Memory 单条限制而截断。配置好的 Memory pipeline 可拆分自己的条目并引用完整 Source，
检索命中仍可通过精确 `MemoryCitation` 回查。

增量采集使用现有 `ConnectorLifecycle`，为其提供对接 Runtime 的 Source sink 和持久化 checkpoint store。
提交 observation 前先通过 manifest 注册 `PI_EVENT_SOURCE_DEFINITION`。已持有该 lifecycle 的应用可这样运行：

```python
from powercontext.sources import ConnectorBinding
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import PiSessionConnector


async def collect_history(lifecycle: ConnectorLifecycle, scope_id: str, path: str, leaf_id: str | None = None):
    connector = PiSessionConnector(path, leaf_id)
    binding = ConnectorBinding(
        scope_id=scope_id,
        binding_id="pi-session-history",
        connector_name=connector.name,
        connector_version=connector.version,
    )
    return await lifecycle.run(connector, binding)
```

多次运行和切换分支时保持同一 binding；不同 session 使用不同 binding。共享祖先只提交一次，另一分支已采集的证据仍然保留。
仅在 observation 成功持久化后，lifecycle 才推进 checkpoint。提交被拒绝、抛出异常或输入损坏时，已提交进度保持不变；
重试会安全去重此前已接受的 Source。

每次运行都会完整读取并验证文件，再校验所有已导入事件的原始记录摘要，包括当前未选中的分支。
已导入事件被改写或移除时抛出 `ValueError`，修复文件后再重试。应保留原 session ID 和已采集记录。
checkpoint 随累计已导入的可见事件数量增长，保存事件 ID 和记录摘要，不重复保存完整工具结果。
进度由 lifecycle 持久化，因此新建 Connector 或重启进程后仍可继续采集。

## 控制提示词采集

默认开启提示词采集。当前工作不应被记录时，请在启动 Pi 前关闭：

```bash
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

看起来包含密钥的提示词，以及超过 200,000 UTF-8 bytes 的提示词，永远不会被采集。打开采集本身不保证会产生
Memory；Server 仍需配置 generation model。

测试时可让采集等待 Source 处理完成：

```bash
export POWERCONTEXT_PI_FLUSH_ON_CAPTURE=true
pi
```

这会增加延迟，并不适合日常交互。未开启时，Pi 会记录 Source position，并在 agent 和会话边界以短时间预算尽力
flush。

## 使用显式工具和命令

`powercontext-project-context` skill 会说明何时调用原生 `pc_*` 工具。核心工具包括：

- `pc_search`、`pc_memory_list`、`pc_memory_get`、`pc_memory_revise`、`pc_memory_retire`；
- `pc_memory_changes` 用于查看变更历史，`pc_stats` 用于查看当前 Scope 的统计诊断；
- `pc_remember`、`pc_prepare_context`、`pc_capture_source`；
- `pc_handoff_activate`、`pc_handoff_prepare`、`pc_handoff_finalize`、`pc_handoff_commit`、
  `pc_handoff_continue`；
- `pc_experience_generate`、`pc_skill_generate`、`pc_experience_get`、`pc_skill_get`、`pc_review_list`、
  `pc_review_get`，用于生成候选以及只读查看 Artifact 和候选材料。
- `pc_topic_search`、`pc_topic_get`，用于按主题查询当前 Topic Memory，并读取带 Source 引用的精确版本。
- `pc_work_contract`、`pc_handoff_current`、`pc_handoff_acknowledge`、`pc_task_outcome`，用于结构化工作连续性。
- `pc_external_scan`、`pc_external_list`、`pc_external_resolve`，用于发现和检查宿主本地 External Skill；`pc_external_import` 只在明确确认后导入或 fork 一个精确解析过的 Skill。

查看候选材料不授予批准、拒绝、修订、安装、发布或执行权限。
Topic Memory 查询是只读的；结果属于不可信历史证据，不是指令来源。
结构化工作工具会改变持久状态，必须经过交互确认；无 UI 时拒绝写入。返回的 Handoff、引用和检查结果必须按原值传递，不能把历史内容当作新的授权；`handoff_receipt_ref` 只能引用 accepted committed Handoff 的 Receipt。

显式持久化写入在交互式 Pi 会话中必须确认；没有交互 UI 时，Pi 会拒绝写入而不会静默持久化。`/pc doctor`、
`/pc search <query>`、`/pc remember <text>`、`/pc flush` 和 `/pc stats` 可直接查看状态和维护内容。

## 连接启用鉴权的 Server

在受保护环境中启动启用鉴权的 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

使用完整匹配 header 启动 Pi：

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

不要把凭据放进 `POWERCONTEXT_PI_BASE_URL`。环回地址默认允许明文 HTTP；非环回 Server 使用 HTTPS，或显式设置
`POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true`，HTTPS 证书校验仍然启用。
安装与持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。

## 验证安装

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` 会检查 Pi 可执行文件是否存在，以及 Pi 是否列出了 PowerContext package。修改 PowerContext 环境变量后
需要重启 Pi。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_PI_SCOPE_ID` | 未设置 | 在 workspace binding 和 Server 默认值之前显式选择已有 Scope |
| `POWERCONTEXT_PI_AUTHORIZATION` | 未设置 | package HTTP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | 把符合条件的用户提示词采集为 Source 证据 |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `1000` | 单请求超时，单位毫秒 |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `4000` | 召回/采集共享 HTTP 时间预算，单位毫秒 |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | 请求并校验的 PreparedContext byte 上限（`512`–`32768`） |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | 在 prompt hook 中等待已采集 Source 的处理 |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | 一个 pending Source 最多 flush 次数 |
| `POWERCONTEXT_PI_DIAGNOSTICS` | `off` | 失败诊断输出：`off`、`stderr`，或写入 JSON 行的绝对文件路径（支持 `~/`） |

Pi 会拒绝包含凭据、query 或 fragment 的 base URL。召回、采集和边界 flush 都会正常降级；显式 `pc_*` 持久化写入
必须确认，Pi 没有交互 UI 时会被拒绝。修改这些变量后需要重启 Pi。

失败诊断默认不输出：Pi 的 TUI 通过光标定位在 stdout 上渲染，写到 stderr 的内容会落在输入栏里；需要查看时请设置
`POWERCONTEXT_PI_DIAGNOSTICS`。
