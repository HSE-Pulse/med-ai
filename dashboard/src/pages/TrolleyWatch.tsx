import { useEffect, useState } from "react";
import { Activity, Download, RefreshCw } from "lucide-react";
import {
  trolleyCount,
  trolleyHistory,
  trolleyINMOReport,
  trolleyCompliance,
  type TrolleyCount,
} from "../lib/api";

export default function TrolleyWatch() {
  const [count, setCount] = useState<TrolleyCount | null>(null);
  const [history, setHistory] = useState<Array<{ date: string; count: number }>>([]);
  const [inmo, setInmo] = useState<any[]>([]);
  const [compliance, setCompliance] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportDate, setReportDate] = useState<string>("");

  async function refresh() {
    setLoading(true);
    const [c, h, i, cp] = await Promise.all([
      trolleyCount(),
      trolleyHistory(7),
      trolleyINMOReport(reportDate || undefined),
      trolleyCompliance(),
    ]);
    setCount(c);
    setHistory(h || []);
    setInmo(i || []);
    setCompliance(cp);
    setLoading(false);
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [reportDate]);

  const totalToday = count?.total ?? 0;
  const breachThreshold = 15;
  const breach = totalToday >= breachThreshold;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-xl font-semibold text-white">HSE Trolley Watch</h2>
          <p className="text-sm text-slate-400">
            Port 8216 — INMO-compatible daily snapshot & PET-correlation. Counts only, no PHI.
          </p>
        </div>
        <button onClick={refresh} className="px-3 py-1.5 text-sm rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: "ED", value: count?.ed ?? 0 },
          { label: "Corridor", value: count?.corridor ?? 0 },
          { label: "Ward", value: count?.ward ?? 0 },
          { label: "Total (2h window)", value: totalToday, highlight: breach },
        ].map((c) => (
          <div key={c.label} className="bg-bg-card border border-border rounded-xl p-4">
            <div className="text-xs uppercase tracking-wide text-slate-500">{c.label}</div>
            <div className={`mt-2 text-2xl font-semibold ${c.highlight ? "text-rose-400" : "text-white"}`}>
              {c.value}
            </div>
          </div>
        ))}
      </div>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white flex items-center gap-2">
            <Activity className="w-4 h-4 text-amber-400" /> 7-Day History
          </h3>
        </div>
        {loading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : history.length === 0 ? (
          <p className="text-sm text-slate-500">No data.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-slate-400">
              <tr><th className="text-left py-1.5">Date</th><th className="text-right py-1.5">Count</th></tr>
            </thead>
            <tbody>
              {history.map((row) => (
                <tr key={row.date} className="border-t border-border">
                  <td className="py-1.5 font-mono-clinical">{row.date}</td>
                  <td className="py-1.5 text-right text-slate-200">{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white flex items-center gap-2">
            <Download className="w-4 h-4 text-emerald-400" /> INMO Daily Snapshot
          </h3>
          <input
            type="date"
            value={reportDate}
            onChange={(e) => setReportDate(e.target.value)}
            className="bg-bg-primary border border-border rounded-lg px-3 py-1.5 text-sm text-white"
          />
        </div>
        {inmo.length === 0 ? (
          <p className="text-sm text-slate-500">No report for selected date.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-slate-400">
              <tr>
                <th className="text-left py-1.5">Hospital</th>
                <th className="text-left py-1.5">Region</th>
                <th className="text-left py-1.5">Date</th>
                <th className="text-left py-1.5">Time</th>
                <th className="text-right py-1.5">ED</th>
                <th className="text-right py-1.5">Wards</th>
                <th className="text-right py-1.5">Total</th>
              </tr>
            </thead>
            <tbody>
              {inmo.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1.5">{r.hospital}</td>
                  <td className="py-1.5 text-slate-400">{r.region}</td>
                  <td className="py-1.5 font-mono-clinical">{r.date}</td>
                  <td className="py-1.5 font-mono-clinical">{r.time_of_count}</td>
                  <td className="py-1.5 text-right">{r.trolleys_ed}</td>
                  <td className="py-1.5 text-right">{r.trolleys_wards}</td>
                  <td className="py-1.5 text-right text-white font-semibold">{r.total}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3">PET Correlation</h3>
        {compliance ? (
          <div className="text-sm text-slate-300 space-y-1">
            <div>PET-breach events (window): <span className="font-semibold text-white">{compliance.breach_events_window}</span></div>
            <div>Current trolleys (rolling): <span className="font-semibold text-white">{compliance.current_trolleys}</span></div>
            <p className="text-xs text-slate-500 mt-2">{compliance.hint}</p>
          </div>
        ) : (
          <p className="text-sm text-slate-500">—</p>
        )}
      </section>
    </div>
  );
}
