import { useEffect, useMemo, useState } from "react";
import {
  ClipboardList, Search, TrendingUp, Send, Loader2, Building2,
  AlertTriangle, Clock, Users, RefreshCw, ChevronRight, ChevronDown, Activity,
  Radio, Trash2,
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from "recharts";
import { CHART_GRID_PROPS, CHART_TOOLTIP_STYLE } from "../lib/chartConfig";
import { WaitingListAdmissionsPanel } from "../components/UpliftWidgets";
import {
  waitingListByDepartment, waitingListSeedDemo, waitingListBySpecialty,
  waitingListPurgeDemo,
  type WaitlistSummary, type WaitlistSpecialtyRow, type WaitlistTopPatient,
} from "../lib/api";

/* ---------- shared types ---------- */
interface PriorityResult {
  clinical_urgency_score: number; functional_impact_score: number;
  temporal_score: number; equity_modifier: number;
  composite_priority: number; priority_level: string;
}
interface ReferralResult {
  urgency_classification: string; urgency_confidence: number;
  extracted_entities: Record<string, any>; missing_information: string[];
  referral_quality_score: number;
}

const P_COLORS: Record<string, string> = {
  urgent: "#DC2626", soon: "#F59E0B", routine: "#22C55E", planned: "#3B82F6",
};
// Must mirror the model-hospital waiting-list departments defined in
// app_09_waiting_list/backend/app/schemas.py::IRISH_SPECIALTIES.
const SPECIALTIES = [
  "Medicine", "Surgery", "Cardiology", "Respiratory", "Orthopaedics", "Day_Ward",
];

type TabId = "departments" | "priority" | "referral";

export default function WaitingList() {
  const [tab, setTab] = useState<TabId>("departments");

  const tabs: Array<{ id: TabId; label: string; icon: any }> = [
    { id: "departments", label: "Departments", icon: Building2 },
    { id: "priority", label: "Priority Scoring", icon: TrendingUp },
    { id: "referral", label: "Referral NLP Triage", icon: Search },
  ];

  return (
    <div className="space-y-4">
      <div className="flex gap-1 bg-bg-card rounded-xl border border-border p-1">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === id ? "bg-blue-500/20 text-blue-400" : "text-slate-400 hover:text-white hover:bg-slate-700/50"
            }`}
          >
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {tab === "departments" && <DepartmentsTab />}
      {tab === "priority" && <PriorityTab />}
      {tab === "referral" && <ReferralTab />}

      <WaitingListAdmissionsPanel />
    </div>
  );
}

/* =========================================================================
 * Departments Tab — detailed per-specialty summary
 * ========================================================================= */
function DepartmentsTab() {
  const [summary, setSummary] = useState<WaitlistSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [purging, setPurging] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [liveOnly, setLiveOnly] = useState(true);

  async function refresh() {
    setLoading(true);
    try {
      const s = await waitingListByDepartment(liveOnly);
      setSummary(s);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveOnly]);

  async function seedDemo() {
    if (!confirm(
      "Seed 200 synthetic waiting-list patients? These will be tagged as 'demo_seed' and hidden by the Live-only filter.",
    )) return;
    setSeeding(true);
    try {
      const res = await waitingListSeedDemo(200, true);
      if (res === null) {
        alert("Could not reach the Waiting-List service. Demo seed not applied — check System Admin.");
      }
      await refresh();
    } finally {
      setSeeding(false);
    }
  }

  async function purgeDemo() {
    setPurging(true);
    try {
      const res = await waitingListPurgeDemo();
      if (res === null) {
        alert("Could not reach the Waiting-List service. Demo entries were not purged — check System Admin.");
      }
      await refresh();
    } finally {
      setPurging(false);
    }
  }

  const rows = summary?.specialties ?? [];
  const totals = summary?.totals;

  const hasData = rows.some((r) => r.total > 0);

  const priorityChartData = useMemo(() => {
    return rows
      .filter((r) => r.total > 0)
      .slice(0, 12)
      .map((r) => ({
        specialty: r.specialty.split(" ").slice(0, 2).join(" "),
        urgent: r.by_priority.urgent,
        soon: r.by_priority.soon,
        routine: r.by_priority.routine,
        planned: r.by_priority.planned,
        breaches: r.breach_count,
      }));
  }, [rows]);

  const demoCount = summary?.totals.demo_count ?? 0;
  const liveCount = summary?.totals.live_count ?? 0;
  const hasDemoData = (summary?.totals.has_demo_data ?? false) || demoCount > 0;

  return (
    <div className="space-y-4">
      {/* Header + actions */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            <Building2 className="w-5 h-5 text-blue-500" />
            Waiting List — Department Summary
          </h2>
          <p className="text-[11px] text-text-secondary">
            NTPF-style roster across {rows.filter((r) => r.total > 0).length} active specialties.
            Targets per HSE SDU bands (urgent ≤10d, soon ≤30d, routine per specialty target, planned 12mo).
            {summary?.generated_at && (
              <span className="text-text-muted"> · refreshed {new Date(summary.generated_at).toLocaleTimeString()}</span>
            )}
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          {/* Live-only toggle */}
          <label
            className={`inline-flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg border cursor-pointer transition-colors ${
              liveOnly
                ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-600 dark:text-emerald-400"
                : "bg-bg-card border-border text-text-secondary hover:text-text-primary"
            }`}
            title="When ON, demo-seeded entries are excluded. Only real admissions and referrals received since sim start are shown."
          >
            <input
              type="checkbox"
              checked={liveOnly}
              onChange={(e) => setLiveOnly(e.target.checked)}
              className="sr-only"
            />
            <Radio className={`w-3.5 h-3.5 ${liveOnly ? "animate-pulse" : ""}`} />
            Live only
            <span className="font-mono-clinical text-[10px] opacity-80">
              {liveOnly ? "ON" : "OFF"}
            </span>
          </label>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            aria-label={loading ? "Refreshing waiting list" : "Refresh waiting list"}
            className="px-3 py-1.5 text-xs rounded-lg bg-blue-500/20 text-blue-700 dark:text-blue-200 border border-blue-500/40 hover:bg-blue-500/30 hover:border-blue-500/60 flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" /> Refresh
          </button>
          {hasDemoData && (
            <button
              type="button"
              onClick={purgeDemo}
              disabled={purging}
              className="px-3 py-1.5 text-xs rounded-lg bg-rose-500/15 text-rose-700 dark:text-rose-200 hover:bg-rose-500/25 border border-rose-500/40 hover:border-rose-500/60 flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              title="Remove all demo_seed entries; preserves live admissions and referrals."
            >
              {purging ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />}
              Purge demo ({demoCount})
            </button>
          )}
          <button
            type="button"
            onClick={seedDemo}
            disabled={seeding}
            className="px-3 py-1.5 text-xs rounded-lg bg-purple-500/15 text-purple-700 dark:text-purple-200 hover:bg-purple-500/25 border border-purple-500/40 hover:border-purple-500/60 flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            title="Seed 200 synthetic Irish waiting-list patients (tagged 'demo_seed'). For demos only — live filter hides them."
          >
            {seeding ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <ClipboardList className="w-3.5 h-3.5" aria-hidden="true" />}
            Seed demo
          </button>
        </div>
      </div>

      {/* Data source banner */}
      {hasDemoData && !liveOnly && (
        <div className="bg-amber-500/10 border border-amber-500/40 rounded-xl p-3 flex items-start gap-3">
          <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
          <div className="text-[12px] text-text-primary flex-1">
            <span className="font-semibold">Mixed data:</span>{" "}
            <span className="text-text-secondary">
              {liveCount} live · {demoCount} demo-seeded. Turn on
              <span className="font-mono mx-1 px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-600 dark:text-emerald-400">Live only</span>
              to hide synthetic entries, or click <span className="font-mono">Purge demo</span> to remove them.
            </span>
          </div>
        </div>
      )}
      {liveOnly && demoCount > 0 && (
        <div className="bg-emerald-500/10 border border-emerald-500/40 rounded-xl p-3 flex items-start gap-3">
          <Radio className="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5 animate-pulse" />
          <div className="text-[12px] text-text-primary flex-1">
            <span className="font-semibold">Live only:</span>{" "}
            <span className="text-text-secondary">
              showing {liveCount} entry{liveCount === 1 ? "" : "ies"} from real simulation events.
              {demoCount} demo-seeded entr{demoCount === 1 ? "y is" : "ies are"} hidden.
            </span>
          </div>
        </div>
      )}

      {/* Totals strip */}
      {totals && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Stat label="Total waiting" value={totals.grand_total} icon={<Users className="w-4 h-4 text-text-secondary" />} />
          <Stat
            label="Breaches"
            value={totals.total_breaches}
            sub={`${(totals.breach_rate * 100).toFixed(1)}% breach rate`}
            colour={totals.total_breaches > 0 ? "text-rose-500" : undefined}
            icon={<AlertTriangle className="w-4 h-4 text-rose-500" />}
          />
          <Stat
            label="Specialties in breach"
            value={totals.specialties_with_breaches}
            sub={`of ${rows.filter((r) => r.total > 0).length} active`}
            colour={totals.specialties_with_breaches > 0 ? "text-amber-500" : undefined}
          />
          <Stat
            label="High 90d risk (≥0.5)"
            value={totals.total_high_risk}
            colour={totals.total_high_risk > 0 ? "text-rose-500" : undefined}
            icon={<Activity className="w-4 h-4 text-rose-500" />}
          />
          <Stat
            label="Active specialties"
            value={rows.filter((r) => r.total > 0).length}
            sub={`max wait ${Math.max(0, ...rows.map((r) => r.oldest_wait_days))}d`}
          />
        </div>
      )}

      {!hasData && !loading && (
        <div className="bg-bg-card border border-border rounded-xl p-6 text-center">
          <Radio className="w-6 h-6 text-emerald-500 mx-auto mb-2 animate-pulse" />
          <p className="font-semibold text-text-primary text-sm">
            {liveOnly ? "No live waiting-list entries yet." : "No waiting-list entries."}
          </p>
          <p className="text-xs text-text-secondary mt-1 max-w-xl mx-auto">
            {liveOnly ? (
              <>
                This page only shows entries created by real simulation events — admissions forwarded
                from the Digital Twin (<span className="font-mono">/notify-admission</span>) and referrals via
                <span className="font-mono"> /waiting-list/add</span>. As the sim runs, elective admissions
                with acuity ≤3 will populate this list automatically. To preview the UI with synthetic data,
                turn off <span className="font-mono">Live only</span> and click <span className="font-mono">Seed demo</span>.
              </>
            ) : (
              <>
                Click <span className="font-mono">Seed demo</span> above to populate a realistic 200-patient list
                across all 12 Irish specialties, or add patients via the Priority Scoring tab.
              </>
            )}
          </p>
        </div>
      )}

      {/* Priority distribution chart */}
      {hasData && (
        <div className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="text-sm font-semibold text-text-primary mb-2">Priority distribution per specialty</h3>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={priorityChartData} margin={{ top: 8, right: 16, left: 0, bottom: 24 }}>
                <CartesianGrid {...CHART_GRID_PROPS} />
                <XAxis dataKey="specialty" tick={{ fontSize: 10, fill: "#94a3b8" }} angle={-22} textAnchor="end" height={60} interval={0} />
                <YAxis tick={{ fontSize: 10, fill: "#94a3b8" }} />
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                <Bar dataKey="urgent" stackId="p" fill={P_COLORS.urgent} />
                <Bar dataKey="soon" stackId="p" fill={P_COLORS.soon} />
                <Bar dataKey="routine" stackId="p" fill={P_COLORS.routine} />
                <Bar dataKey="planned" stackId="p" fill={P_COLORS.planned} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex items-center gap-4 text-[11px] mt-2 flex-wrap">
            <Legend colour={P_COLORS.urgent} label="Urgent" />
            <Legend colour={P_COLORS.soon} label="Soon" />
            <Legend colour={P_COLORS.routine} label="Routine" />
            <Legend colour={P_COLORS.planned} label="Planned" />
          </div>
        </div>
      )}

      {/* Department table */}
      {hasData && (
        <div className="bg-bg-card border border-border rounded-xl overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-[10px] uppercase tracking-wider text-text-secondary bg-bg-primary border-b border-border sticky top-0 z-10">
              <tr>
                <th className="px-3 py-2 text-left w-8"></th>
                <th className="px-3 py-2 text-left">Specialty</th>
                <th className="px-3 py-2 text-center">Target</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2 text-center">Priority (U·S·R·P)</th>
                <th className="px-3 py-2 text-center">Wait bands</th>
                <th className="px-3 py-2 text-right">Mean</th>
                <th className="px-3 py-2 text-right">Median</th>
                <th className="px-3 py-2 text-right">P90</th>
                <th className="px-3 py-2 text-right">Breaches</th>
                <th className="px-3 py-2 text-right">Oldest</th>
                <th className="px-3 py-2 text-right">90d risk</th>
              </tr>
            </thead>
            <tbody>
              {rows.filter((r) => r.total > 0).map((row) => {
                const isOpen = expanded[row.specialty] ?? false;
                const breachPct = row.total > 0 ? (row.breach_rate * 100) : 0;
                const breachColour = breachPct >= 30 ? "text-rose-500" : breachPct >= 10 ? "text-amber-500" : "text-emerald-500";
                const oldestBreach = row.oldest_wait_days > row.target_wait_weeks * 7;
                return (
                  <>
                    <tr
                      key={row.specialty}
                      onClick={() => setExpanded((p) => ({ ...p, [row.specialty]: !isOpen }))}
                      className={`border-t border-border/40 cursor-pointer transition-colors ${
                        isOpen
                          ? "bg-blue-500/10 hover:bg-blue-500/15"
                          : "hover:bg-bg-primary/60"
                      }`}
                    >
                      <td className={`px-3 py-2 ${isOpen ? "border-l-2 border-blue-500" : ""}`}>
                        {isOpen
                          ? <ChevronDown className="w-3.5 h-3.5 text-blue-500" />
                          : <ChevronRight className="w-3.5 h-3.5 text-text-secondary" />}
                      </td>
                      <td className="px-3 py-2">
                        <div className="font-medium text-text-primary">{row.specialty}</div>
                        <div className="text-[10px] text-text-secondary">
                          {(row.inpatient_pct * 100).toFixed(0)}% inpatient mix
                        </div>
                      </td>
                      <td className="px-3 py-2 text-center text-xs text-text-primary font-mono-clinical">{row.target_wait_weeks}w</td>
                      <td className="px-3 py-2 text-right font-mono-clinical font-semibold text-text-primary">{row.total}</td>
                      <td className="px-3 py-2 text-center">
                        <MiniBar parts={[
                          { n: row.by_priority.urgent, c: P_COLORS.urgent },
                          { n: row.by_priority.soon, c: P_COLORS.soon },
                          { n: row.by_priority.routine, c: P_COLORS.routine },
                          { n: row.by_priority.planned, c: P_COLORS.planned },
                        ]} total={row.total} />
                        <div className="text-[9px] text-text-secondary font-mono-clinical mt-0.5">
                          {row.by_priority.urgent} · {row.by_priority.soon} · {row.by_priority.routine} · {row.by_priority.planned}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-center">
                        <MiniBar parts={[
                          { n: row.by_wait_bucket.le_6w, c: "#22C55E" },
                          { n: row.by_wait_bucket["6_12w"], c: "#84CC16" },
                          { n: row.by_wait_bucket["3_6m"], c: "#F59E0B" },
                          { n: row.by_wait_bucket["6_12m"], c: "#EF4444" },
                          { n: row.by_wait_bucket.gt_12m, c: "#7F1D1D" },
                        ]} total={row.total} />
                        <div className="text-[9px] text-text-secondary font-mono-clinical mt-0.5">
                          ≤6w · 6-12w · 3-6m · 6-12m · &gt;12m
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono-clinical text-text-primary">{row.mean_wait_days.toFixed(0)}d</td>
                      <td className="px-3 py-2 text-right font-mono-clinical text-text-primary">{row.median_wait_days.toFixed(0)}d</td>
                      <td className="px-3 py-2 text-right font-mono-clinical text-text-secondary">{row.p90_wait_days}d</td>
                      <td className={`px-3 py-2 text-right font-mono-clinical font-semibold ${breachColour}`}>
                        {row.breach_count}
                        <span className="text-[9px] text-text-secondary ml-1">{breachPct.toFixed(0)}%</span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className={`font-mono-clinical font-semibold ${oldestBreach ? "text-rose-500" : "text-text-primary"}`}>
                          {row.oldest_wait_days}d
                        </div>
                        <div className="text-[9px] text-text-secondary font-mono-clinical">#{row.oldest_patient_id ?? "—"}</div>
                      </td>
                      <td className={`px-3 py-2 text-right font-mono-clinical font-semibold ${row.mean_deterioration_risk_90d >= 0.5 ? "text-rose-500" : "text-text-primary"}`}>
                        {row.mean_deterioration_risk_90d.toFixed(2)}
                        <div className="text-[9px] text-text-secondary font-normal">
                          {row.high_risk_count} ≥0.5
                        </div>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-blue-500/5 border-b border-border/40">
                        <td colSpan={12} className="px-4 py-3 border-l-2 border-blue-500">
                          <SpecialtyDetail row={row} liveOnly={liveOnly} />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SpecialtyDetail({ row, liveOnly }: { row: WaitlistSpecialtyRow; liveOnly: boolean }) {
  const [fullList, setFullList] = useState<any[] | null>(null);
  const [showAll, setShowAll] = useState(false);

  async function loadFull() {
    const list = await waitingListBySpecialty(row.specialty, liveOnly);
    setFullList(list || []);
    setShowAll(true);
  }

  const visible = showAll && fullList ? fullList : row.top_patients;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold text-text-primary uppercase tracking-wider">
          {showAll ? `All ${fullList?.length ?? 0}` : "Top 5 priority"} patients — {row.specialty}
        </h4>
        {!showAll && row.total > row.top_patients.length && (
          <button
            onClick={loadFull}
            className="text-[11px] text-blue-500 hover:text-blue-600 dark:text-blue-400 dark:hover:text-blue-300 flex items-center gap-1 font-medium"
          >
            Show all {row.total} <ChevronRight className="w-3 h-3" />
          </button>
        )}
      </div>
      {visible.length === 0 ? (
        <p className="text-xs text-text-secondary">No patients.</p>
      ) : (
        <div className="overflow-x-auto rounded border border-border bg-bg-card">
          <table className="w-full text-[11px]">
            <thead className="text-text-secondary text-[10px] uppercase tracking-wider bg-bg-primary">
              <tr className="border-b border-border">
                <th className="px-2 py-1.5 text-left">Patient</th>
                <th className="px-2 py-1.5 text-left">Procedure</th>
                <th className="px-2 py-1.5 text-center">Source</th>
                <th className="px-2 py-1.5 text-center">Status</th>
                <th className="px-2 py-1.5 text-center">Priority</th>
                <th className="px-2 py-1.5 text-right">Score</th>
                <th className="px-2 py-1.5 text-right">Wait</th>
                <th className="px-2 py-1.5 text-right">Target</th>
                <th className="px-2 py-1.5 text-right">90d risk</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p: any) => {
                const pri = p.priority_level || (p.priority || {}).priority_level || "routine";
                const score = p.composite_priority ?? (p.priority || {}).composite_priority ?? 0;
                const target = p.target_wait_days ?? row.target_wait_weeks * 7;
                const waitDays = p.wait_days ?? 0;
                const breach = waitDays > target;
                const risk = p.deterioration_risk_90d ?? 0;
                const source = (p.source || "live") as string;
                return (
                  <tr key={p.patient_id} className="border-b border-border/40 hover:bg-bg-primary/50">
                    <td className="px-2 py-1.5 font-mono-clinical text-text-primary">#{p.patient_id}</td>
                    <td className="px-2 py-1.5 text-text-primary">{p.procedure || p.procedure_requested || "—"}</td>
                    <td className="px-2 py-1.5 text-center">
                      <SourceBadge source={source} />
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <StatusBadge status={p.status || "waiting"} />
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <span
                        className="inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase"
                        style={{
                          backgroundColor: (P_COLORS[pri] || "#64748b") + "20",
                          color: P_COLORS[pri] || "#64748b",
                          border: `1px solid ${(P_COLORS[pri] || "#64748b")}66`,
                        }}
                      >
                        {pri}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono-clinical text-text-primary">
                      {(score * 100).toFixed(0)}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono-clinical font-semibold ${breach ? "text-rose-500" : "text-text-primary"}`}>
                      {waitDays}d
                      {breach && <AlertTriangle className="inline w-3 h-3 ml-1 text-rose-500" />}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono-clinical text-text-secondary">{target}d</td>
                    <td className={`px-2 py-1.5 text-right font-mono-clinical font-semibold ${risk >= 0.5 ? "text-rose-500" : "text-text-primary"}`}>
                      {(risk * 100).toFixed(0)}%
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

function SourceBadge({ source }: { source: string }) {
  if (source === "demo_seed") {
    return (
      <span
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] uppercase font-semibold border bg-purple-500/15 text-purple-600 dark:text-purple-300 border-purple-500/40"
        title="Synthetic entry from /waiting-list/seed-demo"
      >
        demo
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] uppercase font-semibold border bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40"
      title="Live entry — created by a real simulation event"
    >
      <span className="w-1 h-1 rounded-full bg-emerald-500 animate-pulse" />
      live
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const palette: Record<string, { bg: string; fg: string; bd: string }> = {
    waiting:     { bg: "#94a3b822", fg: "#475569", bd: "#94a3b866" },
    scheduled:   { bg: "#3b82f622", fg: "#1d4ed8", bd: "#3b82f666" },
    completed:   { bg: "#10b98122", fg: "#047857", bd: "#10b98166" },
    cancelled:   { bg: "#64748b22", fg: "#64748b", bd: "#64748b66" },
    deteriorated:{ bg: "#ef444422", fg: "#b91c1c", bd: "#ef444466" },
  };
  const p = palette[status] || palette.waiting;
  return (
    <span
      className="inline-block px-1.5 py-0.5 rounded text-[9px] uppercase font-semibold border"
      style={{ backgroundColor: p.bg, color: p.fg, borderColor: p.bd }}
    >
      {status}
    </span>
  );
}

function MiniBar({ parts, total }: { parts: Array<{ n: number; c: string }>; total: number }) {
  if (total === 0) return <div className="text-[10px] text-text-secondary">—</div>;
  return (
    <div className="flex w-[130px] h-2.5 rounded overflow-hidden border border-border bg-bg-primary mx-auto" title={`${total} total`}>
      {parts.map((p, i) => (
        p.n > 0 ? (
          <div key={i} style={{ width: `${(p.n / total) * 100}%`, backgroundColor: p.c }} title={`${p.n}`} />
        ) : null
      ))}
    </div>
  );
}

function Stat({
  label, value, sub, colour, icon,
}: {
  label: string; value: number | string; sub?: string; colour?: string; icon?: React.ReactNode;
}) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-3">
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-text-secondary font-medium">{label}</div>
        {icon}
      </div>
      <div className={`mt-1 text-2xl font-bold font-mono-clinical ${colour || "text-text-primary"}`}>{value}</div>
      {sub && <div className="text-[10px] text-text-secondary mt-0.5">{sub}</div>}
    </div>
  );
}

function Legend({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-text-secondary">
      <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: colour }} />
      {label}
    </span>
  );
}

/* =========================================================================
 * Priority Tab — existing scoring form (kept from previous version)
 * ========================================================================= */
function PriorityTab() {
  const [age, setAge] = useState(65);
  const [gender, setGender] = useState("F");
  const [spec, setSpec] = useState("Medicine");
  const [proc, setProc] = useState("Medical consultation");
  const [urg, setUrg] = useState("routine");
  const [charlson, setCharlson] = useState(2);
  const [pain, setPain] = useState(5);
  const [func, setFunc] = useState("moderate");
  const [region, setRegion] = useState("");
  const [pRes, setPRes] = useState<PriorityResult | null>(null);
  const [pLoading, setPLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scorePriority = async () => {
    setPLoading(true);
    try {
      const r = await fetch("/api/waitlist/score-priority", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patient_id: 1, specialty: spec, procedure_requested: proc,
          clinical_urgency: urg, age, gender, comorbidity_count: charlson,
          charlson_score: charlson, pain_score: pain,
          functional_impact: func, geographic_region: region,
        }),
      });
      const d = await r.json();
      if (d.status === "ok") setPRes(d.data);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setPLoading(false);
  };

  const breakdown = pRes ? [
    { name: "Clinical", value: pRes.clinical_urgency_score, fill: "#DC2626" },
    { name: "Functional", value: pRes.functional_impact_score, fill: "#F59E0B" },
    { name: "Temporal", value: pRes.temporal_score, fill: "#3B82F6" },
    { name: "Equity", value: Math.max(0, pRes.equity_modifier + 0.2), fill: "#8B5CF6" },
  ] : [];

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-2 rounded-lg text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300 ml-4">&times;</button>
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <ClipboardList className="w-4 h-4 text-blue-400" /> Patient Priority Assessment
          </h2>
          <div className="grid grid-cols-2 gap-2">
            <Fld label="Specialty">
              <select value={spec} onChange={e => setSpec(e.target.value)} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary">
                {SPECIALTIES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </Fld>
            <Fld label="Procedure">
              <input value={proc} onChange={e => setProc(e.target.value)} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary" />
            </Fld>
            <Fld label="Age">
              <input type="number" value={age} onChange={e => setAge(+e.target.value)} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical" />
            </Fld>
            <Fld label="Gender">
              <select value={gender} onChange={e => setGender(e.target.value)} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary">
                <option value="M">Male</option><option value="F">Female</option>
              </select>
            </Fld>
            <Fld label="Clinical Urgency">
              <select value={urg} onChange={e => setUrg(e.target.value)} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary">
                <option value="urgent">Urgent</option><option value="soon">Soon</option>
                <option value="routine">Routine</option><option value="planned">Planned</option>
              </select>
            </Fld>
            <Fld label="Charlson Score">
              <input type="number" value={charlson} onChange={e => setCharlson(+e.target.value)} min={0} max={20} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical" />
            </Fld>
            <Fld label="Pain (0-10)">
              <input type="number" value={pain} onChange={e => setPain(+e.target.value)} min={0} max={10} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical" />
            </Fld>
            <Fld label="Functional Impact">
              <select value={func} onChange={e => setFunc(e.target.value)} className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary">
                <option value="none">None</option><option value="mild">Mild</option>
                <option value="moderate">Moderate</option><option value="severe">Severe</option>
                <option value="complete">Complete</option>
              </select>
            </Fld>
            <Fld label="Region">
              <input value={region} onChange={e => setRegion(e.target.value)} placeholder="e.g. rural" className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary" />
            </Fld>
          </div>
          <button onClick={scorePriority} disabled={pLoading}
            className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-medium py-2.5 rounded-lg transition-colors text-sm mt-3">
            {pLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {pLoading ? "Scoring..." : "Calculate Priority"}
          </button>
        </div>

        <div className="lg:col-span-3 bg-bg-card rounded-xl border border-border p-4">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Priority Score Result</h2>
          {pRes ? (
            <div className="space-y-4">
              <div className="text-center py-4 bg-bg-primary rounded-lg">
                <div className="font-mono-clinical text-5xl font-bold" style={{ color: P_COLORS[pRes.priority_level] }}>
                  {(pRes.composite_priority * 100).toFixed(0)}
                </div>
                <div className="text-[10px] text-text-muted mt-1">out of 100</div>
                <div className="inline-flex items-center mt-2 px-3 py-1 rounded-full text-[11px] font-semibold uppercase"
                  style={{ backgroundColor: P_COLORS[pRes.priority_level] + "15",
                           border: `1px solid ${P_COLORS[pRes.priority_level]}40`,
                           color: P_COLORS[pRes.priority_level] }}>
                  {pRes.priority_level}
                </div>
              </div>
              <div style={{ height: 160 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={breakdown} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 0 }}>
                    <CartesianGrid {...CHART_GRID_PROPS} />
                    <XAxis type="number" domain={[0, 1]} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                    <YAxis type="category" dataKey="name" width={70} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} formatter={(v: number) => v.toFixed(3)} />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {breakdown.map((d, i) => <Cell key={i} fill={d.fill} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-20 text-text-muted text-[11px]">
              Enter patient details and click "Calculate Priority" to see score
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* =========================================================================
 * Referral Tab — existing (kept from previous version)
 * ========================================================================= */
function ReferralTab() {
  const [refText, setRefText] = useState("");
  const [rRes, setRRes] = useState<ReferralResult | null>(null);
  const [rLoading, setRLoading] = useState(false);
  const [rError, setRError] = useState<string | null>(null);
  const [age] = useState(65);
  const [gender] = useState("F");

  const triageRef = async () => {
    setRLoading(true);
    setRError(null);
    try {
      const r = await fetch("/api/waitlist/triage-referral", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ referral_text: refText, patient_age: age, patient_gender: gender }),
      });
      if (!r.ok) {
        setRError(`Triage service returned ${r.status}. Try again or check System Admin.`);
        return;
      }
      const d = await r.json();
      if (d.status === "ok") {
        setRRes(d.data);
      } else {
        setRError(d.error || "Triage service did not return a result.");
      }
    } catch (e) {
      setRError(e instanceof Error ? e.message : "Triage request failed.");
    } finally {
      setRLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
          <Search className="w-4 h-4 text-purple-400" /> Referral Letter NLP Triage
        </h2>
        <textarea value={refText} onChange={e => setRefText(e.target.value)}
          rows={16} placeholder="Paste referral letter text here..."
          className="w-full min-h-[300px] bg-bg-input border border-border rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder-slate-600 resize-none" />
        <button onClick={triageRef} disabled={rLoading || !refText.trim()}
          className="w-full flex items-center justify-center gap-2 bg-purple-600 hover:bg-purple-500 disabled:bg-purple-800 text-white font-medium py-2.5 rounded-lg transition-colors text-sm mt-3">
          {rLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          {rLoading ? "Processing..." : "Triage Referral"}
        </button>
      </div>

      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h2 className="text-sm font-semibold text-text-primary mb-3">Triage Result</h2>
        {rError && (
          <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/30 text-red-300 rounded-lg p-3 mb-3 text-xs">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <span>{rError}</span>
          </div>
        )}
        {rRes ? (
          <div className="space-y-3">
            <div className="text-center py-4 bg-bg-primary rounded-lg">
              <div className="inline-flex items-center px-4 py-2 rounded-full text-sm font-bold uppercase"
                style={{ backgroundColor: P_COLORS[rRes.urgency_classification] + "15",
                         border: `1px solid ${P_COLORS[rRes.urgency_classification]}40`,
                         color: P_COLORS[rRes.urgency_classification] }}>
                {rRes.urgency_classification}
              </div>
              <div className="text-[10px] text-text-muted mt-2">
                Confidence: {(rRes.urgency_confidence * 100).toFixed(0)}%
              </div>
            </div>
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="text-[10px] font-semibold text-text-muted mb-1">Quality Score</div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 bg-slate-700 rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-blue-500" style={{ width: `${rRes.referral_quality_score * 100}%` }} />
                </div>
                <span className="font-mono-clinical text-xs text-text-primary">
                  {(rRes.referral_quality_score * 100).toFixed(0)}%
                </span>
              </div>
            </div>
            {Object.keys(rRes.extracted_entities).length > 0 && (
              <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                <div className="text-[10px] font-semibold text-blue-400 mb-2">Extracted Entities</div>
                {Object.entries(rRes.extracted_entities).map(([k, v]) => (
                  <div key={k} className="text-[10px] text-text-secondary">
                    <span className="font-medium capitalize text-text-primary">{k}:</span>{" "}
                    {Array.isArray(v) ? v.join(", ") : String(v)}
                  </div>
                ))}
              </div>
            )}
            {rRes.missing_information.length > 0 && (
              <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3">
                <div className="text-[10px] font-semibold text-yellow-400 mb-1">Missing Information</div>
                {rRes.missing_information.map((m, i) => (
                  <div key={i} className="text-[10px] text-yellow-400/80">- {m}</div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center py-20 text-text-muted text-[11px]">
            Paste a referral letter and click "Triage" to analyze
          </div>
        )}
      </div>
    </div>
  );
}

function Fld({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-[10px] text-text-muted block mb-0.5">{label}</label>
      {children}
    </div>
  );
}
