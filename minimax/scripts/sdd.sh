#!/usr/bin/env bash
# SDD 上下文模块统一入口（Linux / WSL）
#   ./scripts/sdd.sh doctor
#   ./scripts/sdd.sh init-db --recreate
#   ./scripts/sdd.sh context --task "..."
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
export PYTHONPATH="$ROOT/module${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONIOENCODING=utf-8
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi
exec "${SDD_PYTHON:-python3}" -m sddcontext "$@"
