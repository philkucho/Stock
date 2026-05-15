<#
.SYNOPSIS
  Daily Auto Pipeline Task Scheduler 등록 (5-Model Intraday Stack).

.DESCRIPTION
  매일 6단계 자동 실행 (평일 ET 기준):
    1) 09:00 ET — picks logging (v3/scanner/integrated v10/dashboard 적재)
    2) 09:25 ET — preopen (5-Model Intraday Stack → watchlist top 5, dispatch_mode=orb_auto)
    3) 09:30 ET — trade (dispatch_mode=user_fixed plan을 사용자 입력 그대로 bracket 발송)
    4) 09:45 ET — confirm (dispatch_mode=orb_auto에 ORB+VWAP+RVOL → top 3 bracket 발송)
    5) 10:00~15:00 ET (1시간 간격 6회) — monitor (1차 hit 후 stop breakeven 갱신)
    6) 16:30 ET — backfill (1d/5d/10d outcome 적재)

  Trade/Confirm은 AUTO_TRADE_ENABLED=true 일 때만 실제 발송, false면 dry-run.

  로컬 시간 = ET 라고 가정 (사용자가 NJ 거주). 다른 시간대 PC면 인자로 수동 조정.

.PARAMETER LogAt
  Picks 로깅 시각 (HH:mm). 기본값 "09:00".

.PARAMETER PreopenAt
  Watchlist 산출 시각 (HH:mm). 기본값 "09:25" (개장 5분 전).

.PARAMETER TradeAt
  사용자 입력 plan (user_fixed) bracket 발송 시각 (HH:mm). 기본값 "09:30" (개장 시각).

.PARAMETER ConfirmAt
  ORB confirm + bracket 발송 시각 (HH:mm). 기본값 "09:45" (개장 후 15분).

.PARAMETER BackfillAt
  Outcome backfill 시각 (HH:mm). 기본값 "16:30".

.PARAMETER Uninstall
  기존 작업 모두 제거.

.EXAMPLE
  관리자 PowerShell:
    .\scripts\install_daily_pipeline_task.ps1
    .\scripts\install_daily_pipeline_task.ps1 -LogAt 08:55 -PreopenAt 09:20 -ConfirmAt 09:45
    .\scripts\install_daily_pipeline_task.ps1 -Uninstall
#>

[CmdletBinding()]
param(
  [string]$LogAt = "09:00",
  [string]$PreopenAt = "09:25",
  [string]$TradeAt = "09:30",
  [string]$ConfirmAt = "09:45",
  [string]$BackfillAt = "16:30",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  Write-Error "venv python을 찾을 수 없습니다: $Python"
  exit 1
}

$LogTaskName = "DailyStock_LogPicks"
$PreopenTaskName = "DailyStock_Preopen"
$TradeTaskName = "DailyStock_Trade"          # 09:30 — user_fixed dispatch
$ConfirmTaskName = "DailyStock_Confirm"
$MonitorTaskName = "DailyStock_Monitor"
$BackfillTaskName = "DailyStock_BackfillOutcomes"

if ($Uninstall) {
  foreach ($n in @($LogTaskName, $PreopenTaskName, $TradeTaskName, $ConfirmTaskName, $MonitorTaskName, $BackfillTaskName)) {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $n -Confirm:$false
      Write-Host "OK 제거됨: $n"
    } else {
      Write-Host "  등록된 작업 없음: $n"
    }
  }
  exit 0
}

function Register-PipelineTask {
  param(
    [string]$Name,
    [string]$At,
    [string]$Phase,
    [string]$Desc,
    [switch]$WeekdaysOnly
  )

  $Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m scripts.daily_pipeline --phase $Phase" `
    -WorkingDirectory $ProjectRoot

  if ($WeekdaysOnly) {
    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $At
  } else {
    $Trigger = New-ScheduledTaskTrigger -Daily -At $At
  }

  $Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 60)

  $Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited

  if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
    Write-Host "기존 작업 갱신: $Name"
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
  }

  Register-ScheduledTask `
    -TaskName $Name `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description $Desc | Out-Null

  Write-Host "  OK $Name @ $At ($Phase)"
}

Write-Host ""
Write-Host "Daily pipeline 등록 중 (Hybrid dispatch: user_fixed + orb_auto)..."
Register-PipelineTask $LogTaskName $LogAt "log" "평일 ${LogAt} — 4 시스템 picks 적재 (v3/scanner/integrated v10/dashboard)" -WeekdaysOnly
Register-PipelineTask $PreopenTaskName $PreopenAt "preopen" "평일 ${PreopenAt} — 5-Model Intraday Stack watchlist top 5 산출 → trade_plans (dispatch_mode=orb_auto)" -WeekdaysOnly
Register-PipelineTask $TradeTaskName $TradeAt "trade" "평일 ${TradeAt} — dispatch_mode=user_fixed plan을 사용자 입력값 그대로 bracket 발송 (AUTO_TRADE_ENABLED=true 시 실제 발송)" -WeekdaysOnly
Register-PipelineTask $ConfirmTaskName $ConfirmAt "confirm" "평일 ${ConfirmAt} — dispatch_mode=orb_auto에 ORB+VWAP+RVOL 평가 → top 3 bracket 발송 (AUTO_TRADE_ENABLED=true 시 실제 발송)" -WeekdaysOnly

# Monitor: 평일 10:00 ET 시작 + 15분 반복 (10:00~14:45 = 20회) — daily_loss + reconcile + halt + stop breakeven
# 2026-05-14 정정: 1시간 → 15분 (변동성 큰 종목 1차 hit 후 lag 단축)
$MonAction = New-ScheduledTaskAction -Execute $Python -Argument "-m scripts.daily_pipeline --phase monitor" -WorkingDirectory $ProjectRoot
$MonTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "10:00"
$MonTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At "10:00" -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 5)).Repetition
$MonSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$MonPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
if (Get-ScheduledTask -TaskName $MonitorTaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $MonitorTaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $MonitorTaskName -Action $MonAction -Trigger $MonTrigger -Settings $MonSettings -Principal $MonPrincipal -Description "평일 10:00~14:45 ET (15분 간격, 20회) — daily_loss 장중 체크 + reconcile + halt 감지 + 1차 hit 시 stop breakeven raise" | Out-Null
Write-Host "  OK $MonitorTaskName @ 10:00 ET (15min interval x 20, weekdays)"

Register-PipelineTask $BackfillTaskName $BackfillAt "backfill" "매일 ${BackfillAt} — 1d/5d/10d outcome 백필 (lookback=30d)"

Write-Host ""
Write-Host "==== 등록 완료 (Hybrid dispatch) ====" -ForegroundColor Green
Write-Host "  Picks log        : 평일 $LogAt (Mon-Fri)"
Write-Host "  Preopen          : 평일 $PreopenAt (watchlist 산출, dispatch_mode=orb_auto)" -ForegroundColor Cyan
Write-Host "  Trade            : 평일 $TradeAt (개장, user_fixed plan 발송)" -ForegroundColor Magenta
Write-Host "  Confirm          : 평일 $ConfirmAt (개장 15분 후, orb_auto plan에 ORB → bracket)" -ForegroundColor Yellow
Write-Host "  Monitor          : 평일 10:00~14:45 ET (15분 간격 x 20, Mon-Fri) — daily_loss + reconcile + halt + breakeven" -ForegroundColor Cyan
Write-Host "  Outcome backfill : 매일 $BackfillAt (Mon-Sun)"
Write-Host "  Logs             : $ProjectRoot\logs\daily_pipeline\YYYY-MM-DD.log"
Write-Host "                     $ProjectRoot\logs\intraday_confirm\YYYY-MM-DD.log"
Write-Host ""
Write-Host "현재 .env의 AUTO_TRADE_ENABLED 상태 확인:" -ForegroundColor Yellow
$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
  $autoTrade = Select-String -Path $envFile -Pattern "^AUTO_TRADE_ENABLED" | Select-Object -First 1
  if ($autoTrade) {
    Write-Host "  $($autoTrade.Line)"
    if ($autoTrade.Line -like "*=true*") {
      Write-Host "  WARN  실제 paper 주문이 매일 $TradeAt(user_fixed) / $ConfirmAt(orb_auto) 에 발송됩니다 (paper=loss risk 0)" -ForegroundColor Red
    } else {
      Write-Host "  INFO  현재 dry-run only. 실거래 활성화하려면 .env의 AUTO_TRADE_ENABLED=true 변경"
    }
  }
}
Write-Host ""
Write-Host "즉시 테스트 실행:"
Write-Host "  Start-ScheduledTask -TaskName $LogTaskName"
Write-Host "  Start-ScheduledTask -TaskName $PreopenTaskName"
Write-Host "  Start-ScheduledTask -TaskName $TradeTaskName"
Write-Host "  Start-ScheduledTask -TaskName $ConfirmTaskName"
Write-Host "  Start-ScheduledTask -TaskName $BackfillTaskName"
Write-Host ""
Write-Host "마지막 실행 결과 확인:"
Write-Host "  Get-ScheduledTaskInfo -TaskName $TradeTaskName | Format-List"
Write-Host "  Get-ScheduledTaskInfo -TaskName $ConfirmTaskName | Format-List"
Write-Host ""
Write-Host "수동 실행 (즉시):"
Write-Host "  & '$Python' -m scripts.daily_pipeline --phase preopen"
Write-Host "  & '$Python' -m scripts.daily_pipeline --phase trade"
Write-Host "  & '$Python' -m scripts.daily_pipeline --phase confirm"
Write-Host ""
Write-Host "제거:"
Write-Host "  .\scripts\install_daily_pipeline_task.ps1 -Uninstall"
