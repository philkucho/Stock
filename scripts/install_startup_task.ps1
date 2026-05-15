<#
.SYNOPSIS
  Windows Task Scheduler에 로그인 시 dev 스택(Docker + FastAPI + Next.js) 자동 실행 작업 등록.

.DESCRIPTION
  로그인 후 start-dev.ps1을 자동 실행합니다. Docker Desktop이 시작될 시간을 주려고
  기본 60초 지연을 둡니다. start-dev.ps1 내부에서도 Docker daemon이 준비될 때까지
  최대 3분 대기합니다.

  PowerShell 창은 사용자가 로그인한 세션에서 보이게 띄워집니다 (Interactive logon).

.PARAMETER DelaySeconds
  로그인 후 실행까지 지연 시간 (초). 기본 60.

.PARAMETER TaskName
  작업 스케줄러 이름. 기본 "StockDevStackStartup".

.PARAMETER Uninstall
  기존 작업 제거.

.EXAMPLE
  PowerShell 일반 권한으로:
    .\scripts\install_startup_task.ps1
    .\scripts\install_startup_task.ps1 -DelaySeconds 90
    .\scripts\install_startup_task.ps1 -Uninstall
#>

[CmdletBinding()]
param(
  [int]$DelaySeconds = 60,
  [string]$TaskName = "StockDevStackStartup",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $ProjectRoot "scripts\start-dev.ps1"

if (-not (Test-Path $StartScript)) {
  Write-Error "start-dev.ps1을 찾을 수 없습니다: $StartScript"
  exit 1
}

# Uninstall path
if ($Uninstall) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "작업 제거됨: $TaskName" -ForegroundColor Green
  } else {
    Write-Host "등록된 작업 없음: $TaskName"
  }
  exit 0
}

# Action: powershell.exe로 start-dev.ps1 실행. 실행 정책 우회.
$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`"" `
  -WorkingDirectory $ProjectRoot

# Trigger: 현재 사용자 로그인 시 + 지연
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$Trigger.Delay = "PT${DelaySeconds}S"

# Settings: 배터리에서도 실행, 누락 시 재시도
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0)  # 무제한 (dev 서버는 계속 떠 있음)

# Principal: Interactive 로그온 → PowerShell 창이 보임
$Principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Write-Host "기존 작업 갱신: $TaskName"
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Principal $Principal `
  -Description "로그인 후 ${DelaySeconds}초 뒤 Docker(stock_db) + FastAPI + Next.js를 자동 실행" | Out-Null

Write-Host ""
Write-Host "등록 완료" -ForegroundColor Green
Write-Host "  Task name : $TaskName"
Write-Host "  Trigger   : 로그인 시 (지연 ${DelaySeconds}s)"
Write-Host "  Command   : powershell.exe -File $StartScript"
Write-Host "  Workdir   : $ProjectRoot"
Write-Host ""
Write-Host "지금 즉시 테스트 실행:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "마지막 실행 결과 확인:"
Write-Host "    Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host ""
Write-Host "제거:"
Write-Host "    .\scripts\install_startup_task.ps1 -Uninstall"
