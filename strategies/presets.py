"""거장 스타일 프리셋.

각 프리셋은 CompositeStrategyConfig에 그대로 매핑되는 dict 페이로드.
사용자가 UI에서 프리셋 클릭 시 active_signals/buy_threshold/sell_threshold가 즉시 적용됨.

거장 매핑은 진짜 그들의 기법이 아니라 "그 스타일을 시그널 조합으로 근사한 것".
실제 BNF/CIS 성과를 복제한다는 보장 없음 — 가설이고, 매트릭스 백테스트로 검증한다.
"""

from __future__ import annotations

from typing import Any

from signals import SIGNAL_REGISTRY


def _all_signals() -> list[str]:
    return sorted(SIGNAL_REGISTRY.keys())


# preset 페이로드 = CompositeStrategyConfig에 들어갈 키워드 인자 일부
PRESETS: dict[str, dict[str, Any]] = {
    "bnf_style": {
        "label": "BNF Style (25일 MA 이격, regime filter, 5일 강제청산)",
        "description": (
            "BNF 핵심 룰: 25일 MA 이격률(가중 2.0) + RSI 과매도 보조 + 200일 위 regime 필터. "
            "deep dip + (regime OK 또는 RSI 확인) → 진입. 손절 2% / 익절 5% / 5봉 강제청산."
        ),
        "active_signals": ("bnf_ma25_divergence", "rsi_oversold", "above_ma200"),
        "signal_weights": {"bnf_ma25_divergence": 2.0},
        "buy_threshold": 3.0,
        "sell_threshold": 1.0,
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.05,
        "max_hold_bars": 5,
        "position_size_pct": 0.10,  # MVP에선 0.10 유지 (다른 preset과 비교 일관성). 라이브에선 0.02로 줄일 것.
    },
    "cis_style": {
        "label": "CIS Style",
        "description": "돌파 + 정배열 + 거래량 + 장기추세 (스윙 진입)",
        "active_signals": ("breakout_20d", "ma_alignment", "volume_surge", "above_ma200"),
        "buy_threshold": 4.0,
        "sell_threshold": 2.0,
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.20,
    },
    "conservative": {
        "label": "Conservative (3/5 high-conviction)",
        "description": "Positive-only 시그널 5개 중 3개 이상 동의. 자주 안 사지만 강한 합의일 때만 (sell 시그널 섞여 합산이 깎이는 문제 회피)",
        "active_signals": (
            "volume_surge",
            "breakout_20d",
            "support_bounce",
            "bb_lower_bounce",
            "volume_dryup_pop",
        ),
        "buy_threshold": 3.0,
        "sell_threshold": 1.0,
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.15,
    },
    "aggressive": {
        "label": "Aggressive (5/10)",
        "description": "10개 시그널 중 5개 이상 합의 시 진입",
        "active_signals": tuple(_all_signals()),
        "buy_threshold": 5.0,
        "sell_threshold": 2.0,
        "stop_loss_pct": 0.07,
        "take_profit_pct": 0.15,
    },
    "trend_follow": {
        "label": "Trend Follow",
        "description": "정배열 + 돌파 + 장기추세 + 골든크로스",
        "active_signals": ("ma_alignment", "breakout_20d", "above_ma200", "golden_cross"),
        "buy_threshold": 3.0,
        "sell_threshold": 1.0,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.25,
    },
    "mean_reversion": {
        "label": "Mean Reversion",
        "description": "RSI 과매도 + Bollinger 하단 + 지지선 반등",
        "active_signals": ("rsi_oversold", "bb_lower_bounce", "support_bounce"),
        "buy_threshold": 2.0,
        "sell_threshold": 1.0,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.10,
    },
    "vol_targeted": {
        "label": "Vol-targeted (Phase 3, ATR-sized + 5% stop / 8% take / trailing 3%)",
        "description": (
            "스캐너 WHITELIST + Score≥4 가설을 Nautilus에서 검증하기 위한 정직성 preset. "
            "ATR(14) × 2 손절가, account_risk 0.5%로 사이즈 결정 (qty = (equity × 0.005) / (entry - ATR×2)). "
            "정적 stop 5%/profit 8%로 강건성 한 겹 더. 진입 후 peak 대비 -3% 도달 시 trailing exit. "
            "max_hold 5봉(BNF 식). simulate_stops grid에서 Sharpe 3.12 record (n=301)."
        ),
        "active_signals": (
            "volume_trend",
            "ma_alignment",
            "rsi_bullish",
            "macd",
            "above_ma200",
            "breakout_20d",
        ),
        "buy_threshold": 4.0,
        "sell_threshold": 2.0,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.08,
        "max_hold_bars": 5,
        "position_size_pct": 0.10,  # fallback if risk_per_trade_pct off
        # Phase 3 risk layer
        "trailing_stop_pct": 0.03,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "risk_per_trade_pct": 0.005,  # 0.5% account risk per trade
    },
}


def get_preset(name: str) -> dict[str, Any]:
    if name not in PRESETS:
        raise KeyError(f"Unknown preset: {name}. Available: {list(PRESETS)}")
    return PRESETS[name]


def list_presets() -> list[dict[str, Any]]:
    """API/CLI 응답용. label, description, signals, threshold 포함."""
    out = []
    for key, p in PRESETS.items():
        out.append({
            "key": key,
            "label": p["label"],
            "description": p["description"],
            "active_signals": list(p["active_signals"]),
            "buy_threshold": p["buy_threshold"],
            "sell_threshold": p["sell_threshold"],
            "stop_loss_pct": p["stop_loss_pct"],
            "take_profit_pct": p["take_profit_pct"],
        })
    return out
