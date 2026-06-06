"""중장기(3~12개월) 종목 선정 모듈.

[[swing-mode-v1]] 후속으로 2026-06-05 도입. Monthly rebalance, Stage 2 trend
template + RS percentile + 12mo momentum. Fidelity 수동 발주용 추천 시스템.

자동매매 없음. backtest 검증 ([[longterm-v1-backtest]]):
  Sharpe 0.905, alpha +7.05%/yr, MDD -23.81%, turnover 44.3%/mo.
"""
from scanner.longterm.selector import run_longterm_selection

__all__ = ["run_longterm_selection"]
