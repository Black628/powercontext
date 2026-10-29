#!/usr/bin/env bash
# 一键跑通：环境自检 -> 建库索引 -> 前后验证 -> MiniMax/回放改码 -> 状态复用
#   ./scripts/run-demo.sh              # 无 key 时自动离线回放
#   MINIMAX_API_KEY=xxx ./scripts/run-demo.sh   # 真实调用 MiniMax
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDD="$HERE/sdd.sh"

echo "== 1/3 doctor =="
"$SDD" doctor || true
echo
echo "== 2/3 依赖检查 =="
"$SDD" index >/dev/null 2>&1 || {
  echo "index 失败，先尝试 init-db"; "$SDD" init-db --recreate >/dev/null; }
echo
echo "== 3/3 run-demo =="
"$SDD" run-demo "$@"
echo
echo "提示：详细报告见 records/demo-run-*.md，测试日志见 records/test-logs/"
