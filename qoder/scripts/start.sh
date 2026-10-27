#!/usr/bin/env bash
# Recall Coach / 知新 —— Linux / macOS 启动脚本（seekdb 嵌入式 + PowerContext 嵌入式）
# 用法：bash scripts/start.sh [--port 8848]
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${COACH_PORT:-8848}"
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

exec python -m uvicorn app.main:app --host "${COACH_HOST:-127.0.0.1}" --port "$PORT"
