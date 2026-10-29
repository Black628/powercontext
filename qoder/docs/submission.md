# 提测材料索引（Recall 知返）

> 本文件把赛题要求的每一项材料，对应到仓库里的具体路径或可访问地址，方便评审逐项核对。
> 最后更新：2026-10-29

---

## 1. 项目网站网址

| 项 | 值 |
| --- | --- |
| URL | https://recall-review-coach-ehfkrv16fch.qoder.website |
| 可见范围 | `public`（Qoder Sites `update_access_policy` 已设 `confirmPublic`） |
| 站点状态 | `lifecycle: active`、`governance_status: normal` |
| 发布方式 | Qoder Sites（`prepare_site` → `publish_site`，由 Qoder CLI 执行） |
| 独立核验 | 无痕访问返回 `HTTP 200`，页面含「4 / 4 PASS」与验证表 |

站点内容章节：Hero → 痛点 → 解决方案 → 技术分工 → 架构图 → 核心功能 → 验证样例 → 如何运行 → 代码仓库 → 页脚。

## 2. 代码仓库

| 项 | 值 |
| --- | --- |
| 仓库 | https://github.com/Black628/powercontext |
| 分支 | `2026-competition-ecosystem`（题目要求的分支） |
| 项目目录 | [`qoder/`](https://github.com/Black628/powercontext/tree/2026-competition-ecosystem/qoder) |
| 基线 commit | `d647275`（`chore: initialize 2026 competition ecosystem directories`） |

## 3. Qoder 账号

| 项 | 值 |
| --- | --- |
| 用户名 | `BLACK-King` |
| 邮箱 | `3219669025@qq.com` |
| 账号类型 | `personal_professional_trial`（个人账号，满足"任何人都可访问"的发布前提） |
| 登录方式 | browser（`qodercli status` 可复核） |
| Qoder UID | 见提测表单填写值；本地 `qodercli status -o json` 的 `avatar_url` 中含用户 UUID，可在 Qoder `Settings → Profile → Edit` 二次确认 |

## 4. 运行说明

| 项 | 位置 |
| --- | --- |
| 项目入口与完整运行步骤 | [`qoder/README.md`](../README.md) |
| 应用侧说明 | [`qoder/app/README.md`](../app/README.md) |
| 配置模板 | [`qoder/.env.example`](../.env.example) |

运行链路：seekdb（2881）→ PowerContext（8001）→ FastAPI（8000）→ 导入样例 → 自检。
每条命令都在本机实测过，含 `WinError 10048`（端口占用）的排查与替代端口写法。

## 5. 验证样例

| 项 | 位置 |
| --- | --- |
| 样例说明（步骤 / 输入 / 预期 / 证据来源） | [`qoder/docs/verification.md`](verification.md) |
| 可执行自检脚本 | [`qoder/scripts/smoke_test.py`](../scripts/smoke_test.py) |
| 初始数据 | [`qoder/samples/`](../samples/)（3 篇 Markdown） |
| 建表脚本 | [`qoder/scripts/init_seekdb.sql`](../scripts/init_seekdb.sql) |
| 导入脚本 | [`qoder/scripts/seed_docs.py`](../scripts/seed_docs.py) |

最近一次结果：**4/4 PASS，退出码 0**（详见 `verification.md` 第 5.2 节）。

## 6. Qoder 开发记录

| 项 | 位置 |
| --- | --- |
| 索引 | [`qoder/docs/qoder-log/README.md`](qoder-log/README.md) |
| 完整对话（10 个会话，含时间戳） | [`qoder/docs/qoder-log/transcript-*.md`](qoder-log/) |
| 首提示词 / 末输出的速览 | [`qoder/docs/qoder-log/sessions.md`](qoder-log/sessions.md) |

记录由本机 `~/.qoder/projects/**/*.jsonl` 导出，保留 user 与 assistant 的文本回合，
可以核对"需求拆解 → 编码 → 调试 → 发布"的完整过程。

## 7. 技术分工（两项指定技术都实际生效）

| 技术 | 承担什么 | 可核对证据 |
| --- | --- | --- |
| seekdb v1.4.0.0 | 文档切片的存储与**向量 + 全文双路检索**，返回出处（doc_name / page / offset_in_doc） | `app/search.py` 两条独立 SQL 均打印原文与耗时；`chunks` 表含 `FULLTEXT ... WITH PARSER ik` 与 `VECTOR INDEX ... WITH (distance=cosine, type=hnsw, lib=vsag)` |
| PowerContext 1.4.1 | 按用户记住薄弱点与历史，`/api/review` 复用时读取 | `app/memory.py` 调用 `/v1/scopes`、`/v1/memory/remember`、`/v1/memory/entries/list`；样例 3 显示写入成功、重读命中、复习清单出现「聚簇索引」`count=1` |

分工边界：seekdb 只存文档切片、不记录用户；PowerContext 只存记忆条目、不存正文。
`/api/ask` 同时读两者：先向 seekdb 要片段，再把问答与薄弱点写进 PowerContext。

## 8. 已知限制

1. 样例 4 的**手动基线未实测**，因此全站不出现任何"提效 N 倍"的数字；`MANUAL_LOOKUP_SECONDS` 默认 0，
   `/api/stats` 返回 `baseline_measured: false`。
2. PowerContext 在**未配置生成模型**时只能读写记忆、不做语义抽取（启动日志会提示 Inference capability notice）。
3. PowerContext 1.4.1 的 `AtomicMemoryRecord` 不返回创建时间，复习项的 `last_time` 暂为空。
4. 本机 seekdb 跑在 WSL2 发行版中、端口转发到 `127.0.0.1:2881`；纯 Linux 上等价于
   `docker run -d -p 2881:2881 oceanbase/seekdb`。
