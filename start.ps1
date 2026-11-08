# 波段策略回测工作台 —— 一键启动脚本（Windows PowerShell）
# 功能：构建前端（如缺失）-> 启动后端 FastAPI 并托管前端静态产物 -> 打开浏览器
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$dist = Join-Path $frontend 'dist'

# 1) 前端构建（仅在 dist 不存在时；如需强制重建请先删除 dist 目录）
if (-not (Test-Path $dist)) {
    Write-Host '[1/3] 首次启动：构建前端（npm install + vite build，可能需要 1~3 分钟）…'
    Push-Location $frontend
    npm install
    npm run build
    Pop-Location
} else {
    Write-Host '[1/3] 检测到前端构建产物 dist，跳过构建。'
}

# 2) 启动后端（托管 dist，并在数据集为空时从样例 CSV 注入 7 只个股）
Write-Host '[2/3] 启动后端服务（http://127.0.0.1:8765）…'
$sample = Join-Path $backend 'data' 'sample_all_300d.csv'
if (-not (Test-Path $sample)) { $sample = Join-Path $backend 'data' 'sample_600186.csv' }
if (Test-Path $sample) { $env:WORKBENCH_SEED_CSV = $sample }
$env:PYTHONUTF8 = '1'

# 优先使用项目 venv，否则回退到系统 python
$venvPy = Join-Path $root '.venv' 'Scripts' 'python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { 'python' }

Push-Location $backend
Start-Process $py -ArgumentList '-m','uvicorn','main:app','--host','127.0.0.1','--port','8765' `
    -WindowStyle Normal -PassThru | Out-Null
Pop-Location

# 3) 等待服务就绪后打开浏览器
Write-Host '[3/3] 等待服务就绪并打开浏览器…'
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { break }
    } catch { Start-Sleep -Seconds 1 }
}
Start-Process 'http://127.0.0.1:8765/'
Write-Host '已启动。关闭后端窗口即可停止服务。'
