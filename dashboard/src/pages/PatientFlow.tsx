import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, ArrowDownRight, ArrowUpRight, Bed, LogIn, LogOut, Table2, Users,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line,
  LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import StatCard from "../components/StatCard";
import { usePoll } from "../hooks/usePoll";
import { AXIS_TICK_STYLE, CHART_GRID_PROPS, CHART_TOOLTIP_STYLE } from "../lib/chartConfig";

/**
 * Realtime patient flow.
 *
 * Three questions, three charts, deliberately never combined onto shared axes:
 *   where patients ARE   -> census by department, stacked over time (patients)
 *   how fast they MOVE   -> admissions vs discharges per minute (patients/min)
 *   how FULL it is       -> occupied vs capacity per department (beds)
 * A level and a rate on one plot would need two y-scales, which is the single
 * most misleading thing a chart can do, so they stay separate.
 *
 * /department-census is a snapshot with no history, so the stacked series is
 * accumulated client-side from successive polls. /metrics-history does carry
 * history and seeds the rate chart, so that one is populated on first paint.
 */

// ── categorical palette ────────────────────────────────────────────────────
// Fixed order, never cycled. Validated with the dataviz validator against this
// dashboard's real surfaces (light #F8FAFC, dark #0F172A):
//   light — CVD adjacent ΔE 9.1, normal-vision 19.6, 3 slots under 3:1 contrast
//   dark  — CVD adjacent ΔE 8.4, normal-vision 19.3, all slots >= 3:1
// The light-mode sub-3:1 slots are why this page ships a table view and direct
// labels: that is the documented relief, not an optional extra.
const SERIES_LIGHT = [
  "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
  "#e87ba4", "#008300", "#4a3aa7", "#e34948",
];
const SERIES_DARK = [
  "#3987e5", "#d95926", "#199e70", "#c98500",
  "#d55181", "#008300", "#9085e9", "#e66767",
];
/** Slot 9+ never gets an invented hue — everything past the cap folds here. */
const OTHER_COLOR = "#94a3b8";
const OTHER = "Other";
const MAX_SERIES = 7; // + Other = 8 slots

const HISTORY_POINTS = 60; // rolling window of census snapshots
const SMOOTH_WINDOW = 5;   // samples in the flow-rate rolling mean

type Census = { census: Record<string, number>; total: number };
type SimState = {
  running: boolean;
  sim_time: string;
  active_patients: number;
  stats: { total_admissions: number; total_discharges: number; total_transfers: number };
};
type MetricsHistory = {
  count: number;
  history: Array<{
    sim_hours: number; total_admitted: number; total_discharged: number;
    active_patients: number; sim_time: string;
  }>;
};
type BedRow = { department: string; capacity: number; occupied: number; occupancy_rate: number };
type Envelope<T> = { status: string; data: T };
type EdState = {
  total_patients: number; waiting_count: number; in_treatment_count: number;
  boarding_count: number; avg_wait_minutes: number; crowding_level: string;
};

function useIsDark() {
  const [dark, setDark] = useState(
    () => typeof document !== "undefined" && document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(() => setDark(el.classList.contains("dark")));
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

const hhmm = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "--:--"
    : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

export default function PatientFlow() {
  const isDark = useIsDark();
  const palette = isDark ? SERIES_DARK : SERIES_LIGHT;

  const census = usePoll<Census>("/api/sim/department-census", 5000);
  const state = usePoll<SimState>("/api/sim/state", 5000);
  const metrics = usePoll<MetricsHistory>("/api/sim/metrics-history", 15000);
  const beds = usePoll<Envelope<BedRow[]>>("/api/beds/beds/summary", 15000);
  const ed = usePoll<Envelope<EdState>>("/api/ed-flow/ed-state", 5000);

  const [showTable, setShowTable] = useState(false);

  // ── rolling census buffer ────────────────────────────────────────────────
  const [series, setSeries] = useState<Array<Record<string, number | string>>>([]);
  const lastStamp = useRef<string>("");
  useEffect(() => {
    const c = census.data;
    const stamp = state.data?.sim_time ?? "";
    if (!c?.census || !stamp || stamp === lastStamp.current) return;
    lastStamp.current = stamp;
    setSeries((prev) => [...prev, { t: hhmm(stamp), ...c.census }].slice(-HISTORY_POINTS));
  }, [census.data, state.data?.sim_time]);

  // Departments ranked by CURRENT census so colour follows the entity, and the
  // ranking is recomputed only when the department set itself changes.
  const departments = useMemo(() => {
    const c = census.data?.census ?? {};
    return Object.entries(c)
      .sort((a, b) => b[1] - a[1])
      .map(([name]) => name);
  }, [census.data?.census]);

  const shown = departments.slice(0, MAX_SERIES);
  const folded = departments.slice(MAX_SERIES);

  const stacked = useMemo(
    () =>
      series.map((row) => {
        const out: Record<string, number | string> = { t: row.t };
        shown.forEach((d) => (out[d] = Number(row[d] ?? 0)));
        if (folded.length) {
          out[OTHER] = folded.reduce((s, d) => s + Number(row[d] ?? 0), 0);
        }
        return out;
      }),
    [series, shown.join("|"), folded.join("|")],
  );

  // ── flow rate: patients per sim-minute, from cumulative counters ─────────
  // Admissions land as discrete events between samples, so the raw per-sample
  // rate is a 0→spike→0 sawtooth that reads as noise. A centred rolling mean
  // over SMOOTH_WINDOW samples turns it back into the trend the eye is
  // actually looking for. The window is stated on the chart — a smoothed line
  // presented as raw would be a lie.
  const rates = useMemo(() => {
    const h = metrics.data?.history ?? [];
    const raw: Array<{ t: string; admissions: number; discharges: number }> = [];
    for (let i = 1; i < h.length; i++) {
      const dh = h[i].sim_hours - h[i - 1].sim_hours;
      if (dh <= 0) continue; // reset boundary or duplicate sample
      const dAdm = Math.max(0, h[i].total_admitted - h[i - 1].total_admitted);
      const dDis = Math.max(0, h[i].total_discharged - h[i - 1].total_discharged);
      raw.push({
        t: hhmm(h[i].sim_time),
        admissions: dAdm / (dh * 60),
        discharges: dDis / (dh * 60),
      });
    }
    const w = SMOOTH_WINDOW;
    const half = Math.floor(w / 2);
    const smoothed = raw.map((_, i) => {
      const lo = Math.max(0, i - half);
      const hi = Math.min(raw.length, i + half + 1);
      const slice = raw.slice(lo, hi);
      const mean = (k: "admissions" | "discharges") =>
        +(slice.reduce((n, r) => n + r[k], 0) / slice.length).toFixed(3);
      return { t: raw[i].t, admissions: mean("admissions"), discharges: mean("discharges") };
    });
    return smoothed.slice(-HISTORY_POINTS);
  }, [metrics.data]);

  const bedRows = (beds.data?.data ?? [])
    .filter((b) => b.capacity > 0)
    .sort((a, b) => b.occupancy_rate - a.occupancy_rate)
    // free + a prebuilt label string: LabelList needs a real dataKey, and the
    // direct labels are not decoration here — they are the documented relief
    // for the three light-mode slots that sit under 3:1 against this surface.
    .map((b) => ({
      ...b,
      free: Math.max(0, b.capacity - b.occupied),
      label: `${b.occupied}/${b.capacity}`,
    }));

  const s = state.data;
  const edState = ed.data?.data;
  const totalCap = bedRows.reduce((n, b) => n + b.capacity, 0);
  const totalOcc = bedRows.reduce((n, b) => n + b.occupied, 0);
  const netFlow = rates.length
    ? rates.slice(-5).reduce((n, r) => n + r.admissions - r.discharges, 0) / Math.min(5, rates.length)
    : 0;

  const colorFor = (d: string, i: number) => (d === OTHER ? OTHER_COLOR : palette[i % palette.length]);
  const stale = !census.ok || !state.ok;

  return (
    <div className="p-6 space-y-6">
      {/* ── header ─────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-text-primary flex items-center gap-2">
            <Activity className="w-6 h-6 text-blue-500" />
            Patient Flow
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Live movement through the hospital — where patients are, how fast they move,
            and how full each department is.
            {s?.sim_time && (
              <span className="ml-2 font-mono text-xs">sim {hhmm(s.sim_time)}</span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
              stale
                ? "bg-amber-500/10 text-amber-500"
                : s?.running
                  ? "bg-emerald-500/10 text-emerald-500"
                  : "bg-slate-500/10 text-slate-400"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${stale ? "bg-amber-500" : s?.running ? "bg-emerald-500 animate-pulse" : "bg-slate-400"}`} />
            {stale ? "stale — upstream not answering" : s?.running ? "live" : "sim paused"}
          </span>
          <button
            onClick={() => setShowTable((v) => !v)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border border-border text-slate-400 hover:text-text-primary transition-colors"
            aria-pressed={showTable}
          >
            <Table2 className="w-3.5 h-3.5" />
            {showTable ? "Hide" : "Show"} table
          </button>
        </div>
      </div>

      {/* ── stat tiles ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard
          icon={<Users className="w-4 h-4" />}
          label="In hospital"
          value={s?.active_patients ?? "—"}
          accentColor={palette[0]}
          subtitle={`${census.data?.total ?? 0} across ${departments.length} departments`}
        />
        <StatCard
          icon={<LogIn className="w-4 h-4" />}
          label="Admissions"
          value={s?.stats.total_admissions ?? "—"}
          accentColor={palette[2]}
          subtitle="cumulative this run"
        />
        <StatCard
          icon={<LogOut className="w-4 h-4" />}
          label="Discharges"
          value={s?.stats.total_discharges ?? "—"}
          accentColor={palette[1]}
          subtitle="cumulative this run"
        />
        <StatCard
          icon={netFlow > 0 ? <ArrowUpRight className="w-4 h-4" /> : <ArrowDownRight className="w-4 h-4" />}
          label="Net flow"
          value={`${netFlow >= 0 ? "+" : ""}${netFlow.toFixed(2)}/min`}
          accentColor={Math.abs(netFlow) < 0.005 ? "#94a3b8" : netFlow > 0 ? "#F97316" : "#22C55E"}
          subtitle={
            Math.abs(netFlow) < 0.005
              ? "steady — arrivals match discharges"
              : netFlow > 0
                ? "filling — arrivals outpace discharges"
                : "emptying — discharges outpace arrivals"
          }
        />
        <StatCard
          icon={<Bed className="w-4 h-4" />}
          label="Occupancy"
          value={totalCap ? `${Math.round((totalOcc / totalCap) * 100)}%` : "—"}
          accentColor={palette[6]}
          subtitle={`${totalOcc} of ${totalCap} beds`}
        />
      </div>

      {/* ── 1. where patients are ──────────────────────────────────────── */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="text-sm font-semibold text-text-primary">
            Census by department
          </h2>
          <span className="text-xs text-slate-400">patients · rolling {HISTORY_POINTS} samples</span>
        </div>
        <p className="text-xs text-slate-400 mb-4">
          Built live from successive polls — the census endpoint is a snapshot, so the
          history starts when this page opens.
          {folded.length > 0 && ` ${folded.length} smaller departments folded into "${OTHER}".`}
        </p>
        {stacked.length < 2 ? (
          <div className="h-72 flex items-center justify-center text-sm text-slate-400">
            Collecting samples… first points appear within ~10s.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={288}>
            <AreaChart data={stacked} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid {...CHART_GRID_PROPS} vertical={false} />
              <XAxis dataKey="t" tick={AXIS_TICK_STYLE} tickLine={false} axisLine={false} minTickGap={28} />
              <YAxis tick={AXIS_TICK_STYLE} tickLine={false} axisLine={false} width={44} />
              <Tooltip
                contentStyle={CHART_TOOLTIP_STYLE}
                labelStyle={{ fontSize: 11, marginBottom: 4 }}
                itemStyle={{ fontSize: 11, padding: 0 }}
              />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconType="circle" iconSize={8} />
              {[...shown, ...(folded.length ? [OTHER] : [])].map((d, i) => (
                <Area
                  key={d}
                  type="monotone"
                  dataKey={d}
                  stackId="census"
                  stroke={colorFor(d, i)}
                  fill={colorFor(d, i)}
                  fillOpacity={0.85}
                  // 2px surface gap between stacked segments so adjacent fills
                  // stay separable without relying on hue alone.
                  strokeWidth={2}
                  strokeOpacity={0}
                  isAnimationActive={false}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── 2. how fast they move ──────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <div className="bg-bg-card rounded-xl border border-border p-5">
          <div className="flex items-baseline justify-between mb-1">
            <h2 className="text-sm font-semibold text-text-primary">Flow rate</h2>
            <span className="text-xs text-slate-400">patients / sim-minute</span>
          </div>
          <p className="text-xs text-slate-400 mb-4">
            Derived from the cumulative counters, smoothed over a {SMOOTH_WINDOW}-sample
            rolling mean — admissions arrive as discrete events, so the unsmoothed rate is a
            sawtooth. Where admissions sit above discharges the hospital is filling.
          </p>
          {rates.length < 2 ? (
            <div className="h-64 flex items-center justify-center text-sm text-slate-400">
              Not enough history yet.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={256}>
              <LineChart data={rates} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID_PROPS} vertical={false} />
                <XAxis dataKey="t" tick={AXIS_TICK_STYLE} tickLine={false} axisLine={false} minTickGap={28} />
                <YAxis tick={AXIS_TICK_STYLE} tickLine={false} axisLine={false} width={44} />
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} itemStyle={{ fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconType="circle" iconSize={8} />
                <ReferenceLine y={0} stroke="var(--color-chart-grid)" />
                <Line type="monotone" dataKey="admissions" name="Admissions"
                      stroke={palette[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="discharges" name="Discharges"
                      stroke={palette[2]} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* ── 3. how full it is ───────────────────────────────────────── */}
        <div className="bg-bg-card rounded-xl border border-border p-5">
          <div className="flex items-baseline justify-between mb-1">
            <h2 className="text-sm font-semibold text-text-primary">Occupancy by department</h2>
            <span className="text-xs text-slate-400">occupied of capacity</span>
          </div>
          <p className="text-xs text-slate-400 mb-4">
            Sequential fill — darker means fuller. Values are labelled directly, so the bars
            never carry meaning by colour alone.
          </p>
          {bedRows.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-sm text-slate-400">
              No bed data.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={Math.max(256, bedRows.length * 30)}>
              <BarChart data={bedRows} layout="vertical"
                        margin={{ top: 4, right: 44, left: 4, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID_PROPS} horizontal={false} />
                <XAxis type="number" tick={AXIS_TICK_STYLE} tickLine={false} axisLine={false} />
                <YAxis type="category" dataKey="department" width={104}
                       tick={AXIS_TICK_STYLE} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={CHART_TOOLTIP_STYLE}
                  itemStyle={{ fontSize: 11 }}
                  formatter={(v: number, n: string) => [v, n === "occupied" ? "Occupied" : "Free"]}
                />
                <Bar dataKey="occupied" stackId="beds" radius={[0, 0, 0, 0]} isAnimationActive={false}>
                  {bedRows.map((b) => (
                    <Cell
                      key={b.department}
                      // one hue, light->dark by magnitude: sequential, not categorical
                      fill={palette[0]}
                      fillOpacity={0.35 + Math.min(1, b.occupancy_rate) * 0.65}
                    />
                  ))}
                </Bar>
                <Bar dataKey="free" name="free" stackId="beds"
                     fill="var(--color-chart-grid)" fillOpacity={0.45}
                     radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  <LabelList dataKey="label" position="right" fontSize={10} fill="#94a3b8" />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── ED stages ──────────────────────────────────────────────────── */}
      {edState && (
        <div className="bg-bg-card rounded-xl border border-border p-5">
          <h2 className="text-sm font-semibold text-text-primary mb-3">ED stages</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: "Waiting", value: edState.waiting_count, c: palette[3] },
              { label: "In treatment", value: edState.in_treatment_count, c: palette[0] },
              { label: "Boarding", value: edState.boarding_count, c: palette[1] },
              { label: "Avg wait", value: `${Math.round(edState.avg_wait_minutes)}m`, c: palette[6] },
            ].map((st) => (
              <div key={st.label} className="rounded-lg border border-border p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: st.c }} />
                  <span className="text-xs text-slate-400">{st.label}</span>
                </div>
                <div className="text-xl font-bold text-text-primary">{st.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── table view: the documented relief for sub-3:1 light slots ───── */}
      {showTable && (
        <div className="bg-bg-card rounded-xl border border-border p-5 overflow-x-auto">
          <h2 className="text-sm font-semibold text-text-primary mb-3">
            Current census — table view
          </h2>
          <table className="w-full text-sm">
            <caption className="sr-only">
              Current patient census and bed occupancy per department
            </caption>
            <thead>
              <tr className="text-left text-xs text-slate-400 border-b border-border">
                <th scope="col" className="py-2 pr-4 font-medium">Department</th>
                <th scope="col" className="py-2 pr-4 font-medium text-right">Patients</th>
                <th scope="col" className="py-2 pr-4 font-medium text-right">Occupied</th>
                <th scope="col" className="py-2 pr-4 font-medium text-right">Capacity</th>
                <th scope="col" className="py-2 font-medium text-right">Occupancy</th>
              </tr>
            </thead>
            <tbody>
              {departments.map((d, i) => {
                const bed = bedRows.find((b) => b.department === d);
                return (
                  <tr key={d} className="border-b border-border/50">
                    <th scope="row" className="py-2 pr-4 font-normal text-text-primary">
                      <span className="inline-flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full"
                              style={{ backgroundColor: colorFor(i < MAX_SERIES ? d : OTHER, i) }} />
                        {d.replace(/_/g, " ")}
                      </span>
                    </th>
                    <td className="py-2 pr-4 text-right tabular-nums text-text-primary">
                      {census.data?.census?.[d] ?? 0}
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums text-slate-400">{bed?.occupied ?? "—"}</td>
                    <td className="py-2 pr-4 text-right tabular-nums text-slate-400">{bed?.capacity ?? "—"}</td>
                    <td className="py-2 text-right tabular-nums text-slate-400">
                      {bed ? `${Math.round(bed.occupancy_rate * 100)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
