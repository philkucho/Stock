"""극공포 컨트래리언 분할진입 vs 추세 재진입 — 약세장 비교 백테스트.

세 전략을 2018Q4 / 2020 / 2022 약세장에 적용하고 forward 6/12개월 수익률 비교.

  A) Contrarian staged: 폭락 직전 동결한 중장기 리더 워치리스트에
     극공포(VIX>30 & SPY -15%↓) 3-트랜치 분할 진입
  B) Trend re-entry (현 방식): 바닥 후 SPY가 200SMA 회복 시 그 시점 중장기 picks 진입
  C) 레퍼런스: 각 타이밍에 SPY 매수 (타이밍 효과 분리)

run_longterm 의 선정 로직(_evaluate_symbol/_score_candidates)을 그대로 재사용.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from backtests.data_cache import get_bars
from backtests.run_longterm import _evaluate_symbol, _score_candidates

UNIVERSE_FILE = "data/sp500_full.json"
TOP_N = 10
VIX_TRIGGER = 30.0
DD_TRIGGER = -0.15        # SPY 52주 고점 대비
TRANCHE_STEP = -0.07      # 다음 트랜치: SPY -7% 추가 하락
TRANCHE_TIME_GAP = 20     # 또는 20거래일 경과 시 강제 배치
N_TRANCHES = 3

# (snapshot=폭락직전 건강한 레짐, peak, search_start, search_end)
EPISODES = {
    "2018Q4": ("2018-08-31", "2018-09-20", "2018-10-01", "2019-06-30"),
    "2020COVID": ("2020-01-31", "2020-02-19", "2020-02-20", "2020-12-31"),
    "2022": ("2021-12-31", "2022-01-03", "2022-01-10", "2023-06-30"),
}


def load_universe() -> list[str]:
    with open(UNIVERSE_FILE) as f:
        return json.load(f)["tickers"]


def build_watchlist(universe, bars_dict, snapshot_ts, top_n=TOP_N):
    """snapshot 시점 중장기 top N (Stage2+RS+momentum 게이트/점수)."""
    cands = []
    for sym, bars in bars_dict.items():
        ev = _evaluate_symbol(sym, bars, snapshot_ts)
        if ev is not None:
            cands.append(ev)
    scored = _score_candidates(cands)
    return [c["symbol"] for c in scored[:top_n]]


def price_on_or_after(df, ts, col="open"):
    rows = df[df.index >= ts]
    if rows.empty:
        return None
    return float(rows.iloc[0][col])


def fwd_return(df, entry_ts, months):
    entry_p = price_on_or_after(df, entry_ts)
    target = entry_ts + pd.DateOffset(months=months)
    rows = df[df.index >= target]
    if entry_p is None or entry_p <= 0 or rows.empty:
        return None
    return float(rows.iloc[0]["close"]) / entry_p - 1.0


def find_fear_triggers(spy):
    """극공포 트랜치 진입일 리스트 (search window 내)."""
    vix = get_bars("^VIX", "2010-01-01", date.today().isoformat())
    high_252 = spy["high"].rolling(252).max()
    dd = spy["close"] / high_252 - 1.0
    vix_al = vix["close"].reindex(spy.index, method="ffill")
    fear = (vix_al > VIX_TRIGGER) & (dd <= DD_TRIGGER)
    return spy.index[fear]


def find_fear_triggers_hard(spy, vix_th, dd_th):
    vix = get_bars("^VIX", "2010-01-01", date.today().isoformat())
    high_252 = spy["high"].rolling(252).max()
    dd = spy["close"] / high_252 - 1.0
    vix_al = vix["close"].reindex(spy.index, method="ffill")
    ma5 = spy["close"].rolling(5).mean()
    # 강화: 더 깊은 DD + VIX, 그리고 "SPY가 5일선 위로 회복(단기 반등 시작)" 확인
    fear = (vix_al > vix_th) & (dd <= dd_th) & (spy["close"] > ma5)
    return spy.index[fear]


def staged_entry_dates(spy, search_start, search_end, hardened=False):
    """3-트랜치 진입일: 1차=첫 극공포, 이후 SPY -7% 또는 20거래일."""
    if hardened:
        fear_days = find_fear_triggers_hard(spy, vix_th=30.0, dd_th=-0.20)
    else:
        fear_days = find_fear_triggers(spy)
    win = fear_days[(fear_days >= search_start) & (fear_days <= search_end)]
    if len(win) == 0:
        return []
    idx = list(spy.index)
    t1 = win[0]
    dates = [t1]
    last_pos = idx.index(t1)
    last_price = float(spy.loc[t1, "close"])
    while len(dates) < N_TRANCHES:
        nxt = None
        for pos in range(last_pos + 1, len(idx)):
            ts = idx[pos]
            if ts > search_end:
                break
            px = float(spy.loc[ts, "close"])
            if px <= last_price * (1 + TRANCHE_STEP) or (pos - last_pos) >= TRANCHE_TIME_GAP:
                nxt = (ts, pos, px)
                break
        if nxt is None:
            break
        dates.append(nxt[0])
        last_pos, last_price = nxt[1], nxt[2]
    return dates


def trend_reentry_date(spy, search_start):
    """현 시스템 모사: ① SPY가 200SMA 아래로 빠져 약세 확인된 뒤
    ② 다시 200SMA 위로 회복하는 첫 시점 → 그 다음 월초 거래일에 재진입."""
    sma200 = spy["close"].rolling(200).mean()
    sub = spy[spy.index >= search_start]
    below_confirmed = False
    reclaim_ts = None
    for ts in sub.index:
        c = float(spy.loc[ts, "close"])
        s = sma200.loc[ts]
        if pd.isna(s):
            continue
        if not below_confirmed:
            if c < s:                 # ① 약세 확인 (200SMA 하향 이탈)
                below_confirmed = True
        else:
            if c > s:                 # ② 회복
                reclaim_ts = ts
                break
    if reclaim_ts is None:
        return None
    nm = reclaim_ts + pd.offsets.MonthBegin(1)
    after = spy[spy.index >= nm]
    return after.index[0] if not after.empty else reclaim_ts


def basket_fwd_return(symbols, bars_dict, entry_ts, months):
    rets = [fwd_return(bars_dict[s], entry_ts, months) for s in symbols if s in bars_dict]
    rets = [r for r in rets if r is not None]
    return sum(rets) / len(rets) if rets else None


# ── 2단계 트리거 ──
S1_VIX, S1_DD = 30.0, -0.15      # 1단(정찰): 얕은 공포
S2_VIX, S2_DD = 35.0, -0.20      # 2단(본진): 깊은 공포
S1_WEIGHT, S2_WEIGHT = 1.0 / 3.0, 2.0 / 3.0
STAGE2_FALLBACK_DAYS = 40        # 2단 미발동 시 1단 +40거래일에 본진 강제 배치


def two_stage_entry(spy, search_start, search_end):
    """[(진입일, 가중치)] — 1단 1/3, 2단 2/3. 둘 다 5일선 반등 확인."""
    vix = get_bars("^VIX", "2010-01-01", date.today().isoformat())
    high_252 = spy["high"].rolling(252).max()
    dd = spy["close"] / high_252 - 1.0
    vix_al = vix["close"].reindex(spy.index, method="ffill")
    reclaim = spy["close"] > spy["close"].rolling(5).mean()

    s1_mask = (vix_al > S1_VIX) & (dd <= S1_DD) & reclaim
    win = spy.index[(spy.index >= search_start) & (spy.index <= search_end)]
    s1_days = [t for t in win if bool(s1_mask.loc[t])]
    if not s1_days:
        return []
    s1 = s1_days[0]

    s2_mask = ((vix_al > S2_VIX) | (dd <= S2_DD)) & reclaim
    idx = list(spy.index)
    s1_pos = idx.index(s1)
    s2 = None
    for pos in range(s1_pos + 1, len(idx)):
        ts = idx[pos]
        if ts > search_end:
            break
        if bool(s2_mask.loc[ts]):
            s2 = ts
            break
    if s2 is None:  # 폴백: 더 깊은 공포 안 오면 시간 기준 본진 배치
        fb_pos = min(s1_pos + STAGE2_FALLBACK_DAYS, len(idx) - 1)
        s2 = idx[fb_pos]
        if s2 > win[-1]:
            s2 = win[-1]
    return [(s1, S1_WEIGHT), (s2, S2_WEIGHT)]


def basket_weighted_fwd_return(symbols, bars_dict, w_tranches, months):
    """가중 분할진입(1/3, 2/3) forward 수익률. anchor = 1단일 + months."""
    if not w_tranches:
        return None
    anchor = w_tranches[0][0]
    target = anchor + pd.DateOffset(months=months)
    sym_rets = []
    for s in symbols:
        df = bars_dict.get(s)
        if df is None:
            continue
        num = den = 0.0
        for t, w in w_tranches:
            p = price_on_or_after(df, t)
            if p and p > 0:
                num += w * p
                den += w
        if den == 0:
            continue
        avg_entry = num / den
        rows = df[df.index >= target]
        if rows.empty:
            continue
        sym_rets.append(float(rows.iloc[0]["close"]) / avg_entry - 1.0)
    return sum(sym_rets) / len(sym_rets) if sym_rets else None


def max_paper_dd_weighted(symbols, bars_dict, w_tranches):
    """1단 진입 후 가중 바스켓 최대 평가손."""
    if not w_tranches:
        return None
    t1 = w_tranches[0][0]
    sym_paths = []
    for s in symbols:
        df = bars_dict.get(s)
        if df is None:
            continue
        num = den = 0.0
        for t, w in w_tranches:
            p = price_on_or_after(df, t)
            if p and p > 0:
                num += w * p
                den += w
        if den == 0:
            continue
        avg_entry = num / den
        path = df[(df.index >= t1) & (df.index <= t1 + pd.DateOffset(months=12))]
        if path.empty:
            continue
        sym_paths.append(path["close"] / avg_entry - 1.0)
    if not sym_paths:
        return None
    return float(pd.concat(sym_paths, axis=1).mean(axis=1).min())


def basket_staged_fwd_return(symbols, bars_dict, tranche_dates, months):
    """달러 가중 분할 진입(균등 트랜치) 후 forward 수익률.

    각 트랜치 동일 금액 → 종목별 평균 진입가 = 트랜치 가격 평균.
    horizon anchor = 1차 트랜치일 + months.
    """
    if not tranche_dates:
        return None
    anchor = tranche_dates[0]
    target = anchor + pd.DateOffset(months=months)
    sym_rets = []
    for s in symbols:
        df = bars_dict.get(s)
        if df is None:
            continue
        entries = [price_on_or_after(df, t) for t in tranche_dates]
        entries = [p for p in entries if p and p > 0]
        if not entries:
            continue
        avg_entry = sum(entries) / len(entries)
        rows = df[df.index >= target]
        if rows.empty:
            continue
        sym_rets.append(float(rows.iloc[0]["close"]) / avg_entry - 1.0)
    return sum(sym_rets) / len(sym_rets) if sym_rets else None


def max_paper_drawdown(symbols, bars_dict, tranche_dates, search_end):
    """1차 트랜치 후 바스켓이 겪는 최대 평가손 (knife-catch 고통 지표)."""
    if not tranche_dates:
        return None
    t1 = tranche_dates[0]
    worst = 0.0
    sym_paths = []
    for s in symbols:
        df = bars_dict.get(s)
        if df is None:
            continue
        entries = [price_on_or_after(df, t) for t in tranche_dates]
        entries = [p for p in entries if p and p > 0]
        if not entries:
            continue
        avg_entry = sum(entries) / len(entries)
        path = df[(df.index >= t1) & (df.index <= t1 + pd.DateOffset(months=12))]
        if path.empty:
            continue
        sym_paths.append((path["close"] / avg_entry - 1.0))
    if not sym_paths:
        return None
    basket = pd.concat(sym_paths, axis=1).mean(axis=1)
    return float(basket.min())


def run():
    universe = load_universe()
    spy = get_bars("SPY", "2017-01-01", date.today().isoformat())

    # 전체 유니버스 bars 한 번 로드
    bars_dict = {}
    for sym in universe:
        try:
            df = get_bars(sym, "2017-01-01", date.today().isoformat())
            if df is not None and not df.empty and len(df) >= 252:
                bars_dict[sym] = df
        except Exception:
            pass

    results = {}
    for name, (snap, peak, ss, se) in EPISODES.items():
        snap_ts = pd.Timestamp(snap, tz="UTC")
        ss_ts = pd.Timestamp(ss, tz="UTC")
        se_ts = pd.Timestamp(se, tz="UTC")

        watchlist = build_watchlist(universe, bars_dict, snap_ts)
        tranches = staged_entry_dates(spy, ss_ts, se_ts)
        tranches_h = staged_entry_dates(spy, ss_ts, se_ts, hardened=True)
        two_stage = two_stage_entry(spy, ss_ts, se_ts)
        b_date = trend_reentry_date(spy, ss_ts)

        ep = {
            "watchlist": watchlist,
            "tranche_dates": [t.date().isoformat() for t in tranches],
            "tranche_dates_hardened": [t.date().isoformat() for t in tranches_h],
            "two_stage": [(t.date().isoformat(), round(w, 3)) for t, w in two_stage],
            "trend_reentry_date": b_date.date().isoformat() if b_date is not None else None,
        }
        # A2) 2단계 트리거 (1단 1/3 정찰 + 2단 2/3 본진)
        ep["A2_two_stage"] = {
            "fwd_6mo": basket_weighted_fwd_return(watchlist, bars_dict, two_stage, 6),
            "fwd_12mo": basket_weighted_fwd_return(watchlist, bars_dict, two_stage, 12),
            "max_paper_dd": max_paper_dd_weighted(watchlist, bars_dict, two_stage),
        }

        # A) 컨트래리언 분할진입 (동결 워치리스트)
        ep["A_contrarian"] = {
            "fwd_6mo": basket_staged_fwd_return(watchlist, bars_dict, tranches, 6),
            "fwd_12mo": basket_staged_fwd_return(watchlist, bars_dict, tranches, 12),
            "max_paper_dd": max_paper_drawdown(watchlist, bars_dict, tranches, se_ts),
        }
        # A') 강화 트리거 (더 깊은 -20% DD + 5일선 반등 확인)
        ep["A_hardened"] = {
            "fwd_6mo": basket_staged_fwd_return(watchlist, bars_dict, tranches_h, 6),
            "fwd_12mo": basket_staged_fwd_return(watchlist, bars_dict, tranches_h, 12),
            "max_paper_dd": max_paper_drawdown(watchlist, bars_dict, tranches_h, se_ts),
        }
        # B) 추세 재진입 (그 시점 fresh picks)
        if b_date is not None:
            b_picks = build_watchlist(universe, bars_dict, b_date)
            ep["B_trend_reentry"] = {
                "picks": b_picks,
                "fwd_6mo": basket_fwd_return(b_picks, bars_dict, b_date, 6),
                "fwd_12mo": basket_fwd_return(b_picks, bars_dict, b_date, 12),
            }
        # C) 레퍼런스: SPY를 각 타이밍에
        ep["C_spy_at_contrarian"] = {
            "fwd_6mo": basket_staged_fwd_return(["SPY"], {"SPY": spy}, tranches, 6),
            "fwd_12mo": basket_staged_fwd_return(["SPY"], {"SPY": spy}, tranches, 12),
        }
        if b_date is not None:
            ep["C_spy_at_trend"] = {
                "fwd_6mo": fwd_return(spy, b_date, 6),
                "fwd_12mo": fwd_return(spy, b_date, 12),
            }
        results[name] = ep

    return results


def auto_episodes(spy, dd_threshold=-0.10, recover_to=-0.03):
    """SPY에서 약세장 에피소드 자동 탐지: (peak_date, trough_date, depth).

    고점 대비 dd_threshold 돌파 → 약세장. recover_to 회복 시 종료.
    체리피킹 배제 — 임의 선택 대신 데이터가 정의.
    """
    eps = []
    closes = spy["close"]
    peak = float(closes.iloc[0]); peak_d = closes.index[0]
    trough = peak; trough_d = peak_d
    breached = False
    for ts, c in closes.items():
        c = float(c)
        if c >= peak:
            if breached and (trough / peak - 1.0) <= dd_threshold:
                eps.append((peak_d, trough_d, trough / peak - 1.0))
            peak = c; peak_d = ts; trough = c; trough_d = ts; breached = False
        else:
            if c < trough:
                trough = c; trough_d = ts
            if (c / peak - 1.0) <= dd_threshold:
                breached = True
    if breached and (trough / peak - 1.0) <= dd_threshold:
        eps.append((peak_d, trough_d, trough / peak - 1.0))
    return eps


def is_tuned(trough_d):
    """튜닝에 사용된 3개 약세장(2018Q4/2020/2022)인지."""
    y, m = trough_d.year, trough_d.month
    return (y == 2018 and m >= 10) or (y == 2020 and m <= 4) or (y == 2022)


def run_auto():
    universe = load_universe()
    spy = get_bars("SPY", "2010-01-01", date.today().isoformat())
    bars_dict = {}
    for sym in universe:
        try:
            df = get_bars(sym, "2010-01-01", date.today().isoformat())
            if df is not None and not df.empty and len(df) >= 252:
                bars_dict[sym] = df
        except Exception:
            pass

    eps = auto_episodes(spy)
    rows = []
    for peak_d, trough_d, depth in eps:
        # 워치리스트 build 위해 peak 시점 252봉 필요
        if len(spy[spy.index < peak_d]) < 252:
            continue
        snap_ts = peak_d
        ss_ts = peak_d
        se_ts = trough_d + pd.DateOffset(months=3)

        watchlist = build_watchlist(universe, bars_dict, snap_ts)
        if not watchlist:
            continue
        two_stage = two_stage_entry(spy, ss_ts, se_ts)
        tranches_base = staged_entry_dates(spy, ss_ts, se_ts)
        b_date = trend_reentry_date(spy, ss_ts)

        a2_12 = basket_weighted_fwd_return(watchlist, bars_dict, two_stage, 12)
        a2_dd = max_paper_dd_weighted(watchlist, bars_dict, two_stage)
        abase_12 = basket_staged_fwd_return(watchlist, bars_dict, tranches_base, 12)
        abase_dd = max_paper_drawdown(watchlist, bars_dict, tranches_base, se_ts)
        b_12 = None
        if b_date is not None:
            b_picks = build_watchlist(universe, bars_dict, b_date)
            b_12 = basket_fwd_return(b_picks, bars_dict, b_date, 12)
        spy_ts_12 = basket_weighted_fwd_return(["SPY"], {"SPY": spy}, two_stage, 12)

        rows.append({
            "peak": peak_d.date().isoformat(),
            "trough": trough_d.date().isoformat(),
            "depth": depth,
            "tuned": is_tuned(trough_d),
            "s1": two_stage[0][0].date().isoformat() if two_stage else None,
            "s2": two_stage[1][0].date().isoformat() if len(two_stage) > 1 else None,
            "A2_12mo": a2_12,
            "A2_dd": a2_dd,
            "Abase_12mo": abase_12,
            "Abase_dd": abase_dd,
            "Abase_fired": bool(tranches_base),
            "B_12mo": b_12,
            "SPY_12mo": spy_ts_12,
        })
    return rows


def pct(x):
    return f"{x*100:+.1f}%" if isinstance(x, (int, float)) else "n/a"


def main_auto():
    rows = run_auto()
    rows.sort(key=lambda r: r["peak"])
    hdr = (f"{'peak':>10} {'trough':>10} {'depth':>7} {'tag':>5} | "
           f"{'A2 12mo':>8} {'A2 DD':>7} | {'Abase':>8} {'AbDD':>7} | {'B 12mo':>8} {'SPY':>8}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        tag = "TUNE" if r["tuned"] else "OOS"
        print(f"{r['peak']:>10} {r['trough']:>10} {pct(r['depth']):>7} {tag:>5} | "
              f"{pct(r['A2_12mo']):>8} {pct(r['A2_dd']):>7} | "
              f"{pct(r['Abase_12mo']):>8} {pct(r['Abase_dd']):>7} | "
              f"{pct(r['B_12mo']):>8} {pct(r['SPY_12mo']):>8}")

    # 집계: OOS만 따로
    def agg(subset, key):
        vals = [r[key] for r in subset if isinstance(r[key], (int, float))]
        return (sum(vals) / len(vals)) if vals else None
    oos = [r for r in rows if not r["tuned"]]
    tune = [r for r in rows if r["tuned"]]
    print("\n=== 발동 빈도 & 평균 12mo ===")
    for label, subset in [("TUNED", tune), ("OOS", oos), ("ALL", rows)]:
        a2_fired = [r for r in subset if isinstance(r["A2_12mo"], (int, float))]
        ab_fired = [r for r in subset if r.get("Abase_fired")]
        a2 = agg(subset, "A2_12mo"); ab = agg(subset, "Abase_12mo"); b = agg(subset, "B_12mo")
        print(f"  {label:>6} (n={len(subset)}): "
              f"A2 발동 {len(a2_fired)}/{len(subset)} avg {pct(a2)} | "
              f"Abase 발동 {len(ab_fired)}/{len(subset)} avg {pct(ab)} | B avg {pct(b)}")
    print("\n" + json.dumps(rows, indent=2, default=str))


if __name__ == "__main__":
    import sys
    if "--auto" in sys.argv:
        main_auto()
        sys.exit(0)
    res = run()
    print("\n" + "=" * 78)
    for ep, d in res.items():
        print(f"\n### {ep}")
        print(f"  동결 워치리스트(top10): {', '.join(d['watchlist'])}")
        print(f"  컨트래리언 트랜치 진입일: {d['tranche_dates']}")
        print(f"  추세 재진입일: {d['trend_reentry_date']}")
        print(f"  강화 트리거 트랜치(-20%+반등확인): {d['tranche_dates_hardened']}")
        A = d["A_contrarian"]
        print(f"  [A] 컨트래리언(VIX+ -15%)  6mo {pct(A['fwd_6mo'])}  12mo {pct(A['fwd_12mo'])}"
              f"  (최대평가손 {pct(A['max_paper_dd'])})")
        H = d["A_hardened"]
        print(f"  [A'] 강화(-20%+반등확인)   6mo {pct(H['fwd_6mo'])}  12mo {pct(H['fwd_12mo'])}"
              f"  (최대평가손 {pct(H['max_paper_dd'])})")
        print(f"  2단계 진입(일,가중): {d['two_stage']}")
        T = d["A2_two_stage"]
        print(f"  [A2] ★2단계(1/3+2/3)    6mo {pct(T['fwd_6mo'])}  12mo {pct(T['fwd_12mo'])}"
              f"  (최대평가손 {pct(T['max_paper_dd'])})")
        if "B_trend_reentry" in d:
            B = d["B_trend_reentry"]
            print(f"  [B] 추세 재진입(현방식)  6mo {pct(B['fwd_6mo'])}  12mo {pct(B['fwd_12mo'])}")
        print(f"  [C] SPY@컨트래리언      6mo {pct(d['C_spy_at_contrarian']['fwd_6mo'])}"
              f"  12mo {pct(d['C_spy_at_contrarian']['fwd_12mo'])}")
        if "C_spy_at_trend" in d:
            print(f"  [C] SPY@추세재진입      6mo {pct(d['C_spy_at_trend']['fwd_6mo'])}"
                  f"  12mo {pct(d['C_spy_at_trend']['fwd_12mo'])}")
    print("\n" + "=" * 78)
    print(json.dumps(res, indent=2, default=str))
