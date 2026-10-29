#!/usr/bin/env bash
# 一条命令把 Recall（知返）跑起来：建表 → 导入样例 → 启动服务。
# 前置：seekdb 在 127.0.0.1:2881（root 空密码）；PowerContext 在 127.0.0.1:8001（可选，缺失则记忆降级）。
#
# 用法：
#   bash qoder/run.sh              # 默认端口 8000
#   PORT=8002 bash qoder/run.sh    # 端口被占用时换一个
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${PORT:-8000}"
PY="${PYTHON:-python3}"

echo "[1/3] 安装依赖"
$PY -m pip install -q -r requirements.txt

echo "[2/3] 建表并导入样例（seekdb 需已在 2881 就绪）"
$PY - <<'PYEOF'
import re, pathlib, pymysql
sql = pathlib.Path("scripts/init_seekdb.sql").read_text(encoding="utf-8")
sql = re.sub(r"--[^\n]*", "", sql)
conn = pymysql.connect(host="127.0.0.1", port=2881, user="root", password="", charset="utf8mb4", autocommit=True)
cur = conn.cursor()
for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
    cur.execute(stmt)
conn.close()
print("  schema ok")
PYEOF
$PY scripts/seed_docs.py

echo "[3/3] 启动服务 http://127.0.0.1:${PORT}"
exec $PY -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
