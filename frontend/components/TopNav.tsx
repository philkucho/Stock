"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type Tab = {
  href: string;
  label: string;
  title?: string;
  inactive: string;
  active: string;
};

const TABS: Tab[] = [
  {
    href: "/",
    label: "🏠 홈",
    title: "메인 — Backend health / Account",
    inactive:
      "border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
    active:
      "border-2 border-zinc-800 bg-zinc-200 text-zinc-900 font-bold dark:border-zinc-300 dark:bg-zinc-700 dark:text-white",
  },
  {
    href: "/market",
    label: "🧭 시장 진단",
    title: "오늘의 시장 상황 — 7개 시그널 자동 평가 + 권장 행동 (장 open 전 자동 갱신)",
    inactive:
      "border-sky-300 bg-sky-50 text-sky-700 hover:bg-sky-100 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-950",
    active:
      "border-2 border-sky-500 bg-sky-200 text-sky-900 font-bold dark:border-sky-400 dark:bg-sky-700 dark:text-white",
  },
  {
    href: "/trading",
    label: "🎯 매매 Plan",
    title: "오늘의 매매 Plan — 5-Model 단타 워치리스트 + 금액 입력",
    inactive:
      "border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950",
    active:
      "border-2 border-rose-500 bg-rose-200 text-rose-900 font-bold dark:border-rose-400 dark:bg-rose-700 dark:text-white",
  },
  {
    href: "/dashboard",
    label: "🎯 대시보드",
    title: "통합 대시보드 — 시그널/진입가/자연어 해석",
    inactive:
      "border-violet-300 bg-violet-50 text-violet-700 hover:bg-violet-100 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300 dark:hover:bg-violet-950",
    active:
      "border-2 border-violet-500 bg-violet-200 text-violet-900 font-bold dark:border-violet-400 dark:bg-violet-700 dark:text-white",
  },
  {
    href: "/scanner",
    label: "📊 스캐너",
    title: "거래량+모멘텀 스캐너 (v3 화이트리스트)",
    inactive:
      "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300 dark:hover:bg-emerald-950",
    active:
      "border-2 border-emerald-500 bg-emerald-200 text-emerald-900 font-bold dark:border-emerald-400 dark:bg-emerald-700 dark:text-white",
  },
  {
    href: "/picks",
    label: "Picks",
    title: "Daily Picks (Stage 2)",
    inactive:
      "border-blue-300 bg-blue-50 text-blue-700 hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300 dark:hover:bg-blue-950",
    active:
      "border-2 border-blue-500 bg-blue-200 text-blue-900 font-bold dark:border-blue-400 dark:bg-blue-700 dark:text-white",
  },
  {
    href: "/comparison",
    label: "📈 시스템 비교",
    title: "3 시스템 picks 1d/5d/10d 후 실현 수익 비교",
    inactive:
      "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300 dark:hover:bg-amber-950",
    active:
      "border-2 border-amber-500 bg-amber-200 text-amber-900 font-bold dark:border-amber-400 dark:bg-amber-700 dark:text-white",
  },
  {
    href: "/sources",
    label: "🚦 진입 경로",
    title: "3가지 진입 경로(사용자/ORB자동/AI자문)별 실거래 성과 + 거래내역",
    inactive:
      "border-indigo-300 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-950",
    active:
      "border-2 border-indigo-500 bg-indigo-200 text-indigo-900 font-bold dark:border-indigo-400 dark:bg-indigo-700 dark:text-white",
  },
  {
    href: "/longterm",
    label: "📅 중장기",
    title: "3~12개월 보유 종목 추천 (Fidelity 수동 발주용) — Stage 2 + RS + 12mo 모멘텀",
    inactive:
      "border-lime-300 bg-lime-50 text-lime-700 hover:bg-lime-100 dark:border-lime-900 dark:bg-lime-950/40 dark:text-lime-300 dark:hover:bg-lime-950",
    active:
      "border-2 border-lime-500 bg-lime-200 text-lime-900 font-bold dark:border-lime-400 dark:bg-lime-700 dark:text-white",
  },
  {
    href: "/review",
    label: "📒 일일 리뷰",
    title: "오늘 계획 vs 실제 결과 — EOD 리뷰",
    inactive:
      "border-teal-300 bg-teal-50 text-teal-700 hover:bg-teal-100 dark:border-teal-900 dark:bg-teal-950/40 dark:text-teal-300 dark:hover:bg-teal-950",
    active:
      "border-2 border-teal-500 bg-teal-200 text-teal-900 font-bold dark:border-teal-400 dark:bg-teal-700 dark:text-white",
  },
  {
    href: "/history",
    label: "📒 매매 History",
    title: "매매 Plan 내역 + 브로커 주문 + 누적 PnL",
    inactive:
      "border-cyan-300 bg-cyan-50 text-cyan-700 hover:bg-cyan-100 dark:border-cyan-900 dark:bg-cyan-950/40 dark:text-cyan-300 dark:hover:bg-cyan-950",
    active:
      "border-2 border-cyan-500 bg-cyan-200 text-cyan-900 font-bold dark:border-cyan-400 dark:bg-cyan-700 dark:text-white",
  },
  {
    href: "/activity",
    label: "📜 활동 기록",
    title: "시스템 ↔ AI 자문 ↔ Telegram ↔ Broker 모든 이벤트 시간순 통합",
    inactive:
      "border-pink-300 bg-pink-50 text-pink-700 hover:bg-pink-100 dark:border-pink-900 dark:bg-pink-950/40 dark:text-pink-300 dark:hover:bg-pink-950",
    active:
      "border-2 border-pink-500 bg-pink-200 text-pink-900 font-bold dark:border-pink-400 dark:bg-pink-700 dark:text-white",
  },
  {
    href: "/strategies",
    label: "Strategies",
    title: "전략 프리셋 / 활성 전략",
    inactive:
      "border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
    active:
      "border-2 border-zinc-800 bg-zinc-200 text-zinc-900 font-bold dark:border-zinc-300 dark:bg-zinc-700 dark:text-white",
  },
  {
    href: "/matrix",
    label: "Matrix",
    title: "Strategy × Symbol Fitness Matrix",
    inactive:
      "border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
    active:
      "border-2 border-zinc-800 bg-zinc-200 text-zinc-900 font-bold dark:border-zinc-300 dark:bg-zinc-700 dark:text-white",
  },
  {
    href: "/backtests",
    label: "Backtests",
    title: "저장된 백테스트 결과",
    inactive:
      "border-zinc-300 bg-white text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
    active:
      "border-2 border-zinc-800 bg-zinc-200 text-zinc-900 font-bold dark:border-zinc-300 dark:bg-zinc-700 dark:text-white",
  },
];

function isActiveTab(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

export default function TopNav() {
  const path = usePathname() ?? "/";
  return (
    <nav
      aria-label="메인 네비게이션"
      className="-mx-1 flex flex-wrap gap-2 border-b border-zinc-200 pb-3 dark:border-zinc-800"
    >
      {TABS.map((t) => {
        const active = isActiveTab(path, t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            title={t.title}
            aria-current={active ? "page" : undefined}
            className={`whitespace-nowrap rounded-lg border px-3 py-2 text-sm transition-colors ${
              active ? t.active : t.inactive
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
