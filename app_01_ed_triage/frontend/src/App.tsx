import React, { useState, useEffect, useCallback } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */
interface TriageInput {
  age: number;
  gender: string;
  heart_rate: number | null;
  respiratory_rate: number | null;
  spo2: number | null;
  sbp: number | null;
  dbp: number | null;
  temperature: number | null;
  wbc: number | null;
  hemoglobin: number | null;
  lactate: number | null;
  glucose: number | null;
  creatinine: number | null;
  arrival_mode: string;
}

interface TriagePrediction {
  acuity_level: number;
  acuity_label: string;
  acuity_color: string;
  confidence: number;
  class_probabilities: Record<string, number>;
  disposition: string;
  ed_los_estimate_hours: number;
  risk_factors: string[];
}

interface ModelInfo {
  model_name: string;
  model_type: string;
  metrics: Record<string, unknown>;
  feature_count: number;
  class_labels: Record<string, string>;
}

interface DatasetStats {
  total_samples: number;
  class_distribution: Record<string, number>;
  feature_importance: Record<string, number>;
  missing_rates: Record<string, number>;
}

/* ------------------------------------------------------------------ */
/* API base URL                                                        */
/* ------------------------------------------------------------------ */
const API_BASE =
  window.__ED_TRIAGE_CONFIG__?.API_BASE || "/api";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const body = await res.json();
  return body.data ?? body;
}

/* ------------------------------------------------------------------ */
/* Acuity color map                                                    */
/* ------------------------------------------------------------------ */
const ACUITY_COLORS: Record<number, string> = {
  1: "#DC2626",
  2: "#F97316",
  3: "#EAB308",
  4: "#22C55E",
  5: "#3B82F6",
};

const ACUITY_LABELS: Record<number, string> = {
  1: "Resuscitation",
  2: "Emergent",
  3: "Urgent",
  4: "Less Urgent",
  5: "Non-urgent",
};

/* ------------------------------------------------------------------ */
/* Helper: numeric input field                                         */
/* ------------------------------------------------------------------ */
function NumField({
  label,
  unit,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  unit?: string;
  value: number | null;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <div className="flex flex-col">
      <label className="text-xs font-medium text-gray-500 mb-1">
        {label} {unit && <span className="text-gray-400">({unit})</span>}
      </label>
      <input
        type="number"
        className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        value={value ?? ""}
        onChange={(e) =>
          onChange(e.target.value === "" ? null : parseFloat(e.target.value))
        }
        min={min}
        max={max}
        step={step ?? 1}
        placeholder="--"
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Triage Input Form                                                   */
/* ------------------------------------------------------------------ */
function TriageForm({
  onPredict,
  loading,
}: {
  onPredict: (inp: TriageInput) => void;
  loading: boolean;
}) {
  const [form, setForm] = useState<TriageInput>({
    age: 55,
    gender: "M",
    heart_rate: null,
    respiratory_rate: null,
    spo2: null,
    sbp: null,
    dbp: null,
    temperature: null,
    wbc: null,
    hemoglobin: null,
    lactate: null,
    glucose: null,
    creatinine: null,
    arrival_mode: "AMBULANCE",
  });

  const set = <K extends keyof TriageInput>(key: K, val: TriageInput[K]) =>
    setForm((prev) => ({ ...prev, [key]: val }));

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <h2 className="text-lg font-semibold text-gray-800 mb-4">
        Patient Triage Assessment
      </h2>

      {/* Demographics */}
      <div className="mb-4">
        <h3 className="text-sm font-medium text-gray-600 mb-2 uppercase tracking-wide">
          Demographics
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <NumField
            label="Age"
            unit="years"
            value={form.age}
            onChange={(v) => set("age", v ?? 0)}
            min={0}
            max={120}
          />
          <div className="flex flex-col">
            <label className="text-xs font-medium text-gray-500 mb-1">
              Gender
            </label>
            <select
              className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              value={form.gender}
              onChange={(e) => set("gender", e.target.value)}
            >
              <option value="M">Male</option>
              <option value="F">Female</option>
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium text-gray-500 mb-1">
              Arrival Mode
            </label>
            <select
              className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              value={form.arrival_mode}
              onChange={(e) => set("arrival_mode", e.target.value)}
            >
              <option value="AMBULANCE">Ambulance</option>
              <option value="WALK_IN">Walk-in</option>
              <option value="TRANSFER">Transfer</option>
              <option value="PHYSICIAN_REFERRAL">Physician Referral</option>
              <option value="UNKNOWN">Unknown</option>
            </select>
          </div>
        </div>
      </div>

      {/* Vital Signs */}
      <div className="mb-4">
        <h3 className="text-sm font-medium text-gray-600 mb-2 uppercase tracking-wide">
          Vital Signs
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <NumField label="Heart Rate" unit="bpm" value={form.heart_rate} onChange={(v) => set("heart_rate", v)} min={20} max={300} />
          <NumField label="Respiratory Rate" unit="/min" value={form.respiratory_rate} onChange={(v) => set("respiratory_rate", v)} min={0} max={80} />
          <NumField label="SpO2" unit="%" value={form.spo2} onChange={(v) => set("spo2", v)} min={50} max={100} />
          <NumField label="Systolic BP" unit="mmHg" value={form.sbp} onChange={(v) => set("sbp", v)} min={30} max={350} />
          <NumField label="Diastolic BP" unit="mmHg" value={form.dbp} onChange={(v) => set("dbp", v)} min={10} max={250} />
          <NumField label="Temperature" unit="C" value={form.temperature} onChange={(v) => set("temperature", v)} min={30} max={45} step={0.1} />
        </div>
      </div>

      {/* Lab Results */}
      <div className="mb-6">
        <h3 className="text-sm font-medium text-gray-600 mb-2 uppercase tracking-wide">
          Lab Results
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <NumField label="WBC" unit="K/uL" value={form.wbc} onChange={(v) => set("wbc", v)} min={0} step={0.1} />
          <NumField label="Hemoglobin" unit="g/dL" value={form.hemoglobin} onChange={(v) => set("hemoglobin", v)} min={0} step={0.1} />
          <NumField label="Lactate" unit="mmol/L" value={form.lactate} onChange={(v) => set("lactate", v)} min={0} step={0.1} />
          <NumField label="Glucose" unit="mg/dL" value={form.glucose} onChange={(v) => set("glucose", v)} min={0} />
          <NumField label="Creatinine" unit="mg/dL" value={form.creatinine} onChange={(v) => set("creatinine", v)} min={0} step={0.1} />
        </div>
      </div>

      <button
        className="w-full bg-blue-600 text-white font-medium py-3 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
        onClick={() => onPredict(form)}
        disabled={loading}
      >
        {loading ? "Predicting..." : "Predict Acuity"}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Prediction Result Display                                           */
/* ------------------------------------------------------------------ */
function PredictionResult({ pred }: { pred: TriagePrediction }) {
  const probData = Object.entries(pred.class_probabilities).map(
    ([key, val]) => ({
      name: key,
      probability: Math.round(val * 100),
    })
  );

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <h2 className="text-lg font-semibold text-gray-800 mb-4">
        Triage Result
      </h2>

      {/* Acuity badge */}
      <div className="flex items-center justify-center mb-6">
        <div
          className="w-32 h-32 rounded-full flex flex-col items-center justify-center text-white shadow-lg"
          style={{ backgroundColor: pred.acuity_color }}
        >
          <span className="text-4xl font-bold">{pred.acuity_level}</span>
          <span className="text-xs font-medium mt-1">{pred.acuity_label}</span>
        </div>
      </div>

      {/* Confidence */}
      <div className="text-center mb-4">
        <span className="text-sm text-gray-500">Confidence: </span>
        <span className="text-lg font-semibold text-gray-800">
          {(pred.confidence * 100).toFixed(1)}%
        </span>
      </div>

      {/* Disposition & LOS */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="bg-gray-50 rounded-lg p-3 text-center">
          <div className="text-xs text-gray-500 uppercase">Disposition</div>
          <div className="text-sm font-semibold text-gray-800 mt-1">
            {pred.disposition.replace(/_/g, " ")}
          </div>
        </div>
        <div className="bg-gray-50 rounded-lg p-3 text-center">
          <div className="text-xs text-gray-500 uppercase">Est. ED LOS</div>
          <div className="text-sm font-semibold text-gray-800 mt-1">
            {pred.ed_los_estimate_hours} hours
          </div>
        </div>
      </div>

      {/* Class probabilities bar chart */}
      <div className="mb-4">
        <h3 className="text-sm font-medium text-gray-600 mb-2">
          Class Probabilities
        </h3>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={probData} layout="vertical">
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
            <YAxis type="category" dataKey="name" width={50} tick={{ fontSize: 12 }} />
            <Tooltip formatter={(v: number) => `${v}%`} />
            <Bar dataKey="probability" radius={[0, 4, 4, 0]}>
              {probData.map((entry, i) => (
                <Cell key={i} fill={ACUITY_COLORS[i + 1] || "#6B7280"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Risk factors */}
      {pred.risk_factors.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-600 mb-2">
            Risk Factors
          </h3>
          <ul className="space-y-1">
            {pred.risk_factors.map((rf, i) => (
              <li
                key={i}
                className="text-sm text-red-700 bg-red-50 rounded px-3 py-1"
              >
                {rf}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ED Census Board                                                     */
/* ------------------------------------------------------------------ */
function CensusBoard({ stats }: { stats: DatasetStats | null }) {
  if (!stats || !stats.class_distribution) return null;

  const pieData = Object.entries(stats.class_distribution).map(
    ([label, count]) => ({
      name: label,
      value: count,
    })
  );

  const PIE_COLORS = [
    "#DC2626",
    "#F97316",
    "#EAB308",
    "#22C55E",
    "#3B82F6",
    "#8B5CF6",
    "#EC4899",
  ];

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <h2 className="text-lg font-semibold text-gray-800 mb-1">
        ED Census -- Acuity Distribution
      </h2>
      <p className="text-xs text-gray-400 mb-4">
        Training set: {stats.total_samples.toLocaleString()} admissions
      </p>
      <ResponsiveContainer width="100%" height={280}>
        <PieChart>
          <Pie
            data={pieData}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={100}
            paddingAngle={2}
            dataKey="value"
            label={({ name, percent }) =>
              `${name.split("(")[0].trim()} ${(percent * 100).toFixed(0)}%`
            }
            labelLine={false}
          >
            {pieData.map((_entry, i) => (
              <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip formatter={(v: number) => v.toLocaleString()} />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Model Stats Dashboard                                               */
/* ------------------------------------------------------------------ */
function ModelStats({
  modelInfo,
  stats,
}: {
  modelInfo: ModelInfo | null;
  stats: DatasetStats | null;
}) {
  const metrics = modelInfo?.metrics as Record<string, unknown> | undefined;

  const fiData = stats?.feature_importance
    ? Object.entries(stats.feature_importance)
        .slice(0, 10)
        .map(([name, imp]) => ({
          name: name.length > 18 ? name.slice(0, 16) + ".." : name,
          importance: Math.round(imp * 1000) / 10,
        }))
    : [];

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <h2 className="text-lg font-semibold text-gray-800 mb-4">
        Model Performance
      </h2>

      {modelInfo && (
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-blue-50 rounded-lg p-3 text-center">
            <div className="text-xs text-blue-500 uppercase">Model</div>
            <div className="text-sm font-semibold text-blue-900 mt-1">
              {modelInfo.model_name}
            </div>
          </div>
          <div className="bg-blue-50 rounded-lg p-3 text-center">
            <div className="text-xs text-blue-500 uppercase">Features</div>
            <div className="text-sm font-semibold text-blue-900 mt-1">
              {modelInfo.feature_count}
            </div>
          </div>
          {metrics?.accuracy != null && (
            <div className="bg-green-50 rounded-lg p-3 text-center">
              <div className="text-xs text-green-600 uppercase">Accuracy</div>
              <div className="text-lg font-bold text-green-900 mt-1">
                {((metrics.accuracy as number) * 100).toFixed(1)}%
              </div>
            </div>
          )}
          {metrics?.weighted_f1 != null && (
            <div className="bg-green-50 rounded-lg p-3 text-center">
              <div className="text-xs text-green-600 uppercase">
                Weighted F1
              </div>
              <div className="text-lg font-bold text-green-900 mt-1">
                {((metrics.weighted_f1 as number) * 100).toFixed(1)}%
              </div>
            </div>
          )}
          {metrics?.auroc != null && (
            <div className="bg-purple-50 rounded-lg p-3 text-center col-span-2">
              <div className="text-xs text-purple-600 uppercase">
                AUROC (weighted)
              </div>
              <div className="text-lg font-bold text-purple-900 mt-1">
                {((metrics.auroc as number) * 100).toFixed(1)}%
              </div>
            </div>
          )}
        </div>
      )}

      {/* Feature importance */}
      {fiData.length > 0 && (
        <>
          <h3 className="text-sm font-medium text-gray-600 mb-2">
            Top Feature Importances
          </h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={fiData} layout="vertical" margin={{ left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis
                type="category"
                dataKey="name"
                width={120}
                tick={{ fontSize: 11 }}
              />
              <Tooltip />
              <Bar dataKey="importance" fill="#6366F1" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main App                                                            */
/* ------------------------------------------------------------------ */
export default function App() {
  const [prediction, setPrediction] = useState<TriagePrediction | null>(null);
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch model info and stats on mount
  useEffect(() => {
    apiFetch<ModelInfo>("/model-info").then(setModelInfo).catch(() => {});
    apiFetch<DatasetStats>("/stats").then(setStats).catch(() => {});
  }, []);

  const handlePredict = useCallback(async (inp: TriageInput) => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiFetch<TriagePrediction>("/predict", {
        method: "POST",
        body: JSON.stringify(inp),
      });
      setPrediction(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Prediction failed");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              ED Triage AI Dashboard
            </h1>
            <p className="text-sm text-gray-500 mt-0.5">
              ESI-Equivalent Acuity Prediction System
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-block w-2 h-2 rounded-full bg-green-500"></span>
            <span className="text-sm text-gray-500">
              {modelInfo ? modelInfo.model_name : "Connecting..."}
            </span>
          </div>
        </div>
      </header>

      {/* Body */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left column: input form */}
          <div className="lg:col-span-1">
            <TriageForm onPredict={handlePredict} loading={loading} />
          </div>

          {/* Middle column: prediction result */}
          <div className="lg:col-span-1">
            {prediction ? (
              <PredictionResult pred={prediction} />
            ) : (
              <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 flex items-center justify-center h-full min-h-[300px]">
                <p className="text-gray-400 text-sm text-center">
                  Enter patient data and click "Predict Acuity" to see results
                </p>
              </div>
            )}
          </div>

          {/* Right column: model stats */}
          <div className="lg:col-span-1 space-y-6">
            <ModelStats modelInfo={modelInfo} stats={stats} />
          </div>
        </div>

        {/* Bottom row: census board */}
        <div className="mt-6">
          <CensusBoard stats={stats} />
        </div>

        {/* ESI Level Reference */}
        <div className="mt-6 bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-3">
            ESI Acuity Reference
          </h2>
          <div className="grid grid-cols-5 gap-3">
            {[1, 2, 3, 4, 5].map((level) => (
              <div
                key={level}
                className="rounded-lg p-3 text-center text-white text-sm font-medium"
                style={{ backgroundColor: ACUITY_COLORS[level] }}
              >
                <div className="text-2xl font-bold">{level}</div>
                <div className="mt-1 text-xs">{ACUITY_LABELS[level]}</div>
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}
