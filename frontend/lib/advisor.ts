// AI 자문 에이전트 API 클라이언트.
// 백엔드: api/routes/advisor.py
import { API_BASE } from "@/lib/api";

export type AdvisorRecommendation = {
  id: number;
  rec_date: string;
  rec_type: "morning" | "intraday_entry" | "intraday_add" | "intraday_exit";
  symbol: string;
  side: "BUY" | "SELL";
  entry_price: string | null;
  stop_price: string | null;
  target_1r: string | null;
  target_2r: string | null;
  qty: number | null;
  confidence: string | null;
  reasoning_text: string | null;
  status: "pending" | "approved" | "rejected" | "expired" | "executed";
  user_decision_at: string | null;
  reject_reason: string | null;
  expires_at: string;
  trade_plan_id: number | null;
  model_version: string | null;
  prompt_version: string | null;
  created_at: string;
};

export async function fetchAdvisorRecommendationsToday(): Promise<AdvisorRecommendation[]> {
  const res = await fetch(`${API_BASE}/api/advisor/recommendations/today`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`자문 목록 조회 실패: ${res.status}`);
  return res.json();
}

export async function triggerMorningBrief(opts: { dryRun?: boolean; notifyTelegram?: boolean } = {}): Promise<unknown> {
  const res = await fetch(`${API_BASE}/api/advisor/morning-brief`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: opts.dryRun ?? false,
      notify_telegram: opts.notifyTelegram ?? true,
    }),
  });
  if (!res.ok) throw new Error(`Morning brief 트리거 실패: ${res.status}`);
  return res.json();
}

export async function approveAdvisorRecommendation(
  recId: number,
  amountUsd?: number,
): Promise<{ status: string; trade_plan_id?: number; shares?: number; amount_usd?: number; message?: string }> {
  const res = await fetch(`${API_BASE}/api/advisor/recommendations/${recId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(amountUsd ? { amount_usd: amountUsd } : {}),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`승인 실패: ${res.status} ${body}`);
  }
  return res.json();
}

export async function rejectAdvisorRecommendation(
  recId: number,
  reason: string,
): Promise<{ status: string; rec_type?: string }> {
  const res = await fetch(`${API_BASE}/api/advisor/recommendations/${recId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`거부 실패: ${res.status} ${body}`);
  }
  return res.json();
}
