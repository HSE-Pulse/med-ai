import { useEffect, useState } from "react";
import { Brain, TrendingUp, FileText, RefreshCw } from "lucide-react";
import {
  xaiDecisionLog,
  xaiModelCard,
  xaiOverrideStats,
  type XAIDecision,
} from "../lib/api";

const MODULES = ["ed_triage", "sepsis_icu", "oncology_ai", "hospital_ops", "bed_management"];

export default function XAIAudit() {
  const [decisions, setDecisions] = useState<XAIDecision[]>([]);
  const [overrides, setOverrides] = useState<any[]>([]);
  const [module, setModule] = useState<string>("ed_triage");
  const [card, setCard] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    const [d, o, c] = await Promise.all([
      xaiDecisionLog({ module, limit: 50 }),
      xaiOverrideStats(),
      xaiModelCard(module),
    ]);
    setDecisions(d || []);
    setOverrides(o || []);
    setCard(c);
    setLoading(false);
  }

  useEffect(() => { refresh(); }, [module]);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <Brain className="w-5 h-5 text-indigo-400" /> XAI Audit — EU AI Act Arts. 13, 14, 72
          </h2>
          <p className="text-sm text-slate-400">Port 8218 — SHAP-backed decision log, model cards, override rate as drift proxy.</p>
        </div>
        <button onClick={refresh} className="px-3 py-1.5 text-sm rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div role="tablist" aria-label="Module" className="flex gap-2 items-center flex-wrap">
        <span className="text-sm text-slate-400">Module:</span>
        {MODULES.map((m) => {
          const active = module === m;
          return (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setModule(m)}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-colors ${
                active
                  ? "bg-indigo-600 text-white border border-indigo-500"
                  : "bg-bg-card text-slate-400 border border-border hover:text-slate-200 hover:border-slate-500"
              }`}
            >
              {m.replace(/_/g, " ")}
            </button>
          );
        })}
      </div>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <FileText className="w-4 h-4 text-blue-400" /> Model Card
        </h3>
        {card ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            <div><span className="text-slate-400">Name:</span> <span className="text-white">{card.name}</span></div>
            <div><span className="text-slate-400">Task:</span> <span className="text-white">{card.task}</span></div>
            <div className="md:col-span-2"><span className="text-slate-400">Training data:</span> <span className="text-slate-200">{card.training_data}</span></div>
            <div><span className="text-slate-400">Performance:</span>
              <pre className="text-xs bg-bg-primary rounded p-2 mt-1">{JSON.stringify(card.performance, null, 2)}</pre>
            </div>
            <div><span className="text-slate-400">Known limitations:</span>
              <ul className="list-disc list-inside text-xs text-slate-300 mt-1">
                {(card.known_limitations || []).map((l: string, i: number) => <li key={i}>{l}</li>)}
              </ul>
            </div>
            <div className="md:col-span-2"><span className="text-slate-400">Intended use:</span> <span className="text-white">{card.intended_use}</span></div>
          </div>
        ) : <p className="text-sm text-slate-500">No card available.</p>}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-amber-400" /> Override Stats (drift proxy)
        </h3>
        <table className="w-full text-sm">
          <thead className="text-slate-400">
            <tr>
              <th className="text-left py-1.5">Module</th>
              <th className="text-right py-1.5">Decisions</th>
              <th className="text-right py-1.5">Overrides</th>
              <th className="text-right py-1.5">Override rate</th>
            </tr>
          </thead>
          <tbody>
            {overrides.map((o, i) => (
              <tr key={i} className="border-t border-border">
                <td className="py-1.5">{o.module}</td>
                <td className="py-1.5 text-right font-mono-clinical">{o.total_decisions}</td>
                <td className="py-1.5 text-right font-mono-clinical">{o.overrides}</td>
                <td className={`py-1.5 text-right font-mono-clinical ${o.override_rate > 0.1 ? "text-rose-400" : "text-slate-200"}`}>
                  {(o.override_rate * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
            {overrides.length === 0 && <tr><td colSpan={4} className="py-3 text-center text-slate-500 text-sm">No overrides recorded yet.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3">Recent Decisions ({decisions.length})</h3>
        {loading ? <p className="text-sm text-slate-500">Loading…</p> : decisions.length === 0 ? (
          <p className="text-sm text-slate-500">No decisions logged for this module.</p>
        ) : (
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-sm">
              <thead className="text-slate-400 sticky top-0 bg-bg-card">
                <tr>
                  <th className="text-left py-1.5">Time</th>
                  <th className="text-left py-1.5">Prediction</th>
                  <th className="text-left py-1.5">SHAP method</th>
                  <th className="text-left py-1.5">Top features</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1 font-mono-clinical text-xs text-slate-400">{d.timestamp}</td>
                    <td className="py-1 text-xs">{JSON.stringify(d.prediction)}</td>
                    <td className="py-1 text-xs">{d.shap_method || "—"}</td>
                    <td className="py-1 text-xs">
                      {(d.shap_values || []).slice(0, 3).map((s, j) => (
                        <span key={j} className="inline-block mr-2 text-slate-300">
                          {s.feature}:{s.value.toFixed(2)}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
