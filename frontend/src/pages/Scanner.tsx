import { useState, useRef, useEffect } from "react";
import {
  ScanLine, Search, Loader2, AlertCircle, CheckCircle2, XCircle,
  MinusCircle, Calendar, TrendingUp,
} from "lucide-react";
import { echarts } from "@/lib/echarts";

const STRATEGIES = [
  { key: "elliott-wave", label: "Elliott Wave" },
  { key: "chanlun", label: "Chanlun" },
  { key: "smc", label: "SMC" },
  { key: "technical-basic", label: "Tech-Basic" },
  { key: "ichimoku", label: "Ichimoku" },
  { key: "candlestick", label: "Candlestick" },
] as const;

const INTERVALS = [
  { key: "1D", label: "Daily" },
  { key: "1H", label: "1 Hour" },
  { key: "30m", label: "30 min" },
  { key: "15m", label: "15 min" },
  { key: "5m", label: "5 min" },
] as const;

const UNIVERSES = [
  { key: "csi300", label: "CSI 300" },
  { key: "csi500", label: "CSI 500" },
  { key: "all_a", label: "All A-shares" },
  { key: "custom", label: "Custom codes" },
] as const;

interface TriggeredStock {
  code: string;
  name?: string | null;
  signal: number;
  signal_label: string;
  effective_date?: string;
  date?: string;
  strategy?: string;
  bar_time?: string;
  close_price?: number | null;
}

interface DateGroup {
  date: string;
  count: number;
  stocks: TriggeredStock[];
}

interface StrategyResult {
  status: string;
  error?: string;
  scanned: number;
  trading_days?: number;
  triggered_count: number;
  hit_codes: string[];
  hit_code_count: number;
  triggered?: TriggeredStock[];
  triggered_by_date?: DateGroup[];
}

interface PlotBar {
  t: string; o: number | null; h: number | null; l: number | null; c: number | null;
}
interface PlotSignal {
  t: string; price: number | null; label: string; strategy: string;
}
interface PlotStock {
  bars: PlotBar[];
  signals: PlotSignal[];
}
interface ScanResult {
  status: string;
  strategies: string[];
  interval?: string;
  target_date?: string;
  start_date?: string;
  end_date?: string;
  universe: string;
  effective_source?: string;
  total_stock_count?: number;
  total_scanned: number;
  fetch_errors?: number;
  failed_codes?: string[];
  trading_days_scanned?: number;
  triggered_count: number;
  results_by_strategy: Record<string, StrategyResult>;
  triggered: TriggeredStock[];
  triggered_by_date?: DateGroup[];
  plot_data?: Record<string, PlotStock>;
}

export function Scanner() {
  const [selectedStrategies, setSelectedStrategies] = useState<Set<string>>(
    new Set(["elliott-wave"])
  );
  const [mode, setMode] = useState<"single" | "range">("range");
  const [targetDate, setTargetDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [interval, setInterval] = useState("1D");
  const [universe, setUniverse] = useState("csi300");
  const [codes, setCodes] = useState("");
  const [maxStocks, setMaxStocks] = useState("100");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null);

  const toggleStrategy = (key: string) => {
    setSelectedStrategies((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size > 1) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const scan = async () => {
    setError(null);
    setResult(null);
    setExpandedStrategy(null);
    setLoading(true);

    const params = new URLSearchParams();
    params.set("strategy", [...selectedStrategies].join(","));

    if (mode === "single") {
      params.set("target_date", targetDate);
    } else {
      params.set("start_date", startDate);
      params.set("end_date", endDate);
    }

    if (universe === "custom") {
      if (codes.trim()) params.set("codes", codes.trim());
    } else {
      params.set("universe", universe);
      if (universe === "all_a" && maxStocks.trim()) {
        params.set("max_stocks", maxStocks.trim());
      }
    }
    params.set("interval", interval);

    try {
      const res = await fetch(`/scanner/screen?${params.toString()}`, {
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = body.detail || body.message || detail;
        } catch { /* ignore */ }
        throw new Error(detail);
      }
      const data: ScanResult = await res.json();
      setResult(data);
      if (data.results_by_strategy) {
        for (const [k, v] of Object.entries(data.results_by_strategy)) {
          if (v.triggered_count > 0) {
            setExpandedStrategy(k);
            break;
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  const signalIcon = (label: string) => {
    switch (label) {
      case "long":
        return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />;
      case "short":
        return <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />;
      case "partial":
        return <MinusCircle className="h-3.5 w-3.5 text-amber-500 shrink-0" />;
      default:
        return <MinusCircle className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
    }
  };

  const signalBadge = (label: string) => {
    const base = "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium";
    switch (label) {
      case "long":
        return `${base} bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400`;
      case "short":
        return `${base} bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400`;
      default:
        return `${base} bg-muted text-muted-foreground`;
    }
  };

  const STRATEGY_COLORS: Record<string, string> = {
    "elliott-wave": "border-l-indigo-500",
    "chanlun": "border-l-purple-500",
    "smc": "border-l-amber-500",
    "technical-basic": "border-l-cyan-500",
    "ichimoku": "border-l-emerald-500",
    "candlestick": "border-l-rose-500",
  };

  return (
    <div className="flex flex-col gap-6 p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <ScanLine className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">Stock Scanner</h1>
      </div>

      {/* Controls */}
      <div className="flex flex-col gap-4 border rounded-lg p-4">
        {/* Strategy — multi-select */}
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">
            Strategies{" "}
            <span className="text-muted-foreground font-normal">
              ({selectedStrategies.size} selected)
            </span>
          </label>
          <div className="flex flex-wrap gap-1.5">
            {STRATEGIES.map(({ key, label }) => {
              const active = selectedStrategies.has(key);
              return (
                <button
                  key={key}
                  onClick={() => toggleStrategy(key)}
                  className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                    active
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-muted-foreground/30 hover:border-primary"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Mode */}
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">Mode</label>
          <div className="flex gap-1.5">
            {(["single", "range"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm border transition-colors ${
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "border-muted-foreground/30 hover:border-primary"
                }`}
              >
                <Calendar className="h-3.5 w-3.5" />
                {m === "single" ? "Single date" : "Date range"}
              </button>
            ))}
          </div>
        </div>

        {mode === "single" ? (
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Target date</label>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              className="w-48 px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
        ) : (
          <div className="flex items-end gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">Start date</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-48 px-3 py-2 rounded-md border bg-background text-sm"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">End date</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-48 px-3 py-2 rounded-md border bg-background text-sm"
              />
            </div>
          </div>
        )}

        {/* Interval */}
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">Interval</label>
          <div className="flex flex-wrap gap-1.5">
            {INTERVALS.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setInterval(key)}
                className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                  interval === key
                    ? "bg-primary text-primary-foreground"
                    : "border-muted-foreground/30 hover:border-primary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Universe */}
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">Universe</label>
          <div className="flex flex-wrap gap-1.5">
            {UNIVERSES.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setUniverse(key)}
                className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                  universe === key
                    ? "bg-primary text-primary-foreground"
                    : "border-muted-foreground/30 hover:border-primary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {universe === "custom" && (
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Stock codes</label>
            <input
              type="text"
              value={codes}
              onChange={(e) => setCodes(e.target.value)}
              placeholder="600519.SH,000858.SZ,000636.SZ"
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
        )}

        {universe === "all_a" && (
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Max stocks to scan</label>
            <input
              type="number"
              value={maxStocks}
              onChange={(e) => setMaxStocks(e.target.value)}
              placeholder="100"
              min="10"
              max="5000"
              className="w-32 px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground">
              ~5,000 stocks total. Capping avoids timeouts. 100-300 recommended.
            </p>
          </div>
        )}

        <button
          onClick={scan}
          disabled={loading}
          className="self-start flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {loading ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> Scanning...</>
          ) : (
            <><Search className="h-4 w-4" /> Scan</>
          )}
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 text-sm text-red-600 dark:text-red-400 border border-red-500/30 rounded p-3 bg-red-50 dark:bg-red-950/20">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          {error}
        </div>
      )}

      {result && (
        <div className="flex flex-col gap-4">
          {/* Global summary */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-muted-foreground border rounded-lg p-4">
            <span>
              Strategies:{" "}
              <strong className="text-foreground">{result.strategies.join(", ")}</strong>
            </span>
            {result.interval && (
              <span>
                Interval: <strong className="text-foreground">{result.interval}</strong>
              </span>
            )}
            {result.effective_source && (
              <span>
                Source: <strong className="text-foreground">{result.effective_source}</strong>
              </span>
            )}
            {mode === "range" ? (
              <>
                <span>
                  Range: <strong className="text-foreground">{result.start_date} → {result.end_date}</strong>
                </span>
                <span>
                  Trading days: <strong className="text-foreground">{result.trading_days_scanned ?? "—"}</strong>
                </span>
              </>
            ) : (
              <span>
                Date: <strong className="text-foreground">{result.target_date}</strong>
              </span>
            )}
            <span>
              Stocks: <strong className="text-foreground">{result.total_scanned}</strong>
              {result.total_stock_count != null && result.total_stock_count !== result.total_scanned && (
                <> / {result.total_stock_count} total</>
              )}
            </span>
            {result.failed_codes && result.failed_codes.length > 0 && (
              <span className="text-amber-600 dark:text-amber-400 text-xs">
                Failed: {result.failed_codes.slice(0, 3).join(", ")}
                {result.failed_codes.length > 3 ? ` +${result.failed_codes.length - 3} more` : ""}
              </span>
            )}
            <span className="flex items-center gap-1">
              Total signals: <strong className="text-foreground">{result.triggered_count}</strong>
            </span>
          </div>

          {/* Per-strategy cards */}
          {Object.entries(result.results_by_strategy).map(([key, sr]) => (
            <div
              key={key}
              className={`border rounded-lg overflow-hidden border-l-4 ${STRATEGY_COLORS[key] || "border-l-primary"}`}
            >
              <button
                onClick={() =>
                  setExpandedStrategy((prev) => (prev === key ? null : key))
                }
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/30 transition-colors text-left"
              >
                <div className="flex items-center gap-3">
                  <span className="font-semibold text-foreground">{key}</span>
                  {sr.status === "ok" ? (
                    <span className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <TrendingUp className="h-3 w-3" />
                        {sr.triggered_count} signals
                      </span>
                      <span>
                        {sr.hit_code_count} stocks hit
                      </span>
                      <span>
                        {sr.scanned} stocks analyzed
                      </span>
                    </span>
                  ) : (
                    <span className="text-xs text-red-500">{sr.error}</span>
                  )}
                </div>
                <span className="text-xs text-muted-foreground">
                  {expandedStrategy === key ? "▲" : "▼"}
                </span>
              </button>

              {expandedStrategy === key && sr.status === "ok" && (
                <div className="border-t px-4 py-3">
                  {sr.hit_codes.length > 0 && (
                    <div className="mb-3">
                      <div className="text-xs font-medium text-muted-foreground mb-1.5">
                        Hit stocks ({sr.hit_code_count})
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {sr.hit_codes.map((code) => (
                          <span
                            key={code}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-muted font-mono"
                          >
                            {code}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {sr.triggered_by_date && sr.triggered_by_date.length > 0 ? (
                    <div className="flex flex-col gap-2 max-h-80 overflow-y-auto">
                      {sr.triggered_by_date
                        .filter((d) => d.count > 0)
                        .map((dg) => (
                          <div key={dg.date} className="border rounded-md">
                            <div className="flex items-center gap-2 px-2 py-1 bg-muted/40 text-xs font-medium text-muted-foreground">
                              <Calendar className="h-3 w-3" />
                              <span className="font-mono">{dg.date}</span>
                              <span>{dg.count} signal{dg.count !== 1 ? "s" : ""}</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5 p-2">
                              {dg.stocks.map((s, i) => (
                                <span
                                  key={`${s.code}-${i}`}
                                  className={signalBadge(s.signal_label)}
                                >
                                  {signalIcon(s.signal_label)}
                                  <span className="font-mono">{s.code}</span>
                                  {s.bar_time && s.bar_time.includes(":") && (
                                    <span className="opacity-70 text-[10px]">
                                      {s.bar_time.slice(-5)}
                                    </span>
                                  )}
                                  {s.close_price != null && (
                                    <span className="opacity-70">¥{s.close_price}</span>
                                  )}
                                </span>
                              ))}
                            </div>
                          </div>
                        ))}
                    </div>
                  ) : sr.triggered && sr.triggered.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {sr.triggered.map((s, i) => (
                        <span
                          key={`${s.code}-${i}`}
                          className={signalBadge(s.signal_label)}
                        >
                          {signalIcon(s.signal_label)}
                          <span className="font-mono">{s.code}</span>
                          {s.bar_time && s.bar_time.includes(":") && (
                            <span className="opacity-70 text-[10px]">
                              {s.bar_time.slice(-5)}
                            </span>
                          )}
                          {s.close_price != null && (
                            <span className="opacity-70">¥{s.close_price}</span>
                          )}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      No signals triggered with {key}.
                    </p>
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Price charts with signal markers */}
          {result.plot_data && Object.keys(result.plot_data).length > 0 && (
            <div className="flex flex-col gap-6 mt-2">
              <h3 className="text-sm font-semibold text-muted-foreground">
                Price & Signal Charts
              </h3>
              {Object.entries(result.plot_data).map(([code, pd]) => (
                <SignalChart
                  key={code}
                  code={code}
                  data={pd}
                  isIntraday={result.interval !== "1D"}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SignalChart({
  code,
  data,
  isIntraday,
}: {
  code: string;
  data: PlotStock;
  isIntraday: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !data.bars.length) return;
    const chart = echarts.init(ref.current);

    const dates = data.bars.map((b) => b.t);
    const closes = data.bars.map((b) => b.c);
    const opens = data.bars.map((b) => b.o);
    const highs = data.bars.map((b) => b.h);
    const lows = data.bars.map((b) => b.l);

    const buySignals = data.signals
      .filter((s) => s.label === "long")
      .map((s) => [s.t, s.price ?? null]);
    const sellSignals = data.signals
      .filter((s) => s.label === "short")
      .map((s) => [s.t, s.price ?? null]);

    const signalTooltips = Object.fromEntries(
      data.signals.map((s) => [s.t, `${s.label} (${s.strategy})`])
    );

    const option = {
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const p = params as Array<{ axisValue: string; seriesName: string; value: unknown }>;
          const t = p[0]?.axisValue ?? "";
          const sig = signalTooltips[t];
          let html = `<strong>${t}</strong><br/>`;
          for (const item of p) {
            if (item.seriesName === "Buy" || item.seriesName === "Sell") continue;
            const v = item.value as number[];
            if (Array.isArray(v)) {
              html += `O:${v[1]} H:${v[2]} L:${v[3]} C:${v[4]}<br/>`;
            } else {
              html += `${item.seriesName}: ${item.value}<br/>`;
            }
          }
          if (sig) html += `<br/><span style="font-weight:bold;color:#f59e0b">⚡ ${sig}</span>`;
          return html;
        },
      },
      grid: { top: 30, right: 20, bottom: 40, left: 60 },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: {
          rotate: isIntraday ? 45 : 0,
          formatter: (v: string) =>
            isIntraday ? v.slice(5) : v.slice(5), // MM-DD or MM-DD HH:MM
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { formatter: (v: number) => `¥${v}` },
      },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
      legend: { data: ["Price", "Buy", "Sell"], top: 0 },
      series: [
        {
          name: "Price",
          type: "candlestick",
          data: dates.map((_, i) => [opens[i], closes[i], lows[i], highs[i]]),
          itemStyle: {
            color: "#ef4444",
            color0: "#22c55e",
            borderColor: "#ef4444",
            borderColor0: "#22c55e",
          },
        },
        {
          name: "Buy",
          type: "scatter",
          data: buySignals,
          symbolSize: 10,
          itemStyle: { color: "#ef4444" },
        },
        {
          name: "Sell",
          type: "scatter",
          data: sellSignals,
          symbolSize: 10,
          itemStyle: { color: "#22c55e" },
        },
      ],
    };

    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [data, isIntraday]);

  return (
    <div className="border rounded-lg p-3">
      <div className="text-xs font-semibold text-muted-foreground mb-2 font-mono">
        {code}
      </div>
      <div ref={ref} style={{ width: "100%", height: 320 }} />
    </div>
  );
}
