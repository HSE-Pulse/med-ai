import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Stethoscope,
  HeartPulse,
  Activity,
  AlertTriangle,
  Bed,
  DoorOpen,
  Radio,
  ArrowRight,
  TrendingUp,
  TrendingDown,
  Minus,
  Shield,
  Brain,
  Clock,
  Search,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  Area,
  ComposedChart,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
} from "recharts";
import { usePoll } from "../hooks/usePoll";
import { useAlerts, type AlertSeverity } from "../context/AlertsContext";

// Most apps wrap responses in shared BaseResponse: {status, data, error}
interface Envelope<T> {
  status: string;
  data: T;
  error: string | null;
}
const unwrap = <T,>(r: Envelope<T> | null): T | null => (r?.data ?? null);

// ──────────────────────────────────────────────────────────────────────
// Shared types matching the real backend shapes (validated via curl probes)
// ──────────────────────────────────────────────────────────────────────

interface SimStats {
  total_active: number;
  total_discharged: number;
  icu_count: number;
  ed_count: number;
  critical_count: number;
  avg_los_hours: number;
  department_distribution?: Record<string, number>;
  recent_admissions?: Array<{ subject_id: number; department: string }>;
  sim_time?: string;
}

interface EdBoardPatient {
  id: string;
  subject_id: number;
  hadm_id: string;
  department: string;
  acuity: 1 | 2 | 3 | 4 | 5;
  wait_minutes: number;
  status: string;
}
interface EdBoard {
  count: number;
  patients: EdBoardPatient[];
}

interface IcuBoardPatient {
  hadm_id: string;
  subject_id: number;
  department: string;
  sofa_total: number;
  risk_level: string;
}
interface IcuBoard {
  count: number;
  patients: IcuBoardPatient[];
}

interface DeteriorationStats {
  active_patients: number;
  high_band: number;
  medium_band: number;
  mean_score: number;
  by_department?: Record<string, number>;
  unacknowledged_escalations: number;
}

interface DeteriorationAlert {
  hadm_id: string;
  subject_id: number;
  department: string;
  observed_at: string;
  score: {
    total: number;
    risk_band: "high" | "medium" | "low";
    recommended_response?: string;
  };
  scoring_system: string;
  trend: {
    delta: number;
    trajectory: "rising" | "falling" | "stable";
    is_clinically_rising: boolean;
  };
}

interface TrolleyCount {
  ed: number;
  corridor: number;
  ward: number;
  total: number;
}

interface BedSummaryRow {
  department: string;
  capacity: number;
  occupied: number;
  occupancy_rate: number;
  alert_level: "green" | "amber" | "red" | "black";
  predicted_discharges_24h: number;
  predicted_admissions_24h: number;
}

interface DischargeBoardRow {
  bed_id: string;
  department: string;
  patient_id: number;
  predicted_discharge?: string;
  discharge_readiness: number;
}

interface EdState {
  total_patients: number;
  waiting_count: number;
  in_treatment_count: number;
  boarding_count: number;
  resus_occupied: number;
  resus_capacity: number;
  patients_by_mts?: Record<string, number>;
  avg_wait_minutes: number;
  longest_wait_minutes: number;
}

interface CrowdingPoint {
  hour: number;
  time: string;
  predicted_nedocs: number;
  predicted_census?: number;
  crowding_level: "normal" | "busy" | "overcrowded" | "severe" | "critical";
  source?: "observed" | "no_data";
}

interface DischargeLoungeStatus {
  capacity: number;
  occupied: number;
  available: number;
  patients: Array<{ hadm_id: string }>;
}

interface OverrideStatsRow {
  module: string;
  overrides: number;
  decisions: number;
  rate: number;
}

// ──────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────

const SEVERITY_DOT: Record<AlertSeverity, string> = {
  critical: "bg-red-500",
  high: "bg-orange-500",
  medium: "bg-amber-500",
  info: "bg-slate-500",
};

const BED_ALERT_COLOR: Record<BedSummaryRow["alert_level"], string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
  black: "bg-slate-900 ring-2 ring-red-500",
};

const CROWDING_COLOR: Record<CrowdingPoint["crowding_level"], string> = {
  normal: "#22c55e",
  busy: "#eab308",
  overcrowded: "#f97316",
  severe: "#ef4444",
  critical: "#b91c1c",
};

function timeAgo(iso?: string): string {
  if (!iso) return "";
  try {
    const dt = new Date(iso).getTime();
    const delta = Math.max(0, Date.now() - dt);
    const s = Math.floor(delta / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    return `${Math.floor(m / 60)}h ago`;
  } catch {
    return "";
  }
}

// ──────────────────────────────────────────────────────────────────────
// Layout primitives
// ──────────────────────────────────────────────────────────────────────

function Tile({
  title,
  to,
  right,
  tone = "neutral",
  children,
}: {
  title: string;
  to: string;
  right?: React.ReactNode;
  tone?: "neutral" | "warn" | "crit" | "ok";
  children: React.ReactNode;
}) {
  const ring =
    tone === "crit"
      ? "border-red-500/40"
      : tone === "warn"
        ? "border-amber-500/40"
        : tone === "ok"
          ? "border-emerald-500/30"
          : "border-border";
  return (
    <Link
      to={to}
      className={`group bg-bg-card rounded-xl border ${ring} p-3 hover:border-blue-500/50 transition-colors flex flex-col min-w-0`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">
          {title}
        </span>
        <div className="flex items-center gap-1 text-slate-500">
          {right}
          <ArrowRight className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </div>
      {children}
    </Link>
  );
}

function Panel({
  title,
  to,
  children,
  right,
}: {
  title: string;
  to?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="bg-bg-card rounded-xl border border-border p-4 min-w-0"
    >
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        <div className="flex items-center gap-2">
          {right}
          {to && (
            <Link
              to={to}
              className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
            >
              View full <ArrowRight className="w-3 h-3" aria-hidden="true" />
            </Link>
          )}
        </div>
      </div>
      {children}
    </section>
  );
}

function BigNumber({
  value,
  suffix,
  tone = "default",
}: {
  value: string | number;
  suffix?: string;
  tone?: "default" | "warn" | "crit" | "ok";
}) {
  const color =
    tone === "crit"
      ? "text-red-400"
      : tone === "warn"
        ? "text-amber-400"
        : tone === "ok"
          ? "text-emerald-400"
          : "text-white";
  return (
    <div className={`font-mono-clinical text-2xl font-bold ${color} leading-none`}>
      {value}
      {suffix ? <span className="text-sm ml-1 text-slate-500">{suffix}</span> : null}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────────────────

export default function Overview() {
  const { unacked } = useAlerts();
  const criticalUnacked = unacked.filter((a) => a.severity === "critical").length;
  const highUnacked = unacked.filter((a) => a.severity === "high").length;

  // Sim endpoints: raw JSON (no envelope)
  const statsRaw = usePoll<SimStats>("/api/sim/stats-dashboard", 5000);
  const edBoardRaw = usePoll<EdBoard>("/api/sim/ed-board", 5000);
  const icuBoardRaw = usePoll<IcuBoard>("/api/sim/icu-board", 5000);
  // BaseResponse-wrapped endpoints
  const deterRaw = usePoll<Envelope<DeteriorationStats>>(
    "/api/deterioration/deterioration/stats",
    5000,
  );
  const deterAlertsRaw = usePoll<Envelope<DeteriorationAlert[]>>(
    "/api/deterioration/deterioration/active-alerts",
    5000,
  );
  const trolleyRaw = usePoll<Envelope<TrolleyCount>>(
    "/api/trolley/trolley/count",
    10000,
  );
  const bedSummaryRaw = usePoll<Envelope<BedSummaryRow[]>>(
    "/api/beds/beds/summary",
    7000,
  );
  const dischargeBoardRaw = usePoll<Envelope<DischargeBoardRow[]>>(
    "/api/beds/discharge-board",
    10000,
  );
  const edStateRaw = usePoll<Envelope<EdState>>("/api/ed-flow/ed-state", 5000);
  const crowdingRaw = usePoll<Envelope<CrowdingPoint[]>>(
    "/api/ed-flow/forecast/crowding",
    60000,
  );
  const loungeRaw = usePoll<Envelope<DischargeLoungeStatus>>(
    "/api/discharge-lounge/discharge-lounge/status",
    15000,
  );
  const overrideStatsRaw = usePoll<Envelope<OverrideStatsRow[]>>(
    "/api/xai/xai/override-stats",
    30000,
  );

  // Normalised accessors — keep the old .ok / .data shape for tiles
  const stats = { ok: statsRaw.ok, data: statsRaw.data };
  const edBoard = { ok: edBoardRaw.ok, data: edBoardRaw.data };
  const icuBoard = { ok: icuBoardRaw.ok, data: icuBoardRaw.data };
  const deter = { ok: deterRaw.ok, data: unwrap(deterRaw.data) };
  const deterAlerts = { ok: deterAlertsRaw.ok, data: unwrap(deterAlertsRaw.data) };
  const trolley = { ok: trolleyRaw.ok, data: unwrap(trolleyRaw.data) };
  const bedSummary = { ok: bedSummaryRaw.ok, data: unwrap(bedSummaryRaw.data) };
  const dischargeBoard = {
    ok: dischargeBoardRaw.ok,
    data: unwrap(dischargeBoardRaw.data),
  };
  const edState = { ok: edStateRaw.ok, data: unwrap(edStateRaw.data) };
  const crowding = { ok: crowdingRaw.ok, data: unwrap(crowdingRaw.data) };
  const lounge = { ok: loungeRaw.ok, data: unwrap(loungeRaw.data) };
  const overrideStats = { ok: overrideStatsRaw.ok, data: unwrap(overrideStatsRaw.data) };

  // Derived
  const esiCounts = useMemo(() => {
    const c = [0, 0, 0, 0, 0];
    (edBoard.data?.patients ?? []).forEach((p) => {
      const idx = Math.max(1, Math.min(5, p.acuity ?? 3)) - 1;
      c[idx]++;
    });
    return c;
  }, [edBoard.data]);

  const highSofa = useMemo(
    () => (icuBoard.data?.patients ?? []).filter((p) => p.sofa_total >= 10).length,
    [icuBoard.data],
  );

  const bedsRed = useMemo(() => {
    const rows = bedSummary.data ?? [];
    return rows.filter((r) => r.alert_level === "red" || r.alert_level === "black").length;
  }, [bedSummary.data]);

  const bedsAmber = useMemo(() => {
    const rows = bedSummary.data ?? [];
    return rows.filter((r) => r.alert_level === "amber").length;
  }, [bedSummary.data]);

  const dischargeReady = useMemo(() => {
    const rows = dischargeBoard.data ?? [];
    return rows.filter((r) => (r.discharge_readiness ?? 0) >= 0.6).length;
  }, [dischargeBoard.data]);

  const risingAlerts = useMemo(() => {
    return (deterAlerts.data ?? []).filter((a) => a.trend?.is_clinically_rising);
  }, [deterAlerts.data]);

  const forecastPeak = useMemo(() => {
    const arr = crowding.data ?? [];
    if (arr.length === 0) return null;
    return arr.reduce((acc, p) => (p.predicted_nedocs > acc.predicted_nedocs ? p : acc), arr[0]);
  }, [crowding.data]);

  const overrideTotal = useMemo(() => {
    const rows = overrideStats.data ?? [];
    const dec = rows.reduce((s, r) => s + (r.decisions ?? 0), 0);
    const ov = rows.reduce((s, r) => s + (r.overrides ?? 0), 0);
    return { rate: dec > 0 ? ov / dec : null, decisions: dec, overrides: ov };
  }, [overrideStats.data]);

  // Top-10 bed-occupancy rows, memoised so the chart doesn't reorder
  // / re-render on every poll tick (was causing row-jitter).
  const bedTop10 = useMemo(() => {
    return (bedSummary.data ?? [])
      .slice()
      .sort((a, b) => b.occupancy_rate - a.occupancy_rate)
      .slice(0, 10);
  }, [bedSummary.data]);

  return (
    <div className="space-y-4">
      {/* ─── COMMAND STRIP ──────────────────────────────────────────────── */}
      <div className="bg-bg-card rounded-xl border border-border px-4 py-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            {stats.ok && (
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            )}
            <span
              className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                stats.ok ? "bg-emerald-500" : "bg-slate-500"
              }`}
            />
          </span>
          <span className="text-xs font-semibold text-emerald-400 uppercase tracking-wider">
            {stats.ok ? "Live" : "Offline"}
          </span>
        </div>
        <div className="text-sm text-slate-300">
          <span className="font-mono-clinical text-white font-bold">
            {stats.data?.total_active ?? "—"}
          </span>{" "}
          active ·{" "}
          <span
            className={`font-mono-clinical ${
              // Highlight the rare-but-known case where the simulator has been
              // running for a while but the discharge counter is still zero
              // (F26 — likely a backend bug in the DES discharge path). At
              // least surface the anomaly so downstream charts (LOS,
              // throughput) aren't misread as steady-state truth.
              stats.data && stats.data.total_active > 0 && stats.data.total_discharged === 0
                ? "text-amber-400"
                : "text-slate-300"
            }`}
            title={
              stats.data && stats.data.total_active > 0 && stats.data.total_discharged === 0
                ? "0 discharges with active patients > 0 is unusual — the simulator may not be releasing patients. LOS and throughput downstream should be treated cautiously."
                : undefined
            }
          >
            {stats.data?.total_discharged ?? "—"}
          </span>{" "}
          discharged
        </div>

        <div className="ml-auto flex items-center gap-2">
          {criticalUnacked > 0 && (
            <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-red-500/10 text-red-400 border border-red-500/30 animate-pulse">
              <AlertTriangle className="w-3 h-3" />
              {criticalUnacked} critical
            </span>
          )}
          {highUnacked > 0 && (
            <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-orange-500/10 text-orange-400 border border-orange-500/30">
              {highUnacked} high
            </span>
          )}
          {unacked.length === 0 && (
            <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
              All clear
            </span>
          )}
          <button
            onClick={() => {
              const ev = new KeyboardEvent("keydown", {
                key: "k",
                metaKey: true,
                ctrlKey: true,
                bubbles: true,
              });
              window.dispatchEvent(ev);
            }}
            className="inline-flex items-center gap-2 text-xs px-2 py-1 rounded-lg bg-slate-700/50 hover:bg-slate-700 text-slate-300 transition-colors"
            title="Search (⌘K)"
          >
            <Search className="w-3 h-3" />
            <kbd className="text-[11px]">⌘K</kbd>
          </button>
          <Link
            to="/system"
            className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg text-slate-400 hover:text-slate-200 transition-colors"
          >
            <Shield className="w-3 h-3" />
            Admin
          </Link>
        </div>
      </div>

      {/* ─── NOW TILES (6) ───────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {/* ED queue */}
        <Tile
          title="ED queue"
          to="/ed-triage"
          tone={
            esiCounts[0] > 0 ? "crit" : esiCounts[1] > 0 ? "warn" : "neutral"
          }
          right={<Stethoscope className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={
              edState.data?.total_patients !== undefined
                ? edState.data.total_patients
                : "—"
            }
          />
          <div className="flex items-center gap-1 mt-1.5 text-[11px] text-slate-400">
            {edState.data ? (
              <>
                <span>Waiting {edState.data.waiting_count ?? "—"}</span>
                <span>·</span>
                <span>In tx {edState.data.in_treatment_count ?? "—"}</span>
              </>
            ) : (
              // Falling back to /api/sim/ed-board.count would over-count
              // the ED tile (it returns the whole sim census, not just
              // ED) — so when ed-flow is offline, surface that explicitly
              // rather than silently displaying a 3× inflated number.
              <span className="text-amber-400">ED flow service offline</span>
            )}
          </div>
          <div className="flex gap-0.5 mt-2">
            {esiCounts.map((n, i) => (
              <div
                key={i}
                className="h-1 flex-1 rounded"
                style={{
                  backgroundColor: ["#DC2626", "#F97316", "#EAB308", "#22C55E", "#3B82F6"][i],
                  opacity: n > 0 ? 1 : 0.15,
                }}
                title={`ESI-${i + 1}: ${n}`}
              />
            ))}
          </div>
        </Tile>

        {/* ICU SOFA */}
        <Tile
          title="ICU SOFA"
          to="/sepsis"
          tone={highSofa > 0 ? "crit" : "neutral"}
          right={<HeartPulse className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={icuBoard.data?.count ?? 0}
            tone={highSofa > 0 ? "crit" : "default"}
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            {highSofa} high SOFA (≥10)
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5">
            {icuBoard.data?.patients?.length ?? 0} in unit
          </div>
        </Tile>

        {/* Deterioration */}
        <Tile
          title="Deterioration"
          to="/deterioration"
          tone={
            (deter.data?.high_band ?? 0) > 0
              ? "crit"
              : (deter.data?.medium_band ?? 0) > 0
                ? "warn"
                : "neutral"
          }
          right={<AlertTriangle className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={deter.data?.active_patients ?? 0}
            tone={(deter.data?.high_band ?? 0) > 0 ? "crit" : "default"}
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            <span className="text-red-400">{deter.data?.high_band ?? 0}</span> high ·{" "}
            <span className="text-amber-400">{deter.data?.medium_band ?? 0}</span> medium
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5">
            {risingAlerts.length} rising · {deter.data?.unacknowledged_escalations ?? 0} unack
          </div>
        </Tile>

        {/* Trolleys */}
        <Tile
          title="Trolleys"
          to="/trolley"
          tone={(trolley.data?.total ?? 0) > 0 ? "warn" : "ok"}
          right={<Activity className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={trolley.data?.total ?? 0}
            tone={(trolley.data?.total ?? 0) > 0 ? "warn" : "ok"}
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            ED {trolley.data?.ed ?? 0} · Cor {trolley.data?.corridor ?? 0} · Ward{" "}
            {trolley.data?.ward ?? 0}
          </div>
        </Tile>

        {/* Bed status */}
        <Tile
          title="Bed status"
          to="/bed-management"
          tone={bedsRed > 0 ? "crit" : bedsAmber > 0 ? "warn" : "ok"}
          right={<Bed className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={bedsRed > 0 ? `${bedsRed}×red` : bedsAmber > 0 ? `${bedsAmber}×amber` : "Clear"}
            tone={bedsRed > 0 ? "crit" : bedsAmber > 0 ? "warn" : "ok"}
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            {(bedSummary.data ?? []).length} departments
          </div>
          <div className="flex gap-0.5 mt-2">
            {(bedSummary.data ?? []).slice(0, 14).map((d) => (
              <div
                key={d.department}
                className={`h-1.5 flex-1 rounded-sm ${BED_ALERT_COLOR[d.alert_level]}`}
                title={`${d.department}: ${Math.round(d.occupancy_rate * 100)}% (${d.alert_level})`}
              />
            ))}
          </div>
        </Tile>

        {/* Discharge-ready */}
        <Tile
          title="Discharge-ready"
          to="/bed-management"
          tone="ok"
          right={<DoorOpen className="w-3.5 h-3.5" />}
        >
          <BigNumber value={dischargeReady} tone={dischargeReady > 0 ? "ok" : "default"} />
          <div className="text-[11px] text-slate-400 mt-1.5">
            readiness ≥ 0.6
          </div>
          <div
            className="text-[11px] text-slate-500 mt-0.5"
            title="Total beds with occupants visible to bed_management. Patients still in ED triage / between transfers may not yet have a bed allocation, so this can be lower than total sim-active."
          >
            of {(dischargeBoard.data ?? []).filter((r) => r.patient_id != null).length} patients in beds
          </div>
        </Tile>
      </div>

      {/* ─── LIVE STREAMS ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel
          title="Alert stream"
          to="/system"
          right={
            <span className="text-[11px] text-slate-500">
              {unacked.length} unacked
            </span>
          }
        >
          {unacked.length === 0 ? (
            <p className="text-xs text-slate-500 py-6 text-center">
              No unacknowledged alerts.
            </p>
          ) : (
            <div className="space-y-1 max-h-[280px] overflow-y-auto">
              {unacked.slice(0, 8).map((a) => (
                <Link
                  key={a.id}
                  to={a.route_hint ?? "#"}
                  className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-700/30 transition-colors"
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${SEVERITY_DOT[a.severity]} shrink-0`} />
                  <span className="text-xs text-slate-200 truncate flex-1">
                    {a.title}
                  </span>
                  {a.count && a.count > 1 ? (
                    <span className="text-[11px] px-1.5 rounded bg-slate-700 text-slate-300 font-mono-clinical">
                      ×{a.count}
                    </span>
                  ) : null}
                  {a.patient_id && (
                    <span className="text-[11px] text-blue-400 font-mono-clinical">
                      #{a.patient_id}
                    </span>
                  )}
                  <span className="text-[11px] text-slate-500 font-mono-clinical">
                    {timeAgo(a.last_timestamp ?? a.timestamp)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          title="Deterioration watchboard"
          to="/deterioration"
          right={
            <span className="text-[11px] text-slate-500">
              {risingAlerts.length} rising
            </span>
          }
        >
          {(deterAlerts.data ?? []).length === 0 ? (
            <p className="text-xs text-slate-500 py-6 text-center">
              No active early-warning alerts.
            </p>
          ) : (
            <div className="space-y-1 max-h-[280px] overflow-y-auto">
              {(deterAlerts.data ?? [])
                .slice()
                .sort((a, b) => (b.score?.total ?? 0) - (a.score?.total ?? 0))
                .slice(0, 8)
                .map((a) => {
                  const band = a.score?.risk_band ?? "low";
                  const bandColor =
                    band === "high"
                      ? "bg-red-500"
                      : band === "medium"
                        ? "bg-amber-500"
                        : "bg-emerald-500";
                  const TrendIcon =
                    a.trend?.trajectory === "rising"
                      ? TrendingUp
                      : a.trend?.trajectory === "falling"
                        ? TrendingDown
                        : Minus;
                  const trendColor =
                    a.trend?.trajectory === "rising"
                      ? "text-red-400"
                      : a.trend?.trajectory === "falling"
                        ? "text-emerald-400"
                        : "text-slate-500";
                  return (
                    <Link
                      key={a.hadm_id}
                      to={`/patient/${a.hadm_id}`}
                      className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-700/30 transition-colors"
                    >
                      <div
                        className={`w-7 h-7 ${bandColor} text-white rounded-md flex items-center justify-center text-xs font-bold shrink-0`}
                      >
                        {a.score?.total ?? "?"}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-xs text-slate-200 truncate">
                          #{a.subject_id}
                          <span className="ml-2 text-[11px] text-slate-500 uppercase">
                            {a.scoring_system}
                          </span>
                        </div>
                        <div className="text-[11px] text-slate-500 truncate">
                          {a.department}
                        </div>
                      </div>
                      <div className={`flex items-center gap-0.5 text-[11px] ${trendColor}`}>
                        <TrendIcon className="w-3 h-3" />
                        {a.trend?.delta > 0 ? "+" : ""}
                        {a.trend?.delta ?? 0}
                      </div>
                    </Link>
                  );
                })}
            </div>
          )}
        </Panel>
      </div>

      {/* ─── FORWARD (NEXT 24H) ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel
          title="ED crowding forecast · next 24h"
          to="/ed-flow"
          right={
            forecastPeak ? (
              <span
                className="text-[11px] px-2 py-0.5 rounded-full font-mono-clinical"
                style={{
                  color: CROWDING_COLOR[forecastPeak.crowding_level],
                  backgroundColor: `${CROWDING_COLOR[forecastPeak.crowding_level]}22`,
                }}
              >
                peak NEDOCS {forecastPeak.predicted_nedocs.toFixed(0)} · h+{forecastPeak.hour}
              </span>
            ) : null
          }
        >
          {(crowding.data ?? []).length === 0 ? (
            <p className="text-xs text-slate-500 py-6 text-center">
              ED flow service offline.
            </p>
          ) : (crowding.data ?? [])[0]?.source === "no_data" ? (
            <div className="py-6 text-center text-xs text-slate-500">
              <p className="font-medium text-slate-400">No live forecast</p>
              <p className="mt-1">No active ED patients — start the simulation to see a real projection.</p>
            </div>
          ) : (
            <div style={{ height: 210 }}>
              <ResponsiveContainer>
                <ComposedChart data={crowding.data ?? []}>
                  <XAxis
                    dataKey="hour"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    tickFormatter={(h) => `+${h}h`}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    domain={[
                      0,
                      (dataMax: number) =>
                        Math.max(100, Math.ceil((dataMax + 10) / 100) * 100),
                    ]}
                    tickCount={5}
                    allowDecimals={false}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#0f172a",
                      border: "1px solid #334155",
                      fontSize: 11,
                    }}
                    labelFormatter={(h) => `+${h}h from now`}
                    formatter={(v: number, _n, p) => {
                      const level = (p.payload as CrowdingPoint).crowding_level;
                      return [`${v.toFixed(0)} (${level})`, "NEDOCS"];
                    }}
                  />
                  {/* coloured dots by crowding level */}
                  <Area
                    type="monotone"
                    dataKey="predicted_nedocs"
                    stroke="#3b82f6"
                    fill="#3b82f622"
                    strokeWidth={1.5}
                  />
                  <Line
                    type="monotone"
                    dataKey="predicted_nedocs"
                    stroke="transparent"
                    dot={(props) => {
                      const { cx, cy, payload } = props as {
                        cx: number;
                        cy: number;
                        payload: CrowdingPoint;
                      };
                      return (
                        <circle
                          key={`dot-${payload.hour}`}
                          cx={cx}
                          cy={cy}
                          r={3}
                          fill={CROWDING_COLOR[payload.crowding_level]}
                        />
                      );
                    }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>

        <Panel
          title="Bed occupancy by department"
          to="/bed-management"
          right={
            <span className="text-[11px] text-slate-500">
              {(bedSummary.data ?? []).length} depts
            </span>
          }
        >
          {bedTop10.length === 0 ? (
            <p className="text-xs text-slate-500 py-6 text-center">
              Bed management offline.
            </p>
          ) : (
            <div style={{ height: 210 }}>
              <ResponsiveContainer>
                <BarChart
                  data={bedTop10.map((d) => ({
                    name: d.department.replace(/_/g, " "),
                    pct: Math.round(d.occupancy_rate * 100),
                    level: d.alert_level,
                  }))}
                  layout="vertical"
                  margin={{ top: 0, right: 10, left: 5, bottom: 0 }}
                >
                  <XAxis
                    type="number"
                    domain={[0, 100]}
                    ticks={[0, 25, 50, 75, 100]}
                    tick={{ fontSize: 11, fill: "#64748b" }}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={110}
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                    tickFormatter={(v: string) => (v.length > 16 ? v.slice(0, 14) + ".." : v)}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#0f172a",
                      border: "1px solid #334155",
                      fontSize: 12,
                    }}
                    formatter={(v: number, _n, p) => [
                      `${v}% (${(p.payload as { level: string }).level})`,
                      "Occupancy",
                    ]}
                  />
                  <Bar dataKey="pct" radius={[0, 3, 3, 0]}>
                    {bedTop10.map((d, i) => (
                      <Cell
                        key={i}
                        fill={
                          d.alert_level === "red" || d.alert_level === "black"
                            ? "#ef4444"
                            : d.alert_level === "amber"
                              ? "#f59e0b"
                              : "#10b981"
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>
      </div>

      {/* ─── GOVERNANCE STRIP ────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Tile
          title="XAI override"
          to="/xai"
          tone={
            overrideTotal.rate !== null && overrideTotal.rate > 0.2 ? "warn" : "neutral"
          }
          right={<Brain className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={
              overrideTotal.rate === null
                ? "—"
                : `${Math.round(overrideTotal.rate * 100)}%`
            }
            tone={
              overrideTotal.rate !== null && overrideTotal.rate > 0.2 ? "warn" : "default"
            }
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            {overrideTotal.overrides} / {overrideTotal.decisions} decisions
          </div>
        </Tile>

        <Tile
          title="Discharge lounge"
          to="/discharge-lounge"
          tone={
            lounge.data && lounge.data.occupied >= lounge.data.capacity * 0.8
              ? "warn"
              : "neutral"
          }
          right={<DoorOpen className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={`${lounge.data?.occupied ?? 0}/${lounge.data?.capacity ?? "—"}`}
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            {lounge.data?.available ?? 0} available
          </div>
        </Tile>

        <Tile
          title="ED avg wait"
          to="/ed-flow"
          tone={
            (edState.data?.avg_wait_minutes ?? 0) > 240
              ? "crit"
              : (edState.data?.avg_wait_minutes ?? 0) > 120
                ? "warn"
                : "ok"
          }
          right={<Clock className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={edState.data?.avg_wait_minutes?.toFixed(0) ?? "—"}
            suffix="min"
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            longest {edState.data?.longest_wait_minutes?.toFixed(0) ?? "—"}m
          </div>
        </Tile>

        <Tile
          title="Sim engine"
          to="/simulation"
          tone={stats.ok ? "ok" : "warn"}
          right={<Radio className="w-3.5 h-3.5" />}
        >
          <BigNumber
            value={stats.data?.total_active ?? 0}
            tone={stats.ok ? "ok" : "default"}
          />
          <div className="text-[11px] text-slate-400 mt-1.5">
            {stats.data?.recent_admissions?.length ?? 0} recent admits
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5">
            {stats.data?.sim_time ? `sim @ ${timeAgo(stats.data.sim_time)}` : "—"}
          </div>
        </Tile>
      </div>
    </div>
  );
}
