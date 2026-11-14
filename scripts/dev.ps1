# 一条命令起本地开发服务（Windows / PowerShell）。
#
# 用法：
#   .\scripts\dev.ps1            # 假模型（不联网）
#   .\scripts\dev.ps1 real       # 用 .env 里的真模型
#   $env:PORT=8126; .\scripts\dev.ps1
#
# 环境变量：PORT（默认 8125）、PYTHON（默认 python）、SKIP_SEED=1 不重铺演示数据。

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Backend = Join-Path $Root "backend"
$Port = if ($env:PORT) { $env:PORT } else { "8125" }
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$Mode = if ($args.Count -gt 0) { $args[0] } else { "fake" }

$VenvPy = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "→ 建虚拟环境 $Backend\.venv"
    & $Python -m venv (Join-Path $Backend ".venv")
}

Write-Host "→ 安装依赖"
# 用 -e：非 editable 安装会在这里落一份 build/ 副本，并把 app/tools 复制进
# site-packages —— 改了源码却跑的是旧拷贝，而且 cwd 一换就少一个包。
& $VenvPy -m pip install -q -e "$Backend[dev]"

$env:PYTHONIOENCODING = "utf-8"

Push-Location $Backend
try {
    New-Item -ItemType Directory -Force -Path var | Out-Null

    if ($env:SKIP_SEED -ne "1") {
        Write-Host "→ 铺演示数据 var/ui_live.db"
        & $VenvPy -m tools.seedui --db var/ui_live.db --reset
    }

    $LlmArgs = @()
    if ($Mode -ne "real") {
        $LlmArgs = @("--llm", "fake", "--script-session", "S-0001", "--frame-delay", "0.01")
    }

    Write-Host "→ 打开 http://127.0.0.1:$Port/"
    & $VenvPy -m tools.serveui `
        --db var/ui_live.db `
        --port $Port `
        --dist (Join-Path $Root "frontend\redesign") `
        --items-dir tests\fixtures\bank `
        @LlmArgs
}
finally {
    Pop-Location
}
