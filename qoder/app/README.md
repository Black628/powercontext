# Recall（知返）后端 · 阶段 1

FastAPI 服务：seekdb 向量+全文混合检索（带出处）、PowerContext 记忆（可降级）、
问答与复习清单、运行统计。**本阶段无前端页面。**

## 环境
- Python 3.11+
- seekdb：`docker run -d -p 2881:2881 oceanbase/seekdb`
- PowerContext：按官方文档启动 HTTP 服务（未启动时记忆功能自动降级）

## 配置
```bash
cp .env.example .env   # 全部密钥用占位值，勿提交真实密钥
```

## 初始化
```bash
# 方式一：客户端脚本（与 app/store.py 内 DDL 等价）
mysql -h127.0.0.1 -P2881 -uroot < ../scripts/init_seekdb.sql
# 方式二：跳过，服务启动时 store.init_schema() 会自动建库建表
```

## 依赖与启动
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000   # 在 qoder/ 目录下运行
```

## 接口（REQUIREMENTS 第 4 节）
| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/docs` | multipart 上传 `.md/.txt/.pdf`，返回 `doc_id`，后台切块+建索引 |
| GET | `/api/docs` | 文档列表与索引状态 |
| POST | `/api/search` | `{query, k}` → top-k 片段，含出处与两路得分 |
| POST | `/api/ask` | `{query, uid?, topic?, understood?}` → 回答 + `[n]` 引用 + 命中片段 |
| GET | `/api/review?uid=` | 今日复习清单（薄弱点次数来自 PowerContext 记忆） |
| GET | `/api/stats` | 导入量、检索次数、估算节省时间 |

## 关键行为
- **混合检索**：`app/search.py` 分别执行向量路（`COSINE_DISTANCE ... ORDER BY dist APPROX`）
  与全文路（`MATCH ... AGAINST`，ik 分词）两条 SQL，**两条 SQL 原文与各自耗时都会
  打印到 INFO 日志**，再用 RRF（k=60）融合排序——评审可直接从日志核对。
- **embedding**：默认 `EMBEDDING_PROVIDER=hash`（确定性哈希向量，离线可跑通全部接口）；
  可切 `sentence_transformers`。代码内无任何硬编码密钥。
- **PowerContext**：`app/memory.py` 读失败返回空、写失败静默丢弃，仅打 WARNING，不阻断问答。
- **问答引用**：LLM 网关未配置时退化为抽取式回答，仍带 `[n]` 编号并映射到
  `citations`（doc_name/page/offset），前端阶段可据此跳原文。

## 快速自测
```bash
curl -F "file=@../samples/全文索引与倒排表.md" http://127.0.0.1:8000/api/docs
curl -X POST http://127.0.0.1:8000/api/search -H 'Content-Type: application/json' \
     -d '{"query":"倒排索引为什么比顺序扫描快","k":5}'
# 观察服务端日志：应出现 [向量路] 与 [全文路] 两段 SQL 原文 + 耗时
```
