# Qoder 赛题需求书：Recall（知返）

> 用途：**直接粘进 Qoder 的对话框**，让它按此实现；你在 Qoder 里完成开发并留下记录。
> 目标分数结构：实用价值 / 使用效果 / 完成度 / 技术结合 / Qoder 实践 / 工程质量。

---

## 0. 一句话定位

把「课程笔记 + 论文 PDF」变成一个能按语义找到出处、并且**记得你哪儿没学会**的复习教练。

- **解决的痛点**：复习时找不到上学期某页笔记；读论文记不住哪段支撑了哪个结论；错题反复错。
- **提效指标**（要真实记录）：找一段资料从"翻 30 分钟"到"10 秒出结果并给出处"。

---

## 1. 技术要求映射（评审就是按这个打分）

| 要求 | 本项目怎么满足 | 可核对的证据 |
| --- | --- | --- |
| Qoder 开发（必选） | 需求拆解、编码、调试全程在 Qoder 完成 | Qoder 对话截图/录屏 + UID |
| seekdb（二选一） | 文档 chunk 的**向量 + 全文混合检索**，带出处 | 检索 SQL 日志、命中片段与页码 |
| PowerContext（二选一） | 记录每个人的薄弱知识点、复习进度、历史提问，下次复用 | 记忆写入/读取的请求与响应 |
| Qoder Sites | 发布项目网站（简介/功能/截图/录屏/仓库链接） | 站点 URL（无痕可访问） |

> 建议**两者都用**：seekdb 负责"内容检索"，PowerContext 负责"记人"。分工写清楚，技术结合这一栏能拿满。

---

## 2. 功能范围（MVP，两周内能做完）

### 2.1 必做（基础验收线）

1. **导入**：上传 `.md` / `.txt` / `.pdf`（PDF 先只做文字层），切块（约 500 字/块，带 `doc_id`、`page` 或 `offset`）。
2. **建索引**：每块写入 seekdb，同时建
   - 向量列（`embedding`），
   - 全文索引（`FULLTEXT ... WITH PARSER ik`，中文友好）。
3. **混合检索**：一条 SQL 同时用向量近邻 + `MATCH ... AGAINST`，再用 `RRF`/加权融合排序，**返回 top-k 片段 + 出处**。
4. **问答**：把 top-k 片段作为上下文交给 LLM，回答里**必须带引用编号**，点编号能跳回原文位置。
5. **记忆**：每次提问后，把「主题 / 是否答对 / 薄弱点」写入 PowerContext；复习页据此生成"今天该复习什么"。
6. **项目网站**：Qoder Sites 发布，含简介、功能截图、30 秒录屏、代码仓库、体验入口。

### 2.2 加分（有余力再做）

- 复习卡片自动生成（从薄弱点出题）；
- 会话记忆："上次你问到第三章，这次从那儿继续"；
- 使用统计页（导入文档数、检索次数、节省时间估算）。

---

## 3. 技术架构

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────┐
│  Web 前端     │────▶│  API 服务 (Python) │────▶│  seekdb      │
│  Next/静态页  │     │  FastAPI          │     │  20001/2881  │
└──────────────┘     │                   │     └──────────────┘
                     │                   │────▶┌──────────────┐
                     │                   │     │ PowerContext │
                     └───────────────────┘     │ HTTP API/SDK │
                                               └──────────────┘
```

### 3.1 seekdb 表设计

```sql
CREATE DATABASE IF NOT EXISTS recall;
USE recall;

CREATE TABLE chunks (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id     VARCHAR(64)  NOT NULL,
  doc_name   VARCHAR(256) NOT NULL,
  page       INT          NOT NULL DEFAULT 0,
  offset_in_doc INT       NOT NULL DEFAULT 0,
  content    TEXT         NOT NULL,
  embedding  VECTOR(1024),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FULLTEXT INDEX ft_content(content) WITH PARSER ik,
  VECTOR INDEX vidx(embedding)
) DEFAULT CHARSET = utf8mb4;
```

### 3.2 混合检索（核心 SQL，务必落成一条可展示的语句）

```sql
-- 参数：:qvec 查询向量, :qtext 查询原文, :k 返回条数
SELECT id, doc_name, page, content,
       COSINE_DISTANCE(embedding, :qvec) AS dist
FROM   recall.chunks
WHERE  MATCH(content) AGAINST(:qtext)
ORDER  BY dist ASC
LIMIT  :k;
```

> 若版本不支持在一条语句里同时用向量排序 + 全文过滤，则改为两路检索后在应用层做 RRF 融合，
> 但**两路的原始 SQL 与耗时都要打印到日志**，作为"技术实际生效"的证据。

### 3.3 PowerContext 记忆结构

| 记忆键 | 内容 | 用途 |
| --- | --- | --- |
| `user:{uid}:weak_points` | 薄弱知识点列表（带次数与最近时间） | 复习页排序 |
| `user:{uid}:progress` | 每门课已复习到哪一章 | 断点续学 |
| `user:{uid}:qa_history` | 最近 N 次问答摘要 | 上下文延续 |

写入时机：每次问答结束；读取时机：每次问答开始 + 复习页渲染。

---

## 4. API 约定（前后端对齐用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/docs` | 上传文档，返回 `doc_id`，后台切块+建索引 |
| GET | `/api/docs` | 文档列表与索引状态 |
| POST | `/api/search` | `{query, k}` → 片段列表（含出处、两路得分） |
| POST | `/api/ask` | `{query}` → 回答 + 引用 + 命中片段 |
| GET | `/api/review` | 今日复习清单（来自 PowerContext 记忆） |
| GET | `/api/stats` | 导入量、检索次数、估算节省时间 |

---

## 5. 验证样例（评审要按这个复现，务必真实可跑）

**初始数据**：仓库内 `samples/` 放 3 份文档

- `samples/算法导论-第3章.md`
- `samples/全文索引与倒排表.md`
- `samples/实验报告-索引性能.md`

**样例 1（检索 + 出处）**

- 输入：`倒排索引为什么比顺序扫描快`
- 预期：返回 `全文索引与倒排表.md` 中至少 2 个片段，且片段里能看到"倒排表""词项""文档频率"等字样，命中页/偏移可见。

**样例 2（混合检索的必要性）**

- 输入：`B+ 树和 LSM 树在写放大上的区别`
- 预期：纯关键词检索漏掉的部分（如只写了 "log-structured"）能被向量路召回；页面同时展示两路各自得分与融合结果。

**样例 3（记忆复用）**

- 步骤：先问 `聚簇索引是什么` 并标记"没懂" → 关闭页面 → 重新打开 `/api/review`
- 预期：清单里出现"聚簇索引"，并显示它出现 1 次。
- 证据：PowerContext 的写入与读取日志各一段。

**样例 4（提效对比）**

- 对同一问题记录：手动翻 PDF 用时 vs 本项目用时（各 3 次取中位数），写进网站。

---

## 6. 代码与目录约定

题目要求"相关项目资产放入对应 `qoder` 目录"，并且提交时的分支/commit 基于
`oceanbase/powercontext` 的 `2026-competition-ecosystem` 分支。

```
powercontext/            # 你 fork 的仓库，工作在 2026-competition-ecosystem
└── qoder/
    ├── README.md        # 项目简介 + 运行说明（见第 7 节）
    ├── app/             # 前端 + 后端源码
    ├── samples/         # 验证样例的初始数据
    ├── scripts/
    │   ├── init_seekdb.sql      # 建库建表 + 索引
    │   ├── seed_docs.py         # 导入 samples
    │   └── smoke_test.py        # 跑通 4 个验证样例
    ├── docs/
    │   ├── qoder-log.md         # Qoder 开发记录（截图清单 + UID）
    │   └── verification.md      # 验证样例与预期结果
    └── screenshots/
```

---

## 7. 运行说明（必须让评审能跑起来，照抄这份结构）

```markdown
## 环境
- Linux x86_64，Python 3.11+，Node 20+（如用前端框架）
- seekdb：docker run -d -p 2881:2881 oceanbase/seekdb
- PowerContext：<按官方文档启动的 HTTP 服务地址>

## 配置
cp .env.example .env
# SEEKD B_HOST/PORT/USER/PASSWORD
# POWERCONTEXT_BASE_URL
# EMBEDDING 模型与 API Key（用占位值，不要提交真实 key）

## 初始化
mysql -h127.0.0.1 -P2881 -uroot < scripts/init_seekdb.sql
python scripts/seed_docs.py

## 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000

## 自检
python scripts/smoke_test.py    # 应全部 PASS，并打印两路检索 SQL 与耗时
```

---

## 8. 提交材料对照（缺一项就掉分）

| 题目要求 | 你要交的东西 | 谁来做 |
| --- | --- | --- |
| 项目网站网址 | Qoder Sites 发布后的 URL，**无痕窗口能打开** | 你（Qoder 内点击发布） |
| 代码仓库 | 本仓库 `qoder/` 目录，公开或给评审开权限 | 你 push |
| 运行说明 | 第 7 节那份，替换成真实值 | 你（Qoder 产出，你核对） |
| 验证样例 | `docs/verification.md` + `samples/` | Qoder 实现，你跑一遍留截图 |
| Qoder 开发记录 | 关键环节截图/录屏 + **Qoder 账号 UID** | 你（这是归属核对依据） |
| 使用效果（鼓励） | 访问统计截图、3~5 位同学的使用次数与反馈 | 你 |

---

## 9. 让 Qoder 干活的顺序（建议按此顺序对话，便于留证）

1. **需求拆解**：把本文第 2、3 节丢给 Qoder，让它产出任务清单与接口草稿 → 截图。
2. **建表与导入**：让 Qoder 写 `init_seekdb.sql` 与切块/embedding 脚本 → 截图。
3. **混合检索**：让 Qoder 写检索模块，要求"两路 SQL 都打印日志" → 截图，并保留一次真实查询输出。
4. **记忆接入**：让 Qoder 接 PowerContext，产出写入/读取函数 → 截图，保留一次真实读写日志。
5. **前端与问答**：让 Qoder 出页面与引用跳转 → 截图。
6. **调试**：故意留一个 bug（如中文分词没生效）让 Qoder 定位修复 → 这段最有说服力。
7. **发网站**：Qoder Sites 建站、预览、发布，设为"任何人都可访问" → 录屏。

> 每一轮都保留：输入提示词 + Qoder 的关键输出 + 最终文件变更。这比只放一张成品截图有力得多。

---

## 10. 容易踩的坑

| 坑 | 说明 |
| --- | --- |
| 企业版账号发布 | 无法设"任何人都可访问"，必须用个人账号 |
| 只装组件不用 | 题目明确"仅安装或测试连接不满足要求"，必须有真实调用与结果展示 |
| 提交真实密钥 | 配置文件一律用占位值 |
| 站点发布后又改了内容 | 需再次点「发布」才更新线上版本；评审结束前别暂停/删除站点 |
| 分支不对 | 必须基于 `2026-competition-ecosystem` 开发，资产放 `qoder/` |
