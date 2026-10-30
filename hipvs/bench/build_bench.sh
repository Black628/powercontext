#!/usr/bin/env bash
# 编译并运行 HNSW 构建对比（标准构建 vs 图装配构建）
#
#   ./build_bench.sh                 # 只编译
#   ./build_bench.sh --run out/50k   # 编译后直接跑 out/50k.kg + out/50k.vec
set -euo pipefail
cd "$(dirname "$0")"

CXX=${CXX:-g++}
OUT=${OUT:-build/kg_hnsw_bench}
mkdir -p build

echo "=== 编译 $OUT ==="
# 注意：不要加 -march=native。本机是 Zen4，开了之后 hnswlib 的 AVX-512 距离函数会假设 64 字节对齐，
# 而 data_level0_memory_ 里各点的向量偏移不满足对齐要求，实测直接段错误。
# 纯 -O3（走 SSE 路径）稳定，且标准构建与图装配两边编译条件一致，对比公平。
$CXX -O3 -std=c++17 -pthread \
  -I. -Ivendor \
  hnsw_bench.cpp -o "$OUT"
echo "编译完成: $OUT"

if [[ "${1:-}" == "--run" ]]; then
  PREFIX=${2:?需要给 --run <前缀，如 out/50k>}
  THREADS=${THREADS:-8}
  echo "=== 运行: $PREFIX.kg ==="
  "$OUT" --vectors "$PREFIX.vec" --kg "$PREFIX.kg" \
         --queries "${QUERIES:-200}" --k "${K:-10}" --threads "$THREADS" \
         --json "${JSON:-$PREFIX.bench.json}"
fi
