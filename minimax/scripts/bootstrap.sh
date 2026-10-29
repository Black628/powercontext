#!/usr/bin/env bash
# 依赖准备（Linux / WSL）：安装 pymysql 并做环境自检
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== 安装 Python 依赖 =="
python3 -m pip install --quiet --upgrade pymysql -i https://mirrors.aliyun.com/pypi/simple/ || \
  python3 -m pip install --quiet pymysql
python3 - <<'PY'
import sys
try:
    import pymysql
    print("python", sys.version.split()[0], "| pymysql", pymysql.__version__)
except ImportError as exc:
    print("pymysql 未安装：", exc)
PY

echo
echo "== 端口连通性 =="
for port in 2881 2882 8001 8000; do
  if timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null; then
    echo "  127.0.0.1:$port -> OPEN"
  else
    echo "  127.0.0.1:$port -> closed"
  fi
done

echo
echo "== 模块自检 =="
"$HERE/sdd.sh" doctor
