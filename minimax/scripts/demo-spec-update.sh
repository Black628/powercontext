#!/usr/bin/env bash
# 规格更新演示：v1 -> 重新 index -> 输出变化 -> v2 复原
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDD="$HERE/sdd.sh"

echo "== 1) 切到规格 v1 并重新索引 =="
"$SDD" reset --spec v1 >/dev/null
"$SDD" index >/dev/null
"$SDD" context --name spec-v1 --format text 2>/dev/null | grep -A6 'SPEC-02'

echo
echo "== 2) 切回规格 v2 并重新索引 =="
"$SDD" reset --spec v2 >/dev/null
"$SDD" index >/dev/null
"$SDD" context --name spec-v2 --format text 2>/dev/null | grep -A6 'SPEC-02'

echo
echo "对比：.state/context/spec-v1.txt 与 .state/context/spec-v2.txt"
