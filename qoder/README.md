# Recall（知返）

> 把课程笔记与论文 PDF 变成一个能按语义找到出处、并且记得你哪儿没学会的复习教练。

本文件是整个项目的入口文档。后端代码在 `app/`，样例与验证脚本在 `scripts/`，实测记录在
`docs/verification.md`，展示站在 `site/`。

---

## 1. 解决的问题

- 复习时找不到上学期某页笔记，只能一页页翻 PDF。
- 读完论文记不住哪一段支撑了哪个结论。
- 错题反复错：没人记录你哪道题、哪个概念一直没弄懂。

Recall 把「文档内容」和「你的学习状态」分开存：前者交给 seekdb 做可溯源的混合检索，
后者交给 PowerContext 做记忆复用。

## 2. 核心功能

| 能力 | 实现 |
| --- | --- |
| 文档导入 | `.md` / `.txt` / `.pdf`（PDF 仅文字层），约 500 字切块，保留 `doc_id`、`page`、`offset` |
| 混合检索 | seekdb 向量近邻 + ik 全文检索两路独立 SQL，应用层 RRF（k=60）融合，返回 top-k 片段与出处 |
| 引用问答 | 回答必须带 `[n]` 编号，并映射到 `citations`（doc/page/offset）；LLM 未配置时退化为抽取式回答 |
| 学习记忆 | 每次问答写 `qa_history`；标记「没懂」时另写 `weak_point`，复习页按次数生成清单 |
| 运行统计 | 导入文档数、切片数、检索/问答次数；手动基线实测后才估算节省时间 |

6 个 HTTP 接口（实现见 `app/main.py`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/docs` | 上传 `.md/.txt/.pdf`，返回 `doc_id`，后台切块+建索引 |
| GET | `/api/docs` | 文档列表与索引状态 |
| POST | `/api/search` | `{query, k}` → top-k 片段，含出处与两路得分 |
| POST | `/api/ask` | `{query, uid?, topic?, understood?}` → 回答 + `[n]` 引用 + 命中片段 |
| GET | `/api/review?uid=` | 今日复习清单（薄弱点来自 PowerContext 记忆） |
| GET | `/api/stats` | 导入量、检索次数、估算节省时间 |

## 3. 技术分工

一句话：**seekdb 存“内容”，PowerContext 存“人”**。两者都由 FastAPI 应用调用，应用本身不持久化业务数据。

### 3.1 seekdb：内容检索与出处

- 负责建库建表、保存切片正文与向量、执行两路检索 SQL。
- 表 `chunks` 同时声明：
  - `embedding VECTOR(384)`
  - `FULLTEXT INDEX ft_content(content) WITH PARSER ik`
  - `VECTOR INDEX vidx(embedding) WITH (distance=cosine, type=hnsw, lib=vsag)`

  建表逻辑见 `app/store.py:45-65`，等价 SQL 见 `scripts/init_seekdb.sql`。
- 检索是两条**真实执行**的 SQL（`app/search.py:18-34`）：
  - 向量路：`COSINE_DISTANCE(embedding, %s) ... ORDER BY dist APPROXIMATE`
  - 全文路：`MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE)`
  - 应用层 RRF 融合，常数 `RRF_K = 60`（`app/search.py:37`、`app/search.py:117-154`）。

**可核对证据：**

1. 直接看索引定义：

   ```bash
   mysql -h127.0.0.1 -P2881 -uroot -e "SHOW CREATE TABLE recall.chunks\G"
   ```

   预期输出中能看到（本机实测）：

   ```text
   `embedding` VECTOR(384) DEFAULT NULL,
   FULLTEXT KEY `ft_content` (`content`) WITH PARSER ik ...
   VECTOR KEY `vidx` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, ...)
   ```

2. 跑一次检索，日志会打印两路 SQL 原文、参数、耗时与命中数（`app/search.py:69-72`、`app/search.py:85-88`）。
   自检脚本会把它们汇总成：

   ```text
   向量路：耗时 1.9 ms | 命中 9 条
     SQL: SELECT id, doc_id, doc_name, page, offset_in_doc, content, COSINE_DISTANCE(embedding, %s) AS dist FROM chunks ORDER BY dist APPROXIMATE LIMIT %s
   全文路：耗时 2.9 ms | 命中 7 条
     SQL: SELECT id, doc_id, doc_name, page, offset_in_doc, content, MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score FROM chunks WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) ORDER BY score DESC LIMIT %s
   ```

3. 实测结果见 `docs/verification.md` 的 5.2 节：样例 1 中向量路 2.8 ms、全文路 87.7 ms
   （耗时每次有毫秒级波动，以 `PASS/FAIL` 与 SQL 原文为准）。

### 3.2 PowerContext：按用户记住薄弱点与历史

- 负责按 `uid` 隔离的记忆存储与读取，不参与文档检索。
- 每个 `uid` 对应一个 scope，`title = recall:<uid>`；先分页 `GET /v1/scopes` 查找，
  不存在才 `POST /v1/scopes` 创建，并带 `idempotency_key`（`app/memory.py:83-107`）。
- 写入 `POST /v1/memory/remember`：
  - `kind=weak_point`，`text=<topic>｜<状态>`（如 `聚簇索引｜没懂`）
  - `kind=qa_history`，`text=<query>｜<topic>｜<状态>｜<ISO 时间>`
- 读取 `POST /v1/memory/entries/list`，按 `kind` 过滤并把 `weak_point` 按 topic 聚合为
  `{topic, count}`（`app/memory.py:164-177`、`app/memory.py:224-250`）。
- 服务不可达、超时或解析失败时，读返回空、写放弃，只打 WARNING，不阻断问答（`app/memory.py:104-106`、`app/memory.py:239-247`）。

**可核对证据：**

1. 样例 3 会生成真实写入/读取日志（`docs/verification.md` 5.2 节，节选）：

   ```text
   [memory] 创建 scope recall:smoke-9d11f5db（scp_4084mv5ad092qgpaytbk2ndg51）
   [memory] 写入 user:smoke-9d11f5db:qa_history 成功（共 1 条）
   [memory] 写入 user:smoke-9d11f5db:weak_points 成功（共 1 条）
   [memory] 读取 user:smoke-9d11f5db:weak_points 命中 1 条
   ```

2. 服务端数据可直接查：

   ```bash
   curl -s "http://127.0.0.1:8001/v1/scopes?limit=100"
   ```

   预期返回 JSON 的 `items` 中出现 `"title":"recall:<uid>"`。

3. 接口契约与降级行为详见 `docs/verification.md` 5.3 节；`app/memory.py` 不依赖任何模型能力。

> 分工边界：seekdb 的表只存文档切片，不记录用户；PowerContext 只存记忆条目，不存正文。
> `/api/ask` 同时读两者：先向 seekdb 要片段，再把问答与薄弱点写进 PowerContext。

## 4. 目录结构

```text
qoder/
├── README.md               # 本文件：项目入口、运行说明
├── REQUIREMENTS.md         # 赛题需求书
├── .env.example            # 配置模板（复制为 .env）
├── app/                    # FastAPI 后端
│   ├── main.py             # 6 个接口、切块、抽取式问答兜底
│   ├── search.py           # 向量路 + 全文路 SQL，RRF 融合
│   ├── store.py            # seekdb 连接、建表、写入与统计
│   ├── memory.py           # PowerContext 1.4.x 客户端（可降级）
│   ├── embed.py            # hash / sentence_transformers 两种 embedding
│   ├── requirements.txt
│   └── README.md           # 后端细节
├── scripts/
│   ├── init_seekdb.sql     # 手工建库建表（可选，应用启动会自动建）
│   ├── seed_docs.py        # 导入 samples/ 到 seekdb
│   └── smoke_test.py       # 4 个验证样例，全部 PASS 返回 0
├── samples/                # 3 篇样例文档
├── docs/
│   ├── verification.md     # 验证口径、实测结果、PowerContext 契约
│   └── qoder-log/          # Qoder 开发记录（6 个会话摘要）
└── site/                   # 静态展示站（与后端无运行时耦合）
```

## 5. 从零跑起来

### 5.1 前置条件

- Python 3.11+。本机实测 **3.12.10**。
- 能访问 seekdb 的 MySQL 端口 `2881`；本机装有 `mysql` 客户端（可选，用于核对）。
- PowerContext 服务（本项目实测 **1.4.1**），默认 `http://127.0.0.1:8001`。

获取代码（仓库根目录不是 `qoder/`，项目在子目录）：

```bash
git clone https://github.com/Black628/powercontext.git
cd powercontext/qoder
```

已经在本机时，只需：

```bash
cd qoder
```

> 命令口径：以下命令在本机 **Windows + Git Bash** 下逐条实测。
> Linux / macOS 把 `.venv/Scripts/python.exe` 替换成 `.venv/bin/python`；
> PowerShell / cmd 把路径写成 `.venv\Scripts\python.exe`。

### 5.2 第 1 步：seekdb

本机现状：**seekdb v1.4.0.0 跑在 WSL2 发行版 `seekdb` 里**，WSL 把容器端口转发到
Windows 的 `127.0.0.1:2881`，`root` 空密码。验证连通性：

```bash
mysql -h127.0.0.1 -P2881 -uroot -e "SELECT version();"
```

预期输出（本机实测）：

```text
version()
5.7.25-OceanBase seekdb-v1.4.0.0
```

如果连不上，先确认 WSL 发行版与其中的 seekdb 服务已启动：

```bash
wsl -l -v                    # 应看到 seekdb  Running
wsl -d seekdb                # 进入发行版，按你的安装方式启动 seekdb，完成后 exit 返回
```

本仓库**不包含** seekdb 的启动/安装脚本；WSL 里的启动命令取决于你的安装方式，请按本机实际情况替换。

纯 Linux 的等价部署方式（端口与本文一致）：

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
# 等待服务就绪后再执行上面的 mysql 验证命令
docker logs --tail 20 seekdb
```

> 本次实测版本为 seekdb v1.4.0.0；`oceanbase/seekdb` 镜像标签可能随时间变化，
> 若要严格对齐，请固定到对应版本标签。

应用启动时会自动执行 `CREATE DATABASE/TABLE`（`app/store.py:92-110`），**不需要**先手工建库；
想手工初始化时再执行：

```bash
mysql -h127.0.0.1 -P2881 -uroot < scripts/init_seekdb.sql
```

### 5.3 第 2 步：PowerContext

先建虚拟环境并装依赖（只需一次）：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -U pip
.venv/Scripts/python.exe -m pip install -r app/requirements.txt
.venv/Scripts/python.exe -m pip install "powercontext[server]"
.venv/Scripts/python.exe -c "import importlib.metadata as m; print(m.version('powercontext'))"
```

预期最后一行：

```text
1.4.1
```

> 本次实测版本为 PowerContext 1.4.1。要严格复现可把安装命令改成
> `.venv/Scripts/python.exe -m pip install "powercontext[server]==1.4.1"`。

在**单独终端**启动服务，并保持前台运行：

```bash
.venv/Scripts/python.exe -c "from powercontext.server.cli import app; app()" run --host 127.0.0.1 --port 8001 --no-env-file
```

另开一个终端验证（不要在启动服务的终端里执行）：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/v1/scopes
```

预期输出：

```text
200
```

> PowerContext 没有模型也能启动，只是没有生成能力；本项目的记忆写入/读取不需要模型。
> 若返回 `000` 或连接失败，说明服务没起或端口不是 8001，请先解决再进入下一步（否则样例 3 会 FAIL）。

### 5.4 第 3 步：应用

在 `qoder/` 目录、**另一个终端**启动 FastAPI：

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

预期看到（PID、SQL 日志行会随环境不同，关键是最后一行）：

```text
INFO:     Started server process [xxxxx]
INFO:     Waiting for application startup.
... INFO recall.store: [store] SQL: CREATE TABLE IF NOT EXISTS chunks ...
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

验证：

```bash
curl -s http://127.0.0.1:8000/api/stats
```

首次启动、尚未导入时的预期输出：

```json
{"doc_count":0,"chunk_count":0,"search_count":0,"ask_count":0,"estimated_saved_minutes":null,"baseline_measured":false,"assumption":"手动基线未实测（MANUAL_LOOKUP_SECONDS=0），不估算节省时间"}
```

> **端口冲突（本机实测）**：8000 曾被一个 WSL 转发的 `python -m http.server` 占用，
> `netstat -ano | grep ":8000 "` 会看到 `wslrelay.exe`。此时 uvicorn 会报
> `[WinError 10048]` / `address already in use`。改用其他端口即可，例如
> `--port 8002`，并把后文所有 `8000` 同步替换。这是本机环境冲突，不是应用要求。
>
> seekdb 暂时不可用时，应用仍能启动（只打 `init_schema 失败` 的 WARNING），
> 但检索/问答接口会返回 503。

### 5.5 第 4 步：导入样例

```bash
.venv/Scripts/python.exe scripts/seed_docs.py
```

预期输出（耗时每次不同；`docs=3 chunks=9` 与每篇 `chunks=3` 应一致，
同一秒写入时列表顺序可能不同）：

```text
==============================================================================
Recall（知返）· 样例导入
seekdb : 127.0.0.1:2881/recall
embed  : hash (dim=384)
samples: .../qoder/samples  (3 篇)
chunk  : 约 500 字/块
==============================================================================

[OK] 全文索引与倒排表.md
     doc_id=961d96fb611deac9  字数=1176  块数=3  耗时=151.0 ms
     块偏移=[0, 529, 1053]
...
导入完成：3 篇，共 9 块，总耗时 314.6 ms
  - 算法导论-第3章.md              status=ready    chunks=3 doc_id=245e8acbbcc5e5b0
  - 全文索引与倒排表.md              status=ready    chunks=3 doc_id=961d96fb611deac9
  - 实验报告-索引性能.md             status=ready    chunks=3 doc_id=e251c41b707bb3aa
库内合计：docs=3 chunks=9
==============================================================================
```

脚本可重复执行：导入同名文档前会先删除旧块与登记行（`scripts/seed_docs.py:48-61`）。

### 5.6 第 5 步：自检

```bash
.venv/Scripts/python.exe scripts/smoke_test.py
echo "exit=$?"
```

预期末尾（耗时数值会波动，重点是 4 项全 PASS 且退出码 0）：

```text
====================================================================================
冒烟测试汇总
------------------------------------------------------------------------------------
  PASS  样例 1 检索+出处     全文索引与倒排表.md 命中 2 个片段；关键词覆盖 ['倒排表', '词项', '文档频率']/['倒排表', '词项', '文档频率']；两路耗时 向量=1.9ms 全文=2.9ms
  PASS  样例 2 混合检索      纯关键词 top-5 未命中目标块；向量候选 rank=6（已召回）；融合 rank=6；耗时 关键词=2.5ms 向量=1.3ms 全文=1.6ms
  PASS  样例 3 记忆复用      写入成功、重读命中、复习清单出现「聚簇索引」且次数=1
  PASS  样例 4 提效对比      本项目 3 次均成功，中位数 67.0ms；手动基线未实测，不计算提效倍数
====================================================================================
结果：4/4 项 PASS
exit=0
```

自检会直接调用 `app.search` / `app.memory`，不依赖 8000 端口的应用进程；但要求 seekdb 可连、
样例已导入，样例 3 还要求 PowerContext 可写。任一项失败返回 1。

> 已激活 `.venv` 时，本文的 `.venv/Scripts/python.exe` 都可以简写为 `python`；
> 验收命令即 `python scripts/smoke_test.py`。

## 6. 配置说明（.env）

所有配置都有代码内默认值，`.env` 可只写需要覆盖的项。仓库提供 `.env.example`：

```bash
cp .env.example .env
```

`.env` 已在 `.gitignore` 中，不要提交真实密钥。加载位置：

- 应用：`app/main.py`、`app/store.py` 等通过 `load_dotenv()` 读取**当前工作目录**的 `.env`，
  所以 uvicorn 要在 `qoder/` 下启动。
- `scripts/seed_docs.py`、`scripts/smoke_test.py`：显式读取 `qoder/.env` 与 `qoder/app/.env`。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SEEKDB_HOST` | `127.0.0.1` | seekdb 地址 |
| `SEEKDB_PORT` | `2881` | seekdb MySQL 协议端口 |
| `SEEKDB_USER` | `root` | 用户名 |
| `SEEKDB_PASSWORD` | 空 | 本机 root 空密码；有密码时填入 |
| `SEEKDB_DATABASE` | `recall` | 库名；不存在时应用自动创建 |
| `EMBEDDING_PROVIDER` | `hash` | `hash`（离线确定性）或 `sentence_transformers` |
| `EMBEDDING_DIM` | `384` | 向量维度，必须与表 `VECTOR(n)` 一致 |
| `EMBEDDING_MODEL` | 空 | 仅 `sentence_transformers` 时必填 |
| `CHUNK_SIZE` | `500` | 切块字符数；改了要重新导入 |
| `POWERCONTEXT_BASE_URL` | `http://127.0.0.1:8001` | 留空则记忆功能降级为空操作 |
| `POWERCONTEXT_API_KEY` | 空 | 服务端启用鉴权时才填 |
| `POWERCONTEXT_TIMEOUT` | `3` | 记忆请求超时（秒） |
| `LLM_BASE_URL` | 空 | 完整 chat completions 地址；留空走抽取式回答 |
| `LLM_API_KEY` | 空 | LLM 密钥（可选） |
| `LLM_MODEL` | 空 | 与 `LLM_BASE_URL` 同时配置才启用 LLM |
| `LLM_TIMEOUT` | `30` | LLM 请求超时（秒） |
| `MANUAL_LOOKUP_SECONDS` | `0` | 手动翻找中位数；`0` = 未实测，不计算提效 |
| `RECALL_LOOKUP_SECONDS` | `10` | 本项目单次查找的估算用时（秒），用于 `/api/stats` |

注意：`EMBEDDING_DIM` 一旦改动，已有表的 `VECTOR(384)` 与之不匹配，需要先删除 `chunks` 表
再重新导入（本机默认保持 384）。

## 7. 验证样例

4 个样例由 `scripts/smoke_test.py` 自动执行，完整口径、证据字段与实测记录见
**[`docs/verification.md`](docs/verification.md)**。摘要：

| 样例 | 输入 | 验证点 | 实测 |
| --- | --- | --- | --- |
| 1 · 检索 + 出处 | `倒排索引为什么比顺序扫描快` | `全文索引与倒排表.md` ≥2 个片段；片段含「倒排表/词项/文档频率」；带 doc/page/offset；两路 SQL 与耗时 | PASS |
| 2 · 混合检索的必要性 | `B+ 树和 LSM 树在写放大上的区别` | 纯关键词 top-5 漏掉只写 `log-structured` 的目标块；向量路召回；RRF 融合后仍在结果中 | PASS |
| 3 · 记忆复用 | `聚簇索引是什么`，标记「没懂」 | `weak_points` 写入成功；重读命中；`/api/review` 出现 `聚簇索引`，`count=1` | PASS |
| 4 · 提效对比 | 同一问题检索 3 次取中位数 | 3 次均成功、中位数 > 0；手动基线未实测时不产出倍数 | PASS |

本机最终结果：**4/4 PASS，退出码 0**（2026-10-29，seekdb 1.4.0.0 + PowerContext 1.4.1）。
样例 3 的逐条日志、PowerContext 真实接口契约、常见失败排查均在 `docs/verification.md`
（5.2、5.3、6 节）。

## 8. 已知限制

1. **PowerContext 无模型时的能力边界**：它只负责存取记忆条目，不生成回答、不做语义归纳。
   本项目的问答生成由可选 LLM 或抽取式兜底完成，与 PowerContext 无关。
2. **记忆时间字段缺失**：PowerContext 1.4.1 的 `AtomicMemoryRecord` 不含创建时间与 `reason`，
   所以复习项的 `last_time` 目前为空；时间只保存在 `qa_history` 的 `text` 里
   （`docs/verification.md` 5.3 节）。
3. **手动基线未实测**：`MANUAL_LOOKUP_SECONDS=0`，样例 4 只报告本项目耗时，不给出提效倍数；
   `/api/stats` 的 `estimated_saved_minutes` 为 `null`。补测 3 次手动翻找中位数并配置该变量后才会计算。
4. **默认 embedding 不是语义模型**：`hash` 是确定性哈希词袋，强调离线可复现与零密钥；
   真实语义检索请切 `sentence_transformers`（需安装依赖、设置模型与维度，并重建索引）。
5. **PDF 仅支持文字层**：扫描版 PDF 没有可提取文字，会得到 422；本项目未集成 OCR。
6. **样例 2 是受控演示**：`NATURAL LANGUAGE MODE` 对中文单字也计分，目标块在全文候选中可能以低分出现；
   因此判定口径是「用户可见的 top-k 是否漏召回」，不是「全文候选池是否为空」（`docs/verification.md` 第 1 节）。
7. **展示站是静态页**：`site/` 不连接运行中的后端，也没有可交互前端；所有实测数字以
   `docs/verification.md` 为准。
8. **本机端口/环境差异**：8000 可能被 WSL 转发服务占用（见 5.4）；WSL NAT 模式下
   `localhost` 代理提示与本项目无关，不影响 2881 端口转发。
9. **记忆数据会累积**：PowerContext 按 `uid` 持久化；冒烟测试默认每次生成新 uid，
   保证样例 3 的 `count=1` 可复现。手工用固定 uid 反复测试时，薄弱点次数会累加。

## 9. 代码仓库与站点

- 代码仓库：<https://github.com/Black628/powercontext>（本项目位于仓库的 `qoder/` 子目录）
- 展示站：<https://recall-review-coach-ehfkrv16fch.qoder.website>（公开访问）
- 站点本地预览：

  ```bash
  python -m http.server 8080 -d site
  ```

  然后打开 <http://127.0.0.1:8080>。
- Qoder 开发记录：`docs/qoder-log/sessions.md`（6 个会话的需求拆解、编码与调试摘要）。
