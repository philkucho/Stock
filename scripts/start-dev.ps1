# Starts Docker (Postgres+TimescaleDB), FastAPI backend, and Next.js frontend.
# Usage (from project root):  .\scripts\start-dev.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Test-Port($Port) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return $null -ne $conn
}

# 1. Docker (stock_db)
Write-Host "[1/3] Docker (stock_db)..." -ForegroundColor Cyan

# Wait for Docker daemon to become ready (Docker Desktop can take 30-90s after login).
Write-Host "  waiting for Docker daemon..." -ForegroundColor Yellow
$daemonDeadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $daemonDeadline) {
    docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 3
}
if ($LASTEXITCODE -ne 0) { throw "Docker daemon not reachable after 3 minutes (is Docker Desktop installed/running?)" }

docker compose -f "$Root\docker-compose.yml" up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }

Write-Host "  waiting for healthy..." -ForegroundColor Yellow
$status = $null
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $status = docker inspect -f '{{.State.Health.Status}}' stock_db 2>$null
    if ($status -eq "healthy") { break }
    Start-Sleep -Seconds 2
}
if ($status -ne "healthy") { throw "stock_db not healthy after 60s (status=$status)" }
Write-Host "  ready." -ForegroundColor Green

# 2. FastAPI
if (Test-Port 8000) {
    Write-Host "[2/3] FastAPI: port 8000 already in use, skipping." -ForegroundColor DarkYellow
} else {
    $venv = Join-Path $Root "venv\Scripts\Activate.ps1"
    if (-not (Test-Path $venv)) { throw "venv not found at $venv (run: py -3.11 -m venv venv)" }
    Write-Host "[2/3] FastAPI on :8000..." -ForegroundColor Cyan
    # 0.0.0.0 바인딩 = LAN/Tailscale에서도 접근 가능 (폰에서 접속용)
    $cmd = "cd '$Root'; & '$venv'; uvicorn api.main:app --reload --host 0.0.0.0 --port 8000"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd | Out-Null
}

# 3. Next.js — production mode (dev 모드는 폰에서 hydration 안 됨, prod는 안정)
if (Test-Port 3000) {
    Write-Host "[3/3] Next.js: port 3000 already in use, skipping." -ForegroundColor DarkYellow
} else {
    $frontend = Join-Path $Root "frontend"
    if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
        Write-Host "  node_modules missing - run 'npm install' in frontend/ first." -ForegroundColor Red
    }
    Write-Host "[3/3] Next.js (production) on :3000..." -ForegroundColor Cyan
    # build → start. -H 0.0.0.0 = LAN/Tailscale 접근 가능.
    # 코드 변경 시 다시 실행하면 새로 빌드 후 띄움.
    $cmd = "cd '$frontend'; npm run build; if (`$LASTEXITCODE -eq 0) { npm start -- -H 0.0.0.0 -p 3000 } else { Write-Host 'BUILD FAILED' -ForegroundColor Red; pause }"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd | Out-Null
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  FastAPI: http://127.0.0.1:8000/docs"
Write-Host "  Next.js: http://localhost:3000"
