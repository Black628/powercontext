# SDD 上下文模块统一入口（Windows PowerShell）
#   .\scripts\sdd.ps1 doctor
#   .\scripts\sdd.ps1 init-db --recreate
#   .\scripts\sdd.ps1 context --task "..."
# 注：本文件以 UTF-8 BOM 保存，Windows PowerShell 5.1 才能正确解析中文注释。
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Rest
)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $PSCommandPath
$root = Split-Path -Parent $here

$env:PYTHONPATH = (Join-Path $root 'module')
$env:PYTHONIOENCODING = 'utf-8'
try { chcp 65001 | Out-Null } catch { }
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# 载入 .env（若存在）：键=值 形式，忽略注释与空行
$dotEnv = Join-Path $root '.env'
if (Test-Path -LiteralPath $dotEnv) {
    Get-Content -LiteralPath $dotEnv -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $idx = $line.IndexOf('=')
            $key = $line.Substring(0, $idx).Trim()
            $value = $line.Substring($idx + 1).Trim()
            if ($key) { Set-Item -Path "env:$key" -Value $value }
        }
    }
}

$python = if ($env:SDD_PYTHON) { $env:SDD_PYTHON } else { 'python' }
& $python -m sddcontext @Rest
exit $LASTEXITCODE
