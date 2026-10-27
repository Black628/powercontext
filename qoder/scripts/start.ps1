# Recall Coach / 知新 —— Windows 启动脚本（SQLite 镜像后端 + MemoryMirror）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start.ps1 [-Port 8848]
param(
    [int]$Port = 8848,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not $env:COACH_PORT) { $env:COACH_PORT = "$Port" }
    python -m uvicorn app.main:app --host $BindHost --port $Port
}
finally {
    Pop-Location
}
