#!/usr/bin/env bash
# 端到端跑一组：建图（hipVS/CPU）→ HNSW 装配 → 与标准构建对比
#
#   ./run_one.sh <n> <dim> <k> <M> <threads> <backend> [label]
# 例：
#   ./run_one.sh 5000 128 32 16 4 cpu smoke
#   ./run_one.sh 100000 128 64 16 8 hipvs 100k-hipvs
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)

N=${1:?n}; DIM=${2:?dim}; K=${3:?k}; M=${4:?M}; THREADS=${5:?threads}; BACKEND=${6:?backend}
LABEL=${7:-n${N}}

OUT_DIR="$ROOT/bench/out"
mkdir -p "$OUT_DIR" "$ROOT/bench/results"
PREFIX="$OUT_DIR/$LABEL"

echo "=== 1/2 建图: n=$N dim=$DIM k=$K M=$M backend=$BACKEND ==="
python3 "$ROOT/graphbuild/build_graph.py" \
  --n "$N" --dim "$DIM" --k "$K" --M "$M" \
  --backend "$BACKEND" --out "$PREFIX.kg"

echo "=== 2/2 HNSW 构建对比 ==="
"$ROOT/bench/build/kg_hnsw_bench" \
  --vectors "$PREFIX.vec" --kg "$PREFIX.kg" \
  --queries "${QUERIES:-200}" --k "${TOPK:-10}" --threads "$THREADS" \
  --repeat "${REPEAT:-3}" \
  --json "$ROOT/bench/results/${LABEL}.json"

echo "结果: $ROOT/bench/results/${LABEL}.json"
