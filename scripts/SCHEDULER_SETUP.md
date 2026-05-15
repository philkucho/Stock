# 월별 매트릭스 자동 갱신 — Windows Task Scheduler 등록

## 1. 한 번만 — Task Scheduler 등록

PowerShell **관리자 권한**으로 실행:

```powershell
$action = New-ScheduledTaskAction `
  -Execute "C:\Users\philk\Documents\Stock\venv\Scripts\python.exe" `
  -Argument "-m scripts.monthly_refresh --pool default --presets all --refresh-cache" `
  -WorkingDirectory "C:\Users\philk\Documents\Stock"

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6am

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U

Register-ScheduledTask `
  -TaskName "StockAutotrader-MatrixRefresh" `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Description "매월 첫 일요일 06시 매트릭스 갱신 + walk-forward 검증"
```

매월 첫 일요일 06시 자동 실행 — 시장 닫혀있는 시점.

## 2. 수동 실행 (당장 갱신하고 싶을 때)

```powershell
# 캐시도 새로 받기 (월 1회 권장, 5~10분 추가)
python -m scripts.monthly_refresh --refresh-cache

# 캐시는 그대로 두고 매트릭스만 (캐시가 어제까지 있을 때)
python -m scripts.monthly_refresh

# 특정 시점 기준으로 (백필)
python -m scripts.monthly_refresh --anchor 2025-12-01
```

## 3. 결과 확인

매트릭스 실행 후:
- `data/matrix_runs.parquet`에 새 기간 셀들이 누적됨
- `data/refresh_2026-05.log` 같은 로그 파일에 실행 내역
- 프론트엔드 `/matrix` 페이지의 **Test** 셀렉트에 새 기간이 자동으로 추가됨
- **Train (compare)** 에서 직전 기간을 골라 ROBUST/OVERFIT 자동 검증

## 4. 백필 (과거 월 누락 시)

지난 12개월의 슬라이딩 윈도우를 한 번에 채우고 싶으면:

```powershell
# 매월 1일 기준으로 12회 반복
1..12 | ForEach-Object {
  $month = (Get-Date).AddMonths(-$_).ToString("yyyy-MM-01")
  python -m scripts.monthly_refresh --anchor $month
}
```

각 회차당 ~10분 → 총 2시간. 야간에 한 번만 돌리면 됨.

## 5. 등록된 작업 확인 / 제거

```powershell
# 확인
Get-ScheduledTask -TaskName "StockAutotrader-MatrixRefresh"

# 즉시 실행 (테스트)
Start-ScheduledTask -TaskName "StockAutotrader-MatrixRefresh"

# 제거
Unregister-ScheduledTask -TaskName "StockAutotrader-MatrixRefresh" -Confirm:$false
```
