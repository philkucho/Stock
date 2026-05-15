"""단타 스캐너 — 2단 깔때기 (Universe → Daily Picks).

- stage1_universe: 월 1회 갱신, 30종목 watchlist
- stage2_daily_picks: 매일 08:55 ET 자동 실행, Top 3 + 백업 2
- catalysts: Earnings/News/PR/Recommendation 분리 소스
- scheduler: APScheduler cron
"""
