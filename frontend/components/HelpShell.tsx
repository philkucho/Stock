"use client";

import React, { useEffect } from "react";

export default function HelpShell({
  open,
  onClose,
  title,
  subtitle,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/5 transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={`fixed right-0 top-0 z-50 h-screen w-full max-w-md transform border-l border-zinc-200 bg-white shadow-2xl transition-transform duration-300 dark:border-zinc-800 dark:bg-zinc-950 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        role="dialog"
        aria-modal="false"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950">
          <div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">
              {title}
            </h2>
            {subtitle && (
              <p className="text-[11px] text-zinc-500">{subtitle}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
            aria-label="닫기"
            title="닫기 (ESC)"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 8.586l4.95-4.95 1.414 1.414L11.414 10l4.95 4.95-1.414 1.414L10 11.414l-4.95 4.95-1.414-1.414L8.586 10 3.636 5.05 5.05 3.636 10 8.586z" />
            </svg>
          </button>
        </div>
        <div className="h-[calc(100vh-4.5rem)] overflow-y-auto p-5 text-sm text-zinc-700 dark:text-zinc-300">
          {children}
        </div>
      </aside>
    </>
  );
}

export function HelpSection({
  emoji,
  title,
  children,
}: {
  emoji: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 border-b border-zinc-100 pb-5 last:border-b-0 dark:border-zinc-800">
      <h3 className="mb-2 text-base font-semibold text-zinc-900 dark:text-zinc-50">
        <span className="mr-2">{emoji}</span>
        {title}
      </h3>
      <div className="space-y-2 text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">
        {children}
      </div>
    </section>
  );
}

export function HelpBullets({ children }: { children: React.ReactNode }) {
  return <ul className="ml-1 list-disc space-y-1 pl-4">{children}</ul>;
}

export function HelpNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border border-blue-200 bg-blue-50 p-2 text-[11px] text-blue-900 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-200">
      💡 {children}
    </div>
  );
}

export function HelpFaq({
  q,
  children,
}: {
  q: string;
  children: React.ReactNode;
}) {
  return (
    <details className="rounded border border-zinc-200 p-2 dark:border-zinc-800">
      <summary className="cursor-pointer text-xs font-medium text-zinc-900 dark:text-zinc-50">
        Q. {q}
      </summary>
      <p className="mt-2 pl-3 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-400">
        {children}
      </p>
    </details>
  );
}
