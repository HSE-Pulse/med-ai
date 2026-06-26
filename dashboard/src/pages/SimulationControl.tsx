import { useState, useEffect, useRef, useCallback } from "react";
import {
  Play,
  Square,
  RotateCcw,
  Users,
  Clock,
  Zap,
  LogIn,
  LogOut,
  ListOrdered,
  Database,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell as RCell,
} from "recharts";

/* ──────────────── Types ──────────────── */

interface SimState {
  running: boolean;
  sim_time: string;
  speed: number;
  active_patients: number;
  queued_events: number;
  stats: {
    total_admissions: number;
    total_discharges: number;
    total_transfers: number;
    total_vitals: number;
    total_labs: number;
    total_meds: number;
  };
}

interface SimEvent {
  type: string;
  patient_id?: string;
  department?: string;
  timestamp?: string;
  details?: string;
  [key: string]: unknown;
}

interface DeptCensus {
  [dept: string]: number;
}

interface SimStats {
  [collection: string]: number;
}

interface DTConfig {
  pipeline: string[];
  disabled_modules: string[];
  active_patients: number;
  total_processed: number;
}

interface DTModuleHealth {
  [module: string]: boolean;
}

const DT_MODULE_INFO: Record<string, { label: string; port: number }> = {
  ed_triage: { label: "ED Triage", port: 8201 },
  ed_flow: { label: "ED Flow", port: 8214 },
  bed_management: { label: "Bed Management", port: 8208 },
  oncology_ai: { label: "Oncology AI", port: 8204 },
  clinical_scribe: { label: "Clinical Scribe", port: 8210 },
};

/* ──────────────── Helpers ──────────────── */

const SPEED_OPTIONS = [1, 5, 10, 50, 100];

const COLLECTION_TO_TYPE: Record<string, string> = {
  transfers: "transfer",
  chartevents: "vital",
  labevents: "lab",
  prescriptions: "medication",
  diagnoses_icd: "diagnosis",
  procedures_icd: "procedure",
};

/** Map a raw REST event (from /recent-events) into a SimEvent. */
function mapRawEvent(raw: Record<string, unknown>): SimEvent {
  const collection = (raw._collection as string) || "";
  let type = COLLECTION_TO_TYPE[collection] || collection;
  const eventType = raw.eventtype as string | undefined;
  if (collection === "transfers") {
    if (eventType === "admit") type = "admission";
    else if (eventType === "discharge") type = "discharge";
    else type = "transfer";
  }
  const dept = (raw.careunit as string) || (raw.department as string) || undefined;
  let details = "";
  if (collection === "chartevents") details = `item ${raw.itemid} = ${raw.valuenum}`;
  else if (collection === "labevents") details = `item ${raw.itemid} = ${raw.valuenum} ${raw.valueuom || ""}`;
  else if (collection === "prescriptions") details = `${raw.drug || ""} ${raw.action || ""}`.trim();
  else if (collection === "diagnoses_icd") details = `ICD ${raw.icd_code || raw.icd9_code || ""}`;
  else if (collection === "transfers" && eventType) details = eventType;

  return {
    type,
    patient_id: (raw.hadm_id as string) || undefined,
    department: dept,
    timestamp: (raw.sim_time as string) || undefined,
    details: details || undefined,
  };
}

/** Map a WebSocket broadcast event into a SimEvent. */
function mapWsEvent(raw: Record<string, unknown>): SimEvent {
  const event = (raw.event as string) || "unknown";
  const data = (raw.data as Record<string, unknown>) || {};
  return {
    type: event,
    patient_id: (data.hadm_id as string) || undefined,
    department: (data.careunit as string) || (data.department as string) || undefined,
    timestamp: (raw.sim_time as string) || undefined,
    details: (data.drug as string) || (data.eventtype as string) || undefined,
  };
}

const EVENT_COLORS: Record<string, string> = {
  admission: "bg-blue-500/20 text-blue-400 border-blue-500/40",
  transfer: "bg-purple-500/20 text-purple-400 border-purple-500/40",
  vital: "bg-green-500/20 text-green-400 border-green-500/40",
  lab: "bg-orange-500/20 text-orange-400 border-orange-500/40",
  medication: "bg-cyan-500/20 text-cyan-400 border-cyan-500/40",
  discharge: "bg-red-500/20 text-red-400 border-red-500/40",
  diagnosis: "bg-pink-500/20 text-pink-400 border-pink-500/40",
  procedure: "bg-indigo-500/20 text-indigo-400 border-indigo-500/40",
};

const DEPT_COLORS = [
  "#3b82f6", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
  "#ef4444", "#ec4899", "#6366f1", "#14b8a6", "#f97316",
];

function eventColor(type: string): string {
  return EVENT_COLORS[type] || "bg-slate-500/20 text-slate-400 border-slate-500/40";
}

function formatSimTime(t: string | undefined): string {
  if (!t) return "--:--:--";
  try {
    const d = new Date(t);
    return d.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return t;
  }
}

function summariseEvent(ev: SimEvent): string {
  const parts: string[] = [];
  if (ev.department) parts.push(ev.department);
  if (ev.details) parts.push(ev.details);
  if (parts.length === 0) {
    // Fallback: show any extra keys
    const skip = new Set(["type", "patient_id", "timestamp"]);
    for (const [k, v] of Object.entries(ev)) {
      if (!skip.has(k) && v !== undefined && v !== null) {
        parts.push(`${k}: ${v}`);
      }
      if (parts.length >= 3) break;
    }
  }
  return parts.join(" | ") || "";
}

/* ──────────────── Component ──────────────── */

export default function SimulationControl() {
  const [state, setState] = useState<SimState | null>(null);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [deptCensus, setDeptCensus] = useState<DeptCensus>({});
  const [simStats, setSimStats] = useState<SimStats>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  // Digital Twin state
  const [dtConfig, setDtConfig] = useState<DTConfig | null>(null);
  const [dtHealth, setDtHealth] = useState<DTModuleHealth>({});

  const feedRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const running = state?.running ?? false;

  /* ── Fetch helpers ── */

  const fetchState = useCallback(async () => {
    try {
      const r = await fetch("/api/sim/state");
      if (!r.ok) throw new Error(`state ${r.status}`);
      const data: SimState = await r.json();
      setState(data);
      setError(null);
      setLoading(false);
    } catch (e: unknown) {
      setError((e as Error).message);
      setLoading(false);
    }
  }, []);

  const fetchCensus = useCallback(async () => {
    try {
      const r = await fetch("/api/sim/department-census");
      if (r.ok) {
        const d = await r.json();
        setDeptCensus(d.census || d);
      }
    } catch { /* swallow */ }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch("/api/sim/sim-stats");
      if (r.ok) {
        const d = await r.json();
        setSimStats(d.collections || d);
      }
    } catch { /* swallow */ }
  }, []);

  const fetchDTConfig = useCallback(async () => {
    try {
      const r = await fetch("/api/sim/digital-twin/config");
      if (r.ok) {
        const d = await r.json();
        if (d.status === "ok") setDtConfig(d.data);
      }
    } catch { /* swallow */ }
  }, []);

  const fetchDTState = useCallback(async () => {
    try {
      const r = await fetch("/api/sim/digital-twin/state");
      if (r.ok) {
        const d = await r.json();
        if (d.status === "ok" && d.data?.module_health) {
          setDtHealth(d.data.module_health);
        }
      }
    } catch { /* swallow */ }
  }, []);

  const toggleDTModule = async (name: string, enabled: boolean) => {
    const action = enabled ? "disable" : "enable";
    try {
      const r = await fetch(`/api/sim/digital-twin/modules/${name}/${action}`, { method: "POST" });
      if (r.ok) {
        const d = await r.json();
        if (d.status === "ok" && d.data?.config) setDtConfig(d.data.config);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Toggle failed");
    }
  };

  const fetchRecentEvents = useCallback(async () => {
    try {
      const r = await fetch("/api/sim/recent-events?limit=50");
      if (r.ok) {
        const data = await r.json();
        const raw: Record<string, unknown>[] = Array.isArray(data) ? data : (data.events || []);
        setEvents(raw.map(mapRawEvent));
      }
    } catch { /* swallow */ }
  }, []);

  /* ── Initial load ── */

  useEffect(() => {
    fetchState();
    fetchCensus();
    fetchStats();
    fetchRecentEvents();
    fetchDTConfig();
    fetchDTState();
  }, [fetchState, fetchCensus, fetchStats, fetchRecentEvents, fetchDTConfig, fetchDTState]);

  /* ── Polling ── */

  useEffect(() => {
    const interval = running ? 2000 : 5000;
    const id = setInterval(() => {
      fetchState();
      fetchCensus();
      fetchStats();
      fetchDTConfig();
      fetchDTState();
    }, interval);
    return () => clearInterval(id);
  }, [running, fetchState, fetchCensus, fetchStats, fetchDTConfig, fetchDTState]);

  /* ── WebSocket (throttled to avoid render floods) ── */

  const wsBatchRef = useRef<SimEvent[]>([]);

  useEffect(() => {
    if (!running) {
      wsRef.current?.close();
      wsRef.current = null;
      return;
    }

    let alive = true;
    const connect = () => {
      try {
        const ws = new WebSocket(`ws://${window.location.hostname}:8207/ws`);
        wsRef.current = ws;

        ws.onmessage = (e) => {
          try {
            const raw = JSON.parse(e.data);
            wsBatchRef.current.push(mapWsEvent(raw));
          } catch { /* ignore malformed */ }
        };

        ws.onerror = () => { /* handled by onclose */ };
        ws.onclose = () => {
          wsRef.current = null;
          // Reconnect after 3s if still running
          if (alive) setTimeout(connect, 3000);
        };
      } catch { /* ignore */ }
    };

    connect();

    // Flush WS batch to state every 2 seconds (not every message)
    const flushInterval = setInterval(() => {
      const batch = wsBatchRef.current;
      if (batch.length > 0) {
        wsBatchRef.current = [];
        setEvents((prev) => [...prev, ...batch].slice(-100));
      }
    }, 2000);

    return () => {
      alive = false;
      clearInterval(flushInterval);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [running]);

  /* ── Auto-scroll feed (throttled) ── */

  useEffect(() => {
    const t = setTimeout(() => {
      if (feedRef.current) {
        feedRef.current.scrollTop = feedRef.current.scrollHeight;
      }
    }, 100);
    return () => clearTimeout(t);
  }, [events]);

  /* ── Actions ── */

  async function handleStart() {
    try {
      await fetch("/api/sim/start", { method: "POST" });
      await fetchState();
      await fetchRecentEvents();
    } catch (e: unknown) {
      setError((e as Error).message);
    }
  }

  async function handleStop() {
    try {
      await fetch("/api/sim/stop", { method: "POST" });
      await fetchState();
    } catch (e: unknown) {
      setError((e as Error).message);
    }
  }

  async function handleSpeed(s: number) {
    try {
      await fetch("/api/sim/speed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed: s }),
      });
      await fetchState();
    } catch (e: unknown) {
      setError((e as Error).message);
    }
  }

  async function handleReset() {
    if (!confirmReset) {
      setConfirmReset(true);
      setTimeout(() => setConfirmReset(false), 3000);
      return;
    }
    try {
      await fetch("/api/sim/reset", { method: "POST" });
      setEvents([]);
      setDeptCensus({});
      setSimStats({});
      setConfirmReset(false);
      await fetchState();
    } catch (e: unknown) {
      setError((e as Error).message);
    }
  }

  /* ── Census chart data ── */

  const censusData = Object.entries(deptCensus)
    .map(([name, count]) => ({ name, count: count as number }))
    .sort((a, b) => b.count - a.count);

  /* ── Skeleton ── */

  if (loading) {
    return (
      <div className="space-y-4">
        {/* Control bar skeleton */}
        <div className="bg-bg-card border border-border rounded-xl p-4 animate-pulse">
          <div className="h-10 bg-slate-700/40 rounded-lg w-full" />
        </div>
        {/* KPI skeletons */}
        <div className="grid grid-cols-5 gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="bg-bg-card border border-border rounded-xl p-5 animate-pulse">
              <div className="h-4 bg-slate-700/40 rounded w-20 mb-3" />
              <div className="h-8 bg-slate-700/40 rounded w-16" />
            </div>
          ))}
        </div>
        {/* Chart skeleton */}
        <div className="bg-bg-card border border-border rounded-xl p-5 h-64 animate-pulse">
          <div className="h-full bg-slate-700/40 rounded" />
        </div>
      </div>
    );
  }

  /* ── Render ── */

  const stats = state?.stats;

  return (
    <div className="space-y-4">
      {/* Error Banner */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-2 text-sm">
          Connection error: {error}
        </div>
      )}

      {/* ─── Control Bar ─── */}
      <div className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Start / Stop */}
          {running ? (
            <button
              onClick={handleStop}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30 transition-colors font-medium text-sm"
            >
              <Square className="w-4 h-4" />
              Stop
            </button>
          ) : (
            <button
              onClick={handleStart}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-green-500/20 text-green-400 border border-green-500/40 hover:bg-green-500/30 transition-colors font-medium text-sm"
            >
              <Play className="w-4 h-4" />
              Start
            </button>
          )}

          {/* Speed selector */}
          <div className="flex items-center gap-1 bg-slate-800/50 rounded-lg p-1">
            {SPEED_OPTIONS.map((s) => (
              <button
                key={s}
                onClick={() => handleSpeed(s)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  (state?.speed ?? 1) === s
                    ? "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
                }`}
              >
                {s}x
              </button>
            ))}
          </div>

          {/* Reset */}
          <button
            onClick={handleReset}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium transition-colors ${
              confirmReset
                ? "bg-red-500/20 text-red-400 border-red-500/40 hover:bg-red-500/30"
                : "bg-slate-700/30 text-slate-400 border-slate-600/40 hover:bg-slate-700/50"
            }`}
          >
            <RotateCcw className="w-4 h-4" />
            {confirmReset ? "Confirm Reset" : "Reset"}
          </button>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Running indicator */}
          <div className="flex items-center gap-2 text-sm">
            {running ? (
              <>
                <div className="w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse" />
                <span className="text-green-400 font-medium">Running</span>
              </>
            ) : (
              <>
                <div className="w-2.5 h-2.5 rounded-full bg-slate-500" />
                <span className="text-slate-400">Stopped</span>
              </>
            )}
          </div>

          {/* Sim clock */}
          <div className="flex items-center gap-2 bg-slate-800/50 rounded-lg px-3 py-2">
            <Clock className="w-4 h-4 text-slate-400" />
            <span className="font-mono-clinical text-sm text-slate-200">
              {formatSimTime(state?.sim_time)}
            </span>
          </div>
        </div>
      </div>

      {/* ─── KPI Cards ─── */}
      {/* Sim Speed lives in the control row above (1× / 5× / 10× / …);
          duplicating it here was confusing, so we render four KPIs. */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KPICard
          icon={LogIn}
          label="Total Admissions"
          value={stats?.total_admissions ?? 0}
          color="text-blue-400"
          bgColor="bg-blue-500/10"
        />
        <KPICard
          icon={Users}
          label="Active Patients"
          value={state?.active_patients ?? 0}
          color="text-emerald-400"
          bgColor="bg-emerald-500/10"
        />
        <KPICard
          icon={LogOut}
          label="Total Discharges"
          value={stats?.total_discharges ?? 0}
          color="text-amber-400"
          bgColor="bg-amber-500/10"
        />
        <KPICard
          icon={ListOrdered}
          label="Queued Events"
          value={state?.queued_events ?? 0}
          color="text-purple-400"
          bgColor="bg-purple-500/10"
        />
      </div>

      {/* ─── Department Census ─── */}
      <div className="bg-bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
          <Zap className="w-4 h-4 text-blue-400" />
          Live Department Census
        </h3>
        {censusData.length === 0 ? (
          <p className="text-slate-500 text-sm italic">No department data yet. Start the simulation.</p>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(180, censusData.length * 38)}>
            <BarChart data={censusData} layout="vertical" margin={{ left: 120, right: 20, top: 5, bottom: 5 }}>
              <XAxis type="number" tick={{ fill: "#94a3b8", fontSize: 12 }} />
              <YAxis
                dataKey="name"
                type="category"
                tick={{ fill: "#cbd5e1", fontSize: 12 }}
                width={110}
              />
              <Tooltip
                contentStyle={{
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 8,
                  fontSize: 13,
                }}
                labelStyle={{ color: "#f1f5f9" }}
              />
              <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={28}>
                {censusData.map((_, idx) => (
                  <RCell key={idx} fill={DEPT_COLORS[idx % DEPT_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ─── Digital Twin Pipeline ─── */}
      <div className="grid grid-cols-5 gap-4">
        <div className="col-span-3 bg-bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Zap className="w-4 h-4 text-blue-400" />
            Digital Twin Pipeline
          </h3>
          <div className="space-y-1.5">
            {(dtConfig?.pipeline ?? Object.keys(DT_MODULE_INFO)).map((mod) => {
              const info = DT_MODULE_INFO[mod];
              if (!info) return null;
              const healthy = dtHealth[mod] !== false;
              const enabled = !dtConfig?.disabled_modules?.includes(mod);
              return (
                <div
                  key={mod}
                  className={`flex items-center justify-between px-3 py-2.5 rounded-lg border transition-colors ${
                    enabled
                      ? "bg-slate-800/40 border-slate-700/50"
                      : "bg-slate-900/40 border-slate-800/30 opacity-60"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-2.5 h-2.5 rounded-full ${
                      !enabled ? "bg-slate-600" : healthy ? "bg-green-500" : "bg-red-500"
                    }`} />
                    <span className="text-sm text-slate-200 font-medium">{info.label}</span>
                    <span className="text-[10px] text-slate-500 font-mono-clinical">:{info.port}</span>
                  </div>
                  <button
                    onClick={() => toggleDTModule(mod, enabled)}
                    className={`relative w-10 h-5 rounded-full transition-colors ${
                      enabled ? "bg-blue-600" : "bg-slate-700"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                        enabled ? "left-5.5 translate-x-0" : "left-0.5"
                      }`}
                      style={{ left: enabled ? 22 : 2 }}
                    />
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        <div className="col-span-2 bg-bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Database className="w-4 h-4 text-blue-400" />
            Pipeline Stats
          </h3>
          <div className="space-y-0">
            <StatsRow label="Active Patients" value={dtConfig?.active_patients ?? 0} />
            <StatsRow label="Total Processed" value={dtConfig?.total_processed ?? 0} />
            <StatsRow
              label="Modules Active"
              value={
                (dtConfig?.pipeline?.length ?? 5) -
                (dtConfig?.disabled_modules?.length ?? 0)
              }
            />
          </div>
          <div className="mt-4 pt-3 border-t border-border">
            <div className="text-[10px] text-slate-500 uppercase tracking-wide mb-2">
              Pipeline Order
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(dtConfig?.pipeline ?? []).map((mod, i) => {
                const disabled = dtConfig?.disabled_modules?.includes(mod);
                return (
                  <span
                    key={mod}
                    className={`text-[10px] px-2 py-1 rounded-full border font-medium ${
                      disabled
                        ? "bg-slate-800/30 text-slate-600 border-slate-700/30 line-through"
                        : "bg-blue-500/10 text-blue-400 border-blue-500/30"
                    }`}
                  >
                    {i + 1}. {DT_MODULE_INFO[mod]?.label ?? mod}
                  </span>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* ─── Row 3: Event Feed + Stats ─── */}
      <div className="grid grid-cols-5 gap-4">
        {/* Event Feed (60%) */}
        <div className="col-span-3 bg-bg-card border border-border rounded-xl p-5 flex flex-col">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Zap className="w-4 h-4 text-green-400" />
            Live Event Feed
          </h3>
          <div
            ref={feedRef}
            className="flex-1 min-h-[300px] max-h-[420px] overflow-y-auto space-y-1 pr-1 scrollbar-thin"
          >
            {events.length === 0 ? (
              <p className="text-slate-500 text-sm italic mt-4">No events yet. Start the simulation to see live events.</p>
            ) : (
              events.map((ev, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 py-1.5 px-2 rounded hover:bg-slate-800/40 transition-colors animate-fade-in"
                  style={{ fontFamily: "var(--font-mono, monospace)" }}
                >
                  <span className="text-[11px] text-slate-500 shrink-0 w-36">
                    {ev.timestamp ? formatSimTime(ev.timestamp) : ""}
                  </span>
                  <span
                    className={`text-[11px] px-1.5 py-0.5 rounded border shrink-0 uppercase font-semibold tracking-wide ${eventColor(
                      ev.type
                    )}`}
                  >
                    {ev.type}
                  </span>
                  <span className="text-[11px] text-slate-300 shrink-0 w-20 truncate">
                    {ev.patient_id ?? ""}
                  </span>
                  <span className="text-[11px] text-slate-400 truncate">
                    {summariseEvent(ev)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Collection Stats (40%) */}
        <div className="col-span-2 bg-bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Database className="w-4 h-4 text-purple-400" />
            Collection Stats
          </h3>

          {/* Primary stats from /state */}
          <div className="space-y-0">
            <StatsRow label="Admissions" value={stats?.total_admissions ?? 0} />
            <StatsRow label="Discharges" value={stats?.total_discharges ?? 0} />
            <StatsRow label="Transfers" value={stats?.total_transfers ?? 0} />
            <StatsRow label="Vitals (chartevents)" value={stats?.total_vitals ?? 0} />
            <StatsRow label="Lab Events" value={stats?.total_labs ?? 0} />
            <StatsRow label="Prescriptions" value={stats?.total_meds ?? 0} />
          </div>

          {/* Extra stats from /sim-stats */}
          {Object.keys(simStats).length > 0 && (
            <>
              <div className="my-3 border-t border-border" />
              <h4 className="text-xs text-slate-500 uppercase tracking-wide mb-2">
                DB Collections
              </h4>
              <div className="space-y-0">
                {Object.entries(simStats).map(([key, val]) => (
                  <StatsRow key={key} label={key} value={val as number} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ──────────────── Sub-components ──────────────── */

function KPICard({
  icon: Icon,
  label,
  value,
  color,
  bgColor,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number | string;
  color: string;
  bgColor: string;
}) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-8 h-8 rounded-lg ${bgColor} flex items-center justify-center`}>
          <Icon className={`w-4 h-4 ${color}`} />
        </div>
        <span className="text-xs text-slate-400 font-medium">{label}</span>
      </div>
      <div className={`text-2xl font-bold font-mono-clinical ${color}`}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </div>
  );
}

function StatsRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between py-2 px-1 border-b border-border/50 last:border-b-0">
      <span className="text-sm text-slate-400">{label}</span>
      <span className="text-sm font-mono-clinical text-slate-200 font-semibold">
        {value.toLocaleString()}
      </span>
    </div>
  );
}
