import { useState, useEffect, useCallback } from "react";
import {
  Users, Clock, AlertTriangle, Zap, Shield, Play, Loader2, RefreshCw,
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, LineChart, Line, Cell,
} from "recharts";
import StatCard from "../components/StatCard";
import { CHART_GRID_PROPS, CHART_TOOLTIP_STYLE } from "../lib/chartConfig";

/* ---------- types ---------- */
interface EDState {
  total_patients: number; waiting_count: number;
  in_treatment_count: number; boarding_count: number;
  nedocs_score: number; crowding_level: string;
  pet_compliance_rate: number; patients_at_pet_risk: number;
  avg_wait_minutes: number; longest_wait_minutes: number;
  patients_by_mts: Record<string, number>;
}
interface Forecast {
  horizon_hours: number;
  predicted_arrivals: number;
  predicted_arrivals_lower: number;
  predicted_arrivals_upper: number;
  source?: "observed" | "no_data";
  observed_arrival_rate_per_hour?: number | null;
}
interface Bottleneck { bottleneck_type: string; severity: string; affected_patients: number; recommended_action: string; }
interface Recommendation { priority: string; title: string; description: string; }
interface WhatIf { scenario_type: string; baseline_avg_los: number; simulated_avg_los: number; los_reduction_minutes: number; baseline_pet_compliance: number; simulated_pet_compliance: number; summary: string; }

import { MTS_COLORS, CROWDING_COLORS as CROWD_COLORS } from "../lib/colors";

export default function EDFlowOptimizer() {
  const [st, setSt] = useState<EDState | null>(null);
  const [fc, setFc] = useState<Forecast[]>([]);
  const [bn, setBn] = useState<Bottleneck[]>([]);
  const [rec, setRec] = useState<Recommendation[]>([]);
  const [wi, setWi] = useState<WhatIf | null>(null);
  const [scenario, setScenario] = useState("add_doctor");
  const [sVal, setSVal] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, f, b, r] = await Promise.all([
        fetch("/api/ed-flow/ed-state").then(r => r.json()),
        fetch("/api/ed-flow/forecast/arrivals").then(r => r.json()),
        fetch("/api/ed-flow/ed-state/bottlenecks").then(r => r.json()),
        fetch("/api/ed-flow/recommendations").then(r => r.json()),
      ]);
      if (s.status === "ok") setSt(s.data);
      if (f.status === "ok") setFc(f.data || []);
      if (b.status === "ok") setBn(b.data || []);
      if (r.status === "ok") setRec(r.data || []);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 10_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const runWI = async () => {
    try {
      const r = await fetch("/api/ed-flow/simulate/what-if", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario_type: scenario, parameter_value: sVal, simulation_hours: 8 }),
      });
      const d = await r.json();
      if (d.status === "ok") setWi(d.data);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
  };

  // Always render all five MTS tiers so the chart layout doesn't flicker
  // (categories popping in/out as sim counts cross zero was reported as
  // F24). Zero-count tiers render as empty bars at the same x-position.
  const MTS_ORDER = ["Immediate", "Very Urgent", "Urgent", "Standard", "Non-Urgent"] as const;
  const mtsData = MTS_ORDER.map((name) => ({
    name,
    count: st?.patients_by_mts?.[name] ?? 0,
    fill: MTS_COLORS[name] || "#6B7280",
  }));

  if (loading) return (
    <div className="space-y-4">
      {[1,2,3].map(i => <div key={i} className="skeleton h-24 w-full rounded-xl" />)}
    </div>
  );

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-2 rounded-lg text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300 ml-4">&times;</button>
        </div>
      )}
      {/* KPI row */}
      <div className="grid grid-cols-5 gap-4">
        <StatCard icon={<Users className="w-5 h-5 text-blue-400" />}
          label="ED Census" value={st?.total_patients || 0} accentColor="#3B82F6" />
        <StatCard icon={<Clock className="w-5 h-5 text-yellow-400" />}
          label="Waiting" value={st?.waiting_count || 0}
          subtitle={`avg ${st?.avg_wait_minutes?.toFixed(0) || 0} min`} accentColor="#EAB308" />
        <StatCard icon={<AlertTriangle className="w-5 h-5 text-red-400" />}
          label="Boarding" value={st?.boarding_count || 0} accentColor="#DC2626" />
        <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
            <Zap className="w-5 h-5 text-purple-400" />
          </div>
          <div>
            <div className="text-[10px] text-slate-400 uppercase">NEDOCS</div>
            <div className="font-mono-clinical text-xl font-bold text-text-primary">{st?.nedocs_score?.toFixed(0) || 0}</div>
            <div className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: CROWD_COLORS[st?.crowding_level || "normal"] }} />
              <span className="text-[10px] text-text-muted capitalize">{st?.crowding_level || "normal"}</span>
            </div>
          </div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-green-500/10 flex items-center justify-center">
            <Shield className="w-5 h-5 text-green-400" />
          </div>
          <div>
            <div className="text-[10px] text-slate-400 uppercase">PET (6h)</div>
            <div className="font-mono-clinical text-xl font-bold text-text-primary">
              {((st?.pet_compliance_rate || 0) * 100).toFixed(0)}%
            </div>
            <div className="text-[10px] text-text-muted">{st?.patients_at_pet_risk || 0} at risk</div>
          </div>
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* MTS Distribution */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Patients by MTS Category</h2>
          <div style={{ height: 220 }}>
            {mtsData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={mtsData} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID_PROPS} />
                  <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#94a3b8" }} />
                  <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                    {mtsData.map((d, i) => <Cell key={i} fill={d.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="flex items-center justify-center h-full text-text-muted text-[11px]">No patients tracked</div>}
          </div>
        </div>

        {/* Arrival Forecast */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-text-primary">Arrival Forecast</h2>
              {fc.length > 0 && fc[0].source === "no_data" && (
                <span
                  className="text-[9px] px-1.5 py-0.5 rounded bg-slate-500/20 text-slate-400 border border-slate-500/30"
                  title="No live ED patients — forecast defaults to zero. Start the sim for a real projection."
                >
                  no live data
                </span>
              )}
              {fc.length > 0 && fc[0].source === "observed" && fc[0].observed_arrival_rate_per_hour != null && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                  observed {fc[0].observed_arrival_rate_per_hour.toFixed(1)}/h
                </span>
              )}
            </div>
            <button onClick={fetchAll} className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-blue-600/15 text-blue-400 border border-blue-600/30 hover:bg-blue-600/30 transition-colors">
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>
          <div style={{ height: 220 }}>
            {fc.length === 0 ? (
              <div className="flex items-center justify-center h-full text-text-muted text-[11px]">No forecast data</div>
            ) : fc[0].source === "no_data" ? (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <p className="text-[11px] text-text-muted font-medium">No live ED patients to project from</p>
                <p className="text-[10px] text-text-muted mt-1">Start the simulation to see a real arrival forecast.</p>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={fc} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID_PROPS} />
                  <XAxis dataKey="horizon_hours" tick={{ fontSize: 10, fill: "#94a3b8" }} tickFormatter={(v: number) => `+${v}h`} />
                  <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Line type="monotone" dataKey="predicted_arrivals" stroke="#3B82F6" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="predicted_arrivals_upper" stroke="#EF4444" strokeDasharray="5 5" dot={false} strokeWidth={1} />
                  <Line type="monotone" dataKey="predicted_arrivals_lower" stroke="#22C55E" strokeDasharray="5 5" dot={false} strokeWidth={1} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* Bottlenecks + Recs + What-If */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Bottlenecks */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Active Bottlenecks</h2>
          {bn.length > 0 ? (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {bn.map((b, i) => (
                <div key={i} className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-semibold text-red-400 capitalize">{b.bottleneck_type}</span>
                    <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${
                      b.severity === "severe" ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400"
                    }`}>{b.severity}</span>
                  </div>
                  <div className="text-[10px] text-text-muted mt-1">{b.affected_patients} patients affected</div>
                  <div className="text-[10px] text-text-secondary mt-1">{b.recommended_action}</div>
                </div>
              ))}
            </div>
          ) : <div className="text-text-muted text-[11px] py-8 text-center">No bottlenecks detected</div>}
        </div>

        {/* Recommendations */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Recommendations</h2>
          {rec.length > 0 ? (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {rec.map((r, i) => (
                <div key={i} className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${
                      r.priority === "critical" ? "bg-red-500/20 text-red-400" : "bg-blue-500/20 text-blue-400"
                    }`}>{r.priority}</span>
                    <span className="text-[11px] font-medium text-text-primary">{r.title}</span>
                  </div>
                  <div className="text-[10px] text-text-secondary">{r.description}</div>
                </div>
              ))}
            </div>
          ) : <div className="text-text-muted text-[11px] py-8 text-center">No recommendations</div>}
        </div>

        {/* What-If */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">What-If Analysis</h2>
          <div className="space-y-2">
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Scenario</label>
              <select value={scenario} onChange={e => setScenario(e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary focus:border-blue-500 focus:outline-none">
                <option value="add_doctor">Add Doctor</option>
                <option value="add_beds">Add Beds</option>
                <option value="open_overflow">Open Overflow</option>
                <option value="reduce_lab_tat">Reduce Lab TAT</option>
                <option value="divert_ambulances">Divert Ambulances</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Count</label>
              <input type="number" value={sVal} onChange={e => setSVal(+e.target.value)} min={1} max={10}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical focus:border-blue-500 focus:outline-none transition-colors" />
            </div>
            <button onClick={runWI}
              className="w-full flex items-center justify-center gap-2 bg-purple-600 hover:bg-purple-500 text-white font-medium py-2 rounded-lg transition-colors text-sm">
              <Play className="w-4 h-4" /> Simulate
            </button>
            {wi && (() => {
              const losDelta = wi.simulated_avg_los - wi.baseline_avg_los; // negative = improvement
              const petDeltaPP = (wi.simulated_pet_compliance - wi.baseline_pet_compliance) * 100;
              const fmtSigned = (n: number, unit: string) =>
                `${n > 0 ? "+" : ""}${n.toFixed(1)}${unit}`;
              // Sanitise the backend summary so phrases like "Adding 1 add
              // doctor" (scenario name leaking into the sentence) read
              // grammatically. Keeps the rest of the text untouched.
              const cleanSummary = (wi.summary || "")
                .replace(/\bAdding\s+(\d+)\s+add\s+doctor/gi, (_m, n) => `Adding ${n} doctor${n === "1" ? "" : "s"}`)
                .replace(/\bAdding\s+(\d+)\s+add\s+(\w+)/gi, (_m, n, w) => `Adding ${n} ${w}`)
                .replace(/(reduce|improve|increase|decrease)[^.]*by\s+([\d.]+)\s*%/gi, (m) => m);
              return (
                <div className="bg-purple-500/10 border border-purple-500/20 rounded-lg p-3 mt-2">
                  <div className="text-[11px] font-semibold text-purple-400 mb-2">Simulation Result</div>
                  <div className="space-y-1 text-[11px]">
                    <div className="flex justify-between text-text-secondary">
                      <span>LOS</span>
                      <span className="font-mono-clinical">
                        {wi.baseline_avg_los.toFixed(1)} → {wi.simulated_avg_los.toFixed(1)} min
                        <span className={`ml-2 ${losDelta < 0 ? "text-green-400" : "text-amber-400"}`}>
                          ({fmtSigned(losDelta, "m")})
                        </span>
                      </span>
                    </div>
                    <div className="flex justify-between text-text-secondary">
                      <span>PET</span>
                      <span className="font-mono-clinical">
                        {(wi.baseline_pet_compliance * 100).toFixed(1)}% → {(wi.simulated_pet_compliance * 100).toFixed(1)}%
                        <span className={`ml-2 ${petDeltaPP > 0 ? "text-green-400" : petDeltaPP < 0 ? "text-amber-400" : "text-text-muted"}`}>
                          ({fmtSigned(petDeltaPP, "pp")})
                        </span>
                      </span>
                    </div>
                    <div className="text-[10px] text-text-muted mt-1">{cleanSummary}</div>
                  </div>
                </div>
              );
            })()}
          </div>
        </div>
      </div>
    </div>
  );
}

