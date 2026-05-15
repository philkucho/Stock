"use client";

import { useEffect, useState } from "react";
import TopNav from "@/components/TopNav";
import { fetchAccount, fetchHealth, type Account } from "@/lib/api";
import HelpDrawer from "./HelpDrawer";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; version: string }
  | { kind: "error"; message: string };

export default function Home() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [account, setAccount] = useState<Account | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    fetchHealth()
      .then((h) => setHealth({ kind: "ok", version: h.version }))
      .catch((e) => setHealth({ kind: "error", message: String(e) }));

    fetchAccount()
      .then(setAccount)
      .catch((e) => setAccountError(String(e)));
  }, []);

  return (
    <main className="min-h-screen bg-zinc-50 p-8 dark:bg-black">
      <HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div className="mx-auto max-w-4xl space-y-6">
        <TopNav />
        <header className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-3xl font-bold text-black dark:text-zinc-50">
              Stock Autotrader
            </h1>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              NautilusTrader + Webull OpenAPI dashboard
            </p>
          </div>
          <button
            onClick={() => setHelpOpen(!helpOpen)}
            className={`rounded-lg border px-3 py-2 text-sm transition-colors ${
              helpOpen
                ? "border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-950 dark:text-blue-300"
                : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
            }`}
          >
            ❓ 도움말
          </button>
        </header>

        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-3 text-lg font-semibold text-black dark:text-zinc-50">
            Backend health
          </h2>
          {health.kind === "loading" && (
            <p className="text-zinc-500">Connecting to FastAPI...</p>
          )}
          {health.kind === "ok" && (
            <p className="text-emerald-600 dark:text-emerald-400">
              ✓ API up (v{health.version})
            </p>
          )}
          {health.kind === "error" && (
            <p className="text-red-600 dark:text-red-400">
              ✗ API 서버에 연결할 수 없습니다 — {health.message}
              <br />
              <span className="text-xs text-zinc-500">
                서버가 꺼져 있다면 다음 명령어로 시작하세요(개발자용):{" "}
                <code>uvicorn api.main:app --reload --port 8000</code>
              </span>
            </p>
          )}
        </section>

        <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-3 text-lg font-semibold text-black dark:text-zinc-50">
            Account
          </h2>
          {accountError && (
            <p className="text-red-600 dark:text-red-400">✗ {accountError}</p>
          )}
          {account && (
            <dl className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-zinc-500">Balance (USD)</dt>
                <dd className="text-lg font-mono text-black dark:text-zinc-50">
                  {account.balance_usd ?? "—"}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">Buying power</dt>
                <dd className="text-lg font-mono text-black dark:text-zinc-50">
                  {account.buying_power ?? "—"}
                </dd>
              </div>
              <div className="col-span-2">
                <dt className="text-zinc-500">Status</dt>
                <dd className="text-sm text-zinc-700 dark:text-zinc-300">
                  {account.status}
                </dd>
              </div>
            </dl>
          )}
        </section>

        <footer className="pt-4 text-xs text-zinc-500">
          Day 1 skeleton — Webull adapter implementation pending.
        </footer>
      </div>
    </main>
  );
}
