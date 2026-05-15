// API_BASE = "" → 모든 fetch가 상대경로로 (/api/...) → 같은 origin (Next.js)으로 감.
// next.config.ts의 rewrites가 /api/* 요청을 FastAPI(127.0.0.1:8000)로 proxy.
// 결과: 브라우저는 데스크탑 / Tailscale IP / 어디서 들어오든 동일 origin 호출 → CORS/IP 신경 끄기.
// NEXT_PUBLIC_API_BASE 설정하면 override (예: 분리 배포 시).
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export async function fetchHealth(): Promise<{ status: string; version: string }> {
  const res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return res.json();
}

export type Account = {
  balance_usd: number | null;
  buying_power: number | null;
  status: string;
};

export async function fetchAccount(): Promise<Account> {
  const res = await fetch(`${API_BASE}/api/positions/account`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Account fetch failed: ${res.status}`);
  return res.json();
}

// ── Backtests ────────────────────────────────────────────────────────────

export type BacktestSummary = {
  id: number;
  strategy_name: string;
  symbol: string;
  interval: string;
  period_start: string;
  period_end: string;
  total_pnl: string;
  win_rate: string;
  total_positions: number;
  created_at: string;
};

export type BacktestRun = {
  id: number;
  strategy_name: string;
  strategy_params: Record<string, unknown>;
  symbol: string;
  interval: string;
  period_start: string;
  period_end: string;
  data_source: string;
  starting_cash: string;
  final_equity: string;
  total_pnl: string;
  total_fills: number;
  total_positions: number;
  wins: number;
  losses: number;
  win_rate: string;
  metrics: {
    best_position_pnl?: number;
    worst_position_pnl?: number;
    avg_position_pnl?: number;
  } | null;
  notes: string | null;
  created_at: string;
};

export type BacktestListResponse = {
  items: BacktestSummary[];
  total: number;
  limit: number;
  offset: number;
};

export type BacktestFilters = {
  symbol?: string;
  strategy_name?: string;
  limit?: number;
  offset?: number;
};

export async function fetchBacktests(filters: BacktestFilters = {}): Promise<BacktestListResponse> {
  const params = new URLSearchParams();
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.strategy_name) params.set("strategy_name", filters.strategy_name);
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  const qs = params.toString();
  const url = `${API_BASE}/api/backtests/${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Backtests fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchBacktest(id: number): Promise<BacktestRun> {
  const res = await fetch(`${API_BASE}/api/backtests/${id}`, { cache: "no-store" });
  if (res.status === 404) throw new Error(`Backtest ${id} not found`);
  if (!res.ok) throw new Error(`Backtest fetch failed: ${res.status}`);
  return res.json();
}

// ── Signals ──────────────────────────────────────────────────────────────

export type SignalMeta = {
  name: string;
  description: string;
  category: string;
  min_bars: number;
};

export async function fetchSignals(): Promise<SignalMeta[]> {
  const res = await fetch(`${API_BASE}/api/signals/`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Signals fetch failed: ${res.status}`);
  return res.json();
}

// ── Strategy presets ─────────────────────────────────────────────────────

export type Preset = {
  key: string;
  label: string;
  description: string;
  active_signals: string[];
  buy_threshold: number;
  sell_threshold: number;
  stop_loss_pct: number;
  take_profit_pct: number;
};

export async function fetchPresets(): Promise<Preset[]> {
  const res = await fetch(`${API_BASE}/api/strategies/presets`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Presets fetch failed: ${res.status}`);
  return res.json();
}

// ── Matrix ───────────────────────────────────────────────────────────────

export type MatrixCell = {
  symbol: string;
  preset_key: string;
  period_start: string;
  period_end: string;
  fitness: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
  total_return: number;
  total_pnl: number;
  total_fills: number;
  total_positions: number;
  wins: number;
  losses: number;
  cost_adj_return: number;
  starting_cash: number;
  final_equity: number;
  params_hash: string;
};

export type MatrixResponse = {
  cells: MatrixCell[];
  symbols: string[];
  presets: string[];
  period_start: string | null;
  period_end: string | null;
  total: number;
};

export type MatrixFilters = {
  period_start?: string;
  period_end?: string;
  symbol?: string;
  preset?: string;
};

export async function fetchMatrix(filters: MatrixFilters = {}): Promise<MatrixResponse> {
  const params = new URLSearchParams();
  if (filters.period_start) params.set("period_start", filters.period_start);
  if (filters.period_end) params.set("period_end", filters.period_end);
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.preset) params.set("preset", filters.preset);
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/api/matrix/${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Matrix fetch failed: ${res.status}`);
  return res.json();
}

export type MatrixPeriod = {
  period_start: string;
  period_end: string;
  count: number;
};

export async function fetchMatrixPeriods(): Promise<MatrixPeriod[]> {
  const res = await fetch(`${API_BASE}/api/matrix/periods`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Periods fetch failed: ${res.status}`);
  return res.json();
}

export type RunMatrixPayload = {
  pool?: string;
  presets?: string;
  train?: string;
  test?: string;
  workers?: number;
  cash?: number;
  force?: boolean;
};

export async function triggerMatrixRun(
  payload: RunMatrixPayload = {},
): Promise<{ status: string; pid: number | null; message: string }> {
  const res = await fetch(`${API_BASE}/api/matrix/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Matrix run trigger failed: ${res.status}`);
  return res.json();
}

// ── Regime ──────────────────────────────────────────────────────────────

export type RegimePresetStats = {
  preset_key: string;
  avg_fitness: number;
  median_fitness: number;
  hit_rate: number;
  sample_n: number;
};

export type RegimeSnapshot = {
  label: string;
  period_start: string;
  period_end: string;
  presets: RegimePresetStats[];
  dominant: string | null;
  weakest: string | null;
  cell_count: number;
};

export type RegimeAlert = {
  severity: "info" | "warning" | "critical";
  code: string;
  message: string;
};

export type RegimeResponse = {
  regime: string;
  description: string;
  snapshots: RegimeSnapshot[];
  alerts: RegimeAlert[];
  recommendation: string;
};

export async function fetchRegime(): Promise<RegimeResponse> {
  const res = await fetch(`${API_BASE}/api/regime/`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Regime fetch failed: ${res.status}`);
  return res.json();
}

// ── Assignments ──────────────────────────────────────────────────────────

export type Assignment = {
  id: number;
  symbol: string;
  preset_key: string;
  enabled: boolean;
  params: Record<string, unknown>;
  notes: string | null;
  assigned_at: string;
  updated_at: string;
};

export type AssignmentPayload = {
  symbol: string;
  preset_key: string;
  enabled?: boolean;
  params?: Record<string, unknown>;
  notes?: string | null;
};

export async function fetchAssignments(filters?: {
  symbol?: string;
  enabled_only?: boolean;
}): Promise<Assignment[]> {
  const params = new URLSearchParams();
  if (filters?.symbol) params.set("symbol", filters.symbol);
  if (filters?.enabled_only) params.set("enabled_only", "true");
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/api/assignments/${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Assignments fetch failed: ${res.status}`);
  return res.json();
}

export async function saveAssignment(payload: AssignmentPayload): Promise<Assignment> {
  const res = await fetch(`${API_BASE}/api/assignments/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Save assignment failed: ${res.status}`);
  return res.json();
}

export async function deleteAssignment(id: number): Promise<{ status: string; id: string }> {
  const res = await fetch(`${API_BASE}/api/assignments/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete assignment failed: ${res.status}`);
  return res.json();
}

// ── Daily Picks (단타 Stage 2) ───────────────────────────────────────────

export type GateResults = {
  g1_market: boolean;
  g2_dollar_vol: boolean;
  g3_momentum: boolean;
  g4_spread: boolean;
  g5_catalyst: boolean;
  g6_traps: boolean;
};

export type Rationale = {
  // v2 호환
  gap_pct?: number;
  rvol?: number;
  tight_flag?: boolean;
  breakout_20d?: boolean;
  near_52w_high?: boolean;
  sector_etf?: string | null;
  sector_etf_gap?: number | null;
  sector_aligned?: boolean | null;
  is_whitelist?: boolean;
  catalyst_kind?: string;
  rsi_14?: number | null;
  avg_volume_20d?: number | null;
  last_volume?: number | null;
  volume_vs_avg?: number | null;
  // v3 신규
  regime_score?: number;
  rs_percentile?: number | null;
  rs_1m?: number | null;
  rs_3m?: number | null;
  rs_6m?: number | null;
  stage2_pass?: boolean;
  compression?: boolean;
  expansion?: boolean;
  compression_ratio?: number | null;
  expansion_ratio?: number | null;
  open_location_above_prev_high?: boolean;
  open_location_above_pivot?: boolean;
  rsi_structure_grade?: string;
  rsi_structure_notes?: string;
};

export type ScoreBreakdown = {
  // Block 합계 (편의)
  block_0?: number;
  block_a?: number;
  block_b?: number;
  block_c?: number;
  block_d?: number;
  penalties_total?: number;
  // v3 개별 항목
  b0_regime?: number;
  a1_rs_rating?: number;
  a2_mom_multi_tf?: number;
  a3_stage2_strength?: number;
  b1_rvol?: number;
  b2_vol_surge?: number;
  b3_catalyst?: number;
  c1_pattern?: number;
  c2_pivot_proximity?: number;
  c3_base_duration?: number;
  c4_open_location?: number;
  c5_compression_expansion?: number;
  d1_rsi_structure?: number;
  d2_beta?: number;
  d3_atr_rr?: number;
  d4_sector_strength?: number;
  p1_volume_deficit?: number;
  p2_climax?: number;
  p3_squeeze?: number;
  p4_extended?: number;
  p5_rsi_structure_violation?: number;
  p6_open_location_risk?: number;
  rationale?: Rationale;
  // v2 호환 (deprecated)
  s1_gap_rvol?: number;
  s2_catalyst?: number;
  s3_setup?: number;
  s4_sector?: number;
  s5_whitelist?: number;
};

export type DailyPick = {
  id: number;
  pick_date: string;
  rank: number;
  symbol: string;
  is_backup: boolean;
  total_score: string;
  gate_results: GateResults;
  score_breakdown: ScoreBreakdown;
  pivot_price: string;
  stop_price: string;
  target_1r: string;
  target_2r: string;
  risk_per_share: string;
  position_size: number;
  strategy_tag: string;
  catalyst_summary: string | null;
  catalyst_source: string | null;
  market_context: Record<string, number>;
  sector: string | null;
  created_at: string;
};

export async function fetchPicksToday(): Promise<DailyPick[]> {
  const res = await fetch(`${API_BASE}/api/picks/today`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Picks fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchPicksForDate(d: string): Promise<DailyPick[]> {
  const res = await fetch(`${API_BASE}/api/picks/${d}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Picks fetch failed: ${res.status}`);
  return res.json();
}

export async function triggerPicksRefresh(targetDate?: string, equity = 25000): Promise<unknown> {
  const params = new URLSearchParams();
  if (targetDate) params.set("target_date", targetDate);
  params.set("equity", String(equity));
  const res = await fetch(`${API_BASE}/api/picks/refresh?${params.toString()}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Refresh failed: ${res.status}`);
  return res.json();
}

export async function triggerUniverseRefresh(): Promise<unknown> {
  const res = await fetch(`${API_BASE}/api/picks/universe/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(`Universe refresh failed: ${res.status}`);
  return res.json();
}

// ── Scanner (scan_momentum 파이프라인 결과) ─────────────────────────────────

export type RegimeStatus = {
  on: boolean | null;
  spy_close: number | null;
  spy_ma200: number | null;
  spy_above_ma: boolean | null;
  vix_close: number | null;
  vix_threshold: number;
  last_update: string | null;
};

export type ScannerCandidate = {
  rank: number;
  symbol: string;
  sector: string | null;
  group: string | null;
  earnings_phase: "pre" | "post" | "clean";
  earnings_next: string | null;
  earnings_days: number | null;
  as_of: string;
  close: number;
  volume: number;
  vol_vs_20d_avg: number | null;
  signals: Record<string, number>;
  volume_score: number;
  momentum_score: number;
  total_score: number;
  historical: {
    n: number;
    hit_rate: number;
    avg_ret: number;
    median_ret: number;
  } | null;
};

export type ScannerToday = {
  as_of: string;
  regime: RegimeStatus;
  n_candidates: number;
  n_pre_blackout: number;
  n_post_pead: number;
  n_clean: number;
  candidates: ScannerCandidate[];
};

export type ScannerOptions = {
  targetDate?: string;
  scoreMin?: number;
  earningsMode?: "off" | "annotate" | "exclude" | "pre_only";
  filterMode?: "whitelist-only" | "no-blacklist" | "annotate" | "all";
  top?: number;
};

export async function fetchScannerToday(opts: ScannerOptions = {}): Promise<ScannerToday> {
  const p = new URLSearchParams();
  if (opts.targetDate) p.set("target_date", opts.targetDate);
  if (opts.scoreMin !== undefined) p.set("score_min", String(opts.scoreMin));
  if (opts.earningsMode) p.set("earnings_mode", opts.earningsMode);
  if (opts.filterMode) p.set("filter_mode", opts.filterMode);
  if (opts.top !== undefined) p.set("top", String(opts.top));
  const res = await fetch(`${API_BASE}/api/scanner/today?${p.toString()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Scanner fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchScannerRegime(): Promise<RegimeStatus> {
  const res = await fetch(`${API_BASE}/api/scanner/regime`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Regime fetch failed: ${res.status}`);
  return res.json();
}

export type WhitelistResponse = {
  n_whitelist: number;
  n_blacklist: number;
  n_unknown: number;
  config: Record<string, unknown>;
  by_sector: Record<string, string[]>;
  whitelist: Array<{
    symbol: string;
    n: number;
    hit_rate: number;
    avg_ret: number;
    median_ret: number;
  }>;
};

export async function fetchWhitelist(): Promise<WhitelistResponse> {
  const res = await fetch(`${API_BASE}/api/scanner/whitelist`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Whitelist fetch failed: ${res.status}`);
  return res.json();
}

export type Diagnostics = {
  interval_1d: { n_symbols: number; n_rows: number; earliest: string; latest: string };
  interval_1m: { n_symbols: number; n_rows: number };
  earnings_calendar: { path: string; n_symbols: number };
  filter_path: string;
};

export async function fetchScannerDiagnostics(): Promise<Diagnostics> {
  const res = await fetch(`${API_BASE}/api/scanner/diagnostics`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Diagnostics fetch failed: ${res.status}`);
  return res.json();
}

// ── Dashboard (통합: scanner 정확도 + picks 운용정보) ────────────────────

export type DashboardLevels = {
  entry: number;
  stop: number;
  target_1r: number;
  target_2r: number;
  risk_per_share: number;
  risk_pct: number;
  qty: number;
  position_value: number;
  account_risk_dollar: number;
};

export type DashboardReason = {
  label: string;
  detail: string;
  polarity: "positive" | "negative" | "neutral";
};

export type DashboardCandidate = {
  rank: number;
  tier: "S" | "A" | "B" | "C";
  tier_path:
    | "perfect"
    | "stats"
    | "battle"
    | "score+stats"
    | "post-pead"
    | "high-score"
    | "wl-clean"
    | "wl-pead"
    | "watch";
  symbol: string;
  sector: string | null;
  earnings_phase: "pre" | "post" | "clean";
  earnings_next: string | null;
  earnings_days: number | null;
  close: number;
  volume: number;
  vol_vs_20d_avg: number | null;
  signals: Record<string, number>;
  total_score: number;
  historical: { n: number; hit_rate: number; avg_ret: number; median_ret: number } | null;
  levels: DashboardLevels | null;
  reasons: DashboardReason[];
  score_breakdown: {
    volume_strength: number;
    trend_alignment: number;
    momentum: number;
    breakout: number;
    negatives: number;
  };
};

export type DashboardResponse = {
  as_of: string;
  regime: RegimeStatus;
  n_candidates: number;
  n_tier_s: number;
  n_tier_a: number;
  n_tier_b: number;
  n_tier_c: number;
  config: {
    score_min: number;
    earnings_mode: string;
    equity: number;
    risk_per_trade: number;
    atr_mult: number;
    atr_period: number;
  };
  tiers: {
    S: DashboardCandidate[];
    A: DashboardCandidate[];
    B: DashboardCandidate[];
    C: DashboardCandidate[];
  };
};

export type DashboardOptions = {
  targetDate?: string;
  scoreMin?: number;
  earningsMode?: "off" | "exclude" | "pre_only";
  equity?: number;
  riskPerTrade?: number;
  atrMult?: number;
  tierStrictness?: number; // 1=매우 엄격, 5=매우 완화
  top?: number;
};

export type ChartBar = {
  time: string;  // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type ChartResponse = {
  symbol: string;
  bars: ChartBar[];
  ma20: (number | null)[];
  ma50: (number | null)[];
  ma200: (number | null)[];
  levels: DashboardLevels | null;
};

export async function fetchSymbolBars(
  symbol: string,
  opts: { days?: number; equity?: number; riskPerTrade?: number; atrMult?: number } = {},
): Promise<ChartResponse> {
  const p = new URLSearchParams();
  if (opts.days !== undefined) p.set("days", String(opts.days));
  if (opts.equity !== undefined) p.set("equity", String(opts.equity));
  if (opts.riskPerTrade !== undefined) p.set("risk_per_trade", String(opts.riskPerTrade));
  if (opts.atrMult !== undefined) p.set("atr_mult", String(opts.atrMult));
  const res = await fetch(`${API_BASE}/api/dashboard/bars/${encodeURIComponent(symbol)}?${p.toString()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Bars fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchDashboardToday(opts: DashboardOptions = {}): Promise<DashboardResponse> {
  const p = new URLSearchParams();
  if (opts.targetDate) p.set("target_date", opts.targetDate);
  if (opts.scoreMin !== undefined) p.set("score_min", String(opts.scoreMin));
  if (opts.earningsMode) p.set("earnings_mode", opts.earningsMode);
  if (opts.equity !== undefined) p.set("equity", String(opts.equity));
  if (opts.riskPerTrade !== undefined) p.set("risk_per_trade", String(opts.riskPerTrade));
  if (opts.atrMult !== undefined) p.set("atr_mult", String(opts.atrMult));
  if (opts.tierStrictness !== undefined) p.set("tier_strictness", String(opts.tierStrictness));
  if (opts.top !== undefined) p.set("top", String(opts.top));
  const res = await fetch(`${API_BASE}/api/dashboard/today?${p.toString()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Dashboard fetch failed: ${res.status}`);
  return res.json();
}

// ── Comparison (3 시스템 비교) ──────────────────────────────────────────

export type CompareOutcome = {
  horizon_days: number;
  exit_date: string;
  exit_price: string;
  pct_return: string;
  spy_pct_return: string;
  alpha: string;
  win_simple: boolean;
  win_alpha: boolean;
  realized_pnl_usd: string;
};

export type ComparePickLog = {
  id: number;
  system_id: string;
  pick_date: string;
  rank: number;
  symbol: string;
  score: string;
  sector: string | null;
  strategy_tag: string;
  entry_price: string | null;
  sim_capital_usd: string;
  score_meta: Record<string, unknown>;
  outcomes: CompareOutcome[];
};

export type CompareTodayResponse = {
  pick_date: string;
  by_system: Record<string, ComparePickLog[]>;
};

export type SystemKPI = {
  system_id: string;
  n_picks: number;
  n_with_outcome: number;
  horizon_kpis: Record<string, {
    n: number;
    avg_return_pct: number;
    avg_alpha_pct: number;
    win_rate: number;
    win_alpha_rate: number;
    sharpe: number;
    total_pnl_usd: number;
    max_return_pct: number;
    min_return_pct: number;
  }>;
};

export type CompareSummaryResponse = {
  period_start: string;
  period_end: string;
  systems: SystemKPI[];
  cumulative_pnl_curve: Record<string, { date: string; cum_pnl_usd: number }[]>;
};

export async function fetchCompareToday(d?: string): Promise<CompareTodayResponse> {
  const url = d
    ? `${API_BASE}/api/comparison/picks/${d}`
    : `${API_BASE}/api/comparison/today`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Compare today fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchCompareSummary(days = 30): Promise<CompareSummaryResponse> {
  const res = await fetch(`${API_BASE}/api/comparison/summary?days=${days}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Compare summary fetch failed: ${res.status}`);
  return res.json();
}

export async function triggerCompareLog(target?: string): Promise<unknown> {
  const params = target ? `?target_date=${target}` : "";
  const res = await fetch(`${API_BASE}/api/comparison/log-today${params}`, { method: "POST" });
  if (!res.ok) throw new Error(`Compare log failed: ${res.status}`);
  return res.json();
}

export async function triggerCompareBackfill(target?: string, lookback = 30): Promise<unknown> {
  const params = new URLSearchParams();
  if (target) params.set("target_date", target);
  params.set("lookback_days", String(lookback));
  const res = await fetch(`${API_BASE}/api/comparison/backfill-outcomes?${params.toString()}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Compare backfill failed: ${res.status}`);
  return res.json();
}

// ── Trading (매일 매매 Plan) ──────────────────────────────────────────

export type MarketBrief = {
  regime_score: number;
  regime_mode: string;
  regime_signals: Record<string, boolean>;
  indices: Record<string, number>;
  summary: string;
  position_size_multiplier: number;
  long_blocked: boolean;
};

export type ScoreBreakdownItem = {
  name: string;
  label_ko: string;
  points: number;
  kind: "base" | "bonus" | "multiplier";
};

export type PickRecommendation = {
  rank: number;
  symbol: string;
  sector: string | null;
  composite_score: number;
  tier: number | null;
  entry_price: string;
  stop_price: string;
  target_1r: string;
  target_2r: string;
  risk_per_share: string;
  risk_pct: number;
  score_meta: Record<string, unknown>;
  consensus_systems: string[];
  consensus_tier: "S" | "A" | "B";
  score_breakdown: ScoreBreakdownItem[];
  // "v10" (스윙 기본) | "v9_fallback" (스윙 보충) | "intraday_v1" (단타)
  system_source: "v10" | "v9_fallback" | "intraday_v1";
};

export type TradePlanOutcome = {
  horizon_days: number;
  exit_date: string;
  exit_price: string;
  pct_return: string;
  spy_pct_return: string;
  alpha: string;
  realized_pnl_usd: string;
  hit_target_1r: boolean;
  hit_target_2r: boolean;
  hit_stop: boolean;
  qty_sold_at_1r: number;
  qty_sold_at_2r: number;
  partial_realized_pnl_usd: string;
};

export type TradePlan = {
  id: number;
  plan_date: string;
  symbol: string;
  rank: number;
  amount_usd: string;
  entry_price: string;
  stop_price: string;
  target_1r: string;
  target_2r: string;
  composite_score: string;
  sector: string | null;
  shares: number;
  risk_usd: string;
  score_meta?: Record<string, unknown>;
  // integrated 내부 sub-system 출처. v10 (기본) | v9_fallback | intraday_v1.
  system_source: "v10" | "v9_fallback" | "intraday_v1";
  // Intraday 5-Model Stack (Phase 5)
  confirm_status: "watchlist" | "passed" | "failed" | "sent" | "skipped";
  // 'user_fixed': /trading 직접 입력 plan, 09:30 그대로 발송.
  // 'orb_auto':   스캐너 자동 watchlist, 09:45 ORB 평가 후 발송.
  dispatch_mode: "user_fixed" | "orb_auto";
  orb_high: string | null;
  orb_low: string | null;
  session_vwap: string | null;
  intraday_rvol: string | null;
  premarket_gap_pct: string | null;
  premarket_rvol: string | null;
  created_at: string;
  outcomes: TradePlanOutcome[];
};

export type TradingTodayResponse = {
  plan_date: string;
  market_brief: MarketBrief;
  picks: PickRecommendation[];
  existing_plans: TradePlan[];
};

export type TradePlanPayload = {
  symbol: string;
  rank: number;
  amount_usd?: number;
  shares?: number;
  entry_price: number;
  stop_price: number;
  target_1r: number;
  target_2r: number;
  composite_score?: number;
  sector?: string | null;
  score_meta?: Record<string, unknown>;
};

// ── Market Diagnosis ────────────────────────────────────────────────

export type DiagnosisSignal = {
  key: string;
  label_ko: string;
  value: string;
  raw: number | string | null;
  threshold_ko: string;
  level: "normal" | "warning" | "danger";
  note: string | null;
};

export type DiagnosisPossibility = {
  title: string;
  state: string;
  example: string;
};

export type MarketDiagnosisResponse = {
  diagnosis_date: string;
  verdict: "normal" | "warning" | "defensive";
  verdict_ko: string;
  verdict_summary: string;
  recommendation: string;
  auto_trade_advice: "proceed" | "review" | "halt";
  signal_count_triggered: number;
  signal_count_total: number;
  signals: DiagnosisSignal[];
  possibilities: DiagnosisPossibility[];
};

export async function fetchMarketDiagnosis(
  d?: string,
): Promise<MarketDiagnosisResponse> {
  const url = d
    ? `${API_BASE}/api/market-diagnosis/${d}`
    : `${API_BASE}/api/market-diagnosis/today`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Market diagnosis fetch failed: ${res.status}`);
  return res.json();
}

// ── Daily Review ─────────────────────────────────────────────────────

export type ReviewPlanRow = {
  rank: number;
  symbol: string;
  sector: string | null;
  system_source: "intraday_v1" | "v10" | "v9_fallback";
  confirm_status: "watchlist" | "passed" | "failed" | "sent" | "skipped";
  composite_score: number;

  premarket_gap_pct: number | null;
  premarket_rvol: number | null;
  catalyst_kind: string | null;
  catalyst_summary: string | null;

  orb_high: number | null;
  orb_low: number | null;
  session_vwap: number | null;
  intraday_rvol: number | null;
  fail_reasons: string[];

  planned_entry: number;
  planned_stop: number;
  planned_target_1r: number;
  planned_target_2r: number;
  planned_shares: number;
  planned_amount_usd: number;
  planned_risk_usd: number;

  actual_exit_price: number | null;
  actual_pct_return: number | null;
  actual_alpha: number | null;
  actual_realized_pnl: number | null;
  hit_target_1r: boolean;
  hit_target_2r: boolean;
  hit_stop: boolean;
  qty_sold_at_1r: number;
  qty_sold_at_2r: number;
};

export type ReviewSummary = {
  watchlist_count: number;
  passed_count: number;
  failed_count: number;
  sent_count: number;
  skipped_count: number;
  fail_reason_counts: Record<string, number>;
};

export type ReviewTotals = {
  planned_exposure_usd: number;
  planned_risk_usd: number;
  actual_realized_pnl_usd: number;
  actual_alpha_avg: number | null;
  win_count: number;
  loss_count: number;
};

export type DailyReviewResponse = {
  review_date: string;
  summary: ReviewSummary;
  totals: ReviewTotals;
  plans: ReviewPlanRow[];
};

export async function fetchDailyReview(
  d?: string,
): Promise<DailyReviewResponse> {
  const url = d
    ? `${API_BASE}/api/review/${d}`
    : `${API_BASE}/api/review/today`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Daily review fetch failed: ${res.status}`);
  return res.json();
}

// ── Trading Today (기존) ──────────────────────────────────────────────

export async function fetchTradingToday(): Promise<TradingTodayResponse> {
  const res = await fetch(`${API_BASE}/api/trading/today`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Trading today fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchTradingPlans(days = 30): Promise<TradePlan[]> {
  const res = await fetch(`${API_BASE}/api/trading/plans?days=${days}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Trading plans fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchTradingPlansForDate(d: string): Promise<TradePlan[]> {
  const res = await fetch(`${API_BASE}/api/trading/plans/${d}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Trading plans fetch failed: ${res.status}`);
  return res.json();
}

export async function saveTradePlan(payload: TradePlanPayload): Promise<TradePlan> {
  const res = await fetch(`${API_BASE}/api/trading/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Save trade plan failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function deleteTradePlan(id: number): Promise<unknown> {
  const res = await fetch(`${API_BASE}/api/trading/plan/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete trade plan failed: ${res.status}`);
  return res.json();
}

// ── Orders / Fills (broker history) ─────────────────────────────────────

export type Order = {
  id: number;
  client_order_id: string;
  broker_order_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  quantity: string;
  entry_price: string | null;
  stop_loss_price: string | null;
  take_profit_price: string | null;
  status: string;
  strategy_id: string | null;
  submitted_at: string | null;
  created_at: string;
};

export type Position = {
  account: string;
  symbol: string;
  quantity: string;
  avg_price: string;
  realized_pnl: string;
  updated_at: string;
};

export async function fetchOrders(
  opts: { status?: string; limit?: number; systemOnly?: boolean } = {},
): Promise<Order[]> {
  const p = new URLSearchParams();
  if (opts.status) p.set("status", opts.status);
  if (opts.limit !== undefined) p.set("limit", String(opts.limit));
  if (opts.systemOnly !== undefined) p.set("system_only", String(opts.systemOnly));
  const qs = p.toString();
  const res = await fetch(`${API_BASE}/api/positions/orders${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Orders fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchPositions(): Promise<Position[]> {
  const res = await fetch(`${API_BASE}/api/positions/`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Positions fetch failed: ${res.status}`);
  return res.json();
}

// ── Activity log (시간순 통합 타임라인) ──

export type ActivityEvent = {
  ts: string; // ISO datetime
  type: string;
  symbol: string | null;
  summary: string;
  details: Record<string, unknown>;
};

export type ActivitySummary = {
  picks_count: number;
  advisor_recommendations: number;
  advisor_approved: number;
  advisor_rejected: number;
  advisor_expired: number;
  plans_sent: number;
  broker_orders: number;
  broker_filled: number;
  broker_canceled: number;
  realized_pnl_usd: number;
};

export type ActivityResponse = {
  date: string;
  events: ActivityEvent[];
  summary: ActivitySummary;
  symbols: string[];
};

export async function fetchActivity(
  date: string,
  symbol?: string,
): Promise<ActivityResponse> {
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  const res = await fetch(`${API_BASE}/api/activity/${date}${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Activity fetch failed: ${res.status}`);
  return res.json();
}
