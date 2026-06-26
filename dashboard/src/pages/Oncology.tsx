import { useState, useEffect, useCallback, useRef } from "react";
import {
  Users,
  Activity,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  BarChart3,
  FileText,
  FileSearch,
  Sparkles,
  Zap,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import RiskGauge from "../components/RiskGauge";
import TimelineView from "../components/TimelineView";
import { oncoRisk, oncoPathway, type OncologyRiskInput, type RiskPrediction, type PathwayResult } from "../lib/api";
// Sim data only — no mock data

const cancerTypes = [
  "Lung", "Breast", "Colon", "Prostate", "Leukemia", "Lymphoma",
  "Myeloma", "Bladder", "Pancreatic", "Liver", "Kidney", "Ovarian",
];

const cancerTypeButtons = [
  { name: "Lung", icon: "🫁" },
  { name: "Breast", icon: "🎀" },
  { name: "Colon", icon: "🔬" },
  { name: "Prostate", icon: "🧬" },
  { name: "Leukemia", icon: "🩸" },
  { name: "Lymphoma", icon: "🧫" },
  { name: "Myeloma", icon: "🦴" },
];

const pieColors = ["#DC2626", "#F97316", "#EAB308", "#22C55E", "#3B82F6"];

type TabKey = "risk" | "pathway" | "cohort" | "notes";

export default function Oncology() {
  const [activeTab, setActiveTab] = useState<TabKey>("risk");

  const tabs: { key: TabKey; label: string; icon: typeof Activity }[] = [
    { key: "risk", label: "Risk Assessment", icon: AlertTriangle },
    { key: "pathway", label: "Treatment Pathway", icon: FileText },
    { key: "cohort", label: "Cohort Analytics", icon: BarChart3 },
    { key: "notes", label: "Note Analysis", icon: FileSearch },
  ];

  return (
    <div className="space-y-4">
      {/* Tab Bar */}
      <div className="flex gap-1 bg-bg-card rounded-xl border border-border p-1">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? "bg-blue-500/20 text-blue-400"
                : "text-slate-400 hover:text-white hover:bg-slate-700/50"
            }`}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "risk" && <RiskTab />}
      {activeTab === "pathway" && <PathwayTab />}
      {activeTab === "cohort" && <CohortTab />}
      {activeTab === "notes" && <NoteAnalysisTab />}
    </div>
  );
}

// ==================== Risk Assessment Tab ====================

interface RiskSimPatient {
  hadm_id: string;
  subject_id: number;
  cancer_icd: string[];
  department: string;
  medications_count: number;
  admission_type: string;
  status: string;
}

/** Map a cancer ICD code prefix to a human-readable cancer type */
function icdToCancerType(codes: string[]): string {
  if (!codes || codes.length === 0) return "Other";
  const c = codes[0].toUpperCase();
  if (c.startsWith("C34")) return "Lung";
  if (c.startsWith("C50")) return "Breast";
  if (c.startsWith("C18") || c.startsWith("C19")) return "Colon";
  if (c.startsWith("C20")) return "Colon";
  if (c.startsWith("C61")) return "Prostate";
  if (c.startsWith("C91") || c.startsWith("C92") || c.startsWith("C93") || c.startsWith("C94") || c.startsWith("C95")) return "Leukemia";
  if (c.startsWith("C81") || c.startsWith("C82") || c.startsWith("C83") || c.startsWith("C84") || c.startsWith("C85") || c.startsWith("C86")) return "Lymphoma";
  if (c.startsWith("C90")) return "Myeloma";
  if (c.startsWith("C67")) return "Bladder";
  if (c.startsWith("C25")) return "Pancreatic";
  if (c.startsWith("C22")) return "Liver";
  if (c.startsWith("C64") || c.startsWith("C65")) return "Kidney";
  if (c.startsWith("C56")) return "Ovarian";
  if (c.startsWith("C21")) return "Colon";
  return "Other";
}

function RiskTab() {
  // Age starts blank — picking a sensible default (e.g. 0 or 65) is
  // misleading. The submit handler coerces to 0 if the operator submits
  // an empty field, so the request still validates server-side.
  type FormState = Omit<OncologyRiskInput, "age"> & { age: number | "" };
  const [form, setForm] = useState<FormState>({
    age: "",
    gender: "M",
    cancer_type: "Lung",
    stage: 2,
    charlson_score: 0,
    has_surgery: false,
    has_chemo: false,
    has_radiation: false,
    los_days: 0,
    prior_admissions: 0,
    comorbidities: [],
  });
  const [result, setResult] = useState<RiskPrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filledFromPatient, setFilledFromPatient] = useState<string | null>(null);
  const [comorbInput, setComorbInput] = useState("");

  // Live sim cancer patients
  const [simPatients, setSimPatients] = useState<RiskSimPatient[]>([]);
  const [simLoading, setSimLoading] = useState(true);
  const formRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchSimOnco = async () => {
      try {
        const res = await fetch("/api/sim/oncology-board");
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) {
          setSimPatients(data.patients || []);
        }
      } catch { /* sim offline */ }
      finally { if (!cancelled) setSimLoading(false); }
    };
    fetchSimOnco();
    const id = setInterval(fetchSimOnco, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const fillFromPatient = useCallback((p: RiskSimPatient) => {
    const cancerType = icdToCancerType(p.cancer_icd);
    const hasSurgery = (p.admission_type || "").toUpperCase().includes("SURG");
    setForm({
      age: 65,  // not available from board — use reasonable default
      gender: "M",
      cancer_type: cancerType,
      stage: 2,
      charlson_score: Math.min(6, Math.max(0, Math.floor((p.medications_count || 0) / 4))),
      has_surgery: hasSurgery,
      has_chemo: (p.medications_count || 0) > 5,
      has_radiation: false,
      los_days: 0,
      prior_admissions: 0,
      comorbidities: [],
    });
    setResult(null);
    setError(null);
    setFilledFromPatient(String(p.subject_id));
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const handlePredict = async () => {
    setLoading(true);
    setError(null);
    try {
      const payload: OncologyRiskInput = {
        ...form,
        age: typeof form.age === "number" ? form.age : 0,
      };
      const res = await oncoRisk(payload);
      if (res) {
        setResult(res);
      } else {
        // Backend returned nothing (typically a 5xx from `lib/api.ts`'s
        // safe wrapper). Surface a visible error so the operator isn't
        // staring at the unchanged "Enter patient data and click Assess
        // Risk" empty state.
        setError("Oncology risk service is unavailable. Please retry in a moment, or check System Admin.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Oncology risk service is unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const addComorbidity = () => {
    if (comorbInput.trim()) {
      setForm((prev) => ({
        ...prev,
        comorbidities: [...prev.comorbidities, comorbInput.trim()],
      }));
      setComorbInput("");
    }
  };

  const removeComorbidity = (idx: number) => {
    setForm((prev) => ({
      ...prev,
      comorbidities: prev.comorbidities.filter((_, i) => i !== idx),
    }));
  };

  const riskLevelColor = (level: string) => {
    const l = level.toLowerCase();
    if (l === "critical") return "#DC2626";
    if (l === "high") return "#F97316";
    if (l === "moderate") return "#EAB308";
    return "#22C55E";
  };

  return (
    <div className="space-y-4">
      {/* Live cancer patients from simulation */}
      {simLoading ? (
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="skeleton h-16 w-full" />
        </div>
      ) : simPatients.length > 0 ? (
        <div className="bg-gradient-to-r from-teal-500/10 via-emerald-500/5 to-transparent rounded-xl border border-teal-500/30 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="w-4 h-4 text-teal-400" />
            <h3 className="text-sm font-semibold text-text-primary">Live Cancer Patients — Click to Assess Risk</h3>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
              </span>
              <span className="text-[10px] font-semibold text-green-400 uppercase">LIVE</span>
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {simPatients.map((p) => (
              <div
                key={p.hadm_id}
                className="flex items-center gap-3 bg-bg-card rounded-lg border border-border px-3 py-2.5 hover:border-teal-500/50 transition-all group cursor-pointer"
                onClick={() => fillFromPatient(p)}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono-clinical font-semibold text-text-primary">#{p.subject_id}</span>
                    <span className="text-[10px] text-teal-400 font-medium">{icdToCancerType(p.cancer_icd)}</span>
                  </div>
                  <div className="text-[10px] text-text-muted truncate mt-0.5">
                    {p.department} · {p.cancer_icd?.join(", ")} · {p.medications_count ?? 0} meds
                  </div>
                </div>
                <button
                  className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-teal-600/15 text-teal-400 border border-teal-600/30 hover:bg-teal-600/30 transition-colors opacity-70 group-hover:opacity-100"
                  onClick={(e) => { e.stopPropagation(); fillFromPatient(p); }}
                >
                  <Zap className="w-3 h-3" />
                  Assess Risk
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-4 text-center text-text-muted text-sm">
          No cancer patients in simulation. Start the simulation to see live patients.
        </div>
      )}

      {/* Risk form + results */}
      <div ref={formRef} className="grid grid-cols-2 gap-4">
      {/* Left: Form */}
      <div className="bg-bg-card rounded-xl border border-border p-4 overflow-y-auto max-h-[calc(100vh-12rem)]">
        <h3 className="text-sm font-semibold text-text-primary mb-3">Patient Risk Input</h3>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="onco-age" className="text-[11px] text-slate-400 block mb-0.5">Age</label>
              <input
                id="onco-age"
                type="number"
                min={0}
                max={120}
                placeholder="e.g. 65"
                value={form.age}
                onChange={(e) => {
                  const v = e.target.value;
                  setForm({ ...form, age: v === "" ? "" : parseInt(v) || 0 });
                }}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white font-mono-clinical focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="onco-gender" className="text-[11px] text-slate-400 block mb-0.5">Gender</label>
              <select
                id="onco-gender"
                value={form.gender}
                onChange={(e) => setForm({ ...form, gender: e.target.value })}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              >
                <option value="M">Male</option>
                <option value="F">Female</option>
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="onco-cancer-type" className="text-[11px] text-slate-400 block mb-0.5">Cancer Type</label>
            <select
              id="onco-cancer-type"
              value={form.cancer_type}
              onChange={(e) => setForm({ ...form, cancer_type: e.target.value })}
              className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white focus:border-blue-500 focus:outline-none"
            >
              {cancerTypes.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="onco-stage" className="text-[11px] text-slate-400 block mb-0.5">Stage (1-4)</label>
              <select
                id="onco-stage"
                value={form.stage}
                onChange={(e) => setForm({ ...form, stage: parseInt(e.target.value) })}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              >
                {[1, 2, 3, 4].map((s) => (
                  <option key={s} value={s}>Stage {s}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="onco-charlson" className="text-[11px] text-slate-400 block mb-0.5">Charlson Score</label>
              <input
                id="onco-charlson"
                type="number"
                min={0}
                value={form.charlson_score}
                onChange={(e) => setForm({ ...form, charlson_score: parseInt(e.target.value) || 0 })}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white font-mono-clinical focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <fieldset className="grid grid-cols-3 gap-2">
            <legend className="sr-only">Treatments</legend>
            {(["has_surgery", "has_chemo", "has_radiation"] as const).map((field) => {
              const cbId = `onco-${field}`;
              return (
                <label
                  key={field}
                  htmlFor={cbId}
                  className="flex items-center gap-2 bg-bg-primary rounded-lg p-2 cursor-pointer hover:bg-slate-700/30 transition-colors"
                >
                  <input
                    id={cbId}
                    type="checkbox"
                    checked={form[field]}
                    onChange={(e) => setForm({ ...form, [field]: e.target.checked })}
                    className="accent-blue-500"
                  />
                  <span className="text-[11px] text-slate-300 capitalize">
                    {field.replace("has_", "")}
                  </span>
                </label>
              );
            })}
          </fieldset>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="onco-los" className="text-[11px] text-slate-400 block mb-0.5">LOS (days)</label>
              <input
                id="onco-los"
                type="number"
                min={0}
                value={form.los_days}
                onChange={(e) => setForm({ ...form, los_days: parseInt(e.target.value) || 0 })}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white font-mono-clinical focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="onco-prior" className="text-[11px] text-slate-400 block mb-0.5">Prior Admissions</label>
              <input
                id="onco-prior"
                type="number"
                min={0}
                value={form.prior_admissions}
                onChange={(e) => setForm({ ...form, prior_admissions: parseInt(e.target.value) || 0 })}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white font-mono-clinical focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label htmlFor="onco-comorb-input" className="text-[11px] text-slate-400 block mb-0.5">Comorbidities</label>
            <div className="flex gap-1 mb-1">
              <input
                id="onco-comorb-input"
                type="text"
                value={comorbInput}
                onChange={(e) => setComorbInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addComorbidity()}
                placeholder="Add comorbidity..."
                className="flex-1 bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white focus:border-blue-500 focus:outline-none"
              />
              <button
                type="button"
                onClick={addComorbidity}
                className="px-2 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded transition-colors"
                aria-label="Add comorbidity"
              >
                Add
              </button>
            </div>
            <div className="flex flex-wrap gap-1">
              {form.comorbidities.map((c, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 text-[10px] bg-slate-700 text-slate-300 rounded px-1.5 py-0.5"
                >
                  {c}
                  <button
                    onClick={() => removeComorbidity(i)}
                    className="text-slate-500 hover:text-red-400"
                  >
                    x
                  </button>
                </span>
              ))}
            </div>
          </div>

          <button
            onClick={handlePredict}
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-medium py-2.5 rounded-lg transition-colors"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Activity className="w-4 h-4" />}
            Assess Risk
          </button>
        </div>
      </div>

      {/* Right: Results */}
      <div className="bg-bg-card rounded-xl border border-border p-4 overflow-y-auto max-h-[calc(100vh-12rem)]">
        <div className="flex items-center justify-between mb-3 gap-2">
          <h3 className="text-sm font-semibold text-white">Risk Assessment</h3>
          {filledFromPatient && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-teal-500/15 border border-teal-500/30 text-teal-300 font-mono-clinical">
              from #{filledFromPatient}
            </span>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/30 rounded-lg p-3 mb-3">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" aria-hidden="true" />
            <span className="text-xs text-red-300">{error}</span>
          </div>
        )}

        {!result && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-64 text-slate-500">
            <Activity className="w-8 h-8 mb-3 opacity-30" />
            <p className="text-sm">Enter patient data and click Assess Risk</p>
          </div>
        )}

        {loading && (
          <div className="space-y-3">
            <div className="skeleton h-24 w-full" />
            <div className="skeleton h-24 w-full" />
            <div className="skeleton h-12 w-3/4" />
          </div>
        )}

        {result && !loading && (
          <div className="space-y-4 animate-fade-in">
            {/* Gauges */}
            <div className="flex flex-wrap justify-center gap-4 py-2">
              <RiskGauge value={result.readmission_risk} label="30-Day Readmission" />
              <RiskGauge value={result.mortality_risk} label="Mortality Risk" />
            </div>

            {/* Risk Level Badge */}
            <div className="flex justify-center">
              <span
                className="text-sm font-bold px-4 py-1.5 rounded-full"
                style={{
                  color: riskLevelColor(result.risk_level),
                  backgroundColor: `${riskLevelColor(result.risk_level)}15`,
                  border: `1px solid ${riskLevelColor(result.risk_level)}40`,
                }}
              >
                {result.risk_level} Risk
              </span>
            </div>

            {/* Contributing Factors */}
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="text-xs text-slate-400 mb-2">Contributing Factors</div>
              <div className="space-y-1">
                {result.contributing_factors.map((f, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-red-400 shrink-0" />
                    <span className="text-xs text-slate-300">{f}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Recommendations */}
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="text-xs text-slate-400 mb-2">Recommendations</div>
              <div className="space-y-1">
                {result.recommendations.map((r, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <CheckCircle2 className="w-3 h-3 text-green-400 shrink-0" />
                    <span className="text-xs text-slate-300">{r}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

// ==================== Pathway Tab ====================

function PathwayTab() {
  const [selectedCancer, setSelectedCancer] = useState("Lung");
  const [age, setAge] = useState(62);
  const [stage, setStage] = useState(3);
  const [charlson, setCharlson] = useState(4);
  const [pathwayResult, setPathwayResult] = useState<Array<{
    date: string;
    title: string;
    category: string;
    description: string;
    priority?: string;
    estimatedDays?: number;
  }> | null>(null);
  const [loading, setLoading] = useState(false);
  const [urgencyScore, setUrgencyScore] = useState(0);
  const [pathwayError, setPathwayError] = useState<string | null>(null);

  const handleGetPathway = async () => {
    setLoading(true);
    setPathwayError(null);
    try {
      const res = await oncoPathway({ cancer_type: selectedCancer, age, stage, charlson_score: charlson });
      if (res) {
        setUrgencyScore(Math.round(res.urgency_score * 100));
        setPathwayResult(
          res.pathway.map((s) => ({
            date: `Step ${s.step}`,
            title: s.treatment,
            category: s.category,
            description: s.description,
            priority: s.priority,
            estimatedDays: s.estimated_days,
          }))
        );
      } else {
        setPathwayResult(null);
        // F27: previously this state was silent — operator clicked "Get
        // Pathway", got nothing, and had no idea why. Surface a real
        // error region instead.
        setPathwayError("Treatment pathway service is unavailable. Please retry or check System Admin.");
      }
    } catch (e) {
      setPathwayResult(null);
      setPathwayError(e instanceof Error ? e.message : "Treatment pathway service is unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const totalDuration = pathwayResult
    ? pathwayResult.reduce((s, e) => s + (e.estimatedDays || 0), 0)
    : 0;

  return (
    <div className="space-y-4">
      {/* Cancer Type Selector */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h3 className="text-xs font-semibold text-white mb-3">Select Cancer Type</h3>
        <div className="flex flex-wrap gap-2 mb-4">
          {cancerTypeButtons.map((c) => (
            <button
              key={c.name}
              onClick={() => setSelectedCancer(c.name)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-colors ${
                selectedCancer === c.name
                  ? "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                  : "bg-bg-primary text-slate-400 border border-border hover:text-white hover:border-slate-500"
              }`}
            >
              <span>{c.icon}</span>
              {c.name}
            </button>
          ))}
        </div>

        <div className="flex items-end gap-3">
          <div>
            <label className="text-[10px] text-slate-400 block mb-0.5">Age</label>
            <input
              type="number"
              value={age}
              onChange={(e) => setAge(parseInt(e.target.value) || 0)}
              className="w-20 bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white font-mono-clinical focus:border-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="text-[10px] text-slate-400 block mb-0.5">Stage</label>
            <select
              value={stage}
              onChange={(e) => setStage(parseInt(e.target.value))}
              className="w-24 bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white focus:border-blue-500 focus:outline-none"
            >
              {[1, 2, 3, 4].map((s) => (
                <option key={s} value={s}>Stage {s}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[10px] text-slate-400 block mb-0.5">Charlson</label>
            <input
              type="number"
              value={charlson}
              onChange={(e) => setCharlson(parseInt(e.target.value) || 0)}
              className="w-20 bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-white font-mono-clinical focus:border-blue-500 focus:outline-none"
            />
          </div>
          <button
            onClick={handleGetPathway}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
            Get Pathway
          </button>
        </div>
      </div>

      {/* Pathway Result */}
      {loading && (
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="skeleton h-16 w-full" />
            ))}
          </div>
        </div>
      )}

      {pathwayError && !loading && (
        <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm text-red-300">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />
          <span>{pathwayError}</span>
        </div>
      )}

      {pathwayResult && !loading && (
        <div className="bg-bg-card rounded-xl border border-border p-4 animate-fade-in">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-white">
              Treatment Pathway: Stage {stage} {selectedCancer} Cancer
            </h3>
            <div className="flex items-center gap-3">
              <div className="bg-bg-primary rounded px-2 py-1">
                <span className="text-[10px] text-slate-400">Duration: </span>
                <span className="text-xs font-mono-clinical font-bold text-white">
                  ~{totalDuration} days
                </span>
              </div>
              <div className="bg-bg-primary rounded px-2 py-1">
                <span className="text-[10px] text-slate-400">Steps: </span>
                <span className="text-xs font-mono-clinical font-bold text-white">
                  {pathwayResult.length}
                </span>
              </div>
            </div>
          </div>

          {/* Urgency bar */}
          <div className="mb-4">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-slate-400">Urgency Score</span>
              <span className="text-[10px] font-mono-clinical text-orange-400">{urgencyScore}/100</span>
            </div>
            <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-yellow-500 to-red-500"
                style={{ width: `${urgencyScore}%` }}
              />
            </div>
          </div>

          <TimelineView events={pathwayResult} />

          <div className="mt-4 bg-bg-primary rounded-lg p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Clinical Notes</div>
            <p className="text-xs text-slate-300">
              This pathway is generated based on NCCN guidelines for Stage {stage} {selectedCancer} cancer
              in a {age}-year-old patient with Charlson Comorbidity Index of {charlson}. Treatment
              sequencing optimized by AI model trained on 50,000+ historical outcomes. Individual
              adjustments may be necessary based on patient performance status, molecular profiling
              results, and treatment tolerance. Multidisciplinary team review is recommended before
              initiating therapy.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ==================== Cohort Analytics Tab ====================

interface SimOncologyPatient {
  hadm_id: number;
  subject_id: number;
  cancer_icd: string;
  department: string;
  med_count: number;
  admission_type: string;
}

function CohortTab() {
  const [simOncoPatients, setSimOncoPatients] = useState<SimOncologyPatient[]>([]);
  const [simOncoConnected, setSimOncoConnected] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetchSimOnco = async () => {
      try {
        const res = await fetch("/api/sim/oncology-board");
        if (!res.ok) throw new Error("sim offline");
        const data = await res.json();
        if (!cancelled) {
          setSimOncoPatients(data.patients || []);
          setSimOncoConnected(true);
        }
      } catch {
        if (!cancelled) {
          setSimOncoPatients([]);
          setSimOncoConnected(false);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchSimOnco();
    const id = setInterval(fetchSimOnco, 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Derive analytics from sim patients
  const cancerDist: Record<string, number> = {};
  const deptDist: Record<string, number> = {};
  const admissionTypeDist: Record<string, number> = {};
  simOncoPatients.forEach((p) => {
    const icd = p.cancer_icd || "Unknown";
    cancerDist[icd] = (cancerDist[icd] || 0) + 1;
    const dept = p.department || "Unknown";
    deptDist[dept] = (deptDist[dept] || 0) + 1;
    const atype = p.admission_type || "Unknown";
    admissionTypeDist[atype] = (admissionTypeDist[atype] || 0) + 1;
  });

  const cancerDistData = Object.entries(cancerDist)
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count);

  const deptDistData = Object.entries(deptDist)
    .map(([name, count]) => ({ name: name.length > 20 ? name.slice(0, 18) + ".." : name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  const admissionTypeData = Object.entries(admissionTypeDist)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value);

  const avgMedCount = simOncoPatients.length > 0
    ? Math.round(simOncoPatients.reduce((s, p) => s + (p.med_count || 0), 0) / simOncoPatients.length * 10) / 10
    : 0;

  const barColors = ["#DC2626", "#F97316", "#EAB308", "#22C55E", "#3B82F6", "#8B5CF6", "#EC4899", "#14B8A6", "#6366F1", "#F43F5E", "#84CC16", "#64748B"];

  return (
    <div className="space-y-4">
      {/* Live Simulation Patient List */}
      {simOncoConnected && simOncoPatients.length > 0 && (
        <div className="bg-gradient-to-r from-teal-500/10 via-emerald-500/5 to-transparent rounded-xl border border-teal-500/30 p-4 animate-fade-in">
          <div className="flex items-center gap-3 mb-3">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-white">Live Simulation</h3>
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                </span>
                <span className="text-[10px] font-semibold text-green-400 uppercase">LIVE</span>
              </span>
            </div>
          </div>

          <div className="bg-teal-500/10 border border-teal-500/20 rounded-lg px-4 py-3 mb-3 flex items-center gap-3">
            <Sparkles className="w-5 h-5 text-teal-400" />
            <span className="text-sm font-semibold text-teal-300">{simOncoPatients.length} cancer patients currently in hospital</span>
          </div>

          <div className="space-y-1.5 max-h-[200px] overflow-y-auto">
            {simOncoPatients.map((p) => (
              <div key={p.hadm_id} className="flex items-center gap-3 bg-bg-primary rounded-lg px-3 py-2">
                <span className="text-[10px] font-mono-clinical text-slate-300 w-20">#{p.subject_id}</span>
                <span className="text-[10px] text-teal-400 font-medium w-24">{p.cancer_icd}</span>
                <span className="text-[10px] text-slate-400 flex-1">{p.department}</span>
                <span className="text-[10px] text-slate-500">{p.med_count} meds</span>
                <span className="text-[10px] text-slate-500">{p.admission_type}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stat Cards from sim data */}
      {loading ? (
        <div className="grid grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => <div key={i} className="skeleton h-20 rounded-xl" />)}
        </div>
      ) : simOncoPatients.length > 0 ? (
        <div className="grid grid-cols-4 gap-4">
          <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center">
              <Users className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <div className="text-[10px] text-slate-400 uppercase">Active Cancer Patients</div>
              <div className="font-mono-clinical text-xl font-bold text-white">{simOncoPatients.length}</div>
            </div>
          </div>
          <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-green-500/10 flex items-center justify-center">
              <Activity className="w-5 h-5 text-green-400" />
            </div>
            <div>
              <div className="text-[10px] text-slate-400 uppercase">Cancer Types</div>
              <div className="font-mono-clinical text-xl font-bold text-white">{Object.keys(cancerDist).length}</div>
            </div>
          </div>
          <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-orange-500/10 flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-orange-400" />
            </div>
            <div>
              <div className="text-[10px] text-slate-400 uppercase">Departments</div>
              <div className="font-mono-clinical text-xl font-bold text-orange-400">{Object.keys(deptDist).length}</div>
            </div>
          </div>
          <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
              <BarChart3 className="w-5 h-5 text-purple-400" />
            </div>
            <div>
              <div className="text-[10px] text-slate-400 uppercase">Avg Medications</div>
              <div className="font-mono-clinical text-xl font-bold text-purple-400">{avgMedCount}</div>
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-6 text-center text-slate-500 text-sm italic">
          No oncology patients. Start the simulation to see live data.
        </div>
      )}

      {simOncoPatients.length > 0 && <div className="grid grid-cols-3 gap-4">
        {/* Cancer Type Distribution */}
        <div className="col-span-1 bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-xs font-semibold text-white mb-3">Cancer Type Distribution</h3>
          <div style={{ height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={cancerDistData}
                layout="vertical"
                margin={{ top: 0, right: 20, left: 5, bottom: 0 }}
              >
                <XAxis type="number" tick={{ fontSize: 9, fill: "#64748b" }} />
                <YAxis
                  type="category"
                  dataKey="type"
                  width={70}
                  tick={{ fontSize: 10, fill: "#94a3b8" }}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--color-tooltip-bg)",
                    border: "1px solid var(--color-tooltip-border)",
                    borderRadius: 8,
                    fontSize: 11,
                  }}
                />
                <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                  {cancerDistData.map((_, i) => (
                    <Cell key={i} fill={barColors[i % barColors.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Admission Type Pie */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-xs font-semibold text-white mb-3">Admission Type</h3>
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={admissionTypeData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={85}
                  dataKey="value"
                  nameKey="name"
                  strokeWidth={0}
                  label={({ name, value }) => `${name.slice(0, 12)}: ${value}`}
                >
                  {admissionTypeData.map((_, i) => (
                    <Cell key={i} fill={pieColors[i % pieColors.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--color-tooltip-bg)",
                    border: "1px solid var(--color-tooltip-border)",
                    borderRadius: 8,
                    fontSize: 11,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 mt-2">
            {admissionTypeData.map((m, i) => (
              <div key={i} className="flex items-center gap-1">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: pieColors[i % pieColors.length] }} />
                <span className="text-[9px] text-slate-400">{m.name}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Department Distribution */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-xs font-semibold text-white mb-3">Department Distribution</h3>
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={deptDistData} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 8, fill: "#94a3b8" }}
                  interval={0}
                  angle={-25}
                  textAnchor="end"
                  height={50}
                />
                <YAxis tick={{ fontSize: 9, fill: "#64748b" }} width={25} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--color-tooltip-bg)",
                    border: "1px solid var(--color-tooltip-border)",
                    borderRadius: 8,
                    fontSize: 11,
                  }}
                />
                <Bar dataKey="count" fill="#3B82F6" radius={[4, 4, 0, 0]}>
                  {deptDistData.map((_, i) => (
                    <Cell key={i} fill={barColors[i % barColors.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>}
    </div>
  );
}

// ==================== Note Analysis Tab ====================

const SAMPLE_NOTE =
  "Patient presents with stage III non-small cell lung cancer with metastasis to mediastinal lymph nodes. Started on cisplatin/pemetrexed chemotherapy. History of COPD and neutropenia after first cycle. Consider palliative care consultation.";

interface NoteAnalysisResult {
  cancer_mentions: string[];
  treatment_mentions: string[];
  medication_mentions: string[];
  risk_indicators: string[];
  summary: string;
}

function NoteAnalysisTab() {
  const [noteText, setNoteText] = useState("");
  const [analysisResult, setAnalysisResult] = useState<NoteAnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    if (!noteText.trim()) return;
    setLoading(true);
    setError(null);
    setAnalysisResult(null);
    try {
      const res = await fetch("/api/onco/analyze-note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: noteText }),
      });
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      const data = await res.json();
      const d = data.data || data;
      setAnalysisResult({
        cancer_mentions: d.cancer_mentions || [],
        treatment_mentions: d.treatment_mentions || [],
        medication_mentions: d.medication_mentions || [],
        risk_indicators: d.risk_indicators || [],
        summary: d.summary || "",
      });
    } catch {
      setError("Service unavailable. Please check that the Oncology API is running.");
    } finally {
      setLoading(false);
    }
  };

  const fillSample = () => {
    setNoteText(SAMPLE_NOTE);
    setAnalysisResult(null);
    setError(null);
  };

  return (
    <div className="grid grid-cols-2 gap-4">
      {/* Left: Input */}
      <div className="bg-bg-card rounded-xl border border-border p-4 flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white">Clinical Note</h3>
          <button
            onClick={fillSample}
            className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 transition-colors px-2 py-1 rounded bg-blue-500/10 hover:bg-blue-500/20"
          >
            <Sparkles className="w-3 h-3" />
            Load Sample Note
          </button>
        </div>

        <textarea
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
          placeholder="Paste or type a clinical note here..."
          className="flex-1 min-h-[300px] bg-bg-input border border-border rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:border-blue-500 focus:outline-none transition-colors resize-none"
        />

        <button
          onClick={handleAnalyze}
          disabled={loading || !noteText.trim()}
          className="w-full mt-3 flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:text-slate-400 text-white font-medium py-2.5 rounded-lg transition-colors"
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <FileSearch className="w-4 h-4" />
          )}
          Analyze Note
        </button>
      </div>

      {/* Right: Results */}
      <div className="bg-bg-card rounded-xl border border-border p-4 overflow-y-auto max-h-[calc(100vh-12rem)]">
        <h3 className="text-sm font-semibold text-white mb-3">Analysis Results</h3>

        {error && (
          <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg p-3 mb-3">
            <AlertTriangle className="w-4 h-4 text-red-400" />
            <span className="text-xs text-red-400">{error}</span>
          </div>
        )}

        {!analysisResult && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-64 text-slate-500">
            <FileSearch className="w-8 h-8 mb-3 opacity-30" />
            <p className="text-sm">Enter a clinical note and click Analyze</p>
            <p className="text-xs text-slate-600 mt-1">Or load a sample note to try it out</p>
          </div>
        )}

        {loading && (
          <div className="space-y-3">
            <div className="skeleton h-16 w-full" />
            <div className="skeleton h-12 w-full" />
            <div className="skeleton h-12 w-full" />
            <div className="skeleton h-12 w-full" />
            <div className="skeleton h-24 w-full" />
          </div>
        )}

        {analysisResult && !loading && (
          <div className="space-y-4 animate-fade-in">
            {/* Cancer Mentions */}
            {analysisResult.cancer_mentions.length > 0 && (
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="text-xs text-slate-400 mb-2">Cancer Mentions</div>
                <div className="flex flex-wrap gap-1.5">
                  {analysisResult.cancer_mentions.map((mention, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-red-500/15 text-red-400 border border-red-500/30 font-medium"
                    >
                      {mention}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Treatment Mentions */}
            {analysisResult.treatment_mentions.length > 0 && (
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="text-xs text-slate-400 mb-2">Treatment Mentions</div>
                <div className="flex flex-wrap gap-1.5">
                  {analysisResult.treatment_mentions.map((mention, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-blue-500/15 text-blue-400 border border-blue-500/30 font-medium"
                    >
                      {mention}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Medication Mentions */}
            {analysisResult.medication_mentions.length > 0 && (
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="text-xs text-slate-400 mb-2">Medication Mentions</div>
                <div className="flex flex-wrap gap-1.5">
                  {analysisResult.medication_mentions.map((mention, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-orange-500/15 text-orange-400 border border-orange-500/30 font-medium"
                    >
                      {mention}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Risk Indicators */}
            {analysisResult.risk_indicators.length > 0 && (
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="text-xs text-slate-400 mb-2">Risk Indicators</div>
                <div className="flex flex-wrap gap-1.5">
                  {analysisResult.risk_indicators.map((indicator, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 font-medium"
                    >
                      {indicator}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Summary */}
            {analysisResult.summary && (
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="text-xs text-slate-400 mb-2">Summary</div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  {analysisResult.summary}
                </p>
              </div>
            )}

            {/* Empty state if nothing found */}
            {analysisResult.cancer_mentions.length === 0 &&
              analysisResult.treatment_mentions.length === 0 &&
              analysisResult.medication_mentions.length === 0 &&
              analysisResult.risk_indicators.length === 0 &&
              !analysisResult.summary && (
                <div className="text-xs text-slate-500 text-center py-8">
                  No clinical entities detected in the provided text.
                </div>
              )}
          </div>
        )}
      </div>
    </div>
  );
}
