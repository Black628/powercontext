# Recall（知返）后端 · FastAPI

项目入口与完整运行步骤见 **[../README.md](../README.md)**。本目录只含 6 个 HTTP 接口、
seekdb 存储与混合检索、PowerContext 记忆客户端；展示站在 [../site](../site)，与后端无运行时耦合。

## 环境（本机实测）

| 项 | 值 |
| --- | --- |
| Python | 3.12.10（要求 3.11+） |
| seekdb | v1.4.0.0，`127.0.0.1:2881`，`root` 空密码；跑在 WSL2 发行版 `seekdb`，端口转发到 Windows。纯 Linux 等价：`docker run -d -p 2881:2881 oceanbase/seekdb` |
| PowerContext | 1.4.1，`http://127.0.0.1:8001`；无模型也能启动，记忆读写不需要模型 |

## 安装与启动（均在 `qoder/` 目录、Git Bash 下执行）

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -U pip
.venv/Scripts/python.exe -m pip install -r app/requirements.txt
.venv/Scripts/python.exe -m pip install "powercontext[server]"
cp .env.example .env
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux / macOS 把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`。
配置项逐条说明见 [../README.md 第 6 节](../README.md#6-配置说明env)。

PowerContext 另开终端启动：

```bash
.venv/Scripts/python.exe -c "from powercontext.server.cli import app; app()" run --host 127.0.0.1 --port 8001 --no-env-file
```

## 初始化

不需要手工建库：应用启动时 `store.init_schema()`（`app/store.py:92`）会自动建库建表。
需要手工初始化时：

```bash
mysql -h127.0.0.1 -P2881 -uroot < scripts/init_seekdb.sql
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

- **混合检索**：`app/search.py` 分别执行向量路（`COSINE_DISTANCE ... ORDER BY dist APPROXIMATE`）
  与全文路（`MATCH ... AGAINST`，ik 分词）两条 SQL，**两条 SQL 原文与各自耗时都会
  打印到 INFO 日志**，再用 RRF（k=60）融合排序。
- **embedding**：默认 `EMBEDDING_PROVIDER=hash`（确定性哈希向量，离线可跑通全部接口）；
  可切 `sentence_transformers`。代码内无任何硬编码密钥。
- **PowerContext**：`app/memory.py` 读失败返回空、写失败静默丢弃，仅打 WARNING，不阻断问答。
- **问答引用**：LLM 未配置时退化为抽取式回答，仍带 `[n]` 编号并映射到
  `citations`（doc_name/page/offset）。
- **端口**：本机复核时 8000 曾被 WSL 转发的 `http.server` 占用；若 uvicorn 报
  `WinError 10048`，改用 `--port 8002` 并同步替换下面的 URL。

## 快速自测

Windows Git Bash 下 `curl.exe` 无法打开中文文件名，JSON 里的中文也可能被 argv 转码，
因此下面用 Python `requests`（跨平台一致）：

```bash
# 1) 上传样例（会与 seed_docs 的文档重复；测完运行 seed_docs.py 清理重建）
.venv/Scripts/python.exe -X utf8 -c "import requests; r=requests.post('http://127.0.0.1:8000/api/docs', files={'file': open('samples/全文索引与倒排表.md','rb')}); print(r.status_code, r.text)"
sleep 1

# 2) 混合检索（观察服务端日志里的 [向量路] / [全文路] SQL 与耗时）
.venv/Scripts/python.exe -X utf8 -c "import requests; r=requests.post('http://127.0.0.1:8000/api/search', json={'query':'倒排索引为什么比顺序扫描快','k':5}); print(r.status_code, len(r.json()['results']))"

# 3) 恢复干净数据
.venv/Scripts/python.exe scripts/seed_docs.py

# 4) 完整验证：4 个样例、打印证据、全 PASS 时退出码 0
.venv/Scripts/python.exe scripts/smoke_test.py
echo "exit=$?"
```

样例口径与实测记录见 **[../docs/verification.md](../docs/verification.md)**。
