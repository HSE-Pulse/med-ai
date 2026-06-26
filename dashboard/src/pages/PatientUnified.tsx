import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  User,
  Activity,
  Heart,
  Beaker,
  Pill,
  AlertTriangle,
  Clock,
  FileSearch,
  Stethoscope,
  Loader2,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useAlerts } from "../context/AlertsContext";
import { usePoll } from "../hooks/usePoll";
import { useResolvePatient, type ResolvedPatient } from "../hooks/useResolvePatient";
import { formatDate, computeLosHours, formatLos } from "../utils/format";
import StatCard from "../components/StatCard";
import RiskGauge from "../components/RiskGauge";
import VitalSignChart from "../components/VitalSignChart";
import TimelineView from "../components/TimelineView";
import DeteriorationSummary from "../components/PatientPanels/DeteriorationSummary";
import RiskRationale, { type RiskPayload } from "../components/PatientPanels/RiskRationale";
import ClinicalContext from "../components/PatientPanels/ClinicalContext";

type TabKey = "summary" | "vitals" | "labs" | "meds" | "timeline" | "clinical";

const TABS: { key: TabKey; label: string; icon: typeof Activity }[] = [
  { key: "summary", label: "Summary", icon: User },
  { key: "vitals", label: "Vitals", icon: Heart },
  { key: "labs", label: "Labs", icon: Beaker },
  { key: "meds", label: "Medications", icon: Pill },
  { key: "timeline", label: "Timeline", icon: Clock },
  { key: "clinical", label: "Clinical", icon: Stethoscope },
];

// NEWS2-derived normal ranges, used by VitalSignChart reference lines
const VITAL_CONFIG: Record<string, { unit: string; normalMin: number; normalMax: number }> = {
  "Heart Rate":       { unit: "bpm",    normalMin: 60,  normalMax: 90 },
  "SBP":              { unit: "mmHg",   normalMin: 110, normalMax: 180 },
  "DBP":              { unit: "mmHg",   normalMin: 60,  normalMax: 90 },
  "Mean BP":          { unit: "mmHg",   normalMin: 70,  normalMax: 105 },
  "Respiratory Rate": { unit: "/min",   normalMin: 12,  normalMax: 20 },
  "SpO2":             { unit: "%",      normalMin: 96,  normalMax: 100 },
  "Temperature":      { unit: "°C",     normalMin: 36,  normalMax: 38 },
};

const VITAL_ORDER = [
  "Heart Rate", "SBP", "DBP", "Mean BP", "Respiratory Rate", "SpO2", "Temperature",
];

interface JourneySummary {
  status?: string;
  data?: {
    subject_id?: number;
    gender?: string | null;
    anchor_age?: number | null;
    admissions?: AdmissionRow[];
  };
}

interface AdmissionRow {
  hadm_id: number;
  admittime?: string;
  dischtime?: string;
  admission_type?: string;
  admission_location?: string;
  discharge_location?: string;
  insurance?: string;
  race?: string;
  hospital_expire_flag?: number;
  // Decorations from patient_journey's /summary handler.
  is_active?: boolean;
  is_expired?: boolean;
  state_label?: "active" | "discharged" | "expired";
  discharge_reason?: string;
}

export default function PatientUnified() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabKey>("summary");
  const { patient, loading: resolving } = useResolvePatient(id);

  if (resolving) {
    return (
      <div className="flex items-center justify-center h-96 text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        Resolving patient {id}…
      </div>
    );
  }

  if (!patient || patient.resolved_via === "unresolved") {
    return <UnresolvedState id={id ?? ""} />;
  }

  return (
    <div className="space-y-4">
      <PatientStateBanner patient={patient} />
      <Header patient={patient} tab={tab} setTab={setTab} onBack={() => navigate(-1)} />

      <div>
        {tab === "summary" && <SummaryTab patient={patient} />}
        {tab === "vitals" && <VitalsTab patient={patient} />}
        {tab === "labs" && <LabsTab patient={patient} />}
        {tab === "meds" && <MedsTab patient={patient} />}
        {tab === "timeline" && <TimelineTab patient={patient} />}
        {tab === "clinical" && <ClinicalContext hadmId={patient.hadm_id} />}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Patient state banner
//
// Surfaces "expired" / "discharged" status prominently at the top of the
// page so a user landing on a stale URL immediately understands the
// admission is closed and is no longer in the active operations system.
// Active patients show no banner. Driven by the ``state_label`` decoration
// the patient_journey API now adds to each admission row.
// ─────────────────────────────────────────────────────────────────────

function PatientStateBanner({ patient }: { patient: ResolvedPatient }) {
  const label = patient.state_label;
  if (!label || label === "active") return null;

  if (label === "expired") {
    return (
      <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 flex items-center gap-3">
        <AlertTriangle className="w-5 h-5 text-red-400 shrink-0" />
        <div className="flex-1">
          <div className="text-sm font-semibold text-red-300">
            EXPIRED — patient deceased
            {patient.dischtime ? ` on ${formatDate(patient.dischtime)}` : ""}.
          </div>
          <div className="text-xs text-red-300/70 mt-0.5">
            This admission is closed. Record shown for clinical review only — patient is
            not in the active operations system.
          </div>
        </div>
      </div>
    );
  }

  // discharged — not currently active in the operations system
  return (
    <div className="rounded-lg border border-slate-500/40 bg-slate-500/10 px-4 py-3 flex items-center gap-3">
      <AlertTriangle className="w-5 h-5 text-slate-400 shrink-0" />
      <div className="flex-1">
        <div className="text-sm font-semibold text-slate-200">
          DISCHARGED — admission closed
          {patient.dischtime ? ` on ${formatDate(patient.dischtime)}` : ""}.
        </div>
        <div className="text-xs text-slate-400 mt-0.5">
          Historical record only. Patient is no longer in the active operations system.
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Header
// ─────────────────────────────────────────────────────────────────────

function Header({
  patient, tab, setTab, onBack,
}: {
  patient: ResolvedPatient;
  tab: TabKey;
  setTab: (k: TabKey) => void;
  onBack: () => void;
}) {
  const { alerts, connected } = useAlerts();
  const patientAlerts = useMemo(
    () => alerts.filter((a) => a.patient_id && a.patient_id.toString() === patient.subject_id),
    [alerts, patient.subject_id],
  );
  const unackedCount = patientAlerts.filter((a) => !a.acknowledged).length;

  return (
    <div className="sticky top-0 z-10 -mx-5 px-5 pt-2 pb-4 bg-bg-primary/95 backdrop-blur border-b border-border">
      <button
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 mb-2"
      >
        <ArrowLeft className="w-3 h-3" />
        Back
      </button>
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-full bg-blue-500/20 text-blue-300 flex items-center justify-center">
          <User className="w-6 h-6" />
        </div>
        <div className="flex-1">
          <h1 className="text-xl font-semibold text-white">
            Patient{" "}
            <span className="font-mono-clinical text-blue-300">{patient.hadm_id}</span>
          </h1>
          <div className="text-xs text-slate-400 mt-0.5 flex flex-wrap gap-x-4 gap-y-1 items-center">
            <span>
              Subject:{" "}
              <span className="font-mono-clinical text-slate-300">{patient.subject_id}</span>
            </span>
            {patient.admission_type && <span>Type: {patient.admission_type}</span>}
            {patient.admission_location && <span>Location: {patient.admission_location}</span>}
            <span>Admitted: {formatDate(patient.admittime)}</span>
            {patient.dischtime && <span>Discharged: {formatDate(patient.dischtime)}</span>}
            <ResolvedViaBadge via={patient.resolved_via} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          {connected ? (
            <span className="flex items-center gap-1 text-[10px] text-green-400" title="Live alert stream connected">
              <Wifi className="w-3 h-3" /> live
            </span>
          ) : (
            <span className="flex items-center gap-1 text-[10px] text-slate-500" title="Falling back to polling">
              <WifiOff className="w-3 h-3" /> polling
            </span>
          )}
          {patientAlerts.length > 0 && (
            <div
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border ${
                unackedCount > 0
                  ? "bg-red-500/10 border-red-500/30 text-red-400"
                  : "bg-slate-500/10 border-border text-slate-400"
              }`}
            >
              <AlertTriangle className="w-4 h-4" />
              {unackedCount > 0
                ? `${unackedCount} unacked`
                : `${patientAlerts.length} alert${patientAlerts.length === 1 ? "" : "s"}`}
            </div>
          )}
        </div>
      </div>

      <nav className="flex items-center gap-1 mt-4" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            role="tab"
            aria-selected={tab === t.key}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
              tab === t.key
                ? "bg-blue-500/10 text-blue-400"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/40"
            }`}
          >
            <t.icon className="w-4 h-4" />
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}

function ResolvedViaBadge({ via }: { via: ResolvedPatient["resolved_via"] }) {
  const map: Record<ResolvedPatient["resolved_via"], { label: string; cls: string } | null> = {
    journey: null,
    alerts_search: null,
    deterioration_alerts: {
      label: "live (deterioration)",
      cls: "bg-amber-500/10 text-amber-300 border-amber-500/30",
    },
    unresolved: { label: "unresolved", cls: "bg-red-500/10 text-red-300 border-red-500/30" },
  };
  const m = map[via];
  if (!m) return null;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${m.cls}`}>{m.label}</span>
  );
}

function UnresolvedState({ id }: { id: string }) {
  return (
    <div className="bg-bg-card border border-border rounded-lg p-6 max-w-2xl">
      <div className="flex items-center gap-2 mb-3">
        <FileSearch className="w-5 h-5 text-amber-400" aria-hidden="true" />
        <h2 className="text-lg font-semibold text-white">Patient not resolved</h2>
      </div>
      <p className="text-sm text-slate-300 mb-3">
        Could not find an admission for ID{" "}
        <code className="font-mono-clinical text-blue-300">{id}</code>.
      </p>
      <p className="text-sm text-slate-400 mb-4">
        If this is a digital-twin patient that hasn&rsquo;t generated any events yet,
        give the simulator a moment and refresh — the page will reload data from the
        live feed.
      </p>
      {/* Diagnostic info hidden by default — shown only when a developer
          opens the disclosure. Avoids leaking internal lookup mechanics
          to clinical users. */}
      <details className="mt-2 text-xs text-slate-500 group">
        <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-300 transition-colors">
          Diagnostic details
        </summary>
        <div className="mt-2 pl-2 border-l border-border">
          <p className="text-xs text-slate-500 mb-1">Resolver tried, in order:</p>
          <ol className="text-xs text-slate-400 space-y-1 list-decimal list-inside">
            <li>Journey summary lookup (treats id as numeric subject_id)</li>
            <li>Alerts patient-search index</li>
            <li>SIM-prefix extraction → alerts search by extracted hadm_id</li>
            <li>Deterioration active-alerts scan (live SIM patients)</li>
          </ol>
        </div>
      </details>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Summary tab
// ─────────────────────────────────────────────────────────────────────

function SummaryTab({ patient }: { patient: ResolvedPatient }) {
  const summary = usePoll<JourneySummary>(
    `/api/journey/patient/${encodeURIComponent(patient.subject_id)}/summary`,
    20000,
  );
  // Onco GET-by-id endpoint doesn't exist (only POST /predict-risk). Try the
  // timeline endpoint as a best-effort source — it's the only GET path that
  // can carry pre-computed risk for a known patient. If it 500s, fields stay
  // null and the gauges show "—".
  const onco = usePoll<{ data?: RiskPayload | { latest?: RiskPayload } }>(
    `/api/onco/patient/${encodeURIComponent(patient.subject_id)}/timeline`,
    30000,
  );
  const { alerts } = useAlerts();
  const patientAlerts = useMemo(
    () => alerts.filter((a) => a.patient_id && a.patient_id.toString() === patient.subject_id),
    [alerts, patient.subject_id],
  );

  const admissions = summary.data?.data?.admissions ?? [];
  const totalAdmissions = admissions.length;
  const latest = useMemo(
    () =>
      [...admissions].sort((a, b) => {
        const ta = Date.parse((a.admittime ?? "").replace(" ", "T")) || 0;
        const tb = Date.parse((b.admittime ?? "").replace(" ", "T")) || 0;
        return tb - ta;
      })[0],
    [admissions],
  );
  const losHours = latest ? computeLosHours(latest.admittime, latest.dischtime) : null;

  // Onco timeline may wrap risk in {latest: {...}} or return the payload directly
  const oncoPayload = useMemo<RiskPayload | null>(() => {
    const d = onco.data?.data;
    if (!d) return null;
    if ("latest" in d && d.latest) return d.latest;
    return d as RiskPayload;
  }, [onco.data]);

  const readmission = oncoPayload?.readmission_30d_risk ?? null;
  const mortality = oncoPayload?.mortality_risk ?? null;
  const mortalityEvents = admissions.filter((a) => a.hospital_expire_flag).length;

  return (
    <div className="space-y-4">
      {/* Top-line KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          icon={<Clock className="w-4 h-4" />}
          label="Latest LOS"
          value={formatLos(losHours)}
          accentColor="#3B82F6"
          subtitle={latest?.admission_type ?? undefined}
        />
        <StatCard
          icon={<User className="w-4 h-4" />}
          label="Admissions"
          value={totalAdmissions || "—"}
          accentColor="#A855F7"
          subtitle={
            mortalityEvents > 0
              ? `${mortalityEvents} expired in hospital`
              : "no in-hospital deaths"
          }
        />
        <StatCard
          icon={<Activity className="w-4 h-4" />}
          label="Active alerts"
          value={patientAlerts.length || "—"}
          accentColor={patientAlerts.length > 0 ? "#F97316" : "#475569"}
          subtitle={
            patientAlerts.length === 0
              ? "no recent events"
              : `${patientAlerts.filter((a) => !a.acknowledged).length} unacked`
          }
        />
        <StatCard
          icon={<Stethoscope className="w-4 h-4" />}
          label="Disposition"
          value={
            latest?.state_label === "expired"
              ? "EXPIRED"
              : latest?.discharge_location
                ? latest.discharge_location.length > 14
                  ? latest.discharge_location.slice(0, 12) + "…"
                  : latest.discharge_location
                : latest?.dischtime
                  ? "discharged"
                  : "in-hospital"
          }
          accentColor={latest?.state_label === "expired" ? "#EF4444" : "#22C55E"}
          subtitle={latest?.dischtime ? formatDate(latest.dischtime) : "—"}
        />
      </div>

      {/* Risk gauges + rationale */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-stretch">
        <div className="bg-bg-card border border-border rounded-lg p-4 flex flex-col items-center justify-center">
          {readmission !== null ? (
            <RiskGauge value={readmission * 100} label="30-day readmission" size={180} />
          ) : (
            <RiskUnavailable label="30-day readmission" />
          )}
        </div>
        <div className="bg-bg-card border border-border rounded-lg p-4 flex flex-col items-center justify-center">
          {mortality !== null ? (
            <RiskGauge value={mortality * 100} label="Mortality" size={180} />
          ) : (
            <RiskUnavailable label="Mortality" />
          )}
        </div>
        <div>
          {oncoPayload ? (
            <RiskRationale data={oncoPayload} />
          ) : (
            <div className="bg-bg-card border border-border border-dashed rounded-lg p-4 text-xs text-slate-500 h-full flex items-center">
              Risk rationale not available — the oncology service has no GET-by-id risk
              endpoint. Run a prediction via the Oncology page to populate this.
            </div>
          )}
        </div>
      </div>

      {/* Deterioration */}
      <DeteriorationSummary hadmId={patient.hadm_id} />

      {/* Admissions history */}
      {admissions.length > 0 && (
        <div className="bg-bg-card border border-border rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Admissions history
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[10px] text-slate-500 uppercase">
                <th className="text-left px-3 py-1.5">hadm_id</th>
                <th className="text-left px-3 py-1.5">Type</th>
                <th className="text-left px-3 py-1.5">Admitted</th>
                <th className="text-left px-3 py-1.5">Discharged</th>
                <th className="text-left px-3 py-1.5">LOS</th>
                <th className="text-left px-3 py-1.5">Disposition</th>
              </tr>
            </thead>
            <tbody>
              {admissions.slice(0, 12).map((a) => {
                const h = computeLosHours(a.admittime, a.dischtime);
                const isCurrent = String(a.hadm_id) === patient.hadm_id;
                return (
                  <tr
                    key={a.hadm_id}
                    className={`border-b border-border last:border-b-0 ${
                      isCurrent ? "bg-blue-500/5" : ""
                    }`}
                  >
                    <td className="px-3 py-1.5 font-mono-clinical text-blue-300">
                      {a.hadm_id}
                      {isCurrent && (
                        <span className="ml-2 text-[10px] uppercase text-blue-400">current</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-slate-400 text-xs">{a.admission_type ?? "—"}</td>
                    <td className="px-3 py-1.5 text-slate-400 text-xs">{formatDate(a.admittime)}</td>
                    <td className="px-3 py-1.5 text-slate-400 text-xs">{formatDate(a.dischtime)}</td>
                    <td className="px-3 py-1.5 text-slate-300 font-mono-clinical text-xs">
                      {formatLos(h)}
                    </td>
                    <td
                      className={`px-3 py-1.5 text-xs ${
                        a.hospital_expire_flag ? "text-red-400" : "text-slate-400"
                      }`}
                    >
                      {a.hospital_expire_flag
                        ? "Expired in hospital"
                        : (a.discharge_location ?? "—")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Patient-scoped alerts */}
      <div className="bg-bg-card border border-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          <span className="text-sm font-semibold text-white">Recent alerts</span>
          <span className="text-xs text-slate-500">({patientAlerts.length})</span>
        </div>
        {patientAlerts.length === 0 ? (
          <p className="text-xs text-slate-500">No alerts recorded for this patient.</p>
        ) : (
          <ul className="space-y-1.5">
            {patientAlerts.slice(0, 10).map((a) => (
              <li key={a.id} className="flex items-center gap-2 text-xs">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    a.severity === "critical"
                      ? "bg-red-500"
                      : a.severity === "high"
                        ? "bg-orange-500"
                        : a.severity === "medium"
                          ? "bg-amber-500"
                          : "bg-slate-500"
                  }`}
                />
                <span className="text-slate-300 flex-1">{a.title}</span>
                <span className="text-slate-500">{a.source_module}</span>
                {a.acknowledged && <span className="text-green-500 text-[10px]">ack</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RiskUnavailable({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center text-slate-500">
      <div className="w-32 h-16 border-2 border-dashed border-border rounded-t-full flex items-end justify-center pb-2">
        <span className="text-2xl font-mono-clinical text-slate-600">—</span>
      </div>
      <span className="text-xs mt-1">{label}</span>
      <span className="text-[10px] mt-0.5 opacity-60">model not available</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Vitals tab
// ─────────────────────────────────────────────────────────────────────

function VitalsTab({ patient }: { patient: ResolvedPatient }) {
  const vitals = usePoll<{ data?: { vitals?: Record<string, Array<{ ts?: string; time?: string; value: number }>> } }>(
    `/api/journey/patient/${encodeURIComponent(patient.subject_id)}/admission/${encodeURIComponent(patient.hadm_id)}/vitals`,
    5000,
  );
  const data = vitals.data?.data?.vitals ?? null;

  if (vitals.loading && !data) return <LoadingBlock />;
  if (!data || Object.keys(data).length === 0) {
    return <EmptyBlock message="No vitals recorded for this admission." />;
  }

  const present = Object.keys(data).filter((k) => (data[k]?.length ?? 0) > 0);
  const channels = [
    ...VITAL_ORDER.filter((k) => present.includes(k)),
    ...present.filter((k) => !VITAL_ORDER.includes(k)),
  ];
  if (channels.length === 0) {
    return <EmptyBlock message="No vital samples recorded." />;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {channels.map((channel) => {
        const cfg = VITAL_CONFIG[channel] ?? { unit: "", normalMin: 0, normalMax: 100 };
        const series = (data[channel] ?? []).map((d) => ({
          time: d.time ?? d.ts ?? "",
          value: d.value,
        }));
        if (series.length === 0) return null;
        return (
          <VitalSignChart
            key={channel}
            data={series}
            label={channel}
            unit={cfg.unit}
            normalMin={cfg.normalMin}
            normalMax={cfg.normalMax}
            height={120}
          />
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Labs tab
// ─────────────────────────────────────────────────────────────────────

interface LabValue {
  time?: string;
  value: number;
  unit?: string;
  flag?: string;
  ref_lower?: number;
  ref_upper?: number;
}

type LabPanel = Record<string, LabValue[]>;

function LabsTab({ patient }: { patient: ResolvedPatient }) {
  const labs = usePoll<{ data?: { panels?: Record<string, LabPanel> } }>(
    `/api/journey/patient/${encodeURIComponent(patient.subject_id)}/admission/${encodeURIComponent(patient.hadm_id)}/labs`,
    60000,
  );
  const panelsObj = labs.data?.data?.panels ?? null;

  if (labs.loading && !panelsObj) return <LoadingBlock />;
  const panels = Object.entries(panelsObj ?? {}).filter(([, p]) => Object.keys(p ?? {}).length > 0);
  if (panels.length === 0) return <EmptyBlock message="No labs recorded for this admission." />;

  const isAbnormal = (flag?: string) =>
    flag === "high" || flag === "low" || flag === "critical" || flag === "abnormal";
  const flagStyle = (flag?: string) => (isAbnormal(flag) ? "text-amber-400" : "text-slate-300");

  return (
    <div className="space-y-3">
      {panels.map(([panelName, analytes]) => {
        // Sort: abnormal first, then alphabetical
        const rows = Object.entries(analytes)
          .map(([name, samples]) => ({ name, latest: samples[samples.length - 1] }))
          .filter((r) => r.latest !== undefined)
          .sort((a, b) => {
            const aFlag = isAbnormal(a.latest.flag) ? 0 : 1;
            const bFlag = isAbnormal(b.latest.flag) ? 0 : 1;
            return aFlag - bFlag || a.name.localeCompare(b.name);
          });
        return (
          <div key={panelName} className="bg-bg-card border border-border rounded-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border text-xs font-semibold text-slate-300 uppercase tracking-wider">
              {panelName}
              <span className="text-slate-500 ml-2 lowercase font-normal">
                ({rows.length} analytes)
              </span>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-[10px] text-slate-500 uppercase">
                  <th className="text-left px-3 py-1.5">Analyte</th>
                  <th className="text-left px-3 py-1.5">Latest</th>
                  <th className="text-left px-3 py-1.5">Reference</th>
                  <th className="text-left px-3 py-1.5">Time</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ name, latest }) => (
                  <tr key={name} className="border-b border-border last:border-b-0">
                    <td className="px-3 py-1.5 text-slate-300">{name}</td>
                    <td className={`px-3 py-1.5 font-mono-clinical ${flagStyle(latest.flag)}`}>
                      {latest.value} {latest.unit ?? ""}
                      {latest.flag && latest.flag !== "normal" ? (
                        <span className="ml-1 text-[10px] uppercase">{latest.flag}</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5 text-slate-500 text-xs">
                      {latest.ref_lower !== undefined && latest.ref_upper !== undefined
                        ? `${latest.ref_lower} – ${latest.ref_upper}`
                        : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-slate-500 text-xs">
                      {formatDate(latest.time)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Meds tab
// ─────────────────────────────────────────────────────────────────────

function MedsTab({ patient }: { patient: ResolvedPatient }) {
  const meds = usePoll<{ data?: { medications?: Array<Record<string, unknown>> } }>(
    `/api/journey/patient/${encodeURIComponent(patient.subject_id)}/admission/${encodeURIComponent(patient.hadm_id)}/medications`,
    60000,
  );
  const data = meds.data?.data?.medications ?? null;

  if (meds.loading && !data) return <LoadingBlock />;
  if (!data || data.length === 0) return <EmptyBlock message="No medications recorded." />;

  return (
    <div className="bg-bg-card border border-border rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-slate-400 uppercase">
            <th className="text-left px-3 py-2">Drug</th>
            <th className="text-left px-3 py-2">Dose</th>
            <th className="text-left px-3 py-2">Route</th>
            <th className="text-left px-3 py-2">Start</th>
          </tr>
        </thead>
        <tbody>
          {data.slice(0, 60).map((m, i) => {
            const doseVal = m.dose_val_rx ?? m.dose;
            const doseUnit = m.dose_unit_rx ?? m.unit ?? "";
            const start = (m.start_time ?? m.starttime) as string | undefined;
            return (
              <tr key={i} className="border-b border-border last:border-b-0">
                <td className="px-3 py-2 text-slate-200">
                  {(m.drug as string) || (m.medication as string) || "—"}
                  {m.prod_strength ? (
                    <span className="ml-2 text-xs text-slate-500">{String(m.prod_strength)}</span>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {doseVal !== undefined && doseVal !== null
                    ? `${doseVal} ${doseUnit}`.trim()
                    : "—"}
                </td>
                <td className="px-3 py-2 text-slate-400">{(m.route as string) ?? "—"}</td>
                <td className="px-3 py-2 text-slate-400 text-xs">{formatDate(start)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Timeline tab — uses shared TimelineView
// ─────────────────────────────────────────────────────────────────────

interface JourneyEvent {
  timestamp?: string;
  event_type?: string;
  category?: string;
  details?: Record<string, unknown>;
}

const CATEGORY_MAP: Record<string, string> = {
  admission: "diagnostic",
  discharge: "followup",
  transfer: "supportive",
  surgery: "surgery",
  procedure: "surgery",
  medication: "chemo",
  chemotherapy: "chemo",
  radiation: "radiation",
  imaging: "diagnostic",
  lab: "diagnostic",
};

function summariseDetails(details?: Record<string, unknown>): string {
  if (!details || typeof details !== "object") return "";
  return Object.entries(details)
    .filter(([k]) => !["hadm_id", "subject_id"].includes(k))
    .slice(0, 4)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(" · ");
}

function TimelineTab({ patient }: { patient: ResolvedPatient }) {
  const tl = usePoll<{ data?: { events?: JourneyEvent[] } }>(
    `/api/journey/patient/${encodeURIComponent(patient.subject_id)}/admission/${encodeURIComponent(patient.hadm_id)}/timeline`,
    10000,
  );
  const events = tl.data?.data?.events ?? null;

  if (tl.loading && !events) return <LoadingBlock />;
  if (!events || events.length === 0) return <EmptyBlock message="No timeline events." />;

  const adapted = events.slice(0, 200).map((e) => ({
    date: formatDate(e.timestamp),
    title: e.event_type ?? "event",
    category:
      CATEGORY_MAP[(e.category ?? "").toLowerCase()] ??
      CATEGORY_MAP[(e.event_type ?? "").toLowerCase()] ??
      "diagnostic",
    description: summariseDetails(e.details),
  }));

  return <TimelineView events={adapted} />;
}

// ─────────────────────────────────────────────────────────────────────
// Shared blocks
// ─────────────────────────────────────────────────────────────────────

function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-12 text-slate-400">
      <Loader2 className="w-4 h-4 animate-spin mr-2" />
      Loading…
    </div>
  );
}

function EmptyBlock({ message }: { message: string }) {
  return (
    <div className="bg-bg-card border border-border rounded-lg p-6 text-center text-sm text-slate-500">
      {message}
    </div>
  );
}
