# 依赖准备（Windows）：安装 pymysql；检查 seekdb 与 PowerContext 可达性
#   .\scripts\bootstrap.ps1
# 注：本文件以 UTF-8 BOM 保存，Windows PowerShell 5.1 才能正确解析中文。
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $PSCommandPath
$sdd = Join-Path $here 'sdd.ps1'

Write-Host '== 安装 Python 依赖 ==' -ForegroundColor Cyan
python -m pip install --quiet pymysql -i https://mirrors.aliyun.com/pypi/simple/
if ($LASTEXITCODE -ne 0) {
    Write-Host 'pymysql 安装失败，请手动执行：python -m pip install pymysql' -ForegroundColor Red
}
python -c "import pymysql, sys; print('python', sys.version.split()[0], '| pymysql', pymysql.__version__)"

Write-Host ''
Write-Host '== 端口连通性 ==' -ForegroundColor Cyan
foreach ($port in 2881, 2882, 8001, 8000) {
    $client = New-Object System.Net.Sockets.TcpClient
    $ok = $false
    try {
        $ok = $client.ConnectAsync('127.0.0.1', $port).Wait(800)
    } catch { $ok = $false }
    finally { $client.Close() }
    $state = if ($ok) { 'OPEN' } else { 'closed' }
    Write-Host ("  127.0.0.1:{0} -> {1}" -f $port, $state)
}

Write-Host ''
Write-Host '== 模块自检 ==' -ForegroundColor Cyan
& $sdd doctor
