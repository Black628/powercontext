# 一键跑通（Windows）：环境自检 -> 建库索引 -> 前后验证 -> 改码 -> 状态复用
#   .\scripts\run-demo.ps1
#   $env:MINIMAX_API_KEY="xxx"; .\scripts\run-demo.ps1
# 注：本文件以 UTF-8 BOM 保存，Windows PowerShell 5.1 才能正确解析中文。
param(
    [string] $PatchSource = 'auto',
    [string] $Spec = 'v2'
)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $PSCommandPath
$sdd = Join-Path $here 'sdd.ps1'

Write-Host '== 1/3 doctor ==' -ForegroundColor Cyan
& $sdd doctor
Write-Host ''
Write-Host '== 2/3 建库 + 索引 ==' -ForegroundColor Cyan
& $sdd init-db --recreate | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host 'init-db 失败，终止' -ForegroundColor Red; exit $LASTEXITCODE }
Write-Host ''
Write-Host '== 3/3 run-demo ==' -ForegroundColor Cyan
& $sdd run-demo --patch-source $PatchSource --spec $Spec
$code = $LASTEXITCODE
Write-Host ''
Write-Host '详细报告：records/demo-run-*.md ；测试日志：records/test-logs/' -ForegroundColor DarkGray
exit $code
