# 技术说明（一页）

## 1. 模块结构

```
module/sddcontext/
  cli.py        ── 命令入口（doctor/init-db/index/context/state/apply/verify/reset/run-demo/show/prompt）
  config.py     ── 环境变量配置 + seekdb 端口探测 + PowerContext 地址探测（不硬编码任何机器路径）
  pipeline.py   ── 建库 / 增量索引 / 上下文构建 / 基线恢复（CLI 与 demo 共用同一套逻辑）
  db.py         ── seekdb 访问层：DDL、FULLTEXT(ik) 索引、REPLACE 增量写入、MATCH/LIKE/符号三种检索、SQL 留证
  indexer.py    ── 扫描 specs/** + 源码 + tests/**，按 markdown 二级小节 / Python AST 顶层符号切片
  retriever.py  ── 任务文本 → 检索词（标识符 / CJK 片段 / SPEC-ID）→ 逐 kind 查询 → 归并排序
  context.py    ── 组装 JSON / 人读文本 / MiniMax prompt，附来源清单与四项自检
  state.py      ── 状态持久化：PowerContext HTTP 后端（主） + 本地 JSONL 后端（兜底）
  minimax_client.py ── OpenAI 兼容调用、模型回退、完整日志（prompt/response/usage/耗时/HTTP 状态）
  patcher.py    ── 纯 Python 的 unified diff 解析与应用（不依赖 patch/git，允许 ±20 行偏移匹配）
  apply.py      ── 上下文 → patch → 写盘 → 开发记录（任务/来源/补丁/实际写入一一对应）
  verify.py     ── 跑目标/回归/全量三个 unittest suite，留证并写回状态
  demo.py       ── run-demo 的 11 步编排与报告渲染
```

数据流：`任务 + 工程根目录` → `retriever`（seekdb 真查）→ `context`（带来源的规格/代码/测试 + 状态）
→ `apply`（MiniMax 或回放产出 patch → 应用）→ `verify`（前后对比测试）→ `state`（写回 PowerContext）。

## 2. 上下文是怎么选出来的

1. **切片**：规格按 `## ` 二级标题切分，symbol 取块内第一个 `SPEC-xx`；源码/测试用 AST 按顶层
   `def`/`class` 切分，测试类里的 `test_*` 方法单独成块（symbol 形如 `Class.test_name`）。
   每个切片都记住 `path + line_start + line_end + symbol + content_hash`。
2. **入库**：`REPLACE INTO sdd_docs`（唯一键 `doc_key = kind:path#symbol`），按 `content_hash`
   跳过未变切片，并把该文件已消失的旧切片删掉——所以「改规格 → 重新 index」是真的替换，不是新增。
3. **检索**（三种查询，全部真查库）：
   - `MATCH(content, path, symbol) AGAINST(<任务文本 + 标识符 + SPEC-ID>)`，FULLTEXT 索引 parser=ik；
   - `LIKE` 兜底（标识符、去掉下划线后的片段、CJK 连续片段），用于短词/新词；
   - `symbol IN (...)` 精确命中，用于把 `apply_discount` 这类标识符对应的函数块直接捞出。
4. **排序与配额**：命中方式优先（pinned > symbol > fulltext > like），同组内按 MATCH 相关度降序；
   配额 spec 5 / code 6 / test 6。规格先出，其次代码，最后测试。
5. **兼容性测试钉住**：`demo_project/sdd.json` 的 `always_include_tests` 声明回归测试文件，
   这些切片始终并入 `tests[]`（hit=pinned），避免「语义检索把兼容性测试排到第 7 条之外」。
6. **自检**：上下文附带 4 项检查（specs 命中、代码命中 `apply_discount`、tests 命中、
   规格文本是否出现 `min_total`），全部为 true 才算上下文合格；`sample/expected.json` 是机检口径。

## 3. 三项技术的分工

| 技术 | 在这个模块里负责什么 | 为什么是它 |
| --- | --- | --- |
| **seekdb** | 工程资料库：存切片 + FULLTEXT(ik) 索引；`context` 每次真查库并返回带 `path:line` 的来源 | 需要中文分词 + 结构化过滤（kind）+ 可审计的 SQL；每次查询都落 `records/seekdb-queries.md`，评审可直接看到 SQL 与返回行数 |
| **PowerContext** | 状态库：任务的已完成/待办/历史验证结果，跨进程（重启）读回，并注入下一次上下文 | 它本来就是「按 Scope 组织的持久上下文」，天然适合跨会话状态；写入用显式 Memory（`kind=sdd_task_state`），不依赖模型生成能力 |
| **MiniMax** | 唯一「会写代码」的一环：把规格 + 待改代码 + 测试拼成 prompt，产出 unified diff | 结构化上下文已由前两者准备完毕，模型只做受约束的代码变换；无 key 或额度不足时用同一份参照 patch 回放，保证流程可复现 |

三者的边界刻意做得很硬：**seekdb 不做推理、PowerContext 不存代码、MiniMax 不查库**。
所以任何一环不可用都能被单独识别、单独替换（例如把 `state.py` 换成 Handoff、把检索换成向量索引），
而不影响其他两环。

## 4. 可验证的关键设计

- **不写死**：`context` 无缓存、无常量答案；规格版本切换由「改文件 → `index` → 重查」驱动，
  `run-demo` 对 v1/v2 正文做字符串不等断言。
- **可回溯**：每条命中都带 `path:line-range`；`records/code-change.md` 记录「任务 → 用了哪些来源 →
  产出的 patch → 实际写入的文件」。
- **可复现**：`--patch-source replay` 使用 `sample/offline_patch.diff`（由 before/after 快照生成，
  `scripts/make_patch.py --check` 可校验是否漂移），日志里标注 `mode=replay / offline_replay=true`。
- **诚实留证**：真实调用失败（本机实测 HTTP 402 余额不足 / 404 模型下线）也会把状态码、
  `request_id`、原始错误体写进 `records/minimax.jsonl` 与 `records/minimax-response.json`，
  不伪装成成功。
