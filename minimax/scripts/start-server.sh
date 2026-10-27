#!/usr/bin/env bash
# Start a local PowerContext Server for the minimax SDD demo (Linux / macOS).
#
#   ./scripts/start-server.sh [--port 8000]
#
# The SQLite database and runtime files live under minimax/run/powercontext so
# the demo never touches your personal PowerContext data. Stop it with Ctrl-C.
# The launcher substitutes a newer SQLite when the interpreter's is older than
# the 3.38 that PowerContext requires.
set -euo pipefail
cd "$(dirname "$0")/.."
export POWERCONTEXT_HOME="${POWERCONTEXT_HOME:-$PWD/run/powercontext}"
exec "${POWERCONTEXT_PYTHON:-python3}" scripts/powercontext_launcher.py \
  server run --no-env-file "$@"
