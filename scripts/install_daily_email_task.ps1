<#
.SYNOPSIS
  Windows Task Scheduler에 매일 09:00(로컬) Daily Email Report 작업을 등록.

.DESCRIPTION
  사용자가 9 AM ET 발송을 원하므로, 본 PC의 시스템 시간대가 미국 동부(ET) 라고
  가정하고 09:00 로컬에 등록합니다. 다른 시간대 PC라면 -At 인자로 수동 조정하세요.

.PARAMETER At
  발송 시각 (HH:mm, 24h 형식). 기본값 "09:00".

.PARAMETER TaskName
  작업 스케줄러에 등록될 이름. 기본값 "DailyStockEmailReport".

.PARAMETER Uninstall
  지정 시 기존 작업 제거.

.EXAMPLE
  PowerShell 관리자 권한으로:
    .\scripts\install_daily_email_task.ps1
    .\scripts\install_daily_email_task.ps1 -At 08:55
    .\scripts\install_daily_email_task.ps1 -Uninstall
#>

[CmdletBinding()]
param(
  [string]$At = "09:00",
  [string]$TaskName = "DailyStockEmailReport",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  Write-Error "venv python을 찾을 수 없습니다: $Python"
  exit 1
}

# Uninstall path
if ($Uninstall) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "✅ 작업 제거됨: $TaskName"
  } else {
    Write-Host "ℹ️ 등록된 작업 없음: $TaskName"
  }
  exit 0
}

# Install / re-install
$Action = New-ScheduledTaskAction `
  -Execute $Python `
  -Argument "-m scripts.daily_email_report" `
  -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $At

# AC/배터리 모두에서 실행, 누락 시 1시간 내 재시도
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# 현재 사용자 권한으로 실행 (콘솔 로그인 필요 없음 — 단, 비번 입력 필요할 수 있음)
$Principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType S4U `
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
  -Description "매일 ${At} 로컬에 모멘텀 스캐너 + 데일리 픽 + 전일 PnL을 이메일로 발송" | Out-Null

Write-Host ""
Write-Host "✅ 등록 완료" -ForegroundColor Green
Write-Host "  Task name : $TaskName"
Write-Host "  Schedule  : 매일 $At (로컬 시간)"
Write-Host "  Command   : $Python -m scripts.daily_email_report"
Write-Host "  Workdir   : $ProjectRoot"
Write-Host ""
Write-Host "▶ 즉시 테스트 실행:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "▶ 마지막 실행 결과 확인:"
Write-Host "    Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host ""
Write-Host "▶ 제거:"
Write-Host "    .\scripts\install_daily_email_task.ps1 -Uninstall"
