<#
.SYNOPSIS
  Longterm (중장기) Task Scheduler 등록 — 2026-06-05 도입.

.DESCRIPTION
  매월 1일(or 첫 거래일) + 매일 outcome backfill 두 작업 등록:
    1) DailyStock_Longterm_Monthly  : 매월 1일 09:00 ET — 종목 재선정 (longterm_picks 갱신)
    2) DailyStock_Longterm_Outcomes : 매일 16:35 ET — 21/63/126/252d alpha 적재

  Longterm 시스템은 swing/intraday와 분리. Fidelity 수동 발주용 추천 (자동매매 X).

.PARAMETER MonthlyAt
  Monthly pick 시각 (HH:mm). 기본값 "09:00".

.PARAMETER OutcomesAt
  Outcome backfill 시각 (HH:mm). 기본값 "16:35" (기존 16:30 backfill 직후).

.PARAMETER Uninstall
  기존 작업 모두 제거.

.EXAMPLE
  관리자 PowerShell:
    .\scripts\install_longterm_task.ps1
    .\scripts\install_longterm_task.ps1 -Uninstall
#>

[CmdletBinding()]
param(
  [string]$MonthlyAt = "09:00",
  [string]$OutcomesAt = "16:35",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  Write-Error "venv python을 찾을 수 없습니다: $Python"
  exit 1
}

$MonthlyTaskName = "DailyStock_Longterm_Monthly"
$OutcomesTaskName = "DailyStock_Longterm_Outcomes"

if ($Uninstall) {
  foreach ($n in @($MonthlyTaskName, $OutcomesTaskName)) {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $n -Confirm:$false
      Write-Host "OK 제거됨: $n"
    } else {
      Write-Host "  등록된 작업 없음: $n"
    }
  }
  exit 0
}

# 1) Monthly pick — 매월 1일 09:00 ET
$MonthlyAction = New-ScheduledTaskAction `
  -Execute $Python `
  -Argument "-m scripts.longterm_monthly_pick" `
  -WorkingDirectory $ProjectRoot

# Windows Task Scheduler "Monthly" trigger
$MonthlyTrigger = New-ScheduledTaskTrigger -Daily -At $MonthlyAt
# Monthly trigger은 PS에서 직접 지원 안 함 — Daily + 스크립트 내 day-of-month 가드가 더 단순.
# 또는 schtasks /SC MONTHLY 직접 호출.

# 실제: Daily 트리거로 등록하고, monthly_pick 자체가 매일 호출돼도 같은 month에 이미 picks 있으면
# (pick_month, symbol) UNIQUE 제약 + on_conflict_do_update 로 멱등성 보장.
# 단 매일 503 universe 평가는 비용. → schtasks MONTHLY 사용:

$MonthlySettings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$MonthlyPrincipal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType S4U `
  -RunLevel Limited

if (Get-ScheduledTask -TaskName $MonthlyTaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $MonthlyTaskName -Confirm:$false
}

# schtasks MONTHLY 1일 09:00 (직접 schtasks.exe 호출 — Register-ScheduledTask Monthly 미지원)
$schtasksArgs = @(
  "/Create",
  "/TN", $MonthlyTaskName,
  "/TR", "`"$Python`" -m scripts.longterm_monthly_pick",
  "/SC", "MONTHLY",
  "/D", "1",
  "/ST", $MonthlyAt,
  "/RL", "LIMITED",
  "/F"
)
& schtasks.exe @schtasksArgs | Out-Null
Write-Host "  OK $MonthlyTaskName @ 매월 1일 $MonthlyAt"

# 2) Daily outcomes backfill
$OutcomesAction = New-ScheduledTaskAction `
  -Execute $Python `
  -Argument "-m scripts.longterm_outcomes_daily" `
  -WorkingDirectory $ProjectRoot

$OutcomesTrigger = New-ScheduledTaskTrigger -Daily -At $OutcomesAt

$OutcomesSettings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

if (Get-ScheduledTask -TaskName $OutcomesTaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $OutcomesTaskName -Confirm:$false
}

Register-ScheduledTask `
  -TaskName $OutcomesTaskName `
  -Action $OutcomesAction `
  -Trigger $OutcomesTrigger `
  -Settings $OutcomesSettings `
  -Principal $MonthlyPrincipal `
  -Description "매일 $OutcomesAt — Longterm picks의 21/63/126/252d alpha 적재" | Out-Null

Write-Host "  OK $OutcomesTaskName @ 매일 $OutcomesAt"

Write-Host ""
Write-Host "==== 등록 완료 (Longterm) ====" -ForegroundColor Green
Write-Host "  Monthly pick   : 매월 1일 $MonthlyAt — 종목 재선정"
Write-Host "  Outcomes daily : 매일 $OutcomesAt — alpha 적재"
Write-Host "  Logs           : $ProjectRoot\logs\longterm\YYYY-MM-DD.log"
Write-Host "                   $ProjectRoot\logs\longterm_outcomes\YYYY-MM-DD.log"
Write-Host ""
Write-Host "즉시 테스트 실행:"
Write-Host "  Start-ScheduledTask -TaskName $MonthlyTaskName"
Write-Host "  Start-ScheduledTask -TaskName $OutcomesTaskName"
Write-Host ""
Write-Host "수동 실행 (dry-run):"
Write-Host "  & '$Python' -m scripts.longterm_monthly_pick --dry-run"
Write-Host ""
Write-Host "제거:"
Write-Host "  .\scripts\install_longterm_task.ps1 -Uninstall"
