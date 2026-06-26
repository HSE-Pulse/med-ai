import { useState, useRef, useEffect, useCallback } from "react";
import { ChatHistoryViewer } from "../components/UpliftWidgets";
import {
  Send,
  ChevronDown,
  ChevronRight,
  Brain,
  Cog,
  User,
  Bot,
  AlertTriangle,
  Clock,
  Pill,
  Activity,
  Users,
  Table,
  Bell,
  X,
  ChevronUp,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import RiskGauge from "../components/RiskGauge";

// crypto.randomUUID is only defined in secure contexts (HTTPS or
// localhost). On http://homelab:3010 the browser does not consider the
// page secure, so calling it throws and React unmounts → blank page.
// This shim prefers the native API when available and falls back to a
// Math.random-based v4 generator otherwise.
function uuid(): string {
  const c = (typeof crypto !== "undefined" ? crypto : undefined) as Crypto | undefined;
  if (c?.randomUUID) {
    try {
      return c.randomUUID();
    } catch {
      /* secure-context error → fall through */
    }
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/* ─── Types ─── */

type Widget =
  | { type: "vitals_chart"; data: Record<string, Array<{ time: string; value: number }>> }
  | { type: "risk_gauge"; data: { readmission_risk: number; mortality_risk: number; risk_level: string } }
  | { type: "lab_panel"; data: Record<string, Array<{ time: string; value: number; flag: string }>> }
  | { type: "timeline"; data: Array<{ timestamp: string; event_type: string; category: string; details: any }> }
  | { type: "medication_list"; data: Array<{ drug: string; category: string; start_time: string; stop_time: string }> }
  | { type: "pathway"; data: Array<{ step: number; treatment: string; category: string; estimated_days: number }> }
  | { type: "triage_result"; data: { esi_level: number; confidence: number; disposition: string; risk_factors: string[] } }
  | { type: "patient_summary"; data: { subject_id: number; gender: string; anchor_age: number; admissions: any[] } }
  | { type: "cohort_stats"; data: Record<string, any> }
  | { type: "table"; data: { headers: string[]; rows: any[][] } };

interface Alert {
  severity: string;
  type: string;
  message: string;
}

interface PendingAction {
  intent: string;
  missing: string[];
}

interface SessionContext {
  patient_id: number | null;
  hadm_id: number | null;
  patient_name: string | null;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string[];
  widgets?: Widget[];
  alerts?: Alert[];
  pending_action?: PendingAction | null;
  session?: SessionContext | null;
  timestamp: Date;
  model?: string;
}

/* ─── Color helpers ─── */

const VITALS_COLORS = ["#3B82F6", "#22C55E", "#F97316", "#EAB308", "#EC4899", "#8B5CF6", "#06B6D4"];

const CATEGORY_COLORS: Record<string, string> = {
  antibiotic: "#22C55E",
  vasopressor: "#DC2626",
  analgesic: "#F97316",
  sedative: "#8B5CF6",
  chemotherapy: "#EC4899",
  immunotherapy: "#06B6D4",
  surgery: "#3B82F6",
  radiation: "#EAB308",
  default: "#64748B",
};

function categoryColor(cat: string) {
  return CATEGORY_COLORS[cat?.toLowerCase()] ?? CATEGORY_COLORS.default;
}

const FLAG_COLORS: Record<string, string> = {
  high: "text-red-400",
  low: "text-blue-400",
  critical: "text-red-500 font-bold",
  normal: "text-green-400",
};

/* ─── Alert Banner Component ─── */

function AlertBanner({ alerts }: { alerts: Alert[] }) {
  return (
    <div className="space-y-1.5 mb-2">
      {alerts.map((alert, i) => {
        const isCritical = alert.severity?.toLowerCase() === "critical";
        return (
          <div
            key={i}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${
              isCritical
                ? "bg-red-500/15 border-red-500/30 animate-pulse-border"
                : "bg-yellow-500/15 border-yellow-500/30"
            }`}
          >
            <AlertTriangle
              className={`w-4 h-4 shrink-0 ${
                isCritical ? "text-red-400" : "text-yellow-400"
              }`}
            />
            <span
              className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${
                isCritical
                  ? "bg-red-500/25 text-red-300"
                  : "bg-yellow-500/25 text-yellow-300"
              }`}
            >
              {alert.severity}
            </span>
            <span
              className={`text-xs font-medium ${
                isCritical ? "text-red-200" : "text-yellow-200"
              }`}
            >
              {alert.message}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Session Context Bar ─── */

function SessionBar({
  session,
  onClear,
}: {
  session: SessionContext;
  onClear: () => void;
}) {
  if (!session.patient_id) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-500/10 border border-blue-500/20 rounded-lg text-xs">
      <User className="w-3.5 h-3.5 text-blue-400" />
      <span className="text-blue-300">
        Patient #{session.patient_id}
        {session.hadm_id && (
          <span className="text-blue-400/70"> | Admission #{session.hadm_id}</span>
        )}
        {session.patient_name && (
          <span className="text-blue-400/70"> - {session.patient_name}</span>
        )}
        <span className="text-blue-500/50 ml-1">in context</span>
      </span>
      <button
        onClick={onClear}
        className="ml-auto text-blue-400/50 hover:text-blue-300 transition-colors"
        title="Clear Context"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

/* ─── Pending Action Indicator ─── */

function PendingActionBar({ action }: { action: PendingAction }) {
  return (
    <div className="mt-2 px-3 py-1.5 bg-slate-700/50 rounded-lg text-xs text-slate-400 italic flex items-center gap-2">
      <Clock className="w-3.5 h-3.5 text-slate-500" />
      <span>
        Waiting for: {action.missing.join(", ")}
      </span>
    </div>
  );
}

/* ─── Alert History Panel ─── */

function AlertHistoryPanel({
  alerts,
  expanded,
  onToggle,
}: {
  alerts: Alert[];
  expanded: boolean;
  onToggle: () => void;
}) {
  if (alerts.length === 0) return null;

  const criticalCount = alerts.filter(
    (a) => a.severity?.toLowerCase() === "critical"
  ).length;

  return (
    <div className="border border-border rounded-lg bg-bg-card overflow-hidden">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-slate-700/30 transition-colors"
      >
        <Bell className="w-3.5 h-3.5 text-slate-400" />
        <span className="text-xs text-slate-300 font-medium">
          Alert History
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {criticalCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/20 text-red-400 font-bold">
              {criticalCount} critical
            </span>
          )}
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-700 text-slate-400">
            {alerts.length}
          </span>
          {expanded ? (
            <ChevronUp className="w-3.5 h-3.5 text-slate-500" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-slate-500" />
          )}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1 max-h-48 overflow-y-auto">
          {alerts.map((alert, i) => {
            const isCritical = alert.severity?.toLowerCase() === "critical";
            return (
              <div
                key={i}
                className={`flex items-center gap-2 px-2 py-1 rounded text-xs ${
                  isCritical
                    ? "bg-red-500/10 text-red-300"
                    : "bg-yellow-500/10 text-yellow-300"
                }`}
              >
                <AlertTriangle
                  className={`w-3 h-3 shrink-0 ${
                    isCritical ? "text-red-400" : "text-yellow-400"
                  }`}
                />
                <span className="text-[10px] font-bold uppercase opacity-70">
                  {alert.type}
                </span>
                <span className="flex-1 truncate">{alert.message}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ─── Widget Components ─── */

function VitalsWidget({ data }: { data: Record<string, Array<{ time: string; value: number }>> }) {
  const [visible, setVisible] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    Object.keys(data).forEach((k) => (init[k] = true));
    return init;
  });

  const keys = Object.keys(data);
  // Merge all time series into unified rows
  const timeSet = new Set<string>();
  keys.forEach((k) => data[k].forEach((p) => timeSet.add(p.time)));
  const times = Array.from(timeSet).sort();
  const merged = times.map((t) => {
    const row: Record<string, any> = { time: t };
    keys.forEach((k) => {
      const pt = data[k].find((p) => p.time === t);
      if (pt) row[k] = pt.value;
    });
    return row;
  });

  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        {keys.map((k, i) => (
          <button
            key={k}
            onClick={() => setVisible((v) => ({ ...v, [k]: !v[k] }))}
            className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
              visible[k]
                ? "border-transparent"
                : "border-slate-600 opacity-40"
            }`}
            style={{ backgroundColor: visible[k] ? VITALS_COLORS[i % VITALS_COLORS.length] + "33" : "transparent", color: VITALS_COLORS[i % VITALS_COLORS.length] }}
          >
            {k}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={merged}>
          <XAxis dataKey="time" tick={{ fill: "#94A3B8", fontSize: 10 }} />
          <YAxis tick={{ fill: "#94A3B8", fontSize: 10 }} />
          <Tooltip
            contentStyle={{ backgroundColor: "var(--color-tooltip-bg)", border: "1px solid var(--color-tooltip-border)", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "var(--color-text-primary)" }}
          />
          {keys.map((k, i) =>
            visible[k] ? (
              <Line
                key={k}
                dataKey={k}
                stroke={VITALS_COLORS[i % VITALS_COLORS.length]}
                strokeWidth={2}
                dot={false}
                name={k}
              />
            ) : null
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function RiskWidget({ data }: { data: { readmission_risk: number; mortality_risk: number; risk_level: string } }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2 flex items-center gap-6 justify-center">
      <RiskGauge value={data.readmission_risk} label="Readmission" size={140} />
      <RiskGauge value={data.mortality_risk} label="Mortality" size={140} />
      <div className="text-center">
        <span className="text-xs text-slate-400 block">Overall Risk</span>
        <span
          className={`text-sm font-bold ${
            data.risk_level === "critical"
              ? "text-red-400"
              : data.risk_level === "high"
              ? "text-orange-400"
              : data.risk_level === "moderate"
              ? "text-yellow-400"
              : "text-green-400"
          }`}
        >
          {data.risk_level?.toUpperCase()}
        </span>
      </div>
    </div>
  );
}

function LabWidget({ data }: { data: Record<string, Array<{ time: string; value: number; flag: string }>> }) {
  const labs = Object.keys(data);
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-400 border-b border-slate-700">
            <th className="text-left py-1 pr-4">Lab</th>
            <th className="text-left py-1 pr-4">Time</th>
            <th className="text-right py-1 pr-4">Value</th>
            <th className="text-left py-1">Flag</th>
          </tr>
        </thead>
        <tbody>
          {labs.map((lab) =>
            data[lab].slice(-3).map((entry, i) => (
              <tr key={`${lab}-${i}`} className="border-b border-slate-700/50">
                {i === 0 ? (
                  <td className="py-1 pr-4 text-slate-300 font-medium" rowSpan={Math.min(3, data[lab].length)}>
                    {lab}
                  </td>
                ) : null}
                <td className="py-1 pr-4 text-slate-500 font-mono-clinical">{entry.time}</td>
                <td className="py-1 pr-4 text-right font-mono-clinical text-slate-200">{entry.value}</td>
                <td className={`py-1 font-mono-clinical ${FLAG_COLORS[entry.flag?.toLowerCase()] ?? "text-slate-400"}`}>
                  {entry.flag}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function TimelineWidget({ data }: { data: any }) {
  const arr = Array.isArray(data) ? data : [];
  const events = arr.slice(0, 10);
  if (!events.length) return <p className="text-slate-500 text-xs mt-2">No timeline events.</p>;
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2">
      <div className="space-y-2">
        {events.map((ev, i) => (
          <div key={i} className="flex items-start gap-3">
            <div className="flex flex-col items-center">
              <div className="w-2.5 h-2.5 rounded-full mt-1" style={{ backgroundColor: categoryColor(ev.category) }} />
              {i < events.length - 1 && <div className="w-px h-6 bg-slate-700" />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-200">{ev.event_type}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">{ev.category}</span>
              </div>
              <p className="text-[10px] text-slate-500 font-mono-clinical">{ev.timestamp}</p>
              {ev.details && typeof ev.details === "string" && (
                <p className="text-[11px] text-slate-400 mt-0.5">{ev.details}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MedicationWidget({ data }: { data: any }) {
  const items = Array.isArray(data) ? data : data?.medications || [];
  if (!items.length) return <p className="text-slate-500 text-xs mt-2">No medication data.</p>;
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2">
      <div className="space-y-1.5">
        {items.map((med: any, i: number) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: categoryColor(med.category) }} />
            <span className="text-slate-200 font-medium flex-1 truncate">{med.drug}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">{med.category}</span>
            <span className="text-slate-500 font-mono-clinical text-[10px]">
              {med.start_time} → {med.stop_time || "ongoing"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PathwayWidget({ data }: { data: any }) {
  // Handle various response shapes from the API
  const items: Array<{ step: number; treatment: string; category: string; estimated_days: number }> =
    Array.isArray(data) ? data :
    data?.data?.treatment_sequence ? data.data.treatment_sequence :
    data?.treatment_sequence ? data.treatment_sequence :
    [];
  if (!items.length) return <p className="text-slate-500 text-xs mt-2">No pathway data available.</p>;
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2">
      <div className="space-y-2">
        {items.map((s, i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="w-6 h-6 rounded-full bg-blue-500/20 text-blue-400 flex items-center justify-center text-xs font-bold shrink-0">
              {s.step}
            </div>
            <div className="flex-1 min-w-0">
              <span className="text-xs text-slate-200">{s.treatment}</span>
            </div>
            <span
              className="text-[10px] px-1.5 py-0.5 rounded"
              style={{ backgroundColor: categoryColor(s.category) + "22", color: categoryColor(s.category) }}
            >
              {s.category}
            </span>
            <span className="text-[10px] text-slate-500 font-mono-clinical">{s.estimated_days}d</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TriageWidget({ data }: { data: any }) {
  // Unwrap: data may be {acuity_level,...} or {data: {acuity_level,...}}
  const d = data?.acuity_level != null ? data : data?.data || data || {};
  const esiLevel = d.acuity_level ?? d.esi_level ?? 3;
  const confidence = (d.confidence ?? 0) * (d.confidence < 1 ? 100 : 1);
  const disposition = d.disposition ?? "unknown";
  const riskFactors = d.risk_factors ?? [];
  const esiColors: Record<number, string> = {
    1: "#DC2626",
    2: "#F97316",
    3: "#EAB308",
    4: "#22C55E",
    5: "#3B82F6",
  };
  const color = esiColors[esiLevel] ?? "#64748B";

  return (
    <div className="bg-slate-800/50 rounded-lg p-4 mt-2">
      <div className="flex items-center gap-6">
        {/* ESI Badge */}
        <div className="flex flex-col items-center">
          <div
            className="w-16 h-16 rounded-full flex items-center justify-center text-2xl font-bold border-4"
            style={{ borderColor: color, color }}
          >
            {esiLevel}
          </div>
          <span className="text-[10px] text-slate-400 mt-1">ESI Level</span>
        </div>

        <div className="flex-1 space-y-3">
          {/* Confidence bar */}
          <div>
            <div className="flex justify-between text-xs mb-1">
              <span className="text-slate-400">Confidence</span>
              <span className="font-mono-clinical text-slate-200">{confidence.toFixed(1)}%</span>
            </div>
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${confidence}%`, backgroundColor: color }}
              />
            </div>
          </div>

          {/* Disposition */}
          <div className="text-xs">
            <span className="text-slate-400">Disposition: </span>
            <span className="text-slate-200 font-medium">{disposition}</span>
          </div>

          {/* Risk factors */}
          {riskFactors.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {riskFactors.map((rf: string, i: number) => (
                <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 border border-red-500/20">
                  {rf}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PatientWidget({ data }: { data: { subject_id: number; gender: string; anchor_age: number; admissions: any[] } }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2">
      <div className="flex items-center gap-4">
        <div className="w-10 h-10 rounded-full bg-blue-500/20 text-blue-400 flex items-center justify-center">
          <User className="w-5 h-5" />
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-slate-200">Patient #{data.subject_id}</div>
          <div className="text-xs text-slate-400 flex gap-3 mt-0.5">
            <span>Gender: {data.gender}</span>
            <span>Age: {data.anchor_age}</span>
            <span>Admissions: {data.admissions?.length ?? 0}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function CohortWidget({ data }: { data: Record<string, any> }) {
  const entries = Object.entries(data);
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {entries.map(([key, value]) => (
          <div key={key} className="bg-slate-700/40 rounded p-2 text-center">
            <div className="text-xs text-slate-400 truncate">{key.replace(/_/g, " ")}</div>
            <div className="text-sm font-mono-clinical text-slate-200 mt-0.5">
              {typeof value === "number" ? value.toLocaleString() : String(value)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TableWidget({ data }: { data: { headers: string[]; rows: any[][] } }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 mt-2 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-400 border-b border-slate-700">
            {data.headers.map((h, i) => (
              <th key={i} className="text-left py-1 pr-3 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, ri) => (
            <tr key={ri} className="border-b border-slate-700/50">
              {row.map((cell, ci) => (
                <td key={ci} className="py-1 pr-3 text-slate-300 font-mono-clinical">
                  {String(cell ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function unwrapData(raw: any): any {
  // API responses may be wrapped in {status, data, error} envelope
  if (raw && typeof raw === "object" && "status" in raw && "data" in raw) return raw.data;
  // Some are double-wrapped: {summary: {status, data}}
  if (raw && typeof raw === "object") {
    const keys = Object.keys(raw);
    if (keys.length === 1 && typeof raw[keys[0]] === "object" && "status" in raw[keys[0]] && "data" in raw[keys[0]]) {
      return raw[keys[0]].data;
    }
  }
  return raw;
}

function WidgetRenderer({ widget }: { widget: Widget }) {
  const data = unwrapData(widget.data);
  try {
    switch (widget.type) {
      case "vitals_chart":
        return <VitalsWidget data={data?.vitals || data} />;
      case "risk_gauge":
        return <RiskWidget data={data} />;
      case "lab_panel":
        return <LabWidget data={data?.panels || data} />;
      case "timeline":
        return <TimelineWidget data={data?.events || (Array.isArray(data) ? data : [])} />;
      case "medication_list":
        return <MedicationWidget data={data?.medications || (Array.isArray(data) ? data : [])} />;
      case "pathway":
        return <PathwayWidget data={data} />;
      case "triage_result":
        return <TriageWidget data={data} />;
      case "patient_summary":
        return <PatientWidget data={data} />;
      case "cohort_stats":
        return <CohortWidget data={data} />;
      case "table":
        return <TableWidget data={data} />;
      default:
        return null;
    }
  } catch {
    return <p className="text-red-400 text-xs mt-1">Widget render error</p>;
  }
}

/* ─── Thinking Display ─── */

function ThinkingSection({
  thinking,
  expanded,
  onToggle,
  loading,
}: {
  thinking: string[];
  expanded: boolean;
  onToggle: () => void;
  loading?: boolean;
}) {
  return (
    <div className="bg-slate-800/50 border-l-2 border-blue-500/30 rounded-r-lg mt-2">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-slate-700/30 transition-colors rounded-tr-lg"
      >
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-slate-400" />
        )}
        <Brain className="w-3.5 h-3.5 text-blue-400/60" />
        <span className="text-xs text-slate-400">
          Thinking{loading ? <AnimatedDots /> : `... (${thinking.length} steps)`}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1">
          {thinking.map((step, i) => (
            <div key={i} className="flex items-start gap-2">
              <Cog className="w-3 h-3 text-slate-600 mt-0.5 shrink-0" />
              <span className="text-xs text-slate-500">{step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Animated Dots ─── */

function AnimatedDots() {
  return (
    <span className="inline-flex ml-1">
      <span className="animate-bounce" style={{ animationDelay: "0ms", animationDuration: "1s" }}>.</span>
      <span className="animate-bounce" style={{ animationDelay: "150ms", animationDuration: "1s" }}>.</span>
      <span className="animate-bounce" style={{ animationDelay: "300ms", animationDuration: "1s" }}>.</span>
    </span>
  );
}

/* ─── Suggestion Chips ─── */

const SUGGESTIONS = [
  "Triage patient with HR 120, SpO2 88",
  "Look up patient 10312052",
  "Analyze oncology cohort",
  "What is SOFA score?",
  "Assess cancer risk for 65M Stage 3 Lung",
];

/* ─── Models ─── */

const MODELS = [
  { value: "auto", label: "Auto (Smart Routing)" },
  { value: "deepseek-r1:8b", label: "DeepSeek-R1 8B (Medical CoT)" },
  { value: "MedAIBase/MedGemma1.5:4b-it", label: "MedGemma 1.5 4B (Fast Medical)" },
  { value: "koesn/llama3-openbiollm-8b:q4_K_M", label: "OpenBioLLM 8B (Biomedical)" },
  { value: "qwen3:8b", label: "Qwen3 8B (Reasoning)" },
  { value: "llama3.2:3b", label: "Llama 3.2 3B (Fast)" },
  { value: "meditron:7b", label: "Meditron 7B (Medical)" },
];

/* ─── Placeholder helper ─── */

function getInputPlaceholder(pendingAction: PendingAction | null | undefined): string {
  if (!pendingAction || !pendingAction.missing || pendingAction.missing.length === 0) {
    return "Ask a clinical question...";
  }
  const field = pendingAction.missing[0];
  switch (field) {
    case "patient_id":
      return "Enter patient ID...";
    case "hadm_id":
      return "Enter admission ID...";
    default:
      return `Enter ${field.replace(/_/g, " ")}...`;
  }
}

/* ─── Main Component ─── */

export default function ClinicalChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState("auto");
  const [thinkingExpanded, setThinkingExpanded] = useState<Record<string, boolean>>({});

  // Agentic state
  const [sessionId] = useState(() => uuid());
  const [sessionContext, setSessionContext] = useState<SessionContext | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [alertHistory, setAlertHistory] = useState<Alert[]>([]);
  const [alertPanelExpanded, setAlertPanelExpanded] = useState(false);
  const [historyViewerOpen, setHistoryViewerOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Fetch session context from backend.
  //
  // Unconditionally sync local state with what the server has. Previous
  // builds skipped ``setSessionContext(null)`` when the server had no
  // patient — that meant the context bar could display a stale patient
  // even after the server had cleared it. Now we always reflect server
  // truth.
  const fetchSession = useCallback(async () => {
    try {
      const res = await fetch(`/api/chat/session/${sessionId}`);
      if (res.ok) {
        const data = await res.json();
        if (data && (data.patient_id || data.hadm_id)) {
          setSessionContext(data);
        } else {
          setSessionContext(null);
        }
      }
    } catch {
      // Session endpoint may not exist yet - silently ignore
    }
  }, [sessionId]);

  // Welcome message + initial session fetch on mount
  useEffect(() => {
    const welcomeMsg: ChatMessage = {
      id: "welcome",
      role: "assistant",
      content: `Welcome to MedAI Clinical Assistant. I can help you with:
- Patient lookup & journey tracking
- ED triage predictions
- Cancer risk assessment & treatment pathways
- Lab & vital sign analysis
- Clinical note NLP extraction
- SOFA score & sepsis assessment

Try asking: "Look up patient 10312052" or "Triage a patient with HR 42, SpO2 82, SBP 78"`,
      timestamp: new Date(),
    };
    setMessages([welcomeMsg]);
    fetchSession();
  }, [fetchSession]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [messages, loading]);

  const clearSessionContext = async () => {
    // Clear locally first so the UI responds even if the network call is slow.
    setSessionContext(null);
    setPendingAction(null);
    try {
      // Use the granular /clear-patient endpoint so we keep the chat topics
      // buffer and conversation history. Previously we issued a full session
      // DELETE, which also wiped patient_data_cache + conversation_topics —
      // meaning follow-up non-patient questions lost useful continuity.
      await fetch(`/api/chat/session/${sessionId}/clear-patient`, { method: "POST" });
    } catch {
      // Server may be offline — local state is already cleared which is what
      // the user sees.
    }
  };

  const sendMessage = async (text?: string) => {
    const msg = text ?? input.trim();
    if (!msg || loading) return;

    const userMsg: ChatMessage = {
      id: uuid(),
      role: "user",
      content: msg,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    // Build history for API
    const history = messages
      .filter((m) => m.id !== "welcome")
      .map((m) => ({ role: m.role, content: m.content }));

    try {
      // ── FAQ cache fast-path (<50 ms) ──────────────────────────
      try {
        const fast = await fetch("/api/chat/chat/fast", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg, history, model, session_id: sessionId }),
        });
        if (fast.ok) {
          const fd = await fast.json();
          if (fd.cache_hit) {
            const assistantMsg: ChatMessage = {
              id: uuid(),
              role: "assistant",
              content: fd.response ?? "",
              thinking: fd.thinking,
              widgets: fd.widgets,
              alerts: fd.alerts,
              pending_action: null,
              session: null,
              timestamp: new Date(),
              model: `${model} (cache)`,
            };
            setMessages((prev) => [...prev, assistantMsg]);
            setLoading(false);
            return;
          }
        }
      } catch { /* cache miss or offline — fall through */ }

      // ── Streamed response (<500 ms TTFB) ──────────────────────
      // The placeholder assistant message is appended immediately and
      // filled in as tokens arrive. This is what makes the chat feel
      // instantaneous even when the LLM itself takes 5-60 s.
      const assistantId = uuid();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        thinking: [],
        widgets: [],
        alerts: [],
        pending_action: null,
        session: null,
        timestamp: new Date(),
        model: `${model} (streaming)`,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      let streamed = false;
      let streamStatus = 0;
      try {
        const res = await fetch("/api/chat/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
          body: JSON.stringify({ message: msg, history, model, session_id: sessionId }),
        });
        streamStatus = res.status;
        if (res.ok && res.body) {
          streamed = true;
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buf = "";
          let collectedAlerts: any[] = [];
          let collectedPending: any = null;
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            let idx = buf.indexOf("\n\n");
            while (idx !== -1) {
              const frame = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              idx = buf.indexOf("\n\n");
              // Parse SSE frame.
              // IMPORTANT: do NOT .trim() the data segment — the backend
              // always JSON-encodes the payload (including token strings),
              // so any whitespace *inside* the string (e.g. leading space
              // of " patient") must be preserved through the SSE pipe.
              // SSE spec says only the single space after "data:" is
              // consumed; multiple data: lines are joined with "\n".
              const lines = frame.split("\n");
              let ev = "message";
              const dataLines: string[] = [];
              for (const l of lines) {
                if (l.startsWith("event:")) {
                  ev = l.slice(6).trim();
                } else if (l.startsWith("data:")) {
                  // Strip the one SSE-protocol leading space if present;
                  // keep everything else byte-for-byte.
                  const rest = l.slice(5);
                  dataLines.push(rest.startsWith(" ") ? rest.slice(1) : rest);
                } else if (l.startsWith(":")) {
                  // SSE comment (keepalive) — ignore
                }
              }
              if (dataLines.length === 0) continue;
              const data = dataLines.join("\n");
              let payload: any;
              try {
                payload = JSON.parse(data);
              } catch {
                // Backend always JSON-encodes, so a parse error means a
                // stray non-encoded frame. Fall back to the raw string.
                payload = data;
              }
              if (ev === "token") {
                setMessages((prev) => prev.map((m) =>
                  m.id === assistantId ? { ...m, content: (m.content || "") + String(payload) } : m
                ));
              } else if (ev === "reasoning") {
                // Chain-of-thought tokens (deepseek-r1). Append to the
                // last thinking step if it's already a CoT bucket, else start one.
                setMessages((prev) => prev.map((m) => {
                  if (m.id !== assistantId) return m;
                  const th = [...(m.thinking ?? [])];
                  const last = th[th.length - 1] ?? "";
                  if (last.startsWith("CoT:")) {
                    th[th.length - 1] = last + String(payload);
                  } else {
                    th.push("CoT: " + String(payload));
                  }
                  return { ...m, thinking: th };
                }));
              } else if (ev === "thinking") {
                setMessages((prev) => prev.map((m) =>
                  m.id === assistantId ? { ...m, thinking: [...(m.thinking ?? []), String(payload)] } : m
                ));
              } else if (ev === "context") {
                if (payload && (payload.patient_id || payload.hadm_id)) {
                  setSessionContext(payload);
                  setMessages((prev) => prev.map((m) =>
                    m.id === assistantId ? { ...m, session: payload } : m
                  ));
                }
              } else if (ev === "widgets") {
                const widgets = Array.isArray(payload) ? payload : [];
                setMessages((prev) => prev.map((m) =>
                  m.id === assistantId ? { ...m, widgets } : m
                ));
              } else if (ev === "alerts") {
                const alerts = Array.isArray(payload) ? payload : [];
                collectedAlerts = alerts;
                setMessages((prev) => prev.map((m) =>
                  m.id === assistantId ? { ...m, alerts } : m
                ));
              } else if (ev === "pending_action") {
                collectedPending = payload;
                setPendingAction(payload);
                setMessages((prev) => prev.map((m) =>
                  m.id === assistantId ? { ...m, pending_action: payload } : m
                ));
              }
            }
          }
          if (collectedAlerts.length > 0) {
            setAlertHistory((prev) => [...prev, ...collectedAlerts]);
          }
        }
      } catch {
        streamed = false;
      }

      // ── Legacy fallback: full JSON response ───────────────────
      if (!streamed) {
        const res = await fetch("/api/chat/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg, history, model, session_id: sessionId }),
        });
        if (!res.ok) {
          // Use the streaming status if it's the more informative
          // failure (e.g. 503 from the proxy) — falls back to the
          // legacy POST status otherwise.
          const status = streamStatus && streamStatus !== 200 ? streamStatus : res.status;
          throw new Error(`API error: ${status}`);
        }
        const data = await res.json();
        setMessages((prev) => prev.map((m) => m.id === assistantId ? {
          ...m,
          content: data.response ?? "",
          thinking: data.thinking,
          widgets: data.widgets,
          alerts: data.alerts,
          pending_action: data.pending_action ?? null,
          session: data.session ?? null,
          model,
        } : m));
        if (data.session && (data.session.patient_id || data.session.hadm_id)) {
          setSessionContext(data.session);
        }
        setPendingAction(data.pending_action ?? null);
        if (data.alerts?.length) setAlertHistory((prev) => [...prev, ...data.alerts]);
      }

      fetchSession();
    } catch (err: any) {
      // Replace the streaming placeholder bubble (if it's still empty)
      // with a single error bubble — avoids the dangling "MedAI Assistant
      // (auto (streaming))" empty card the user was seeing on failures.
      const message = `Error: ${err.message}. Please check that the chat service is running, or try again in a moment.`;
      setMessages((prev) => {
        const idx = prev.findIndex(
          (m) => m.role === "assistant" && (m.model || "").includes("streaming") && !m.content,
        );
        const errorMsg: ChatMessage = {
          id: uuid(),
          role: "assistant",
          content: message,
          timestamp: new Date(),
        };
        if (idx === -1) return [...prev, errorMsg];
        const next = prev.slice();
        next[idx] = { ...next[idx], content: message, model: undefined };
        return next;
      });
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const toggleThinking = (id: string) => {
    setThinkingExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <>
    <div className="relative flex h-[calc(100vh-7.25rem)] -m-5">
      {/* Main chat column */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Session context bar */}
        {sessionContext && sessionContext.patient_id && (
          <div className="shrink-0 px-4 pt-2">
            <SessionBar session={sessionContext} onClear={clearSessionContext} />
          </div>
        )}

        {/* Messages area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] min-w-0 ${
                  msg.role === "user"
                    ? "bg-blue-600/20 border border-blue-500/20 rounded-2xl rounded-br-md px-4 py-3"
                    : "bg-bg-card border border-border rounded-2xl rounded-bl-md px-4 py-3"
                }`}
              >
                {/* Role indicator */}
                <div className="flex items-center gap-2 mb-1.5">
                  {msg.role === "assistant" ? (
                    <Bot className="w-3.5 h-3.5 text-blue-400" />
                  ) : (
                    <User className="w-3.5 h-3.5 text-slate-400" />
                  )}
                  <span className="text-[10px] text-slate-500">
                    {msg.role === "assistant" ? "MedAI Assistant" : "You"}
                    {msg.model && <span className="ml-1 text-slate-600">({msg.model})</span>}
                    <span className="ml-2">{msg.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  </span>
                </div>

                {/* Alerts - shown ABOVE response text */}
                {msg.alerts && msg.alerts.length > 0 && (
                  <AlertBanner alerts={msg.alerts} />
                )}

                {/* Content — break-words + overflow-wrap:anywhere so that
                    any long unbroken token string (e.g. markdown bold runs
                    like ``**ClinicalSummary**Thepatientwith...`` that a bad
                    streaming config can produce) still wraps inside the
                    bubble instead of overflowing horizontally off-screen. */}
                <div
                  className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed break-words"
                  style={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
                >
                  {msg.content}
                </div>

                {/* Pending action indicator */}
                {msg.pending_action && msg.pending_action.missing && msg.pending_action.missing.length > 0 && (
                  <PendingActionBar action={msg.pending_action} />
                )}

                {/* Thinking */}
                {msg.thinking && msg.thinking.length > 0 && (
                  <ThinkingSection
                    thinking={msg.thinking}
                    expanded={thinkingExpanded[msg.id] ?? false}
                    onToggle={() => toggleThinking(msg.id)}
                  />
                )}

                {/* Widgets */}
                {msg.widgets && msg.widgets.length > 0 && (
                  <div className="space-y-2 mt-2">
                    {msg.widgets.map((w, i) => (
                      <WidgetRenderer key={i} widget={w} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Loading indicator */}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-bg-card border border-border rounded-2xl rounded-bl-md px-4 py-3 max-w-[80%]">
                <div className="flex items-center gap-2 mb-1.5">
                  <Bot className="w-3.5 h-3.5 text-blue-400" />
                  <span className="text-[10px] text-slate-500">MedAI Assistant</span>
                </div>
                <div className="text-sm text-slate-400">
                  Thinking<AnimatedDots />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Input area */}
        <div className="shrink-0 bg-bg-card border-t border-border">
          {/* Suggestion chips */}
          {messages.length <= 1 && (
            <div className="flex flex-wrap gap-2 px-4 pt-3">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="text-xs px-3 py-1.5 rounded-full bg-bg-primary text-slate-400 hover:bg-slate-700 hover:text-slate-200 transition-colors border border-border"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Pending action context above input */}
          {pendingAction && pendingAction.missing && pendingAction.missing.length > 0 && (
            <div className="px-4 pt-2">
              <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-700/50 rounded-lg text-xs text-slate-400 italic">
                <Clock className="w-3.5 h-3.5 text-slate-500" />
                <span>
                  Provide {pendingAction.missing.join(", ")} to continue {pendingAction.intent} query
                </span>
              </div>
            </div>
          )}

          <div className="flex items-center gap-3 px-4 py-3">
            {/* Model selector */}
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="bg-bg-input border border-border rounded-lg px-2 py-2 text-xs text-slate-300 outline-none focus:border-blue-500/50 shrink-0"
            >
              {MODELS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>

            {/* Text input */}
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={getInputPlaceholder(pendingAction)}
              className="flex-1 bg-bg-input border border-border rounded-lg px-4 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-blue-500/50 transition-colors"
              disabled={loading}
            />

            {/* Send button */}
            <button
              onClick={() => sendMessage()}
              disabled={loading || !input.trim()}
              className="shrink-0 w-9 h-9 flex items-center justify-center rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white transition-colors"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Alert history sidebar */}
      {alertHistory.length > 0 && (
        <div className="shrink-0 w-72 border-l border-border p-3 overflow-y-auto">
          <AlertHistoryPanel
            alerts={alertHistory}
            expanded={alertPanelExpanded}
            onToggle={() => setAlertPanelExpanded((v) => !v)}
          />
        </div>
      )}

      {/* Toggle — opens ConversationBuffer history viewer. Anchored to
          the top-right of the chat area so it sits clear of both the
          composer (bottom) and the model selector (bottom-left). */}
      <button
        type="button"
        onClick={() => setHistoryViewerOpen(true)}
        title="View persisted session history (ConversationBuffer)"
        aria-label="View persisted session history"
        className="absolute top-3 right-3 z-30 px-3 py-1.5 rounded-full bg-indigo-600/90 text-white shadow-lg hover:bg-indigo-500 text-xs flex items-center gap-2"
      >
        View session buffer
      </button>
    </div>

    {/* History viewer modal */}
    {historyViewerOpen && (
      <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={() => setHistoryViewerOpen(false)}>
        <div className="w-full max-w-3xl" onClick={(e) => e.stopPropagation()}>
          <div className="mb-2 flex justify-end">
            <button
              onClick={() => setHistoryViewerOpen(false)}
              className="px-3 py-1 text-xs rounded bg-slate-700 text-slate-200 hover:bg-slate-600"
            >
              Close
            </button>
          </div>
          <ChatHistoryViewer defaultSessionId={sessionId} />
        </div>
      </div>
    )}
    </>
  );
}
