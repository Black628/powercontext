# Start a local PowerContext Server for the minimax SDD demo (Windows).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start-server.ps1 -Port 8000
#
# The SQLite database and runtime files live under minimax\run\powercontext so
# the demo never touches your personal PowerContext data. Stop it with Ctrl-C.
param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:POWERCONTEXT_HOME = Join-Path $root 'run\powercontext'
$python = if ($env:POWERCONTEXT_PYTHON) { $env:POWERCONTEXT_PYTHON } else { 'python' }
& $python (Join-Path $root 'scripts\powercontext_launcher.py') server run --no-env-file --port $Port
