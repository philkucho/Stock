"use client";

import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  LineSeries,
  LineStyle,
  createChart,
} from "lightweight-charts";

import { fetchSymbolBars, type ChartResponse } from "@/lib/api";

type Props = {
  symbol: string;
  onClose: () => void;
  // 카드의 levels를 그대로 받아와서 동기화 (매번 다시 계산하지 않음)
  entry?: number;
  stop?: number;
  target1r?: number;
  target2r?: number;
  // ATR mult 슬라이더 값 그대로 사용
  atrMult?: number;
  equity?: number;
  riskPerTrade?: number;
};

export default function ChartModal({
  symbol,
  onClose,
  entry,
  stop,
  target1r,
  target2r,
  atrMult,
  equity,
  riskPerTrade,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ma20Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma50Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma200Ref = useRef<ISeriesApi<"Line"> | null>(null);

  const [data, setData] = useState<ChartResponse | null>(null);
  const [days, setDays] = useState(120);
  const [showMA20, setShowMA20] = useState(true);
  const [showMA50, setShowMA50] = useState(true);
  const [showMA200, setShowMA200] = useState(true);
  const [showLevels, setShowLevels] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // ESC로 닫기
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 데이터 fetch
  useEffect(() => {
    setErr(null);
    fetchSymbolBars(symbol, { days, atrMult, equity, riskPerTrade })
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [symbol, days, atrMult, equity, riskPerTrade]);

  // 차트 초기화
  useEffect(() => {
    if (!containerRef.current) return;
    const isDark = document.documentElement.classList.contains("dark") ||
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: isDark ? "#18181b" : "#ffffff" },
        textColor: isDark ? "#a1a1aa" : "#27272a",
      },
      grid: {
        vertLines: { color: isDark ? "#27272a" : "#f4f4f5" },
        horzLines: { color: isDark ? "#27272a" : "#f4f4f5" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: isDark ? "#3f3f46" : "#e4e4e7" },
      timeScale: { borderColor: isDark ? "#3f3f46" : "#e4e4e7", timeVisible: false },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });
    const ma20 = chart.addSeries(LineSeries, { color: "#3b82f6", lineWidth: 1, title: "MA20" });
    const ma50 = chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 1, title: "MA50" });
    const ma200 = chart.addSeries(LineSeries, { color: "#a855f7", lineWidth: 1, title: "MA200" });
    chartRef.current = chart;
    candleSeriesRef.current = candle;
    ma20Ref.current = ma20;
    ma50Ref.current = ma50;
    ma200Ref.current = ma200;

    return () => {
      chart.remove();
    };
  }, []);

  // 데이터 적용
  useEffect(() => {
    if (!data || !candleSeriesRef.current || !ma20Ref.current || !ma50Ref.current || !ma200Ref.current) return;
    const candleData = data.bars.map((b) => ({
      time: b.time,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));
    candleSeriesRef.current.setData(candleData);

    const buildLine = (vals: (number | null)[]) =>
      data.bars
        .map((b, i) => (vals[i] !== null ? { time: b.time, value: vals[i] as number } : null))
        .filter((x): x is { time: string; value: number } => x !== null);

    ma20Ref.current.setData(showMA20 ? buildLine(data.ma20) : []);
    ma50Ref.current.setData(showMA50 ? buildLine(data.ma50) : []);
    ma200Ref.current.setData(showMA200 ? buildLine(data.ma200) : []);

    chartRef.current?.timeScale().fitContent();
  }, [data, showMA20, showMA50, showMA200]);

  // 가격선 (entry/stop/1R/2R)
  useEffect(() => {
    const candle = candleSeriesRef.current;
    if (!candle) return;
    // 모든 priceLines 제거 후 새로 추가
    // (lightweight-charts API: createPriceLine + remove 추적)
    const lines: ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[] = [];
    if (showLevels) {
      const ent = entry ?? data?.levels?.entry;
      const stp = stop ?? data?.levels?.stop;
      const t1 = target1r ?? data?.levels?.target_1r;
      const t2 = target2r ?? data?.levels?.target_2r;
      if (ent) {
        lines.push(candle.createPriceLine({ price: ent, color: "#3b82f6", lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: "진입" }));
      }
      if (stp) {
        lines.push(candle.createPriceLine({ price: stp, color: "#ef4444", lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "손절" }));
      }
      if (t1) {
        lines.push(candle.createPriceLine({ price: t1, color: "#10b981", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "1R" }));
      }
      if (t2) {
        lines.push(candle.createPriceLine({ price: t2, color: "#059669", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "2R" }));
      }
    }
    return () => {
      lines.forEach((l) => candle.removePriceLine(l));
    };
  }, [data, showLevels, entry, stop, target1r, target2r]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-5xl max-h-[90vh] overflow-hidden rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-200 dark:border-zinc-800">
          <div>
            <h2 className="text-xl font-bold">{symbol} <span className="text-sm font-normal text-zinc-500">차트</span></h2>
            {data?.levels && (
              <div className="text-xs text-zinc-600 dark:text-zinc-400 font-mono mt-0.5">
                진입 ${data.levels.entry.toFixed(2)} · 손절 ${data.levels.stop.toFixed(2)} · 1R ${data.levels.target_1r.toFixed(2)} · 2R ${data.levels.target_2r.toFixed(2)}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
            aria-label="닫기"
          >
            ✕ 닫기 <span className="text-[10px] text-zinc-500">(ESC)</span>
          </button>
        </div>

        {/* 옵션 */}
        <div className="flex flex-wrap items-center gap-3 px-5 py-2 border-b border-zinc-200 dark:border-zinc-800 text-xs bg-zinc-50 dark:bg-zinc-900/50">
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-500">기간</span>
            {[60, 120, 250, 500].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-2 py-0.5 rounded ${
                  days === d
                    ? "bg-violet-600 text-white"
                    : "bg-zinc-200 dark:bg-zinc-800 hover:bg-zinc-300 dark:hover:bg-zinc-700"
                }`}
              >
                {d}일
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={showMA20} onChange={(e) => setShowMA20(e.target.checked)} />
              <span style={{ color: "#3b82f6" }}>MA20</span>
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={showMA50} onChange={(e) => setShowMA50(e.target.checked)} />
              <span style={{ color: "#f59e0b" }}>MA50</span>
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={showMA200} onChange={(e) => setShowMA200(e.target.checked)} />
              <span style={{ color: "#a855f7" }}>MA200</span>
            </label>
            <label className="flex items-center gap-1 cursor-pointer">
              <input type="checkbox" checked={showLevels} onChange={(e) => setShowLevels(e.target.checked)} />
              <span>진입/손절선</span>
            </label>
          </div>
        </div>

        {/* 차트 영역 */}
        <div className="flex-1 min-h-0 relative">
          {err && (
            <div className="absolute inset-0 flex items-center justify-center text-rose-600">
              오류: {err}
            </div>
          )}
          {!data && !err && (
            <div className="absolute inset-0 flex items-center justify-center text-zinc-500">
              차트 불러오는 중...
            </div>
          )}
          <div ref={containerRef} className="w-full h-[500px]" />
        </div>
      </div>
    </div>
  );
}
