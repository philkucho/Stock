"""Integrated system — scanner의 검증된 알파(OOS Sharpe 3.01)에 v3의 quality layer 결합.

설계 근거 (30일 backfill 비교 결과):
  - scanner: 1d hit rate 73%, 단기 trigger 강점, 그러나 5/10d alpha mean revert (1.4%→0.1%)
  - v3:      좁은 universe + Stage 2 + 압축/확장이 5/10d alpha 누적 (4.4%/7.7%)

통합 전략 (Core=scanner, Layer=v3):
  1. Regime gate (v3 Block 0) — 방어모드면 long 차단
  2. Universe + base score = scanner WHITELIST 122종목 + score ≥ 4
  3. Quality layer = v3 C5 (Compression/Expansion) + C4 (Open Location) + D1 (RSI Structure)
  4. Composite scoring: scanner 50% + v3 quality 50%
  5. Filter out: RSI bad grade (climax/divergence), gap-and-fail
"""
