# 验证样例与预期结果

本文对应 `REQUIREMENTS.md` 第 5 节。所有样例由 `scripts/smoke_test.py` 自动执行，
逐项打印 `PASS/FAIL` 与实际证据；任一项失败时脚本以非 0 退出。

## 1. 前置条件

```bash
# 1) seekdb 已启动，且 .env 中 SEEKDB_* 指向它（首次启动会自动建库建表）
# 2) 导入三份样例文档（可重复执行，脚本会自动清理旧块）
python scripts/seed_docs.py

# 3) 样例 3 需要 PowerContext 1.4.x 服务在跑；默认连 http://127.0.0.1:8001
#    .env: POWERCONTEXT_BASE_URL=http://127.0.0.1:8001（不配置时使用同一默认值）
#    服务不可用时记忆读写自动降级，样例 3 会 FAIL 并打印警告

# 4) 跑 4 个样例
python scripts/smoke_test.py
```

> 样例 2 的判定用「用户可见的 top-k」而不是「全文候选池」：seekdb 的
> `NATURAL LANGUAGE MODE` 会把中文按单字+停用词建索引，目标块只要出现「写」这类
> 单字也会拿到非零分，因此全文路取回 `fetch_n` 个候选时目标块仍会以低分排在末尾。
> 只用倒排表的产品效果是 **top-k 漏掉它**，所以脚本比较的是纯关键词 top-k 是否命中。

样例数据（`samples/`，每篇 800~1500 字，按约 500 字切块）：

| 文档 | 覆盖关键词 | 切块数（约） |
| --- | --- | --- |
| `算法导论-第3章.md` | B+树、LSM、聚簇索引、向量检索 | 3 |
| `全文索引与倒排表.md` | 倒排表、词项、文档频率、分词、ik、聚簇索引、向量检索 | 3 |
| `实验报告-索引性能.md` | B+树、LSM、写放大、log-structured、聚簇索引、分词、ik、向量检索 | 3 |

> `实验报告-索引性能.md` 的附录段落只写了 `log-structured`，不含 `B+树 / LSM / 写放大`
> 字样，专门用于样例 2 证明「关键词路漏召回、向量路能召回」。

## 2. 验证样例表

| 样例 | 步骤 | 输入 | 预期结果 | 证据来源 |
| --- | --- | --- | --- | --- |
| 样例 1 · 检索 + 出处 | 1. 运行 `seed_docs.py` 完成导入<br>2. 调用混合检索 `POST /api/search`（等价：`search.hybrid_search`） | `倒排索引为什么比顺序扫描快`，`k=5` | ① `全文索引与倒排表.md` 至少返回 **2 个**片段；② 片段正文（前 300 字）中能看到 `倒排表`、`词项`、`文档频率`；③ 每条结果带 `doc_name`、`page`、`offset`；④ 向量路与全文路各有一段 SQL 与耗时 | `scripts/smoke_test.py` 的「样例 1/4」段：命中文档名/页码/偏移、片段原文、关键词命中位置、`[向量路]` 与 `[全文路]` 耗时 |
| 样例 2 · 混合检索的必要性 | 1. 按 `content LIKE '%log-structured%'` 定位目标块<br>2. 只跑全文路取 top-`k`，观察目标块是否出现在用户可见结果<br>3. 跑混合检索（取全部融合结果），打印目标块在两路与融合中的名次 | `B+ 树和 LSM 树在写放大上的区别`，`k=5` | ① 纯关键词 top-5 **不含**目标块（关键词漏召回）；② 目标块在**向量路**候选中被召回并有名次与距离；③ 目标块进入 RRF 融合结果并有名次；④ 两路都实际执行且都有命中，分别打印 SQL 与耗时；⑤ 融合结果逐条展示 `vector_rank/vector_dist`、`fulltext_rank/fulltext_score`、`rrf_score` | `scripts/smoke_test.py` 的「样例 2/4」段：纯关键词 top-5 列表、目标块两路名次行、融合前 5 条 + `app/search.py` 打印的两路 SQL 原文 |
| 样例 3 · 记忆复用 | 1. 提问 `聚簇索引是什么` 并以 `understood=false` 标记「没懂」<br>2. 写入 PowerContext 的 `weak_points` 与 `qa_history`<br>3. 模拟关闭页面后重新打开 `GET /api/review?uid=...` | 提问：`聚簇索引是什么`<br>主题：`聚簇索引`<br>`uid`：每次生成唯一值（如 `smoke-a1b2c3d4`） | ① PowerContext 出现 `user:{uid}:weak_points` 的写入成功日志；② 重新读取时有读取命中日志；③ `/api/review` 清单出现 `聚簇索引`，`count=1`，`source=weak_points` | `scripts/smoke_test.py` 的「样例 3/4」段：PowerContext **写入日志**、**读取日志**、`/api/review` 返回 JSON；日志来自 `app/memory.py` 的真实 HTTP 请求 |
| 样例 4 · 提效对比 | 1. 手动翻 PDF 找同一答案 3 次，记录秒数并取中位数（写入网站/`.env` 的 `MANUAL_LOOKUP_SECONDS`）<br>2. 本项目对同一问题检索 3 次（先预热 1 次，不计入），取中位数 | `倒排索引为什么比顺序扫描快`<br>手动基线 `MANUAL_LOOKUP_SECONDS`（默认 `0`，表示未实测） | ① 打印本项目 3 次耗时与中位数；② 本项目 3 次均成功且中位数 > 0；③ 手动基线 > 0 时打印基线并计算提效倍数（要求 > 1）；④ 手动基线为 0 时打印「手动基线未实测，不计算提效倍数」，仍判 PASS；⑤ 手动/本项目均以 3 次中位数为口径 | `scripts/smoke_test.py` 的「样例 4/4」段：3 次墙钟耗时、中位数；手动基线已配置时额外打印基线与提效倍数。累计节省时间可在 `GET /api/stats` 的 `estimated_saved_minutes` 核对（依赖 `MANUAL_LOOKUP_SECONDS`） |

## 3. 证据字段说明

`smoke_test.py` 每个样例都会输出以下字段，便于评审逐项核对：

- **两路 SQL 耗时**：`向量路：耗时 X ms | 命中 N 条`、`全文路：耗时 Y ms | 命中 M 条`，
  并附带**实际执行**的 SQL 原文（向量路为 `COSINE_DISTANCE(...) ORDER BY dist APPROXIMATE`；
  若引擎不支持 `APPROXIMATE`，会自动退回精确 `ORDER BY dist` 并打印 WARNING，
  日志里的 SQL 以实际执行的为准。全文路为 `MATCH(content) AGAINST(... IN NATURAL LANGUAGE MODE)`）。
- **片段与出处**：每条结果打印 `doc_name`、`page`、`offset`、`vector_rank/dist`、
  `fulltext_rank/score`、`rrf_score` 与前 160 字片段。
- **关键词覆盖**：样例 1 会逐个打印 `倒排表 / 词项 / 文档频率` 命中的结果序号与块偏移。
- **PowerContext 读写**：样例 3 打印 `[memory] 写入 user:...:weak_points 成功（共 N 条）`
  与 `[memory] 读取 user:...:weak_points 命中 N 条`，以及 `/api/review` 的完整 JSON。
- **退出码**：4 项全部 PASS 返回 0；任一 FAIL 返回 1，便于 CI/评审脚本判断。

## 4. 样例 4 的计时口径

- **手动基线**：同一问题在 PDF 中人工翻找 3 次，记录秒数取中位数，写入
  `docs/verification.md` 与 `.env` 的 `MANUAL_LOOKUP_SECONDS`。默认值为 `0`，表示
  **尚未实测**；未实测时冒烟测试只报告本项目耗时，不计算提效倍数。
- **本项目用时**：从发起混合检索到拿到 top-k 片段的墙钟时间（含 embedding、两路 SQL、
  RRF 融合），脚本先预热 1 次再测 3 次取中位数。
- **提效倍数**：仅当 `MANUAL_LOOKUP_SECONDS > 0`（已实测）时计算，口径为
  `手动中位数 ÷ 本项目中位数`；`/api/stats` 会按检索/问答次数累计
  `estimated_saved_minutes`，用于网站展示，前提同样是手动基线已实测。

> 说明：样例 4 的“手动 3 次”需要人工操作，脚本只自动化“本项目 3 次”；手动基线的
> 3 次原始记录请连同截图放入 `screenshots/`，并在网站使用效果页引用。

## 5. 本次实测记录

### 5.1 复现环境（可重跑）

| 项 | 值 |
| --- | --- |
| seekdb | `seekdb-v1.4.0.0`（MySQL 5.7.25 协议），跑在 WSL2 发行版 `seekdb` 中，端口转发到 Windows `127.0.0.1:2881` |
| 建表 | `scripts/init_seekdb.sql`，`VECTOR(384)` + `FULLTEXT ... WITH PARSER ik` + `VECTOR INDEX ... WITH (distance=cosine, type=hnsw, lib=vsag)` |
| embedding | `EMBEDDING_PROVIDER=hash`（dim=384，离线确定性） |
| 数据 | `python scripts/seed_docs.py` → 3 篇文档 / 9 块，总耗时 574.5 ms |
| PowerContext | **1.4.1**，跑在 `http://127.0.0.1:8001`（`POWERCONTEXT_BASE_URL` 默认值）；每个 uid 一个 scope，title 为 `recall:<uid>` |

### 5.2 逐项结果

| 样例 | 结果 | 实测证据 |
| --- | --- | --- |
| 样例 1 检索 + 出处 | PASS | `全文索引与倒排表.md` 命中 2 个片段（offset 0 / 529），`倒排表`、`词项`、`文档频率` 全部出现；**向量路 2.8 ms / 全文路 87.7 ms**（两路各自执行并打印 SQL 与耗时） |
| 样例 2 混合检索必要性 | PASS | 纯关键词 top-5 **未**命中目标块；目标块在向量路 rank=6 被召回；RRF 融合后 rank=6；关键词 1.8 ms / 向量 1.3 ms / 全文 1.6 ms |
| 样例 3 记忆复用 | **PASS** | 首次调用创建 scope `recall:smoke-9d11f5db`（`scp_4084mv5ad092qgpaytbk2ndg51`）；写入 `weak_point` 1 条、`qa_history` 1 条；重读命中 1 条；`/api/review` 返回 `topic=聚簇索引, count=1, source=weak_points`。日志见下方 |
| 样例 4 提效对比 | PASS | 本项目 3 次 53.4 / 68.1 / 70.1 ms，中位数 **68.1 ms**，3 次均成功。**手动翻 PDF 的基线未实测**（`MANUAL_LOOKUP_SECONDS=0`），脚本输出「手动基线未实测，不计算提效倍数」 |

汇总：**4/4 PASS，退出码 0**。

样例 3 的真实日志与返回（`scripts/smoke_test.py` 输出节选，非手写）：

```text
PowerContext 写入日志：
  [memory] 创建 scope recall:smoke-9d11f5db（scp_4084mv5ad092qgpaytbk2ndg51）
  [memory] 写入 user:smoke-9d11f5db:qa_history 成功（共 1 条）
  [memory] 写入 user:smoke-9d11f5db:weak_points 成功（共 1 条）

PowerContext 读取日志（模拟关闭页面后重新打开 /api/review）：
  [memory] 读取 user:smoke-9d11f5db:weak_points 命中 1 条

/api/review 返回：{"uid": "smoke-9d11f5db", "date": "2026-10-29", "items": [{"topic": "聚簇索引", "count": 1, "last_time": "", "source": "weak_points"}]}
复习项：topic=聚簇索引 count=1 source=weak_points last_time=
```

> 同一 uid 再次解析 scope 时不会重复创建：第二次只有读取日志、没有「创建 scope」日志，
> 服务端 `GET /v1/scopes` 中 title=`recall:smoke-9d11f5db` 的 scope 数量为 **1**。
> 各次运行的检索耗时会有毫秒级波动，判定口径以 PASS/FAIL 与证据字段为准。

> ⚠️ 样例 4 的"提效倍数"必须等手动基线实测后才有意义：`MANUAL_LOOKUP_SECONDS` 默认 `0`
> 表示未实测，此时脚本明确打印「手动基线未实测，不计算提效倍数」，不产出任何倍数。
> 待按同一问题累计手动翻找 3 次的真实中位数、配置该变量（> 0）后，再计算
> `手动中位数 ÷ 本项目中位数`。网站上的"使用效果"一栏因此保持「待补充」。

### 5.3 样例 3：PowerContext 1.4.1 对接与降级

`app/memory.py` 已整体迁移到 PowerContext 1.4.1 的真实接口，实测契约如下（均为本地 8001 端口验证）：

| 用途 | 接口 | 请求体 | 响应 |
| --- | --- | --- | --- |
| 列 scope | `GET /v1/scopes?limit=100&cursor=...` | — | `{items:[{scope_id,title,summary,...}], next_cursor}` |
| 建 scope | `POST /v1/scopes` | `{title,summary,idempotency_key}` | 201 `{scope_id,title,...}` |
| 写记忆 | `POST /v1/memory/remember` | `{scope_id,kind,text,reason}` | `{changed, records:[...]}` |
| 列记忆 | `POST /v1/memory/entries/list` | `{scope_id,limit,cursor?}` | `{entries:[{artifact,kind,text,state,...}], next_cursor}` |

**scope 策略**：每个 uid 一个 scope，title 固定 `recall:<uid>`。写入/读取前先分页
`GET /v1/scopes` 查找同名 scope，**不存在才创建**，创建时带
`idempotency_key=recall-scope:<uid>`；解析结果在进程内按 uid 缓存，避免重复查询。
实测同一 uid 第二次解析只读不建，服务端同名 scope 数量为 1。

**写入**：`append_memory(uid, records)` 把每个薄弱点写成 `kind=weak_point`，
`text` 固定为 `<topic>｜<状态>`（如 `聚簇索引｜没懂`），`reason` 记录来源（如 `ask`）。
问答历史写成 `kind=qa_history`，`text` 为 `<query>｜<topic>｜<状态>｜<ISO 时间>`。
单条写入失败只打 warning，不影响其余条目与问答主流程。

**读取**：`get_memory(uid)` 用 `entries/list` 分页取回全部条目，过滤 `kind=weak_point`，
以 `text` 中 `｜` 前的 topic 聚合出现次数，输出 main.py 期望的
`{topic, count, source, status, last_time}`；`get_memory(uid, "qa_history")` 反解历史条目。
HTTP 失败或解析失败均降级为空列表，只打 warning。

**降级行为**（保留阶段 1 约定）：`POWERCONTEXT_BASE_URL` 显式置空、服务不可达、
scope 创建失败、请求超时（`POWERCONTEXT_TIMEOUT`，默认 3 s）时，读返回空、写放弃并告警，
问答接口仍正常返回。

**已知限制**：1.4.1 的 `AtomicMemoryRecord` 只有
`artifact/kind/text/state/state_version/merged_into_id`，**不返回创建时间与 reason**，
因此复习项 `last_time` 目前为空；时间仅保存在 `qa_history` 的 `text` 中。
若后续版本暴露时间字段，可在 `get_memory` 中直接映射，无需改调用方。

**复现**：服务启动后直接 `python scripts/smoke_test.py`，样例 3 使用随机 uid，
即使重复执行也不会命中历史数据（每次 `count=1`）。


## 6. 常见失败与排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 前置检查提示“库内没有切片” | 未导入样例 | 运行 `python scripts/seed_docs.py` |
| 样例 1 片段不足 2 个 | 切块参数被改得过大/过小 | 保持 `CHUNK_SIZE=500` 重新导入 |
| 样例 2 纯关键词 top-k 命中目标块 | 样例文档被改写过，目标段落混入了查询词的单字/词项 | 恢复 `samples/实验报告-索引性能.md` 后重新导入 |
| 样例 3 FAIL：写入未成功 | PowerContext 未启动、端口不对，或 scope 创建失败（查看 `[memory]` warning） | 确认 `curl http://127.0.0.1:8001/v1/scopes` 可访问；接口与降级策略见 5.3 |
| 两路耗时显示“未捕获” | 检索在建立连接阶段就失败 | 先确认 seekdb 可连接、`GET /api/docs` 正常 |
