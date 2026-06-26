import { useState, useEffect, useCallback, useMemo } from "react";
import { HighRiskPanel } from "../components/UpliftWidgets";
import {
  Search,
  User,
  Clock,
  Activity,
  FlaskConical,
  Pill,
  ArrowRight,
  AlertTriangle,
  Heart,
  Thermometer,
  Wind,
  Droplets,
  ChevronDown,
  Filter,
  Loader2,
  MapPin,
  Shuffle,
  Skull,
  Syringe,
  BedDouble,
  X,
  BarChart3,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceArea,
  ReferenceDot,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import type {
  PatientSummary,
  TimelineEvent,
  VitalSeries,
  LabPanel,
  MedicationRecord,
  JourneyPath,
  JourneyMetrics,
} from "../lib/api";
import {
  journeyPatientSummaryDetailed,
  journeyTimeline,
  journeyVitals,
  journeyLabs,
  journeyMedications,
  journeyPath,
  journeyMetrics,
} from "../lib/api";
import { getDeptColor } from "../lib/colors";
import SkeletonBlock from "../components/SkeletonBlock";

// ==================== Constants ====================

const SAMPLE_PATIENTS = [11818101, 11168491, 10312052];

const CATEGORY_COLORS: Record<string, string> = {
  movement: "#3B82F6",
  vital_sign: "#22C55E",
  laboratory: "#A855F7",
  medication: "#F97316",
  clinical: "#EF4444",
  procedure: "#EF4444",
  diagnosis: "#EC4899",
};

const CATEGORY_ICONS: Record<string, typeof Activity> = {
  movement: MapPin,
  vital_sign: Activity,
  laboratory: FlaskConical,
  medication: Pill,
  clinical: Syringe,
  procedure: Syringe,
  diagnosis: AlertTriangle,
};

const VITAL_CONFIG: Record<string, { color: string; icon: typeof Heart; label: string; unit: string }> = {
  "Heart Rate": { color: "#EF4444", icon: Heart, label: "Heart Rate", unit: "bpm" },
  "Respiratory Rate": { color: "#3B82F6", icon: Wind, label: "Resp Rate", unit: "/min" },
  "SpO2": { color: "#22C55E", icon: Droplets, label: "SpO2", unit: "%" },
  "SBP": { color: "#F97316", icon: Activity, label: "Systolic BP", unit: "mmHg" },
  "Temperature": { color: "#A855F7", icon: Thermometer, label: "Temperature", unit: "F" },
};

const LAB_PANELS = ["CBC", "BMP", "LFT", "Coag", "Cardiac"];

const MED_CATEGORY_COLORS: Record<string, string> = {
  antibiotic: "#EF4444",
  vasopressor: "#F97316",
  sedation: "#A855F7",
  analgesic: "#3B82F6",
  anticoagulant: "#EAB308",
  insulin: "#22C55E",
  antihypertensive: "#14B8A6",
  other: "#64748B",
};

function getMedCategoryColor(category: string): string {
  return MED_CATEGORY_COLORS[category?.toLowerCase()] || MED_CATEGORY_COLORS.other;
}


function formatDate(ts: string): string {
  try {
    return new Date(ts).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return ts;
  }
}

function formatHours(h: number): string {
  if (h < 24) return `${h.toFixed(1)}h`;
  const days = Math.floor(h / 24);
  const rem = h % 24;
  return `${days}d ${rem.toFixed(0)}h`;
}

// ==================== Sub-components ====================

function MetricCard({
  icon,
  label,
  value,
  color = "#3B82F6",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="bg-bg-card rounded-lg border border-border p-4 relative overflow-hidden">
      <div className="absolute top-0 left-0 w-1 h-full rounded-l-lg" style={{ backgroundColor: color }} />
      <div className="flex items-center gap-2 text-slate-400 mb-2">
        {icon}
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <div className="font-mono-clinical text-xl font-bold text-text-primary">{value}</div>
    </div>
  );
}

// ==================== Tab: Journey Walk-through ====================

/** Icon + colour per vital key, shared between Vitals tab and Walk-through. */
const WALK_VITAL_STYLE: Record<string, { color: string; label: string; unit: string }> = {
  hr:   { color: "#EF4444", label: "HR",   unit: "bpm" },
  rr:   { color: "#8B5CF6", label: "RR",   unit: "/min" },
  spo2: { color: "#3B82F6", label: "SpO₂", unit: "%" },
  sbp:  { color: "#F97316", label: "SBP",  unit: "mmHg" },
  dbp:  { color: "#6366F1", label: "DBP",  unit: "mmHg" },
  temp: { color: "#22C55E", label: "Temp", unit: "°C" },
};

interface WalkthroughSegment {
  order: number;
  careunit: string;
  eventtype: string;
  intime: string | null;
  outtime: string | null;
  ongoing: boolean;
  tStart: number;
  tEnd: number;
  vitalsInWindow: Record<string, SimTimeSeriesPoint[]>;
  labsInWindow: Record<string, SimTimeSeriesPoint[]>;
  medsInWindow: any[];
}

function WalkthroughTab({ simHadmId, staticAdmission }: {
  simHadmId: string | number | null | undefined;
  staticAdmission?: { admittime?: string; dischtime?: string; admission_type?: string };
}) {
  const journey = useSimJourney(simHadmId);

  // Compact admission banner still renders even before first poll lands
  if (!simHadmId) {
    return (
      <div className="text-center py-10 space-y-2">
        <p className="text-slate-300 text-sm">Walk-through view is optimised for live simulation patients.</p>
        <p className="text-slate-500 text-xs">Pick a SIM patient from the sidebar to see a sim-time walk from admission through discharge with live vitals, labs, bed moves, and transfers.</p>
      </div>
    );
  }
  if (!journey) {
    return (
      <div className="space-y-3">
        <SkeletonBlock className="h-16 w-full" />
        <SkeletonBlock className="h-48 w-full" />
        <SkeletonBlock className="h-32 w-full" />
      </div>
    );
  }

  const admission: any = journey.admission || staticAdmission || {};
  const win = journey.window;
  const carePath = journey.care_path || [];
  const vitalsSeries = journey.vitals_series || {};
  const labsSeries = journey.labs_series || {};
  const meds = journey.medications || [];
  const dx = journey.diagnoses || [];
  const procs: any[] = (journey as any).procedures || [];
  const transfers = journey.transfers || [];
  const timeline: Array<{ time?: string | null; event?: string; detail?: string }> = (journey as any).timeline || [];

  // ── Phase state ──────────────────────────────────────────────────────
  const nowMs = win?.end ? Date.parse(win.end) : Date.now();
  const admitMs = win?.start ? Date.parse(win.start) : (admission?.sim_admittime ? Date.parse(admission.sim_admittime) : nowMs);
  const elapsedMin = Math.max(0, Math.round((nowMs - admitMs) / 60000));
  const dischargeLocation = admission?.discharge_location;
  const isDischarged = !!dischargeLocation && admission?.status === "discharged";

  // Build segments from care_path with each one's time window
  const segments: WalkthroughSegment[] = carePath.map((seg, i) => {
    const start = seg.intime ? Date.parse(seg.intime) : admitMs;
    const end = seg.ongoing || !seg.outtime ? nowMs : Date.parse(seg.outtime);
    const inWindow = (pts: SimTimeSeriesPoint[]) =>
      pts.filter((p) => {
        const t = Date.parse(p.time);
        return Number.isFinite(t) && t >= start && t <= end;
      });
    const vw: Record<string, SimTimeSeriesPoint[]> = {};
    for (const [k, pts] of Object.entries(vitalsSeries)) vw[k] = inWindow(pts as any);
    const lw: Record<string, SimTimeSeriesPoint[]> = {};
    for (const [k, pts] of Object.entries(labsSeries)) lw[k] = inWindow(pts as any);
    const mw = meds.filter((m: any) => {
      const ts = m.sim_time ? Date.parse(m.sim_time) : NaN;
      return Number.isFinite(ts) ? ts >= start && ts <= end : i === 0; // unstamped meds fall into first segment
    });
    return {
      order: i + 1,
      careunit: seg.careunit || "Unknown",
      eventtype: seg.eventtype || "transfer",
      intime: seg.intime || null,
      outtime: seg.outtime || null,
      ongoing: !!seg.ongoing,
      tStart: start,
      tEnd: end,
      vitalsInWindow: vw,
      labsInWindow: lw,
      medsInWindow: mw,
    };
  });

  // If carePath is empty but we have an admission, synthesize a single "ED / Admission" segment
  if (segments.length === 0 && admission?.sim_admittime) {
    const start = Date.parse(admission.sim_admittime);
    const inWin = (pts: SimTimeSeriesPoint[]) =>
      (pts || []).filter((p) => Date.parse(p.time) >= start);
    const vw: Record<string, SimTimeSeriesPoint[]> = {};
    for (const [k, pts] of Object.entries(vitalsSeries)) vw[k] = inWin(pts as any);
    const lw: Record<string, SimTimeSeriesPoint[]> = {};
    for (const [k, pts] of Object.entries(labsSeries)) lw[k] = inWin(pts as any);
    segments.push({
      order: 1,
      careunit: admission?.admission_location || "Admission",
      eventtype: "admit",
      intime: admission.sim_admittime,
      outtime: null,
      ongoing: true,
      tStart: start,
      tEnd: nowMs,
      vitalsInWindow: vw,
      labsInWindow: lw,
      medsInWindow: meds,
    });
  }

  // Phase-chip derivation: current location = last ongoing segment's careunit
  const currentSegment = segments[segments.length - 1];
  const currentLocation = currentSegment?.careunit || "—";

  // ── Render ────────────────────────────────────────────────────────────
  return (
    <div className="animate-fade-in space-y-4">
      {/* Header */}
      <div className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
            </span>
            Live journey walk-through — {isDischarged ? "discharged" : "in progress"}
          </div>
          <div className="text-[11px] text-slate-500 font-mono-clinical">
            elapsed {Math.floor(elapsedMin / 60)}h {elapsedMin % 60}m · polling every 2.5 s
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 flex-wrap text-xs">
          <span className="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">
            Admit: {admission?.sim_admittime ? formatDate(admission.sim_admittime) : "—"}
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Type: {admission?.admission_type || "—"}
          </span>
          <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
            Location: {currentLocation}
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Transfers: {carePath.length}
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Vitals: {Object.values(vitalsSeries).reduce((s, v) => s + v.length, 0)} pts
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Labs: {Object.values(labsSeries).reduce((s, v) => s + v.length, 0)} pts
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Meds: {meds.length}
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Dx: {dx.length}
          </span>
          <span className="px-2 py-0.5 rounded bg-slate-700 text-slate-200 border border-slate-600">
            Procs: {procs.length}
          </span>
          {isDischarged && (
            <span className="px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/30">
              Discharge: {dischargeLocation}
            </span>
          )}
        </div>
      </div>

      {/* Phase chips — horizontal stepper */}
      <div className="bg-bg-card border border-border rounded-xl p-3">
        <div className="flex items-center gap-0 overflow-x-auto">
          <PhaseChip label="Arrival / Triage" active filled />
          <PhaseConnector />
          {segments.map((s, i) => (
            <div key={`ph-${i}`} className="flex items-center">
              <PhaseChip
                label={s.careunit}
                active={s.ongoing}
                filled
                subtitle={s.ongoing ? "current" : "completed"}
              />
              {i < segments.length - 1 && <PhaseConnector />}
            </div>
          ))}
          {isDischarged && (
            <>
              <PhaseConnector />
              <PhaseChip label="Discharge" active filled subtitle={dischargeLocation} />
            </>
          )}
        </div>
      </div>

      {/* Segment chapters */}
      <div className="space-y-3">
        {segments.map((seg, idx) => (
          <SegmentChapter
            key={`seg-${idx}`}
            segment={seg}
            admitMs={admitMs}
            totalElapsedMs={nowMs - admitMs}
          />
        ))}
      </div>

      {/* Diagnoses + Procedures sidebar-ish (two columns below chapters) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="text-xs uppercase tracking-wide text-slate-400 mb-2">Confirmed diagnoses ({dx.length})</h3>
          {dx.length === 0 ? <p className="text-slate-500 text-xs">No diagnoses recorded yet.</p> : (
            <ul className="space-y-1 text-[11px] max-h-56 overflow-y-auto">
              {dx.map((d: any, i: number) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="font-mono-clinical text-slate-400 min-w-[56px]">{d.icd_code}</span>
                  <span className="text-slate-300">{d.long_title || ""}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="text-xs uppercase tracking-wide text-slate-400 mb-2">Procedures ({procs.length})</h3>
          {procs.length === 0 ? <p className="text-slate-500 text-xs">No procedures recorded yet.</p> : (
            <ul className="space-y-1 text-[11px] max-h-56 overflow-y-auto">
              {procs.map((p: any, i: number) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="font-mono-clinical text-slate-400 min-w-[56px]">{p.icd_code}</span>
                  <span className="text-slate-300">{p.long_title || ""}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Unified chronological event stream */}
      <div className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="text-xs uppercase tracking-wide text-slate-400 mb-3">Event stream ({timeline.length})</h3>
        {timeline.length === 0 ? (
          <p className="text-slate-500 text-xs">No events yet — waiting for the orchestrator cascade.</p>
        ) : (
          <div className="max-h-72 overflow-y-auto">
            <ul className="space-y-1 text-[11px]">
              {timeline.map((e, i) => (
                <li key={i} className="flex items-start gap-3 border-b border-border/50 pb-1">
                  <span className="font-mono-clinical text-slate-500 w-32 shrink-0">
                    {e.time ? formatDate(e.time) : "—"}
                  </span>
                  <span className={`shrink-0 w-20 ${eventBadgeColor(e.event)}`}>{e.event}</span>
                  <span className="text-slate-300">{e.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function eventBadgeColor(evt?: string): string {
  switch (evt) {
    case "admission": return "text-emerald-400";
    case "transfer":  return "text-blue-400";
    case "discharge": return "text-rose-400";
    case "medication": return "text-violet-400";
    case "diagnosis":  return "text-amber-400";
    case "procedure":  return "text-cyan-400";
    default: return "text-slate-400";
  }
}

function PhaseChip({ label, active, filled, subtitle }: { label: string; active?: boolean; filled?: boolean; subtitle?: string | null }) {
  return (
    <div className="flex flex-col items-center min-w-[96px] px-2">
      <div
        className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold ${
          active
            ? "bg-emerald-500/20 border-2 border-emerald-400 text-emerald-300"
            : filled
              ? "bg-blue-500/20 border-2 border-blue-500 text-blue-300"
              : "bg-slate-700 border-2 border-slate-600 text-slate-400"
        }`}
      >
        {active ? <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" /> : "✓"}
      </div>
      <div className={`text-[10px] mt-1 text-center leading-tight ${active ? "text-emerald-300" : "text-slate-300"}`}>
        {label}
      </div>
      {subtitle && <div className="text-[9px] text-slate-500">{subtitle}</div>}
    </div>
  );
}

function PhaseConnector() {
  return <div className="flex-1 h-[2px] min-w-8 bg-slate-700" />;
}

function SegmentChapter({ segment, admitMs, totalElapsedMs }: { segment: WalkthroughSegment; admitMs: number; totalElapsedMs: number }) {
  const durMin = Math.max(0, Math.round((segment.tEnd - segment.tStart) / 60000));
  const offsetMin = Math.max(0, Math.round((segment.tStart - admitMs) / 60000));
  const widthPct = totalElapsedMs > 0 ? Math.max(4, ((segment.tEnd - segment.tStart) / totalElapsedMs) * 100) : 100;
  const leftPct  = totalElapsedMs > 0 ? ((segment.tStart - admitMs) / totalElapsedMs) * 100 : 0;

  return (
    <div className={`bg-bg-card border rounded-xl p-4 ${segment.ongoing ? "border-emerald-500/40" : "border-border"}`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded-full bg-slate-700 text-white text-xs font-bold flex items-center justify-center">{segment.order}</span>
            <h3 className="text-white text-sm font-semibold">{segment.careunit}</h3>
            {segment.ongoing && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">live</span>
            )}
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">{segment.eventtype}</span>
          </div>
          <div className="text-[10px] text-slate-500 mt-1 font-mono-clinical">
            {segment.intime ? formatDate(segment.intime) : "—"}
            {segment.ongoing ? " → now" : segment.outtime ? ` → ${formatDate(segment.outtime)}` : ""}
            {"  ·  "}duration {Math.floor(durMin / 60)}h {durMin % 60}m
            {"  ·  "}offset +{Math.floor(offsetMin / 60)}h {offsetMin % 60}m from admission
          </div>
        </div>
      </div>

      {/* Timeline bar */}
      <div className="mt-3 h-2 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${segment.ongoing ? "bg-emerald-500/80" : "bg-blue-500/70"}`}
          style={{ marginLeft: `${Math.min(99, leftPct)}%`, width: `${Math.min(100 - leftPct, widthPct)}%` }}
        />
      </div>

      {/* Vitals mini-grid during this segment */}
      <div className="mt-4 grid grid-cols-2 md:grid-cols-3 gap-2">
        {Object.entries(WALK_VITAL_STYLE).map(([key, style]) => {
          const pts = (segment.vitalsInWindow[key] || []).map((p) => ({ t: Date.parse(p.time), v: Number(p.value) }))
            .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v));
          const vals = pts.map((p) => p.v);
          const last = pts[pts.length - 1];
          const min = vals.length ? Math.min(...vals) : null;
          const max = vals.length ? Math.max(...vals) : null;
          return (
            <div key={key} className="bg-bg-primary border border-border rounded px-2 py-2">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-slate-300 flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full" style={{ background: style.color }} />
                  {style.label}
                </span>
                <span className="font-mono-clinical text-slate-100">
                  {last ? (Number.isInteger(last.v) ? last.v : last.v.toFixed(1)) : "—"}
                  <span className="text-slate-500 text-[9px] ml-0.5">{style.unit}</span>
                </span>
              </div>
              <div style={{ height: 32 }} className="mt-1">
                {pts.length >= 2 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={pts} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
                      <Line type="monotone" dataKey="v" stroke={style.color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
                      <YAxis hide domain={["dataMin", "dataMax"]} />
                    </LineChart>
                  </ResponsiveContainer>
                ) : pts.length === 1 ? (
                  <div className="h-full flex items-center justify-center text-[9px] text-slate-500">single sample</div>
                ) : (
                  <div className="h-full flex items-center justify-center text-[9px] text-slate-600">—</div>
                )}
              </div>
              {min !== null && max !== null && pts.length > 0 && (
                <div className="flex justify-between text-[8px] text-slate-500 font-mono-clinical mt-0.5">
                  <span>min {min.toFixed(1)}</span>
                  <span>{pts.length} pts</span>
                  <span>max {max.toFixed(1)}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Labs + Meds for this segment */}
      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <h4 className="text-[11px] uppercase tracking-wide text-slate-400 mb-2">Labs during this segment</h4>
          {Object.keys(segment.labsInWindow).length === 0 ? (
            <p className="text-[11px] text-slate-500">No labs resulted in this window.</p>
          ) : (
            <ul className="space-y-0.5 text-[11px] max-h-32 overflow-y-auto">
              {Object.entries(segment.labsInWindow).flatMap(([name, pts]) =>
                (pts || []).map((p, i) => {
                  const flagCls = p.flag && p.flag !== "normal" && p.flag !== "" ? "text-amber-400" : "text-slate-300";
                  return (
                    <li key={`${name}-${i}`} className="flex items-center gap-2">
                      <span className="font-mono-clinical text-slate-500 w-24">{formatDate(p.time)}</span>
                      <span className="text-slate-400 w-28 truncate">{name}</span>
                      <span className={`font-mono-clinical ${flagCls}`}>
                        {typeof p.value === "number" ? p.value.toFixed(2) : p.value} {p.unit || ""}
                      </span>
                    </li>
                  );
                })
              )}
            </ul>
          )}
        </div>
        <div>
          <h4 className="text-[11px] uppercase tracking-wide text-slate-400 mb-2">Medications ({segment.medsInWindow.length})</h4>
          {segment.medsInWindow.length === 0 ? (
            <p className="text-[11px] text-slate-500">No medications in this window.</p>
          ) : (
            <ul className="space-y-0.5 text-[11px] max-h-32 overflow-y-auto">
              {segment.medsInWindow.slice(-30).map((m: any, i: number) => (
                <li key={i} className="flex items-center gap-2">
                  <span className="font-mono-clinical text-slate-500 w-24">
                    {m.sim_time ? formatDate(m.sim_time) : "—"}
                  </span>
                  <span className="text-slate-200 truncate">{m.drug || m.drug_type || "—"}</span>
                  <span className="text-slate-500">
                    {m.dose_val_rx} {m.dose_unit_rx} {m.route}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ==================== Tab: Timeline ====================

function TimelineTab({
  subjectId,
  hadmId,
}: {
  subjectId: number;
  hadmId: number;
}) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [limit, setLimit] = useState(200);
  const [filters, setFilters] = useState<Record<string, boolean>>({
    movement: true,
    vital_sign: true,
    laboratory: true,
    medication: true,
    clinical: true,
  });

  const load = useCallback(
    async (lim: number) => {
      setLoading(true);
      const res = await journeyTimeline(subjectId, hadmId, undefined, lim);
      if (res) setEvents(res.events);
      setLoaded(true);
      setLoading(false);
    },
    [subjectId, hadmId]
  );

  // auto-load on mount / when admission changes
  useEffect(() => {
    load(limit);
  }, [subjectId, hadmId]);

  const filtered = useMemo(
    () => events.filter((e) => filters[e.category] !== false),
    [events, filters]
  );

  const toggleFilter = (cat: string) =>
    setFilters((f) => ({ ...f, [cat]: !f[cat] }));

  if (loading && !loaded) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <SkeletonBlock key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <Filter className="w-4 h-4 text-slate-400" />
        {Object.keys(filters).map((cat) => (
          <button
            key={cat}
            onClick={() => toggleFilter(cat)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
              filters[cat]
                ? "border-transparent text-white"
                : "border-border text-slate-500 bg-transparent"
            }`}
            style={
              filters[cat]
                ? { backgroundColor: CATEGORY_COLORS[cat] + "30", color: CATEGORY_COLORS[cat] }
                : undefined
            }
          >
            {cat.replace("_", " ")}
          </button>
        ))}
      </div>

      {filtered.length === 0 && loaded && (
        <p className="text-slate-500 text-center py-8">No events match current filters.</p>
      )}

      {/* Event list */}
      <div className="relative ml-4 border-l-2 border-border space-y-1">
        {filtered.slice(0, limit).map((evt, i) => {
          const Icon = CATEGORY_ICONS[evt.category] || Activity;
          const color = CATEGORY_COLORS[evt.category] || "#64748B";
          const det = evt.details || {};
          // For ICD-coded events show "CODE — Long Title" prominently;
          // dump the remaining kv pairs as a secondary detail line.
          const isCoded = evt.event_type === "diagnosis" || evt.event_type === "procedure";
          const headlineLabel = isCoded
            ? (() => {
                const code = det.icd_code ?? "";
                const title = det.long_title ?? "";
                if (code && title) return `${code} — ${title}`;
                if (title) return String(title);
                if (code) return `ICD ${code}`;
                return "";
              })()
            : "";
          const detailStr = Object.entries(det)
            .filter(([k]) => k !== "timestamp" && !(isCoded && (k === "icd_code" || k === "long_title")))
            .map(([k, v]) => `${k}: ${v}`)
            .join(" | ");
          return (
            <div key={i} className="relative pl-6 py-2 group hover:bg-slate-800/30 rounded-r-lg transition-colors">
              <div
                className="absolute -left-[9px] top-3 w-4 h-4 rounded-full border-2 flex items-center justify-center"
                style={{ borderColor: color, backgroundColor: "var(--color-timeline-dot-bg)" }}
              >
                <Icon className="w-2.5 h-2.5" style={{ color }} />
              </div>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-xs font-semibold uppercase tracking-wide"
                      style={{ color }}
                    >
                      {evt.event_type}
                    </span>
                    <span className="text-[10px] text-slate-500">{evt.source_table}</span>
                  </div>
                  {headlineLabel && (
                    <p className="text-[12px] text-slate-200 mt-0.5 truncate max-w-xl">
                      {headlineLabel}
                    </p>
                  )}
                  {detailStr && (
                    <p className="text-xs text-slate-400 mt-0.5 truncate max-w-xl">{detailStr}</p>
                  )}
                </div>
                <span className="font-mono-clinical text-[11px] text-slate-500 whitespace-nowrap shrink-0">
                  {formatDate(evt.timestamp)}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {filtered.length >= limit && (
        <button
          onClick={() => {
            const newLimit = limit + 200;
            setLimit(newLimit);
            load(newLimit);
          }}
          disabled={loading}
          className="mt-4 w-full py-2 text-sm text-blue-400 hover:text-blue-300 border border-border rounded-lg hover:bg-slate-800/50 transition-colors flex items-center justify-center gap-2"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ChevronDown className="w-4 h-4" />}
          Load More Events
        </button>
      )}
    </div>
  );
}

// ==================== Tab: Vitals ====================

/** Map sim vital keys to display labels */
const SIM_VITAL_MAP: Record<string, { label: string; unit: string; color: string; icon: typeof Heart; warn?: (v: number) => boolean }> = {
  hr: { label: "Heart Rate", unit: "bpm", color: "#EF4444", icon: Heart, warn: (v) => v < 60 || v > 100 },
  rr: { label: "Resp Rate", unit: "/min", color: "#8B5CF6", icon: Wind, warn: (v) => v < 12 || v > 20 },
  spo2: { label: "SpO2", unit: "%", color: "#3B82F6", icon: Droplets, warn: (v) => v < 94 },
  sbp: { label: "SBP", unit: "mmHg", color: "#F97316", icon: Activity, warn: (v) => v < 90 || v > 140 },
  dbp: { label: "DBP", unit: "mmHg", color: "#6366F1", icon: Activity, warn: (v) => v < 60 || v > 90 },
  temp: { label: "Temp", unit: "°C", color: "#22C55E", icon: Thermometer, warn: (v) => v > 38.3 },
};

type SimTimeSeriesPoint = { time: string; value: number; flag?: string | null; unit?: string | null };
type SimTimeSeries = Record<string, SimTimeSeriesPoint[]>;

interface SimJourneyPayload {
  sim_time?: string;
  window?: { start: string | null; end: string | null; stale?: boolean } | null;
  admission?: Record<string, any>;
  vitals?: Record<string, number>;
  vitals_series?: SimTimeSeries;
  labs?: Record<string, number>;
  labs_series?: SimTimeSeries;
  medications?: any[];
  diagnoses?: any[];
  procedures?: any[];
  transfers?: any[];
  care_path?: Array<{ careunit?: string; eventtype?: string; intime?: string | null; outtime?: string | null; ongoing?: boolean }>;
  timeline?: Array<{ time?: string | null; event?: string; detail?: string }>;
}

/** Poll /api/sim/patient/{hadm}/journey every 2.5 s while mounted. */
function useSimJourney(simHadmId: string | number | null | undefined, intervalMs = 2500) {
  const [data, setData] = useState<SimJourneyPayload | null>(null);
  useEffect(() => {
    if (!simHadmId) { setData(null); return; }
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`/api/sim/patient/${simHadmId}/journey`);
        if (!res.ok) return;
        const body = await res.json();
        const payload: SimJourneyPayload = body?.data ?? body;
        if (!cancelled) setData(payload);
      } catch {
        /* transient sim hiccup */
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [simHadmId, intervalMs]);
  return data;
}

function VitalsTab({
  subjectId,
  hadmId,
  simVitals,
  simHadmId,
}: {
  subjectId: number;
  hadmId: number;
  simVitals?: Record<string, number> | null;
  simHadmId?: string | number | null;
}) {
  const [vitals, setVitals] = useState<VitalSeries | null>(null);
  const [loading, setLoading] = useState(true);
  const [visible, setVisible] = useState<Record<string, boolean>>({
    "Heart Rate": true,
    "Respiratory Rate": true,
    "SpO2": true,
    "SBP": true,
    "Temperature": true,
  });

  // Live-chart history buffer keyed by sim vital code → array of {t:ms,v:number}
  // Points are appended whenever simVitals updates or the self-poll below
  // fetches a new sim snapshot. Capped at MAX_POINTS per series.
  const MAX_POINTS = 360;
  const [simHistory, setSimHistory] = useState<Record<string, { t: number; v: number }[]>>({});
  const [liveSnapshot, setLiveSnapshot] = useState<Record<string, number> | null>(null);
  const [simWindow, setSimWindow] = useState<{ start: string | null; end: string | null } | null>(null);

  useEffect(() => {
    setLoading(true);
    journeyVitals(subjectId, hadmId, "1h").then((res) => {
      if (res) setVitals(res.vitals);
      setLoading(false);
    });
  }, [subjectId, hadmId]);

  // Append incoming snapshot values to the rolling history buffer.
  const appendSnapshot = useCallback((snap: Record<string, number> | null | undefined) => {
    if (!snap) return;
    const now = Date.now();
    setSimHistory((prev) => {
      const next = { ...prev };
      for (const [k, v] of Object.entries(snap)) {
        if (typeof v !== "number" || !Number.isFinite(v)) continue;
        const series = (next[k] ?? []).slice();
        // Skip identical-value duplicates at the same second
        const last = series[series.length - 1];
        if (!last || last.v !== v || now - last.t > 1000) {
          series.push({ t: now, v });
        }
        if (series.length > MAX_POINTS) series.splice(0, series.length - MAX_POINTS);
        next[k] = series;
      }
      return next;
    });
  }, []);

  // Seed history from initial simVitals prop
  useEffect(() => {
    appendSnapshot(simVitals || null);
    if (simVitals) setLiveSnapshot(simVitals);
  }, [simVitals, appendSnapshot]);

  // Self-polling loop when in sim mode — gives the chart real motion even
  // if the parent doesn't refresh /api/sim/patient/{hadm}/journey.
  // Now prefers the authoritative ``vitals_series`` time-series from the
  // sim endpoint (full history since admission → current sim time); falls
  // back to snapshot-accumulation only if the series is missing.
  useEffect(() => {
    if (!simHadmId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`/api/sim/patient/${simHadmId}/journey`);
        if (!res.ok) return;
        const data = await res.json();
        const vitalsObj: Record<string, number> | undefined = data?.vitals || data?.data?.vitals;
        const series: SimTimeSeries | undefined =
          data?.vitals_series || data?.data?.vitals_series;
        const window = data?.window || data?.data?.window || null;
        if (cancelled) return;
        if (window) setSimWindow(window);
        if (vitalsObj) setLiveSnapshot(vitalsObj);
        if (series && Object.keys(series).length > 0) {
          // Replace history buffer entirely with the authoritative series.
          setSimHistory(() => {
            const next: Record<string, { t: number; v: number }[]> = {};
            for (const [name, points] of Object.entries(series)) {
              const mapped = (points || [])
                .map((p) => ({ t: Date.parse(p.time), v: Number(p.value) }))
                .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v));
              if (mapped.length > MAX_POINTS) {
                mapped.splice(0, mapped.length - MAX_POINTS);
              }
              next[name] = mapped;
            }
            return next;
          });
        } else if (vitalsObj) {
          // Fallback: append the current snapshot.
          appendSnapshot(vitalsObj);
        }
      } catch {
        /* ignore transient sim hiccups */
      }
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { cancelled = true; clearInterval(id); };
  }, [simHadmId, appendSnapshot]);

  const chartData = useMemo(() => {
    if (!vitals) return [];
    const timeMap = new Map<string, Record<string, number>>();
    for (const [name, points] of Object.entries(vitals)) {
      if (!VITAL_CONFIG[name]) continue;
      for (const p of points) {
        if (!timeMap.has(p.time)) timeMap.set(p.time, {});
        timeMap.get(p.time)![name] = p.value;
      }
    }
    return Array.from(timeMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([time, vals]) => ({ time, ...vals }));
  }, [vitals]);

  if (loading) {
    return (
      <div className="space-y-3">
        <SkeletonBlock className="h-8 w-64" />
        <SkeletonBlock className="h-72 w-full" />
      </div>
    );
  }

  // Live mode: show sim vitals as live per-component line charts whenever
  // we have ANY history (either from props or the self-poll). This replaces
  // the older static "snapshot cards" view.
  const hasLiveData =
    (simVitals && Object.keys(simVitals).length > 0) ||
    !!simHadmId ||
    Object.keys(simHistory).length > 0;

  if ((!vitals || chartData.length === 0) && hasLiveData) {
    const currentSnap = liveSnapshot || simVitals || {};
    const orderedKeys = ["hr", "rr", "spo2", "sbp", "dbp", "temp"].filter(
      (k) => SIM_VITAL_MAP[k],
    );
    return (
      <div className="animate-fade-in space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
            </span>
            Live simulation vitals — since admission
          </div>
          <div className="text-[10px] text-slate-500 font-mono-clinical">
            {simWindow?.start && simWindow?.end ? (
              <>
                {(() => {
                  const a = Date.parse(simWindow.start);
                  const b = Date.parse(simWindow.end);
                  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
                  const mins = Math.max(0, Math.round((b - a) / 60000));
                  const h = Math.floor(mins / 60);
                  const m = mins % 60;
                  return `elapsed ${h}h ${m}m · `;
                })()}
              </>
            ) : null}
            polling every 2.5 s · buffer {MAX_POINTS} pts
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          {orderedKeys.map((key) => {
            const cfg = SIM_VITAL_MAP[key];
            const series = simHistory[key] || [];
            const curr = currentSnap[key];
            const currNum = typeof curr === "number" ? curr : null;
            const isWarning = currNum != null ? cfg.warn?.(currNum) : false;
            const chartSeries = series.map((p) => ({
              t: p.t,
              // Label tick every ~10s
              label: new Date(p.t).toLocaleTimeString(undefined, { hour12: false }),
              v: p.v,
            }));
            const vals = series.map((p) => p.v);
            const minV = vals.length ? Math.min(...vals) : undefined;
            const maxV = vals.length ? Math.max(...vals) : undefined;
            return (
              <div
                key={key}
                className={`bg-bg-card rounded-lg border p-3 ${isWarning ? "border-red-500/40" : "border-border"}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <cfg.icon className="w-4 h-4" style={{ color: cfg.color }} />
                    <span className="text-xs text-slate-300">{cfg.label}</span>
                  </div>
                  <div className="text-right">
                    <div
                      className={`font-mono-clinical text-lg font-bold leading-none ${isWarning ? "text-red-400" : "text-text-primary"}`}
                    >
                      {currNum != null
                        ? Number.isInteger(currNum)
                          ? currNum
                          : currNum.toFixed(1)
                        : "—"}
                    </div>
                    <div className="text-[9px] text-slate-500">{cfg.unit}</div>
                  </div>
                </div>
                <div style={{ height: 100 }}>
                  {chartSeries.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-[10px] text-slate-500">
                      waiting for sim data…
                    </div>
                  ) : chartSeries.length === 1 ? (
                    <div className="h-full flex flex-col items-center justify-center">
                      <div className="w-16 h-0.5" style={{ background: cfg.color, opacity: 0.4 }} />
                      <div className="mt-1 text-[10px] text-slate-500">single sample · awaiting next tick</div>
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart
                        data={chartSeries}
                        margin={{ top: 4, right: 4, bottom: 4, left: 4 }}
                      >
                        <CartesianGrid
                          strokeDasharray="3 3"
                          stroke="#1f2937"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="t"
                          type="number"
                          domain={["dataMin", "dataMax"]}
                          tick={{ fontSize: 9, fill: "#64748b" }}
                          tickFormatter={(v: number) =>
                            new Date(v).toLocaleTimeString(undefined, {
                              hour12: false,
                              minute: "2-digit",
                              second: "2-digit",
                            })
                          }
                          minTickGap={40}
                        />
                        <YAxis
                          domain={(() => {
                            if (minV === undefined || maxV === undefined) return ["auto", "auto"] as any;
                            const span = maxV - minV;
                            // Use a proportional pad when we have spread; fall back to a fixed small pad
                            const pad = span > 0 ? Math.max(span * 0.15, 1) : 2;
                            const lo = Math.floor(minV - pad);
                            const hi = Math.ceil(maxV + pad);
                            return [lo, hi];
                          })()}
                          tick={{ fontSize: 9, fill: "#64748b" }}
                          tickFormatter={(v: number) => {
                            if (Math.abs(v) >= 100) return v.toFixed(0);
                            if (Math.abs(v) >= 10) return v.toFixed(1);
                            return v.toFixed(2);
                          }}
                          width={42}
                        />
                        <Tooltip
                          contentStyle={{
                            background: "#0f172a",
                            border: "1px solid #334155",
                            fontSize: 11,
                          }}
                          labelFormatter={(v: number) =>
                            new Date(v).toLocaleTimeString()
                          }
                          formatter={(val: number) => [
                            typeof val === "number"
                              ? Number.isInteger(val)
                                ? val
                                : val.toFixed(1)
                              : val,
                            cfg.label,
                          ]}
                        />
                        <Line
                          type="monotone"
                          dataKey="v"
                          stroke={cfg.color}
                          strokeWidth={1.75}
                          dot={false}
                          isAnimationActive={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </div>
                {vals.length > 0 && (
                  <div className="flex justify-between text-[9px] text-slate-500 mt-1 font-mono-clinical">
                    <span>min {minV!.toFixed(1)}</span>
                    <span>{vals.length} pts</span>
                    <span>max {maxV!.toFixed(1)}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (!vitals || chartData.length === 0) {
    return (
      <div className="text-center py-8 space-y-2">
        <p className="text-slate-400 text-sm">No vital signs data for this admission.</p>
        <p className="text-slate-500 text-xs">Vitals are recorded in ICU chartevents. Select an admission with an ICU stay to view vitals.</p>
      </div>
    );
  }

  return (
    <div className="animate-fade-in space-y-4">
      {/* Toggle checkboxes */}
      <div className="flex flex-wrap gap-4">
        {Object.entries(VITAL_CONFIG).map(([key, cfg]) => (
          <label
            key={key}
            className="flex items-center gap-2 cursor-pointer text-sm"
            style={{ color: visible[key] ? cfg.color : "#64748B" }}
          >
            <input
              type="checkbox"
              checked={visible[key]}
              onChange={() => setVisible((v) => ({ ...v, [key]: !v[key] }))}
              className="accent-blue-500"
            />
            <cfg.icon className="w-4 h-4" />
            {cfg.label}
          </label>
        ))}
      </div>

      {/* Chart */}
      <div className="bg-bg-card rounded-lg border border-border p-4">
        <ResponsiveContainer width="100%" height={360}>
          <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-segment-border)" />
            <XAxis
              dataKey="time"
              tickFormatter={(v) => formatDate(v)}
              stroke="#64748B"
              tick={{ fontSize: 10 }}
              interval="preserveStartEnd"
            />
            <YAxis stroke="#64748B" tick={{ fontSize: 10 }} />
            <Tooltip
              contentStyle={{
                backgroundColor: "var(--color-tooltip-bg)",
                border: "1px solid var(--color-tooltip-border)",
                borderRadius: 8,
                fontSize: 12,
              }}
              labelFormatter={(v) => formatDate(v as string)}
            />
            <Legend />
            {Object.entries(VITAL_CONFIG).map(([key, cfg]) =>
              visible[key] ? (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  name={cfg.label}
                  stroke={cfg.color}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                />
              ) : null
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ==================== Tab: Labs ====================

function LabsTab({ subjectId, hadmId, simHadmId }: { subjectId: number; hadmId: number; simHadmId?: string | number | null }) {
  const [panels, setPanels] = useState<Record<string, LabPanel> | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedPanel, setSelectedPanel] = useState("CBC");
  const simJourney = useSimJourney(simHadmId);

  useEffect(() => {
    // In sim mode, labs come from the sim endpoint (via useSimJourney); skip MIMIC fetch
    if (simHadmId) { setLoading(false); return; }
    setLoading(true);
    journeyLabs(subjectId, hadmId, LAB_PANELS.join(",")).then((res) => {
      if (res) setPanels(res.panels);
      setLoading(false);
    });
  }, [subjectId, hadmId, simHadmId]);

  const LAB_COLORS = ["#3B82F6", "#22C55E", "#F97316", "#A855F7", "#EF4444", "#EC4899", "#14B8A6", "#EAB308"];

  if (loading) {
    return (
      <div className="space-y-3">
        <SkeletonBlock className="h-10 w-80" />
        <SkeletonBlock className="h-72 w-full" />
      </div>
    );
  }

  // Sim-mode rendering — labs flow from the sim backend since admission → now.
  if (simHadmId) {
    const series = simJourney?.labs_series ?? {};
    const names = Object.keys(series);
    const win = simJourney?.window;
    return (
      <div className="animate-fade-in space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
            </span>
            Live simulation labs — since admission
          </div>
          <div className="text-[10px] text-slate-500 font-mono-clinical">
            {(() => {
              if (!win?.start || !win?.end) return "polling every 2.5 s";
              const a = Date.parse(win.start); const b = Date.parse(win.end);
              if (!Number.isFinite(a) || !Number.isFinite(b)) return "polling every 2.5 s";
              const mins = Math.max(0, Math.round((b - a) / 60000));
              return `elapsed ${Math.floor(mins / 60)}h ${mins % 60}m · polling every 2.5 s`;
            })()}
          </div>
        </div>
        {names.length === 0 ? (
          <p className="text-slate-500 text-center py-8">
            No labs yet. Values appear as the simulated admission progresses.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            {names.map((name, i) => {
              const pts = series[name] || [];
              const chartPts = pts
                .map((p) => ({ t: Date.parse(p.time), v: Number(p.value), flag: p.flag, unit: p.unit }))
                .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v));
              const last = chartPts[chartPts.length - 1];
              const color = LAB_COLORS[i % LAB_COLORS.length];
              const vals = chartPts.map((p) => p.v);
              const minV = vals.length ? Math.min(...vals) : undefined;
              const maxV = vals.length ? Math.max(...vals) : undefined;
              const abnormal = last?.flag && last.flag !== "normal" && last.flag !== "";
              return (
                <div
                  key={name}
                  className={`bg-bg-card rounded-lg border p-3 ${abnormal ? "border-amber-500/50" : "border-border"}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-slate-300">{name}</span>
                    <div className="text-right">
                      <div className={`font-mono-clinical text-lg font-bold leading-none ${abnormal ? "text-amber-400" : "text-text-primary"}`}>
                        {last ? (Number.isInteger(last.v) ? last.v : last.v.toFixed(2)) : "—"}
                      </div>
                      <div className="text-[9px] text-slate-500">{last?.unit || ""}</div>
                    </div>
                  </div>
                  <div style={{ height: 80 }}>
                    {chartPts.length < 2 ? (
                      <div className="h-full flex items-center justify-center text-[10px] text-slate-500">
                        {chartPts.length === 0 ? "waiting…" : "1 sample"}
                      </div>
                    ) : (
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartPts} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                          <XAxis
                            dataKey="t"
                            type="number"
                            domain={["dataMin", "dataMax"]}
                            tick={{ fontSize: 9, fill: "#64748b" }}
                            tickFormatter={(v: number) =>
                              new Date(v).toLocaleTimeString(undefined, { hour12: false, minute: "2-digit", second: "2-digit" })
                            }
                            minTickGap={40}
                          />
                          <YAxis
                            domain={(() => {
                              if (minV === undefined || maxV === undefined) return ["auto", "auto"] as any;
                              const span = maxV - minV;
                              const pad = span > 0 ? Math.max(span * 0.15, Math.abs(minV) * 0.05, 0.5) : Math.max(1, Math.abs(minV) * 0.1);
                              return [Math.floor((minV - pad) * 100) / 100, Math.ceil((maxV + pad) * 100) / 100];
                            })()}
                            tick={{ fontSize: 9, fill: "#64748b" }}
                            width={48}
                          />
                          <Tooltip
                            contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 11 }}
                            labelFormatter={(v: number) => new Date(v).toLocaleTimeString()}
                            formatter={(val: number) => [typeof val === "number" ? val.toFixed(2) : val, name]}
                          />
                          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.75} dot={false} isAnimationActive={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                  </div>
                  <div className="flex justify-between text-[9px] text-slate-500 mt-1 font-mono-clinical">
                    {minV !== undefined && <span>min {minV.toFixed(2)}</span>}
                    <span>{chartPts.length} pts</span>
                    {maxV !== undefined && <span>max {maxV.toFixed(2)}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  if (!panels || Object.keys(panels).length === 0) {
    return <p className="text-slate-500 text-center py-8">No lab data available for this admission.</p>;
  }

  const currentPanel = panels[selectedPanel] || {};
  const labNames = Object.keys(currentPanel);

  // Build chart data
  const chartData = (() => {
    const timeMap = new Map<string, Record<string, any>>();
    for (const [name, points] of Object.entries(currentPanel)) {
      for (const p of (points as any[])) {
        if (!timeMap.has(p.time)) timeMap.set(p.time, {});
        timeMap.get(p.time)![name] = p.value;
        if (p.flag && p.flag !== "normal" && p.flag !== "") {
          timeMap.get(p.time)![`${name}_flag`] = true;
        }
      }
    }
    return Array.from(timeMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([time, vals]) => ({ time, ...vals }));
  })();

  return (
    <div className="animate-fade-in space-y-4">
      {/* Panel selector */}
      <div className="flex flex-wrap gap-2">
        {LAB_PANELS.map((p) => (
          <button
            key={p}
            onClick={() => setSelectedPanel(p)}
            className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
              selectedPanel === p
                ? "bg-blue-500/20 text-blue-400 border-blue-500/30"
                : "bg-bg-card text-slate-400 border-border hover:border-slate-500"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {labNames.length === 0 ? (
        <p className="text-slate-500 text-center py-8">No data for {selectedPanel} panel.</p>
      ) : (
        <div className="bg-bg-card rounded-lg border border-border p-4">
          <ResponsiveContainer width="100%" height={360}>
            <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-segment-border)" />
              <XAxis
                dataKey="time"
                tickFormatter={(v) => formatDate(v)}
                stroke="#64748B"
                tick={{ fontSize: 10 }}
                interval="preserveStartEnd"
              />
              <YAxis stroke="#64748B" tick={{ fontSize: 10 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: "var(--color-tooltip-bg)",
                  border: "1px solid var(--color-tooltip-border)",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelFormatter={(v) => formatDate(v as string)}
              />
              <Legend />
              {labNames.map((name, i) => (
                <Line
                  key={name}
                  type="monotone"
                  dataKey={name}
                  name={name}
                  stroke={LAB_COLORS[i % LAB_COLORS.length]}
                  strokeWidth={2}
                  dot={(props: any) => {
                    const { cx, cy, payload } = props;
                    if (payload[`${name}_flag`]) {
                      return (
                        <circle
                          key={`flag-${name}-${cx}`}
                          cx={cx}
                          cy={cy}
                          r={5}
                          fill="#EF4444"
                          stroke="#EF4444"
                          strokeWidth={2}
                        />
                      );
                    }
                    return <circle key={`dot-${name}-${cx}`} cx={cx} cy={cy} r={0} />;
                  }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
          <p className="text-[10px] text-slate-500 mt-2 flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500" /> Red dots indicate abnormal flags
          </p>
        </div>
      )}
    </div>
  );
}

// ==================== Tab: Care Path ====================

function CarePathTab({ subjectId, hadmId, simHadmId }: { subjectId: number; hadmId: number; simHadmId?: string | number | null }) {
  const [path, setPath] = useState<JourneyPath | null>(null);
  const [meds, setMeds] = useState<MedicationRecord[]>([]);
  const [metrics, setMetrics] = useState<JourneyMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const simJourney = useSimJourney(simHadmId);

  useEffect(() => {
    if (simHadmId) { setLoading(false); return; }
    setLoading(true);
    Promise.all([
      journeyPath(subjectId, hadmId),
      journeyMedications(subjectId, hadmId),
      journeyMetrics(subjectId, hadmId),
    ]).then(([pathRes, medsRes, metricsRes]) => {
      if (pathRes) setPath(pathRes);
      if (medsRes) setMeds(medsRes.medications || []);
      if (metricsRes) setMetrics(metricsRes);
      setLoading(false);
    });
  }, [subjectId, hadmId, simHadmId]);

  if (loading) {
    return (
      <div className="space-y-4">
        <SkeletonBlock className="h-24 w-full" />
        <SkeletonBlock className="h-40 w-full" />
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonBlock key={i} className="h-20" />
          ))}
        </div>
      </div>
    );
  }

  // ────────── Sim-mode care path ──────────
  if (simHadmId) {
    const cp = simJourney?.care_path ?? [];
    const simMeds: any[] = simJourney?.medications ?? [];
    const simDx: any[] = simJourney?.diagnoses ?? [];
    const win = simJourney?.window;
    const elapsedMin = (() => {
      if (!win?.start || !win?.end) return null;
      const a = Date.parse(win.start); const b = Date.parse(win.end);
      if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
      return Math.max(0, Math.round((b - a) / 60000));
    })();
    const totalMin = elapsedMin ?? 1;
    return (
      <div className="animate-fade-in space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
            </span>
            Live simulation care path — since admission
          </div>
          <div className="text-[10px] text-slate-500 font-mono-clinical">
            {elapsedMin !== null
              ? `elapsed ${Math.floor(elapsedMin / 60)}h ${elapsedMin % 60}m · ${cp.length} transfer${cp.length === 1 ? "" : "s"}`
              : `${cp.length} transfers`}
          </div>
        </div>

        {/* Department flow — care_path since admission */}
        <div className="bg-bg-card rounded-lg border border-border p-4">
          <h3 className="text-xs text-slate-400 mb-3 uppercase tracking-wide">Department flow</h3>
          {cp.length === 0 ? (
            <p className="text-slate-500 text-xs">Patient still in first department — no transfers yet.</p>
          ) : (
            <div className="space-y-2">
              {cp.map((xf, i) => {
                const start = xf.intime ? Date.parse(xf.intime) : 0;
                const startMin = win?.start ? Math.max(0, Math.round((start - Date.parse(win.start)) / 60000)) : 0;
                const width = xf.ongoing
                  ? Math.max(4, 100 - (startMin / totalMin) * 100)
                  : (() => {
                      const end = xf.outtime ? Date.parse(xf.outtime) : start;
                      return Math.max(2, ((end - start) / 60000 / totalMin) * 100);
                    })();
                return (
                  <div key={i} className="relative">
                    <div className="flex items-center justify-between text-[11px] mb-1">
                      <span className="text-text-primary">
                        <span className="text-slate-400 mr-1">{i + 1}.</span>
                        {xf.careunit || "Unknown"}
                        {xf.ongoing && (
                          <span className="ml-2 text-[9px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400">live</span>
                        )}
                      </span>
                      <span className="text-slate-500 font-mono-clinical">
                        {xf.intime ? formatDate(xf.intime) : "—"}
                        {xf.outtime && xf.outtime !== xf.intime ? ` → ${formatDate(xf.outtime)}` : xf.ongoing ? " → now" : ""}
                      </span>
                    </div>
                    <div className="h-2 bg-bg-primary rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${xf.ongoing ? "bg-green-500/70" : "bg-blue-500/60"}`}
                        style={{ marginLeft: `${(startMin / totalMin) * 100}%`, width: `${Math.min(width, 100 - (startMin / totalMin) * 100)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {/* Medications started since admission */}
          <div className="bg-bg-card rounded-lg border border-border p-4">
            <h3 className="text-xs text-slate-400 mb-3 uppercase tracking-wide">Medications ({simMeds.length})</h3>
            {simMeds.length === 0 ? (
              <p className="text-slate-500 text-xs">No medications yet.</p>
            ) : (
              <div className="space-y-1 max-h-64 overflow-y-auto">
                {simMeds.slice(-30).reverse().map((m, i) => (
                  <div key={i} className="text-[11px] text-slate-300 flex justify-between border-b border-border/50 pb-1">
                    <span>
                      <span className="text-text-primary">{m.drug}</span>{" "}
                      <span className="text-slate-500">{m.dose_val_rx} {m.dose_unit_rx} {m.route}</span>
                    </span>
                    <span className="text-slate-500 font-mono-clinical">{m.sim_time ? formatDate(m.sim_time) : "—"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Diagnoses confirmed */}
          <div className="bg-bg-card rounded-lg border border-border p-4">
            <h3 className="text-xs text-slate-400 mb-3 uppercase tracking-wide">Diagnoses ({simDx.length})</h3>
            {simDx.length === 0 ? (
              <p className="text-slate-500 text-xs">No diagnoses recorded yet.</p>
            ) : (
              <ul className="space-y-1 max-h-64 overflow-y-auto text-[11px]">
                {simDx.map((dx, i) => (
                  <li key={i} className="text-slate-300">
                    <span className="font-mono-clinical text-slate-400 mr-2">{dx.icd_code}</span>
                    {dx.long_title || ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Department flow
  const transfers = path?.transfers || [];
  const admitStart = transfers.length > 0 ? new Date(transfers[0].intime).getTime() : 0;
  const admitEnd =
    transfers.length > 0
      ? new Date(transfers[transfers.length - 1].outtime || transfers[transfers.length - 1].intime).getTime()
      : 1;
  const totalSpan = Math.max(admitEnd - admitStart, 1);

  // Widget 8: Medication Gantt Chart
  // Normalize medication times - backend may send start_time/stop_time or starttime/endtime
  const normalizedMeds = meds.map((m) => ({
    ...m,
    _start: m.start_time || m.starttime || null,
    _stop: m.stop_time || m.endtime || null,
    _category: m.category || "other",
    _duration: m.duration_hours || 0,
  }));

  // Compute time range from medications
  const medTimes = normalizedMeds.flatMap((m) => {
    const times: number[] = [];
    if (m._start) times.push(new Date(m._start).getTime());
    if (m._stop) times.push(new Date(m._stop).getTime());
    return times;
  });
  const medTimeMin = medTimes.length > 0 ? Math.min(...medTimes) : admitStart;
  const medTimeMax = medTimes.length > 0 ? Math.max(...medTimes) : admitEnd;
  const ganttStart = Math.min(admitStart || medTimeMin, medTimeMin);
  const ganttEnd = Math.max(admitEnd || medTimeMax, medTimeMax);
  const ganttSpan = Math.max(ganttEnd - ganttStart, 1);

  // Group by drug, compute total duration, take top 20 by duration
  const drugDurationMap = new Map<string, { drug: string; entries: typeof normalizedMeds; totalDuration: number; category: string }>();
  for (const m of normalizedMeds) {
    if (!drugDurationMap.has(m.drug)) {
      drugDurationMap.set(m.drug, { drug: m.drug, entries: [], totalDuration: 0, category: m._category });
    }
    const entry = drugDurationMap.get(m.drug)!;
    entry.entries.push(m);
    entry.totalDuration += m._duration || 0;
  }
  const ganttDrugs = Array.from(drugDurationMap.values())
    .sort((a, b) => b.totalDuration - a.totalDuration)
    .slice(0, 20);

  // Collect unique categories for legend
  const ganttCategories = Array.from(new Set(ganttDrugs.map((d) => d.category)));

  // Widget 9: Transfer flow with time spent
  const transfersWithDuration = transfers.map((t) => {
    const inMs = new Date(t.intime).getTime();
    const outMs = t.outtime ? new Date(t.outtime).getTime() : inMs;
    const hoursSpent = Math.max((outMs - inMs) / (1000 * 60 * 60), 0);
    return { ...t, hoursSpent };
  });

  // Generate X-axis tick labels for Gantt (5 evenly spaced)
  const ganttTicks = Array.from({ length: 5 }, (_, i) => {
    const t = ganttStart + (ganttSpan * i) / 4;
    return { pos: (i / 4) * 100, label: formatDate(new Date(t).toISOString()) };
  });

  return (
    <div className="animate-fade-in space-y-6">
      {/* Widget 9: Enhanced Department Flow */}
      <div>
        <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
          <Shuffle className="w-4 h-4 text-blue-400" />
          Department Flow
        </h3>
        {transfersWithDuration.length === 0 ? (
          <p className="text-text-muted text-sm">No transfer data available.</p>
        ) : (
          <div className="bg-bg-card rounded-lg border border-border p-4 overflow-x-auto">
            <div className="flex items-center gap-1 min-w-max">
              {transfersWithDuration.map((t, i) => {
                const start = new Date(t.intime).getTime();
                const end = t.outtime ? new Date(t.outtime).getTime() : start;
                const pct = Math.max(((end - start) / totalSpan) * 100, 5);
                const color = getDeptColor(t.careunit || t.eventtype);
                return (
                  <div key={i} className="flex items-center">
                    <div
                      className="rounded-lg border px-3 py-2 text-center transition-all hover:scale-105"
                      style={{
                        borderColor: color,
                        backgroundColor: color + "20",
                        minWidth: `${Math.max(pct * 3, 100)}px`,
                      }}
                    >
                      <div className="text-xs font-semibold text-text-primary truncate">
                        {t.careunit || t.eventtype}
                      </div>
                      <div
                        className="font-mono-clinical text-[11px] font-bold mt-1 px-1.5 py-0.5 rounded"
                        style={{ color, backgroundColor: color + "15" }}
                      >
                        {formatHours(t.hoursSpent)}
                      </div>
                      <div className="font-mono-clinical text-[9px] mt-0.5 text-slate-500">
                        {formatDate(t.intime)}
                      </div>
                    </div>
                    {i < transfersWithDuration.length - 1 && (
                      <div className="flex flex-col items-center mx-1 shrink-0">
                        <ArrowRight className="w-4 h-4 text-slate-500" />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            {/* Total time summary */}
            <div className="mt-3 pt-2 border-t border-border flex items-center gap-4 text-[10px] text-slate-500">
              <span>Total departments: <span className="text-slate-300 font-semibold">{transfersWithDuration.length}</span></span>
              <span>Total time: <span className="text-slate-300 font-semibold">{formatHours(transfersWithDuration.reduce((s, t) => s + t.hoursSpent, 0))}</span></span>
            </div>
          </div>
        )}
      </div>

      {/* Widget 8: Medication Gantt Chart */}
      <div>
        <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
          <Pill className="w-4 h-4 text-orange-400" />
          Medication Timeline (Gantt)
        </h3>
        {ganttDrugs.length === 0 ? (
          <p className="text-text-muted text-sm">No medication data available.</p>
        ) : (
          <div className="bg-bg-card rounded-lg border border-border p-4 overflow-x-auto">
            {/* Category legend */}
            <div className="flex flex-wrap gap-3 mb-3">
              {ganttCategories.map((cat) => (
                <div key={cat} className="flex items-center gap-1.5 text-[10px]">
                  <div
                    className="w-3 h-3 rounded"
                    style={{ backgroundColor: getMedCategoryColor(cat) }}
                  />
                  <span className="text-text-secondary capitalize">{cat}</span>
                </div>
              ))}
            </div>
            {/* Gantt chart */}
            <div className="min-w-[600px]">
              {/* X-axis labels at top */}
              <div className="flex items-center mb-1" style={{ marginLeft: "160px" }}>
                <div className="flex-1 relative h-4">
                  {ganttTicks.map((tick, i) => (
                    <span
                      key={i}
                      className="absolute text-[9px] text-text-muted font-mono-clinical whitespace-nowrap"
                      style={{
                        left: `${tick.pos}%`,
                        transform: i === ganttTicks.length - 1 ? "translateX(-100%)" : i > 0 ? "translateX(-50%)" : undefined,
                      }}
                    >
                      {tick.label}
                    </span>
                  ))}
                </div>
              </div>
              {/* Drug rows */}
              <div className="space-y-1">
                {ganttDrugs.map(({ drug, entries, category }) => {
                  const color = getMedCategoryColor(category);
                  return (
                    <div key={drug} className="flex items-center gap-3">
                      <div
                        className="w-[148px] text-[11px] text-text-secondary truncate shrink-0 text-right pr-2"
                        title={drug}
                      >
                        {drug}
                      </div>
                      <div className="flex-1 relative h-6 rounded" style={{ backgroundColor: "var(--color-bg-widget)" }}>
                        {entries.map((e, j) => {
                          const s = e._start ? new Date(e._start).getTime() : ganttStart;
                          const en = e._stop ? new Date(e._stop).getTime() : ganttEnd;
                          const left = ((s - ganttStart) / ganttSpan) * 100;
                          const width = Math.max(((en - s) / ganttSpan) * 100, 0.5);
                          return (
                            <div
                              key={j}
                              className="absolute top-0.5 h-5 rounded flex items-center overflow-hidden"
                              style={{
                                left: `${Math.max(left, 0)}%`,
                                width: `${Math.min(width, 100 - Math.max(left, 0))}%`,
                                backgroundColor: color,
                                opacity: 0.85,
                                minWidth: "2px",
                              }}
                              title={`${drug} | ${e.route || "N/A"} | ${e._start ? formatDate(e._start) : "?"} - ${e._stop ? formatDate(e._stop) : "ongoing"}`}
                            >
                              {width > 8 && (
                                <span className="text-[9px] font-medium px-1 truncate" style={{ color: "#fff", textShadow: "0 0 3px rgba(0,0,0,0.5)" }}>
                                  {drug}
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
              {/* X-axis line at bottom */}
              <div className="flex items-center mt-1" style={{ marginLeft: "160px" }}>
                <div className="flex-1 border-t border-border" />
              </div>
            </div>
            <p className="text-[10px] text-text-muted mt-2">
              Showing top {ganttDrugs.length} medications by duration. Bars extend to end of admission if stop time is unknown.
            </p>
          </div>
        )}
      </div>

      {/* Metrics */}
      {metrics && (
        <div>
          <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <Activity className="w-4 h-4 text-green-400" />
            Admission Metrics
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            <MetricCard
              icon={<Clock className="w-4 h-4" />}
              label="Total LOS"
              value={formatHours(metrics.total_los_hours)}
              color="#3B82F6"
            />
            <MetricCard
              icon={<BedDouble className="w-4 h-4" />}
              label="ICU LOS"
              value={formatHours(metrics.icu_los_hours)}
              color="#F97316"
            />
            <MetricCard
              icon={<Shuffle className="w-4 h-4" />}
              label="Transfers"
              value={String(metrics.num_transfers)}
              color="#A855F7"
            />
            <MetricCard
              icon={<BedDouble className="w-4 h-4" />}
              label="ICU Episodes"
              value={String(metrics.num_icu_episodes)}
              color="#F59E0B"
            />
            <MetricCard
              icon={<Syringe className="w-4 h-4" />}
              label="Procedures"
              value={String(metrics.num_procedures)}
              color="#EF4444"
            />
            <MetricCard
              icon={<Pill className="w-4 h-4" />}
              label="Unique Drugs"
              value={String(metrics.num_unique_drugs)}
              color="#F97316"
            />
            <MetricCard
              icon={<Skull className="w-4 h-4" />}
              label="Mortality"
              value={metrics.mortality ? "Yes" : "No"}
              color={metrics.mortality ? "#EF4444" : "#22C55E"}
            />
            {metrics.ed_los_hours > 0 && (
              <MetricCard
                icon={<Clock className="w-4 h-4" />}
                label="ED LOS"
                value={formatHours(metrics.ed_los_hours)}
                color="#EC4899"
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ==================== Widget 10: Cohort Comparison ====================

interface AdmissionCompare {
  currentHadm: number;
  previousHadm: number;
  currentMetrics: JourneyMetrics;
  previousMetrics: JourneyMetrics;
}

const COMPARE_ROWS: { key: keyof JourneyMetrics; label: string; format: (v: any) => string; lowerIsBetter: boolean }[] = [
  { key: "total_los_hours", label: "Total LOS", format: (v: any) => v != null ? formatHours(v) : "—", lowerIsBetter: true },
  { key: "icu_los_hours", label: "ICU LOS", format: (v: any) => v != null ? formatHours(v) : "—", lowerIsBetter: true },
  { key: "ed_los_hours", label: "ED LOS", format: (v: any) => v != null ? formatHours(v) : "—", lowerIsBetter: true },
  { key: "num_transfers", label: "Transfers", format: (v: any) => v != null ? String(v) : "—", lowerIsBetter: true },
  { key: "num_icu_episodes", label: "ICU Episodes", format: (v: any) => v != null ? String(v) : "—", lowerIsBetter: true },
  { key: "num_procedures", label: "Procedures", format: (v: any) => v != null ? String(v) : "—", lowerIsBetter: false },
  { key: "num_unique_drugs", label: "Unique Drugs", format: (v: any) => v != null ? String(v) : "—", lowerIsBetter: true },
  { key: "mortality", label: "Mortality", format: (v: boolean) => (v ? "Yes" : "No"), lowerIsBetter: true },
];

/** Small grouped-bar chart comparing prev vs current for a set of metrics. */
function CompareBarChart({
  title,
  metrics,
  prev,
  curr,
  formatValue,
}: {
  title: string;
  metrics: { key: keyof JourneyMetrics; label: string }[];
  prev: JourneyMetrics;
  curr: JourneyMetrics;
  formatValue?: (v: number) => string;
}) {
  const chartData = metrics.map((m) => ({
    name: m.label,
    Previous: Number((prev as any)[m.key]) || 0,
    Current: Number((curr as any)[m.key]) || 0,
  }));

  return (
    <div className="bg-bg-primary rounded-lg border border-border p-3">
      <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">{title}</h4>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={chartData} layout="vertical" margin={{ top: 0, right: 10, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 10, fill: "#94a3b8" }} tickFormatter={formatValue} />
          <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: "#94a3b8" }} width={75} />
          <Tooltip
            contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value: number) => [formatValue ? formatValue(value) : value.toFixed(1), undefined]}
          />
          <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
          <Bar dataKey="Previous" fill="#6366F1" radius={[0, 3, 3, 0]} barSize={14} />
          <Bar dataKey="Current" fill="#22D3EE" radius={[0, 3, 3, 0]} barSize={14} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Percentage-change waterfall chart across all numeric metrics. */
function DeltaChart({ prev, curr }: { prev: JourneyMetrics; curr: JourneyMetrics }) {
  const rows = COMPARE_ROWS.filter((r) => r.key !== "mortality");
  const chartData = rows.map((row) => {
    const p = Number((prev as any)[row.key]) || 0;
    const c = Number((curr as any)[row.key]) || 0;
    const pct = p !== 0 ? Math.round(((c - p) / Math.abs(p)) * 100) : 0;
    return { name: row.label, pct, improved: row.lowerIsBetter ? pct < 0 : pct > 0 };
  });

  return (
    <div className="bg-bg-primary rounded-lg border border-border p-3">
      <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">Overall Change (%)</h4>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={chartData} layout="vertical" margin={{ top: 0, right: 10, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 10, fill: "#94a3b8" }} tickFormatter={(v) => `${v}%`} />
          <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: "#94a3b8" }} width={75} />
          <Tooltip
            contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value: number) => [`${value > 0 ? "+" : ""}${value}%`, "Change"]}
          />
          <Bar dataKey="pct" barSize={14} radius={[0, 3, 3, 0]}>
            {chartData.map((d, i) => (
              <Cell key={i} fill={d.pct === 0 ? "#475569" : d.improved ? "#22C55E" : "#EF4444"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function AdmissionComparisonTable({
  data,
  onClose,
}: {
  data: AdmissionCompare;
  onClose: () => void;
}) {
  return (
    <div className="bg-bg-card rounded-xl border border-purple-500/30 p-5 animate-fade-in space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-purple-400" />
          Admission Comparison
        </h3>
        <button
          onClick={onClose}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-400 hover:text-red-300 bg-red-500/10 hover:bg-red-500/20 rounded-lg border border-red-500/20 transition-colors"
        >
          <X className="w-3 h-3" />
          Close
        </button>
      </div>

      {/* Metrics table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 text-slate-400 font-medium">Metric</th>
              <th className="text-center py-2 px-3 text-slate-300 font-medium">
                <span className="font-mono-clinical">Previous</span>
                <span className="text-[9px] text-slate-500 ml-1">#{data.previousHadm}</span>
              </th>
              <th className="text-center py-2 px-3 text-slate-300 font-medium">
                <span className="font-mono-clinical">Current</span>
                <span className="text-[9px] text-slate-500 ml-1">#{data.currentHadm}</span>
              </th>
              <th className="text-center py-2 px-3 text-slate-400 font-medium">Change</th>
            </tr>
          </thead>
          <tbody>
            {COMPARE_ROWS.map((row) => {
              const prev = (data.previousMetrics as any)[row.key];
              const curr = (data.currentMetrics as any)[row.key];

              let delta = "";
              let deltaColor = "text-slate-500";
              if (row.key === "mortality") {
                if (prev !== curr) {
                  delta = curr ? "Worsened" : "Improved";
                  deltaColor = curr ? "text-red-400" : "text-green-400";
                } else {
                  delta = "—";
                }
              } else if (prev == null || curr == null) {
                delta = "—";
              } else {
                const diff = Number(curr) - Number(prev);
                if (diff === 0) {
                  delta = "—";
                } else {
                  const pct = Number(prev) !== 0 ? Math.round((diff / Math.abs(Number(prev))) * 100) : 0;
                  const sign = diff > 0 ? "+" : "";
                  delta = `${sign}${pct}%`;
                  const improved = row.lowerIsBetter ? diff < 0 : diff > 0;
                  deltaColor = improved ? "text-green-400" : "text-red-400";
                }
              }

              const prevColor = row.key === "mortality" ? (prev ? "text-red-400" : "text-green-400") : "text-slate-300";
              const currColor = row.key === "mortality" ? (curr ? "text-red-400" : "text-green-400") : "text-slate-300";

              return (
                <tr key={row.key} className="border-b border-border/50 hover:bg-slate-800/30">
                  <td className="py-2.5 px-3 text-slate-400 font-medium">{row.label}</td>
                  <td className={`py-2.5 px-3 text-center font-mono-clinical ${prevColor}`}>{row.format(prev)}</td>
                  <td className={`py-2.5 px-3 text-center font-mono-clinical ${currColor}`}>{row.format(curr)}</td>
                  <td className={`py-2.5 px-3 text-center font-mono-clinical font-semibold ${deltaColor}`}>{delta}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Comparison charts — 2×2 grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <CompareBarChart
          title="Length of Stay (hours)"
          metrics={[
            { key: "total_los_hours", label: "Total" },
            { key: "icu_los_hours", label: "ICU" },
            { key: "ed_los_hours", label: "ED" },
          ]}
          prev={data.previousMetrics}
          curr={data.currentMetrics}
          formatValue={(v) => v < 24 ? `${v.toFixed(0)}h` : `${(v / 24).toFixed(1)}d`}
        />
        <CompareBarChart
          title="Clinical Events"
          metrics={[
            { key: "num_transfers", label: "Transfers" },
            { key: "num_icu_episodes", label: "ICU Episodes" },
          ]}
          prev={data.previousMetrics}
          curr={data.currentMetrics}
        />
        <CompareBarChart
          title="Treatment Intensity"
          metrics={[
            { key: "num_procedures", label: "Procedures" },
            { key: "num_unique_drugs", label: "Drugs" },
          ]}
          prev={data.previousMetrics}
          curr={data.currentMetrics}
        />
        <DeltaChart prev={data.previousMetrics} curr={data.currentMetrics} />
      </div>
    </div>
  );
}

// ==================== Main Page ====================

type TabId = "walkthrough" | "timeline" | "vitals" | "labs" | "carepath";

const TABS: { id: TabId; label: string; icon: typeof Activity }[] = [
  { id: "walkthrough", label: "Journey Walk", icon: ArrowRight },
  { id: "timeline", label: "Timeline", icon: Clock },
  { id: "vitals", label: "Vitals", icon: Heart },
  { id: "labs", label: "Labs", icon: FlaskConical },
  { id: "carepath", label: "Care Path", icon: MapPin },
];

interface SimEdPatient {
  hadm_id: string | number;
  subject_id: number;
  department: string;
  acuity?: number;
}

interface SimJourneyData {
  admission?: { original_hadm_id?: number; [key: string]: any };
  transfers: Array<{ careunit: string; eventtype: string; intime: string; outtime: string }>;
  vitals: Record<string, Array<{ time: string; value: number }>>;
  labs: Record<string, Array<{ time: string; value: number; flag?: string }>>;
  medications: Array<{ drug: string; route?: string; start_time?: string; stop_time?: string; starttime?: string; endtime?: string; category?: string; duration_hours?: number }>;
  diagnoses: Array<{ icd_code: string; long_title: string }>;
}

export default function PatientJourney() {
  const [subjectInput, setSubjectInput] = useState("");
  const [patient, setPatient] = useState<PatientSummary | null>(null);
  const [selectedHadm, setSelectedHadm] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("walkthrough");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sim patients state
  const [simEdPatients, setSimEdPatients] = useState<SimEdPatient[]>([]);
  const [simConnected, setSimConnected] = useState(false);
  const [simJourney, setSimJourney] = useState<SimJourneyData | null>(null);
  const [simPatientLabel, setSimPatientLabel] = useState<string | null>(null);
  // Keep the SIM-prefixed hadm_id so the Vitals tab can self-poll for live updates
  const [simActiveHadm, setSimActiveHadm] = useState<string | number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchSimEd = async () => {
      try {
        // Prefer the ED board (carries full department/status per
        // patient). Fall back to recent_admissions on stats-dashboard
        // if the board endpoint is unavailable.
        const boardRes = await fetch("/api/sim/ed-board");
        if (boardRes.ok) {
          const board = await boardRes.json();
          const patients = (board?.patients ?? []).slice(0, 10).map((p: any) => ({
            hadm_id: p.hadm_id,
            subject_id: p.subject_id,
            department: p.department || p.status || "—",
          }));
          if (!cancelled && patients.length > 0) {
            setSimEdPatients(patients);
            setSimConnected(true);
            return;
          }
        }
        const res = await fetch("/api/sim/stats-dashboard");
        if (!res.ok) throw new Error("sim offline");
        const data = await res.json();
        if (!cancelled) {
          setSimEdPatients(
            (data.recent_admissions || []).slice(0, 10).map((a: any) => ({
              hadm_id: a.hadm_id,
              subject_id: a.subject_id,
              department: a.department || "—",
            }))
          );
          setSimConnected(true);
        }
      } catch {
        if (!cancelled) {
          setSimEdPatients([]);
          setSimConnected(false);
        }
      }
    };
    fetchSimEd();
    const id = setInterval(fetchSimEd, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const loadSimJourney = async (hadmId: string | number, subjectId: number) => {
    setLoading(true);
    setError(null);
    setSimJourney(null);
    setSimPatientLabel(`Sim #${subjectId}`);
    // Clear static patient so we show sim data
    setPatient(null);
    setSelectedHadm(null);
    setSimActiveHadm(hadmId);
    try {
      const res = await fetch(`/api/sim/patient/${hadmId}/journey`);
      if (!res.ok) throw new Error("failed to load sim journey");
      const data: SimJourneyData = await res.json();
      setSimJourney(data);
      // Use the original MIMIC hadm_id so the Patient Journey API (port 8205)
      // can find real timeline/vitals/labs/care-path records.
      const realHadm: number = data.admission?.original_hadm_id ?? (typeof hadmId === "number" ? hadmId : parseInt(String(hadmId).replace(/\D/g, ""), 10));
      setPatient({
        subject_id: subjectId,
        gender: "U",
        anchor_age: 0,
        admissions: [{
          hadm_id: realHadm,
          admittime: data.transfers?.[0]?.intime || new Date().toISOString(),
          dischtime: data.transfers?.[data.transfers.length - 1]?.outtime || new Date().toISOString(),
          admission_type: "SIM",
          discharge_location: "Simulation",
          hospital_expire_flag: 0,
        }],
      } as PatientSummary);
      setSelectedHadm(realHadm);
    } catch {
      setError("Could not load sim patient journey.");
    } finally {
      setLoading(false);
    }
  };

  // Admission comparison: current vs previous admission for the same patient
  const [admissionCompare, setAdmissionCompare] = useState<AdmissionCompare | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);

  // Find the previous admission relative to the selected one
  const previousAdmission = useMemo(() => {
    if (!patient?.admissions || !selectedHadm) return null;
    const sorted = [...patient.admissions].sort(
      (a, b) => new Date(a.admittime ?? 0).getTime() - new Date(b.admittime ?? 0).getTime()
    );
    const idx = sorted.findIndex((a) => a.hadm_id === selectedHadm);
    return idx > 0 ? sorted[idx - 1] : null;
  }, [patient, selectedHadm]);

  const addToCompare = useCallback(async () => {
    if (!patient || !selectedHadm || !previousAdmission) return;
    setCompareLoading(true);
    const [currMetrics, prevMetrics] = await Promise.all([
      journeyMetrics(patient.subject_id, selectedHadm),
      journeyMetrics(patient.subject_id, previousAdmission.hadm_id),
    ]);
    setCompareLoading(false);
    if (!currMetrics || !prevMetrics) return;
    setAdmissionCompare({
      currentHadm: selectedHadm,
      previousHadm: previousAdmission.hadm_id,
      currentMetrics: currMetrics,
      previousMetrics: prevMetrics,
    });
  }, [patient, selectedHadm, previousAdmission]);

  const clearCompare = useCallback(() => {
    setAdmissionCompare(null);
  }, []);

  const loadPatient = useCallback(
    async (id?: number) => {
      const sid = id ?? parseInt(subjectInput, 10);
      if (!sid || isNaN(sid)) {
        setError("Enter a valid patient ID (number).");
        return;
      }
      setError(null);
      setLoading(true);
      setPatient(null);
      setSelectedHadm(null);
      // Searching for a real MIMIC patient → exit sim mode + stop live polling
      setSimJourney(null);
      setSimPatientLabel(null);
      setSimActiveHadm(null);

      const result = await journeyPatientSummaryDetailed(sid);
      setLoading(false);

      if (!result.ok) {
        if (result.reason === "not_found") {
          setError(
            `Patient ${sid} not found in the Patient Journey dataset. Try a different subject_id.`
          );
        } else if (result.reason === "bad_request") {
          setError(result.message);
        } else {
          // Don't leak the python/uvicorn restart command into the
          // operator UI — that's developer guidance. Surface a friendly
          // banner and direct curious users to System Admin.
          setError(
            "Patient Journey service is currently unavailable. Check status on the System Admin page or try again in a moment.",
          );
        }
        return;
      }

      setPatient(result.data);
      setSubjectInput(String(sid));
      if (result.data.admissions && result.data.admissions.length > 0) {
        setSelectedHadm(result.data.admissions[0].hadm_id);
      }

      // Auto-discover an active SIM admission for this subject so the
      // Journey Walk tab has live data (vitals, labs, transfers, meds).
      // If multiple exist, prefer the one with the latest sim_admittime.
      try {
        const res = await fetch(`/api/sim/active-patients`);
        if (res.ok) {
          const body = await res.json();
          const list: any[] = body.patients || body.data || [];
          const matches = list.filter((a) => String(a.subject_id) === String(sid));
          if (matches.length > 0) {
            matches.sort((a, b) => String(b.sim_admittime || "").localeCompare(String(a.sim_admittime || "")));
            const simHadm = matches[0].hadm_id;
            if (simHadm) {
              setSimActiveHadm(simHadm);
              setSimPatientLabel(`Sim #${sid}`);
              // Best-effort preload of the journey snapshot so widgets don't start empty.
              fetch(`/api/sim/patient/${simHadm}/journey`)
                .then((r) => (r.ok ? r.json() : null))
                .then((data) => { if (data) setSimJourney(data as SimJourneyData); })
                .catch(() => {});
            }
          }
        }
      } catch {
        /* sim offline → remain in MIMIC-only mode */
      }
    },
    [subjectInput]
  );

  const selectedAdmission = patient?.admissions?.find((a) => a.hadm_id === selectedHadm);

  return (
    <div className="space-y-5">
      {/* Live Sim Patients */}
      {simConnected && simEdPatients.length > 0 && (
        <div className="bg-bg-card rounded-xl border border-green-500/30 p-4 animate-fade-in">
          <div className="flex items-center gap-2 mb-2.5">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" />
            </span>
            <span className="text-xs font-semibold text-green-400 uppercase">Live Sim Patients</span>
            <span className="text-[10px] text-slate-500">Click to load journey</span>
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {simEdPatients.map((sp) => (
              <button
                key={sp.hadm_id}
                onClick={() => loadSimJourney(sp.hadm_id, sp.subject_id)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono-clinical text-blue-400 hover:text-blue-300 bg-blue-500/10 hover:bg-blue-500/20 rounded-full border border-blue-500/20 transition-colors shrink-0"
              >
                #{sp.subject_id}
                <span className="text-[9px] text-slate-500">{sp.department}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search Bar */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex flex-col sm:flex-row items-start sm:items-end gap-4">
          <div className="flex-1 w-full">
            <label className="block text-xs text-slate-400 font-medium mb-1.5">Patient Subject ID</label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="number"
                value={subjectInput}
                onChange={(e) => setSubjectInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && loadPatient()}
                placeholder="Enter subject_id..."
                className="w-full pl-10 pr-4 py-2.5 bg-bg-primary border border-border rounded-lg text-text-primary text-sm font-mono-clinical placeholder:text-text-muted focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
          </div>
          <button
            onClick={() => loadPatient()}
            disabled={loading}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Load Patient
          </button>
        </div>

        {/* Quick-load sample patients */}
        <div className="flex items-center gap-2 mt-3">
          <span className="text-[11px] text-slate-500">Quick load:</span>
          {SAMPLE_PATIENTS.map((sid) => (
            <button
              key={sid}
              onClick={() => {
                setSubjectInput(String(sid));
                loadPatient(sid);
              }}
              className="px-2.5 py-1 text-xs font-mono-clinical text-blue-400 hover:text-blue-300 bg-blue-500/10 hover:bg-blue-500/20 rounded border border-blue-500/20 transition-colors"
            >
              {sid}
            </button>
          ))}
        </div>

        {error && (
          <div className="mt-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400 whitespace-pre-line">
            <AlertTriangle className="w-4 h-4 inline mr-2" />
            {error}
          </div>
        )}
      </div>

      {/* When the user has searched but nothing loaded (and no error
          banner is showing above), surface an empty-state in the journey
          region so the user isn't staring at vacant whitespace. (F19) */}
      {!patient && !loading && !error && subjectInput.trim() !== "" && (
        <div className="bg-bg-card border border-border rounded-xl p-6 text-sm text-text-muted text-center">
          No journey loaded yet. Click <span className="font-mono-clinical">Load Patient</span> or pick a
          patient chip above.
        </div>
      )}

      {/* Patient Demographics */}
      {patient && (
        <div className="bg-bg-card rounded-xl border border-border p-5 animate-fade-in">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-blue-500/20 flex items-center justify-center">
                <User className="w-6 h-6 text-blue-400" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-text-primary">
                  Patient #{patient.subject_id}
                </h2>
                <p className="text-sm text-slate-400">
                  {patient.gender === "F" ? "Female" : "Male"} | Age ~{patient.anchor_age} |{" "}
                  {patient.admissions?.length || 0} admission(s)
                </p>
                {simActiveHadm && (
                  <p className="text-xs text-emerald-400 mt-1 flex items-center gap-1.5">
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                    </span>
                    Live sim admission linked — <span className="font-mono-clinical text-[11px]">{String(simActiveHadm)}</span>
                  </p>
                )}
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Compare with previous admission button */}
              {selectedHadm && previousAdmission && (
                <button
                  onClick={addToCompare}
                  disabled={compareLoading || (admissionCompare?.currentHadm === selectedHadm)}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-lg border transition-colors ${
                    admissionCompare?.currentHadm === selectedHadm
                      ? "bg-green-500/10 text-green-400 border-green-500/20 cursor-default"
                      : "bg-purple-500/10 text-purple-400 border-purple-500/20 hover:bg-purple-500/20 hover:text-purple-300"
                  }`}
                >
                  {compareLoading ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <BarChart3 className="w-3.5 h-3.5" />
                  )}
                  {admissionCompare?.currentHadm === selectedHadm ? "Compared" : "Compare with Previous"}
                </button>
              )}

              {/* Admission selector */}
              {patient.admissions && patient.admissions.length > 0 && (
                <div className="flex items-center gap-2">
                  <label className="text-xs text-slate-400 whitespace-nowrap">Admission:</label>
                  <select
                    value={selectedHadm ?? ""}
                    onChange={(e) => {
                      setSelectedHadm(Number(e.target.value));
                      setActiveTab("walkthrough");
                    }}
                    className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-sm text-text-primary font-mono-clinical focus:outline-none focus:border-blue-500"
                  >
                    {patient.admissions.map((a) => (
                      <option key={a.hadm_id} value={a.hadm_id}>
                        #{a.hadm_id} | {formatDate(a.admittime)} | {a.admission_type}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>

          {/* Selected admission summary */}
          {selectedAdmission && (
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
              <span>
                Admitted: <span className="font-mono-clinical text-slate-300">{formatDate(selectedAdmission.admittime)}</span>
              </span>
              <span>
                Discharged: <span className="font-mono-clinical text-slate-300">{formatDate(selectedAdmission.dischtime)}</span>
              </span>
              <span>
                Type: <span className="text-slate-300">{selectedAdmission.admission_type}</span>
              </span>
              <span>
                Discharge to: <span className="text-slate-300">{selectedAdmission.discharge_location}</span>
              </span>
              {selectedAdmission.hospital_expire_flag === 1 && (
                <span className="text-red-400 font-semibold">EXPIRED</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      {patient && selectedHadm && (
        <div className="animate-fade-in">
          {/* Tab buttons */}
          <div className="flex gap-1 bg-bg-card rounded-t-xl border border-border border-b-0 p-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? "bg-blue-500/15 text-blue-400"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
                }`}
              >
                <tab.icon className="w-4 h-4" />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="bg-bg-card rounded-b-xl rounded-tr-xl border border-border p-5">
            {activeTab === "walkthrough" && (
              <WalkthroughTab
                key={`wt-${selectedHadm}-${simActiveHadm ?? ""}`}
                simHadmId={simActiveHadm}
                staticAdmission={patient.admissions?.find((a) => a.hadm_id === selectedHadm)}
              />
            )}
            {activeTab === "timeline" && (
              <TimelineTab key={`tl-${selectedHadm}`} subjectId={patient.subject_id} hadmId={selectedHadm} />
            )}
            {activeTab === "vitals" && (
              <VitalsTab
                key={`vt-${selectedHadm}-${simActiveHadm ?? ""}`}
                subjectId={patient.subject_id}
                hadmId={selectedHadm}
                simVitals={simJourney?.vitals as Record<string, number> | undefined}
                simHadmId={simActiveHadm}
              />
            )}
            {activeTab === "labs" && (
              <LabsTab
                key={`lb-${selectedHadm}-${simActiveHadm ?? ""}`}
                subjectId={patient.subject_id}
                hadmId={selectedHadm}
                simHadmId={simActiveHadm}
              />
            )}
            {activeTab === "carepath" && (
              <CarePathTab
                key={`cp-${selectedHadm}-${simActiveHadm ?? ""}`}
                subjectId={patient.subject_id}
                hadmId={selectedHadm}
                simHadmId={simActiveHadm}
              />
            )}
          </div>
        </div>
      )}

      {/* Admission Comparison Table */}
      {admissionCompare && (
        <AdmissionComparisonTable
          data={admissionCompare}
          onClose={clearCompare}
        />
      )}

      {/* Integration 6 — high-risk oncology flags pushed by Oncology AI */}
      <HighRiskPanel />
    </div>
  );
}
