"""Market regime gate — SPY > MA(200) AND VIX < 25.

스캐너/백테스트 진입 게이트로 사용. 베어마켓에서 모든 모멘텀 시그널이
false positive 되는 것을 막기 위한 매크로 필터.

설계 이유 — @register_signal 미사용:
  다른 signals/*.py는 종목별 bars를 입력받아 종목별 시리즈를 반환한다.
  regime은 종목과 무관한 *날짜별* 게이트이므로 symbol-bars 컨트랙트에 맞지 않는다.
  대신 `compute_regime_state()` 헬퍼를 export해서 backtest_scanner /
  scan_momentum이 records를 필터링하거나 진입 시 체크하는 데 사용한다.

API:
    load_macro_bars()              → {'SPY': df, 'VIX': df}  (async, DB 필요)
    compute_regime_state(macro)    → pd.Series[bool]  date → ON/OFF
    is_regime_on(state, day)       → bool  특정 날짜 ON 여부
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import select

if TYPE_CHECKING:
    pass

SPY_MA_PERIOD = 200
VIX_THRESHOLD = 25.0
SPY_SYMBOL = "SPY"
VIX_SYMBOL = "^VIX"


async def load_macro_bars() -> dict[str, pd.DataFrame]:
    """SPY, ^VIX 일봉을 DB에서 로드.

    DB에 데이터 없는 ticker는 결과 dict에서 누락. 호출자가 fallback 결정.
    Returns: {'SPY': df, 'VIX': df}  (key는 ^VIX → VIX로 정규화)
    """
    from api.db import async_session_factory  # noqa: PLC0415  (load_dotenv 순서)
    from api.db.models import Bar  # noqa: PLC0415

    out: dict[str, pd.DataFrame] = {}
    async with async_session_factory() as s:
        for sym in (SPY_SYMBOL, VIX_SYMBOL):
            result = await s.execute(
                select(Bar).where(Bar.symbol == sym, Bar.interval == "1d").order_by(Bar.time)
            )
            rows = result.scalars().all()
            if not rows:
                continue
            df = pd.DataFrame(
                [{"time": b.time, "close": float(b.close)} for b in rows]
            ).set_index("time")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            out[sym.lstrip("^")] = df
    return out


def compute_regime_state(
    macro_bars: dict[str, pd.DataFrame],
    *,
    fallback_when_missing: bool = True,
) -> pd.Series:
    """매크로 데이터로 날짜별 regime ON/OFF 시리즈 생산.

    Returns:
        pd.Series[bool], index = SPY 일자, name='regime_on'
        SPY가 SMA(200) 위 AND VIX < 25 → True

    Args:
        fallback_when_missing: 매크로 데이터 부재 시 동작.
            True (default) → 모든 날짜 ON으로 가정 (dev 환경 진행 가능)
            False           → 빈 시리즈 반환 (운영시 fail-loud)
    """
    spy = macro_bars.get("SPY")
    vix = macro_bars.get("VIX")

    if spy is None or vix is None:
        if fallback_when_missing and spy is not None:
            return pd.Series(True, index=spy.index, name="regime_on")
        return pd.Series(dtype=bool, name="regime_on")

    spy_ma = spy["close"].rolling(SPY_MA_PERIOD).mean()
    spy_above = spy["close"] > spy_ma
    vix_low = vix["close"] < VIX_THRESHOLD

    aligned = pd.concat([spy_above, vix_low], axis=1, join="outer")
    aligned.columns = ["spy_above", "vix_low"]
    aligned = aligned.ffill()  # VIX 결측은 직전 값 유지

    regime = (aligned["spy_above"] & aligned["vix_low"]).fillna(False)
    regime.name = "regime_on"
    return regime


def is_regime_on(state: pd.Series, day: date | datetime | pd.Timestamp) -> bool:
    """특정 날짜의 regime ON 여부. 정확한 날짜 없으면 직전 영업일 사용.

    날짜 인덱스가 비어있으면 fallback True (dev 환경).
    """
    if state.empty:
        return True
    ts = pd.Timestamp(day)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    on_or_before = state.index <= ts + pd.Timedelta(days=1)
    if not on_or_before.any():
        return False
    idx = int(on_or_before.sum()) - 1
    return bool(state.iloc[idx])
