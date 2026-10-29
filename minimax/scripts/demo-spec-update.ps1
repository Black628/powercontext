# 规格更新演示：v1 -> 重新 index -> 输出变化 -> v2 复原
#   .\scripts\demo-spec-update.ps1
# 注：本文件以 UTF-8 BOM 保存，Windows PowerShell 5.1 才能正确解析中文。
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $PSCommandPath
$sdd = Join-Path $here 'sdd.ps1'

Write-Host '== 1) 切到规格 v1 并重新索引 ==' -ForegroundColor Cyan
& $sdd reset --spec v1 | Out-Null
& $sdd index | Out-Null
& $sdd context --name spec-v1 --format text 2>$null | Select-String -Pattern 'SPEC-02' -Context 0,6

Write-Host ''
Write-Host '== 2) 切回规格 v2 并重新索引 ==' -ForegroundColor Cyan
& $sdd reset --spec v2 | Out-Null
& $sdd index | Out-Null
& $sdd context --name spec-v2 --format text 2>$null | Select-String -Pattern 'SPEC-02' -Context 0,6

Write-Host ''
Write-Host '对比两次输出：.state/context/spec-v1.txt 与 .state/context/spec-v2.txt' -ForegroundColor DarkGray
