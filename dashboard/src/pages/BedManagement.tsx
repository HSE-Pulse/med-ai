import { useState, useEffect, useCallback } from "react";
import {
  BedDouble, AlertTriangle, TrendingUp, Activity,
  RefreshCw, Loader2, Send,
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, LineChart, Line, Cell,
} from "recharts";
import StatCard from "../components/StatCard";
import { CHART_GRID_PROPS, CHART_TOOLTIP_STYLE } from "../lib/chartConfig";
import { BedPriorityEscalationsPanel } from "../components/UpliftWidgets";

/* ---------- types ---------- */
interface DeptSummary {
  department: string; department_type: string; capacity: number;
  occupied: number; available: number; blocked: number;
  occupancy_rate: number; alert_level: string;
}
interface ForecastPoint {
  horizon_hours: number; predicted_census: number;
  lower_bound_90: number; upper_bound_90: number;
}
interface DischargePred {
  discharge_probability_24h: number; discharge_probability_48h: number;
  discharge_readiness_score: number; predicted_discharge_time: string;
  current_los_hours: number; key_factors: string[];
  barriers_to_discharge: string[]; model_used: string;
}

const ALERT_BG: Record<string, string> = {
  green: "bg-green-500/15 border-green-500/30 text-green-400",
  amber: "bg-yellow-500/15 border-yellow-500/30 text-yellow-400",
  red:   "bg-red-500/15 border-red-500/30 text-red-400",
  black: "bg-slate-500/15 border-slate-500/30 text-slate-400",
};

export default function BedManagement() {
  const [depts, setDepts] = useState<DeptSummary[]>([]);
  const [selDept, setSelDept] = useState("Medicine");
  const [forecast, setForecast] = useState<ForecastPoint[]>([]);
  const [pred, setPred] = useState<DischargePred | null>(null);
  const [loading, setLoading] = useState(true);
  const [predLoading, setPredLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [age, setAge] = useState(65);
  const [gender, setGender] = useState("M");
  const [dept, setDept] = useState("Medicine");
  const [hasIV, setHasIV] = useState(false);
  const [hasO2, setHasO2] = useState(false);
  const [procs, setProcs] = useState(0);
  // Track when the bed-summary feed was last refreshed so the operator
  // can compare side-by-side counts on `/hospital-ops` and reason about
  // any discrepancies (closes F18).
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/beds/beds/summary");
      const d = await r.json();
      if (d.status === "ok") {
        setDepts(d.data || []);
        setLastUpdated(new Date());
      }
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setLoading(false);
  }, []);

  const loadForecast = useCallback(async (d: string) => {
    try {
      const r = await fetch(`/api/beds/forecast/${d}`);
      const j = await r.json();
      if (j.status === "ok") setForecast(j.data?.forecasts || []);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (selDept) loadForecast(selDept); }, [selDept, loadForecast]);

  const predict = async () => {
    setPredLoading(true);
    try {
      const r = await fetch("/api/beds/predict-discharge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patient_id: Math.floor(Math.random() * 90000) + 10000, hadm_id: Math.floor(Math.random() * 90000) + 10000, department: dept,
          admission_time: new Date(Date.now() - 48 * 3600000).toISOString(),
          age, gender, has_iv: hasIV, has_oxygen: hasO2, procedures_pending: procs,
        }),
      });
      const d = await r.json();
      if (d.status === "ok") setPred(d.data);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setPredLoading(false);
  };

  const totalBeds = depts.reduce((s, d) => s + d.capacity, 0);
  const totalOcc  = depts.reduce((s, d) => s + d.occupied, 0);
  const totalFree = depts.reduce((s, d) => s + d.available, 0);
  const avgOcc    = totalBeds > 0 ? (totalOcc / totalBeds * 100).toFixed(1) : "0";
  const redCount  = depts.filter(d => d.alert_level === "red" || d.alert_level === "black").length;

  const occData = depts.map(d => ({
    name: d.department, pct: Math.round(d.occupancy_rate * 100),
    available: d.available, capacity: d.capacity,
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
      <div className="grid grid-cols-4 gap-4">
        <StatCard icon={<BedDouble className="w-5 h-5 text-blue-400" />} label="Total Beds"
          value={totalBeds} accentColor="#3B82F6" />
        <StatCard icon={<Activity className="w-5 h-5 text-purple-400" />} label="Occupied"
          value={`${totalOcc}`} subtitle={`${avgOcc}% occupancy`} accentColor="#8B5CF6" />
        <StatCard icon={<TrendingUp className="w-5 h-5 text-green-400" />} label="Available"
          value={totalFree} accentColor="#22C55E" />
        <StatCard icon={<AlertTriangle className="w-5 h-5 text-red-400" />} label="At Risk"
          value={redCount} subtitle="red/black depts" accentColor="#DC2626" />
      </div>
      {lastUpdated && (
        <div className="text-[11px] text-text-muted -mt-2">
          Counts as of {lastUpdated.toLocaleTimeString()} — Hospital Ops polls the same feed but on a
          different cadence, so brief skew of ±1–2 patients per department is expected.
        </div>
      )}

      {/* Main content: Occupancy + Alert grid */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Occupancy chart */}
        <div className="lg:col-span-3 bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-text-primary">Department Occupancy</h2>
            <button onClick={load} className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-blue-600/15 text-blue-400 border border-blue-600/30 hover:bg-blue-600/30 transition-colors">
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>
          <div style={{ height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={occData} layout="vertical" margin={{ top: 4, right: 20, left: 4, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID_PROPS} />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 10, fill: "#94a3b8" }}
                  tickFormatter={(v: number) => `${v}%`} />
                <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE}
                  formatter={(v: number) => [`${v}%`, "Occupancy"]} />
                <Bar dataKey="pct" radius={[0, 4, 4, 0]}>
                  {occData.map((d, i) => (
                    <Cell key={i} fill={d.pct > 90 ? "#DC2626" : d.pct > 75 ? "#F59E0B" : "#22C55E"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Alert grid */}
        <div className="lg:col-span-2 bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Department Status</h2>
          <div className="space-y-1.5 max-h-[340px] overflow-y-auto">
            {depts.map(d => (
              <div key={d.department}
                className={`flex items-center justify-between px-3 py-2 rounded-lg border ${ALERT_BG[d.alert_level] || ALERT_BG.green}`}>
                <span className="text-[11px] font-medium">{d.department}</span>
                <span className="text-[10px] font-mono-clinical">{d.occupied}/{d.capacity}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Forecast + Discharge Prediction */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Capacity Forecast */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-text-primary">Capacity Forecast</h2>
            <select value={selDept} onChange={e => setSelDept(e.target.value)}
              className="bg-bg-input border border-border rounded px-2 py-1 text-[11px] text-text-primary focus:border-blue-500 focus:outline-none">
              {depts.map(d => <option key={d.department} value={d.department}>{d.department}</option>)}
            </select>
          </div>
          <div style={{ height: 220 }}>
            {forecast.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={forecast} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID_PROPS} />
                  <XAxis dataKey="horizon_hours" tick={{ fontSize: 10, fill: "#94a3b8" }} tickFormatter={(v: number) => `+${v}h`} />
                  <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                  <Line type="monotone" dataKey="predicted_census" stroke="#3B82F6" strokeWidth={2} dot={{ r: 3 }} />
                  <Line type="monotone" dataKey="upper_bound_90" stroke="#EF4444" strokeDasharray="5 5" dot={false} strokeWidth={1} />
                  <Line type="monotone" dataKey="lower_bound_90" stroke="#22C55E" strokeDasharray="5 5" dot={false} strokeWidth={1} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-text-muted text-[11px]">Select department</div>
            )}
          </div>
          <div className="flex gap-4 mt-2 justify-center">
            {[{ l: "Predicted", c: "#3B82F6" }, { l: "Upper 90%", c: "#EF4444" }, { l: "Lower 90%", c: "#22C55E" }].map(x => (
              <span key={x.l} className="flex items-center gap-1 text-[9px] text-text-muted">
                <span className="w-3 h-[2px] rounded" style={{ backgroundColor: x.c }} /> {x.l}
              </span>
            ))}
          </div>
        </div>

        {/* Discharge Prediction */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Discharge Prediction</h2>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Age</label>
              <input type="number" value={age} onChange={e => setAge(+e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical focus:border-blue-500 focus:outline-none transition-colors" />
            </div>
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Gender</label>
              <select value={gender} onChange={e => setGender(e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary focus:border-blue-500 focus:outline-none">
                <option value="M">Male</option><option value="F">Female</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Department</label>
              <select value={dept} onChange={e => setDept(e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary focus:border-blue-500 focus:outline-none">
                {depts.map(d => <option key={d.department} value={d.department}>{d.department}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Pending Procs</label>
              <input type="number" value={procs} onChange={e => setProcs(+e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical focus:border-blue-500 focus:outline-none transition-colors" />
            </div>
            <label className="flex items-center gap-2 text-[11px] text-text-secondary">
              <input type="checkbox" checked={hasIV} onChange={e => setHasIV(e.target.checked)}
                className="accent-blue-500" /> IV Active
            </label>
            <label className="flex items-center gap-2 text-[11px] text-text-secondary">
              <input type="checkbox" checked={hasO2} onChange={e => setHasO2(e.target.checked)}
                className="accent-blue-500" /> Oxygen
            </label>
          </div>
          <button onClick={predict} disabled={predLoading}
            className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-medium py-2.5 rounded-lg transition-colors text-sm">
            {predLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {predLoading ? "Predicting..." : "Predict Discharge"}
          </button>

          {pred && (() => {
            // The backend `key_factors` list sometimes contains an extra
            // "ML discharge probability (24h): X%" line whose value differs
            // from `discharge_probability_24h` (rules+ML composite vs raw
            // model). Showing both confuses the operator — strip duplicates
            // here and surface the raw ML score in its own labelled field.
            const rawMlMatch = pred.key_factors
              .map((f) => f.match(/ML discharge probability\s*\(24h\)[^\d]*([\d.]+)/i))
              .find(Boolean) as RegExpMatchArray | undefined;
            const rawMlPct = rawMlMatch ? parseFloat(rawMlMatch[1]) : null;
            const otherFactors = pred.key_factors.filter(
              (f) => !/ML discharge probability/i.test(f),
            );
            const compositePct = pred.discharge_probability_24h * 100;
            return (
            <div className="mt-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-text-muted">Composite 24h discharge probability</span>
                <span className="font-mono-clinical text-xl font-bold text-blue-400">
                  {compositePct.toFixed(1)}%
                </span>
              </div>
              <div className="w-full h-2 bg-progress-bg rounded-full overflow-hidden">
                <div className="h-full rounded-full bg-blue-500 transition-all duration-500"
                  style={{ width: `${compositePct}%` }} />
              </div>
              {rawMlPct !== null && Math.abs(rawMlPct - compositePct) > 0.5 && (
                <div className="flex items-center justify-between text-[11px] text-text-muted">
                  <span>Raw ML model output</span>
                  <span className="font-mono-clinical">{rawMlPct.toFixed(1)}%</span>
                </div>
              )}
              <div className="bg-bg-primary rounded-lg p-2 space-y-1">
                {otherFactors.map((f, i) => (
                  <div key={i} className="text-[11px] text-text-secondary">- {f}</div>
                ))}
              </div>
              {pred.barriers_to_discharge.length > 0 && (
                <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-2">
                  <div className="text-[10px] font-semibold text-red-400 mb-1">Barriers</div>
                  {pred.barriers_to_discharge.map((b, i) => (
                    <div key={i} className="text-[10px] text-red-400/80">- {b}</div>
                  ))}
                </div>
              )}
              <div className="text-[10px] text-text-muted">
                Model: <span className="font-mono-clinical">{pred.model_used}</span>
              </div>
            </div>
            );
          })()}
        </div>
      </div>

      {/* Integration 5 — bed-priority escalations pushed by Sepsis ICU / NEWS2 */}
      <BedPriorityEscalationsPanel />
    </div>
  );
}

