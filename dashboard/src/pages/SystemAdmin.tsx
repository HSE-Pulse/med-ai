import { useState, useEffect, useCallback } from "react";
import {
  CheckCircle,
  XCircle,
  RefreshCw,
  Copy,
  Check,
  Server,
  Database,
  Cpu,
  Box,
  Brain,
  BookOpen,
} from "lucide-react";
import {
  DigitalTwinHealthPanel,
  CircuitBreakerPanel,
  ModelsRegistryPanel,
  SafeguardingPanel,
  ResearchGovernancePanel,
} from "../components/UpliftWidgets";

// ==================== Types ====================

interface ServiceHealth {
  name: string;
  port: number;
  online: boolean;
  uptime: number | null;
  responseTime: number | null;
}

interface ModelRow {
  module: string;
  model: string;
  family: string;
  target: string;
  auroc: number | null;
  f1: number | null;
  status: string;
}

interface LlmRow {
  task: string;
  model: string;
  notes: string;
  available: boolean;
}

// ==================== Component ====================

export default function SystemAdmin() {
  const [healthServices, setHealthServices] = useState<ServiceHealth[]>([]);
  const [healthLoading, setHealthLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [modelRows, setModelRows] = useState<ModelRow[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [llmRows, setLlmRows] = useState<LlmRow[]>([]);
  const [llmsAvailable, setLlmsAvailable] = useState<string[]>([]);
  const [llmsLoading, setLlmsLoading] = useState(true);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

  // ---- Health Check ----
  // The full set of services proxied via Vite (kept in sync with
  // `vite.config.ts`). Closes F16 — the previous list only probed 5/19
  // and the table mis-reported "Hospital Ops API" as the only Online row
  // even when six others were actually serving traffic.
  const fetchHealth = useCallback(async () => {
    const endpoints = [
      { name: "ED Triage API", port: 8201, url: "/api/ed/health" },
      { name: "Sepsis ICU API", port: 8202, url: "/api/sepsis/health" },
      { name: "Hospital Ops API", port: 8203, url: "/api/ops/health" },
      { name: "Oncology AI API", port: 8204, url: "/api/onco/health" },
      { name: "Patient Journey API", port: 8205, url: "/api/journey/health" },
      { name: "Clinical Chat API", port: 8206, url: "/api/chat/health" },
      { name: "Simulation Engine", port: 8207, url: "/api/sim/health" },
      { name: "Bed Management API", port: 8208, url: "/api/beds/health" },
      { name: "Waiting List API", port: 8209, url: "/api/waitlist/health" },
      { name: "Clinical Scribe API", port: 8210, url: "/api/scribe/health" },
      { name: "ED Flow Optimizer", port: 8214, url: "/api/ed-flow/health" },
      { name: "Hospital ERP", port: 8215, url: "/api/erp/health" },
      { name: "Trolley Watch", port: 8216, url: "/api/trolley/health" },
      { name: "GDPR Compliance", port: 8217, url: "/api/gdpr/health" },
      { name: "XAI Audit", port: 8218, url: "/api/xai/health" },
      { name: "FHIR Gateway", port: 8219, url: "/api/fhir/health" },
      { name: "Deterioration Monitor", port: 8220, url: "/api/deterioration/health" },
      { name: "Discharge Lounge", port: 8221, url: "/api/discharge-lounge/health" },
      { name: "Alert Stream", port: 8222, url: "/api/alerts/health" },
    ];

    const results: ServiceHealth[] = await Promise.all(
      endpoints.map(async (ep) => {
        try {
          const start = performance.now();
          const res = await fetch(ep.url);
          const elapsed = Math.round(performance.now() - start);
          if (!res.ok)
            return { name: ep.name, port: ep.port, online: false, uptime: null, responseTime: null };
          const data = await res.json();
          // Health endpoints may return either the raw fields at the top
          // level OR wrap them in the standard `{status, data, error}`
          // envelope. Probe both shapes so the Uptime column is populated
          // for every service that's actually reporting.
          const inner = (data && typeof data === "object" && "data" in data && data.data) || {};
          const uptime =
            data.uptime ??
            data.uptime_seconds ??
            inner.uptime ??
            inner.uptime_seconds ??
            null;
          return {
            name: ep.name,
            port: ep.port,
            online: true,
            uptime: typeof uptime === "number" ? uptime : null,
            responseTime: elapsed,
          };
        } catch {
          return { name: ep.name, port: ep.port, online: false, uptime: null, responseTime: null };
        }
      })
    );

    setHealthServices(results);
    setHealthLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    fetchHealth();
  }, [fetchHealth]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchHealth();
  };

  // ---- Model Performance ----
  useEffect(() => {
    async function fetchModels() {
      const staticModels: ModelRow[] = [
        { module: "ED Triage", model: "TriageXGBoost", family: "XGBoost", target: "ESI acuity 1–5", auroc: 0.728, f1: 0.653, status: "Serving" },
        { module: "ED Triage", model: "ed_triage_nn", family: "PyTorch MLP", target: "ESI acuity (backup)", auroc: 0.69, f1: 0.577, status: "Trained" },
        { module: "Sepsis ICU", model: "sepsis_lgbm", family: "LightGBM", target: "Sepsis onset 4–6h ahead", auroc: 0.994, f1: 0.052, status: "Serving" },
        { module: "Sepsis ICU", model: "sepsis_lstm", family: "LSTM-Attention", target: "Sepsis sequence model (60% weight)", auroc: 0.998, f1: 0.179, status: "Serving" },
        { module: "Hospital Ops", model: "MADDPG (14 actors)", family: "Multi-Agent RL (PyTorch)", target: "Dept staffing / priority / queue", auroc: null, f1: null, status: "Serving" },
        { module: "Oncology", model: "xgb_readmission_30d", family: "XGBoost", target: "30-day readmission", auroc: 0.734, f1: 0.511, status: "Serving" },
        { module: "Oncology", model: "xgb_hospital_mortality", family: "XGBoost", target: "In-hospital mortality", auroc: 0.897, f1: 0.322, status: "Serving" },
        { module: "Oncology", model: "transformer_readmission_30d", family: "Transformer (PyTorch)", target: "30-day readmission", auroc: 0.733, f1: 0.506, status: "Trained" },
        { module: "Oncology", model: "transformer_mortality", family: "Transformer (PyTorch)", target: "In-hospital mortality", auroc: 0.876, f1: 0.268, status: "Trained" },
        { module: "Bed Management", model: "bed_discharge_24h", family: "XGBoost", target: "24h discharge likelihood", auroc: null, f1: null, status: "Serving" },
        { module: "Bed Management", model: "LOSRegressor", family: "XGBoost regressor", target: "Length of stay (days)", auroc: null, f1: null, status: "Serving" },
        { module: "Bed Management", model: "CapacityForecaster", family: "Time-series", target: "Bed capacity forecast", auroc: null, f1: null, status: "Serving" },
        { module: "Waiting List", model: "waiting_list_priority", family: "Classifier", target: "Clinical priority score", auroc: null, f1: null, status: "Serving" },
        { module: "Waiting List", model: "waiting_list_adverse", family: "Classifier", target: "90-day adverse event risk", auroc: null, f1: null, status: "Serving" },
        { module: "Clinical Scribe", model: "ICDCoder", family: "LogReg + TF-IDF (sklearn)", target: "ICD-10-AM code suggestion (50 codes)", auroc: 0.91, f1: null, status: "Serving" },
        { module: "Clinical Scribe", model: "NER engine", family: "Rule + classical NLP", target: "Clinical entity extraction", auroc: null, f1: null, status: "Serving" },
        { module: "Clinical Scribe", model: "Section classifier", family: "sklearn", target: "Section tagging", auroc: null, f1: null, status: "Serving" },
        { module: "ED Flow", model: "ed_flow_disposition", family: "Classifier", target: "Admit vs discharge", auroc: null, f1: null, status: "Serving" },
        { module: "ED Flow", model: "ed_flow_los", family: "Regressor", target: "ED length of stay", auroc: null, f1: null, status: "Serving" },
        { module: "ED Flow", model: "ed_flow_pet_breach", family: "Classifier", target: "PET (6h) breach risk", auroc: null, f1: null, status: "Serving" },
        { module: "Deterioration", model: "NEWS2 / PEWS / IMEWS", family: "Rules-based EWS", target: "Early warning score", auroc: null, f1: null, status: "Serving" },
      ];

      try {
        const res = await fetch("/api/ed/model-info");
        if (res.ok) {
          const data = await res.json();
          const metrics = data?.metrics || data?.data?.metrics;
          if (metrics) {
            const idx = staticModels.findIndex(
              (m) => m.module === "ED Triage" && m.model === "XGBoost"
            );
            if (idx >= 0) {
              if (metrics.auroc != null) staticModels[idx].auroc = metrics.auroc;
              if (metrics.weighted_f1 != null) staticModels[idx].f1 = metrics.weighted_f1;
              if (metrics.f1 != null) staticModels[idx].f1 = metrics.f1;
            }
          }
        }
      } catch {
        // Use static values
      }

      setModelRows(staticModels);
      setModelsLoading(false);
    }
    fetchModels();
  }, []);

  // ---- LLM Roster (Ollama-backed via Clinical Chat) ----
  useEffect(() => {
    async function fetchLlms() {
      const configured: Omit<LlmRow, "available">[] = [
        { task: "Intent detection", model: "llama3.2:3b", notes: "Fast classifier (~65 t/s)" },
        { task: "Clinical summary", model: "llama3.2:3b", notes: "Default for structured intents" },
        { task: "Clinical response / Medical QA", model: "deepseek-r1:8b", notes: "Chain-of-thought, 93% MedQA" },
        { task: "Note analysis", model: "MedAIBase/MedGemma1.5:4b-it", notes: "Medical-text fine-tune" },
        { task: "Biomedical Q&A", model: "koesn/llama3-openbiollm-8b:q4_K_M", notes: "Biomedical domain" },
        { task: "Fast fallback", model: "llama3.2:3b", notes: "When speed > accuracy" },
      ];

      let available: string[] = [];
      try {
        const res = await fetch("/api/chat/models");
        if (res.ok) {
          const data = await res.json();
          available = Array.isArray(data?.models) ? data.models : [];
        }
      } catch {
        // Leave list empty; rows render as Missing
      }

      setLlmsAvailable(available);
      setLlmRows(
        configured.map((row) => ({ ...row, available: available.includes(row.model) }))
      );
      setLlmsLoading(false);
    }
    fetchLlms();
  }, []);

  const aurocColor = (v: number) =>
    v > 0.9 ? "#22C55E" : v > 0.7 ? "#EAB308" : "#DC2626";

  // ---- Service Configuration ----
  // All 19 FastAPI backends + dashboard run as `hse-*` containers on the
  // `cancer_default` bridge; ports map to 100.118.170.33 (Tailscale IP).
  const serviceConfig = [
    { name: "ED Triage API",         port: 8201, container: "hse-ed_triage",         module: "app_01_ed_triage.backend.app.main:app" },
    { name: "Sepsis ICU API",        port: 8202, container: "hse-sepsis_icu",        module: "app_02_sepsis_icu.backend.app.main:app" },
    { name: "Hospital Ops API",      port: 8203, container: "hse-hospital_ops",      module: "app_03_hospital_ops.backend.app.main:app" },
    { name: "Oncology AI API",       port: 8204, container: "hse-oncology_ai",       module: "app_04_oncology_ai.backend.app.main:app" },
    { name: "Patient Journey API",   port: 8205, container: "hse-patient_journey",   module: "app_05_patient_journey.backend.api.main:app" },
    { name: "Clinical Chat API",     port: 8206, container: "hse-clinical_chat",     module: "app_06_clinical_chat.backend.main:app" },
    { name: "Simulation Engine",     port: 8207, container: "hse-data_ingestion",    module: "app_07_data_ingestion.backend.api.main:app" },
    { name: "Bed Management API",    port: 8208, container: "hse-bed_management",    module: "app_08_bed_management.backend.app.main:app" },
    { name: "Waiting List API",      port: 8209, container: "hse-waiting_list",      module: "app_09_waiting_list.backend.app.main:app" },
    { name: "Clinical Scribe API",   port: 8210, container: "hse-clinical_scribe",   module: "app_10_clinical_scribe.backend.app.main:app" },
    { name: "ED Flow Optimizer",     port: 8214, container: "hse-ed_flow",           module: "app_14_ed_flow.backend.app.main:app" },
    { name: "Hospital ERP",          port: 8215, container: "hse-erp",               module: "app_15_erp.backend.app.main:app" },
    { name: "Trolley Watch",         port: 8216, container: "hse-trolley_watch",     module: "app_16_trolley_watch.backend.app.main:app" },
    { name: "GDPR Compliance",       port: 8217, container: "hse-gdpr",              module: "app_17_gdpr.backend.app.main:app" },
    { name: "XAI Audit",             port: 8218, container: "hse-xai",               module: "app_18_xai.backend.app.main:app" },
    { name: "FHIR Gateway",          port: 8219, container: "hse-fhir",              module: "app_19_fhir.backend.app.main:app" },
    { name: "Deterioration Monitor", port: 8220, container: "hse-deterioration",     module: "app_20_deterioration.backend.app.main:app" },
    { name: "Discharge Lounge",      port: 8221, container: "hse-discharge_lounge",  module: "app_21_discharge_lounge.backend.app.main:app" },
    { name: "Alert Stream",          port: 8222, container: "hse-alerts",            module: "app_22_alerts.backend.app.main:app" },
    { name: "Dashboard (Vite)",      port: 3010, container: "hse-dashboard",         module: "dashboard/" },
  ];

  const getServiceStatus = (port: number) => {
    if (port === 3010) return true; // Dashboard is always online if we can see this
    const svc = healthServices.find((s) => s.port === port);
    return svc?.online ?? false;
  };

  const copyCommand = (svc: (typeof serviceConfig)[0], idx: number) => {
    const cmd = `docker logs -f ${svc.container}`;
    navigator.clipboard.writeText(cmd);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  };

  // ---- Dataset Inventory ----
  const datasets = [
    { name: "ED Triage", records: "299,267 admissions", size: "5.2 MB", features: "59 features", source: "MIMIC admissions + chartevents + labevents" },
    { name: "Sepsis ICU", records: "329,877 windows", size: "3.1 MB", features: "6x19 seq + 117 flat", source: "MIMIC_ICU chartevents + labevents" },
    { name: "Hospital Ops", records: "1,560,641 transfers", size: "40.9 MB", features: "Transfer sequences", source: "MIMIC transfers + admissions" },
    { name: "Oncology", records: "67,896 admissions", size: "32.8 MB", features: "16 features, 4 targets", source: "MIMIC admissions + diagnoses_icd" },
  ];

  // ---- Technology Stack ----
  const techStack = [
    { category: "Deep Learning", items: "Python 3.14, PyTorch 2.11, MADDPG (custom), Transformer + LSTM-Attention" },
    { category: "Classical ML", items: "XGBoost-cpu 3.2, LightGBM 4.6, scikit-learn 1.7" },
    { category: "LLM serving", items: "Ollama (host) — llama3.2:3b, deepseek-r1:8b, MedGemma1.5, OpenBioLLM-8b" },
    { category: "Backend", items: "FastAPI 0.135, uvicorn 0.42, httpx async, Pydantic v2" },
    { category: "Frontend", items: "React 19.1, Vite 6.4, Tailwind CSS, Recharts, lucide-react" },
    { category: "Data", items: "MongoDB 7 (cancer-mongo, 49 GB), MIMIC-IV CSVs imported via mongoimport" },
    { category: "Messaging", items: "Redpanda (Kafka-API), aiokafka, Redis 7 (cache + pub/sub)" },
    { category: "Observability", items: "OpenTelemetry → Jaeger, Loki + Promtail, Prometheus, Grafana" },
    { category: "Runtime", items: "Docker Compose (19 hse-* + cancer_default bridge), Mongo 7 pinned" },
    { category: "Hardware", items: "Ryzen AI 9 HX 370, 32 GB RAM, RTX 4060 8 GB (Ollama-owned)" },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      {/* ===== Section A: System Health Monitor ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Server className="w-5 h-5 text-blue-400" />
            <h2 className="text-sm font-semibold text-white">System Health Monitor</h2>
          </div>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-700/50 hover:bg-slate-700 rounded-lg border border-border transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        {healthLoading ? (
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="skeleton h-10 w-full" />
            ))}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left border-b border-border">
                  <th className="py-2 px-3 font-medium">Service</th>
                  <th className="py-2 px-3 font-medium text-center">Port</th>
                  <th className="py-2 px-3 font-medium text-center">Status</th>
                  <th className="py-2 px-3 font-medium text-right">Uptime (s)</th>
                  <th className="py-2 px-3 font-medium text-right">Response (ms)</th>
                </tr>
              </thead>
              <tbody>
                {healthServices.map((svc, i) => (
                  <tr
                    key={svc.name}
                    className={`border-b border-border/30 transition-colors ${
                      i % 2 === 0 ? "bg-slate-800/20" : ""
                    }`}
                  >
                    <td className="py-2.5 px-3 text-white font-medium">{svc.name}</td>
                    <td className="py-2.5 px-3 text-center font-mono-clinical text-slate-300">
                      {svc.port}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <span
                        className={`inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full ${
                          svc.online
                            ? "bg-green-500/10 text-green-400 border border-green-500/30"
                            : "bg-red-500/10 text-red-400 border border-red-500/30"
                        }`}
                      >
                        <div
                          className={`w-1.5 h-1.5 rounded-full ${
                            svc.online ? "bg-green-400" : "bg-red-400"
                          }`}
                        />
                        {svc.online ? "Online" : "Offline"}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-clinical text-slate-300">
                      {svc.online && svc.uptime != null ? svc.uptime.toLocaleString() : "\u2014"}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-clinical text-slate-300">
                      {svc.online && svc.responseTime != null ? svc.responseTime : "\u2014"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ===== Section B: Model Performance Table ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center gap-2 mb-4">
          <Cpu className="w-5 h-5 text-purple-400" />
          <h2 className="text-sm font-semibold text-white">Model Performance</h2>
        </div>

        {modelsLoading ? (
          <div className="space-y-2">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="skeleton h-8 w-full" />
            ))}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left border-b border-border">
                  <th className="py-2 px-3 font-medium">Module</th>
                  <th className="py-2 px-3 font-medium">Model</th>
                  <th className="py-2 px-3 font-medium">Family</th>
                  <th className="py-2 px-3 font-medium">Target</th>
                  <th className="py-2 px-3 font-medium text-right">AUROC</th>
                  <th className="py-2 px-3 font-medium text-right">F1</th>
                  <th className="py-2 px-3 font-medium text-center">Status</th>
                </tr>
              </thead>
              <tbody>
                {modelRows.map((row, i) => (
                  <tr
                    key={i}
                    className={`border-b border-border/30 transition-colors ${
                      i % 2 === 0 ? "bg-slate-800/20" : ""
                    }`}
                  >
                    <td className="py-2.5 px-3 text-slate-300">{row.module}</td>
                    <td className="py-2.5 px-3 text-white font-medium">{row.model}</td>
                    <td className="py-2.5 px-3 text-slate-400 text-[10px]">{row.family}</td>
                    <td className="py-2.5 px-3 text-slate-400 text-[10px]">{row.target}</td>
                    <td className="py-2.5 px-3 text-right">
                      {row.auroc != null ? (
                        <span
                          className="font-mono-clinical font-bold"
                          style={{ color: aurocColor(row.auroc) }}
                        >
                          {row.auroc.toFixed(3)}
                        </span>
                      ) : (
                        <span className="text-slate-600">{"—"}</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-right font-mono-clinical text-slate-300">
                      {row.f1 != null ? row.f1.toFixed(3) : <span className="text-slate-600">{"—"}</span>}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <span
                        className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full ${
                          row.status === "Serving"
                            ? "bg-green-500/10 text-green-400 border border-green-500/30"
                            : "bg-slate-700/50 text-slate-400 border border-slate-600/30"
                        }`}
                      >
                        <div
                          className={`w-1.5 h-1.5 rounded-full ${
                            row.status === "Serving" ? "bg-green-400" : "bg-slate-500"
                          }`}
                        />
                        {row.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ===== Section B2: LLMs & Agents (Clinical Chat / Ollama) ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-pink-400" />
            <h2 className="text-sm font-semibold text-white">LLMs & Agents</h2>
          </div>
          <span className="text-[10px] text-slate-500 font-mono-clinical">
            Ollama @ http://ollama:11434 · {llmsAvailable.length} model{llmsAvailable.length === 1 ? "" : "s"} pulled
          </span>
        </div>
        <p className="text-[11px] text-slate-500 mb-3">
          Clinical Chat (port 8206) routes per-task to Ollama models (config in
          <span className="font-mono-clinical"> chat_engine.MODEL_BY_TASK</span>). Agentic loop: intent
          detection → tool-use chains into ED Triage / Oncology / Patient Journey APIs → response generation.
          Falls back to GPT (if key) and template responses when Ollama is unavailable.
        </p>

        {llmsLoading ? (
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="skeleton h-8 w-full" />
            ))}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left border-b border-border">
                  <th className="py-2 px-3 font-medium">Task</th>
                  <th className="py-2 px-3 font-medium">Model</th>
                  <th className="py-2 px-3 font-medium">Notes</th>
                  <th className="py-2 px-3 font-medium text-center">In Ollama</th>
                </tr>
              </thead>
              <tbody>
                {llmRows.map((row, i) => (
                  <tr
                    key={i}
                    className={`border-b border-border/30 transition-colors ${
                      i % 2 === 0 ? "bg-slate-800/20" : ""
                    }`}
                  >
                    <td className="py-2.5 px-3 text-slate-300">{row.task}</td>
                    <td className="py-2.5 px-3 text-white font-mono-clinical text-[11px]">{row.model}</td>
                    <td className="py-2.5 px-3 text-slate-400 text-[10px]">{row.notes}</td>
                    <td className="py-2.5 px-3 text-center">
                      <span
                        className={`inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full ${
                          row.available
                            ? "bg-green-500/10 text-green-400 border border-green-500/30"
                            : "bg-yellow-500/10 text-yellow-400 border border-yellow-500/30"
                        }`}
                      >
                        <div
                          className={`w-1.5 h-1.5 rounded-full ${
                            row.available ? "bg-green-400" : "bg-yellow-400"
                          }`}
                        />
                        {row.available ? "Available" : "Missing"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {llmsAvailable.length > 0 && (
              <div className="mt-3 text-[10px] text-slate-500">
                <span className="text-slate-400">All pulled models:</span>{" "}
                <span className="font-mono-clinical">{llmsAvailable.join(", ")}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ===== Section B3: RAG / Knowledge Base ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center gap-2 mb-2">
          <BookOpen className="w-5 h-5 text-cyan-400" />
          <h2 className="text-sm font-semibold text-white">RAG / Knowledge Base</h2>
        </div>
        <p className="text-[11px] text-slate-400 leading-relaxed">
          <span className="text-yellow-300">No vector store configured.</span> Clinical Chat does
          <em> not</em> retrieve over an embedding index. Instead it uses:
        </p>
        <ul className="mt-2 space-y-1 text-[11px] text-slate-300 list-disc list-inside">
          <li>
            <span className="text-white font-medium">Session memory</span> — recent turns + sticky
            patient context kept per <span className="font-mono-clinical">session_id</span>.
          </li>
          <li>
            <span className="text-white font-medium">Cross-service API chaining</span> — agent tools
            call ED Triage / Oncology / Patient Journey / Sepsis ICU directly to ground answers in
            live data instead of pre-indexed documents.
          </li>
          <li>
            <span className="text-white font-medium">Sim-context snapshot</span> — compressed (~2 KB)
            digital-twin state injected into the LLM context window for situational awareness.
          </li>
        </ul>
        <p className="mt-2 text-[10px] text-slate-500">
          Embedding models in use: <span className="font-mono-clinical">none</span> · Vector DB:{" "}
          <span className="font-mono-clinical">none</span>
        </p>
      </div>

      {/* ===== Section C: Service Configuration ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center gap-2 mb-4">
          <Box className="w-5 h-5 text-orange-400" />
          <h2 className="text-sm font-semibold text-white">Service Configuration</h2>
        </div>

        <p className="text-[11px] text-slate-500 mb-3">
          {serviceConfig.length} services on the <span className="font-mono-clinical">cancer_default</span>{" "}
          bridge, port-mapped to <span className="font-mono-clinical">100.118.170.33</span> (Tailscale).
          Copy gives <span className="font-mono-clinical">docker logs -f &lt;container&gt;</span>.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 text-left border-b border-border">
                <th className="py-2 px-3 font-medium">Service</th>
                <th className="py-2 px-3 font-medium text-center">Port</th>
                <th className="py-2 px-3 font-medium">Container</th>
                <th className="py-2 px-3 font-medium">Module Path</th>
                <th className="py-2 px-3 font-medium text-center">Status</th>
                <th className="py-2 px-3 font-medium text-center">Logs</th>
              </tr>
            </thead>
            <tbody>
              {serviceConfig.map((svc, i) => {
                const online = getServiceStatus(svc.port);
                return (
                  <tr
                    key={svc.name}
                    className={`border-b border-border/30 transition-colors ${
                      i % 2 === 0 ? "bg-slate-800/20" : ""
                    }`}
                  >
                    <td className="py-2.5 px-3 text-white font-medium">{svc.name}</td>
                    <td className="py-2.5 px-3 text-center font-mono-clinical text-slate-300">
                      {svc.port}
                    </td>
                    <td className="py-2.5 px-3 font-mono-clinical text-slate-300 text-[10px]">
                      {svc.container}
                    </td>
                    <td className="py-2.5 px-3 font-mono-clinical text-slate-400 text-[10px]">
                      {svc.module}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <span
                        className={`inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full ${
                          online
                            ? "bg-green-500/10 text-green-400 border border-green-500/30"
                            : "bg-red-500/10 text-red-400 border border-red-500/30"
                        }`}
                      >
                        <div
                          className={`w-1.5 h-1.5 rounded-full ${
                            online ? "bg-green-400" : "bg-red-400"
                          }`}
                        />
                        {online ? "Online" : "Offline"}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <button
                        onClick={() => copyCommand(svc, i)}
                        className="inline-flex items-center gap-1 px-2 py-1 text-[10px] text-slate-400 hover:text-white bg-slate-700/30 hover:bg-slate-700 rounded border border-border transition-colors"
                        title={`docker logs -f ${svc.container}`}
                      >
                        {copiedIdx === i ? (
                          <>
                            <Check className="w-3 h-3 text-green-400" />
                            <span className="text-green-400">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            Copy
                          </>
                        )}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ===== Section D: Dataset Inventory ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center gap-2 mb-4">
          <Database className="w-5 h-5 text-teal-400" />
          <h2 className="text-sm font-semibold text-white">Dataset Inventory</h2>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 text-left border-b border-border">
                <th className="py-2 px-3 font-medium">Dataset</th>
                <th className="py-2 px-3 font-medium">Records</th>
                <th className="py-2 px-3 font-medium text-right">Size</th>
                <th className="py-2 px-3 font-medium">Features</th>
                <th className="py-2 px-3 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((ds, i) => (
                <tr
                  key={ds.name}
                  className={`border-b border-border/30 transition-colors ${
                    i % 2 === 0 ? "bg-slate-800/20" : ""
                  }`}
                >
                  <td className="py-2.5 px-3 text-white font-medium">{ds.name}</td>
                  <td className="py-2.5 px-3 font-mono-clinical text-slate-300">{ds.records}</td>
                  <td className="py-2.5 px-3 text-right font-mono-clinical text-slate-300">
                    {ds.size}
                  </td>
                  <td className="py-2.5 px-3 text-slate-400">{ds.features}</td>
                  <td className="py-2.5 px-3 text-slate-500 text-[10px]">{ds.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ===== Section E: Technology Stack ===== */}
      <div className="bg-bg-card rounded-xl border border-border p-5">
        <div className="flex items-center gap-2 mb-4">
          <Cpu className="w-5 h-5 text-indigo-400" />
          <h2 className="text-sm font-semibold text-white">Technology Stack</h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {techStack.map((t) => (
            <div
              key={t.category}
              className="bg-bg-primary rounded-lg border border-border/50 p-3"
            >
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">
                {t.category}
              </div>
              <div className="text-xs text-slate-300 font-mono-clinical">{t.items}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Engineering-uplift observability panels */}
      <DigitalTwinHealthPanel />
      <CircuitBreakerPanel />
      <ModelsRegistryPanel />
      <ResearchGovernancePanel />
      <SafeguardingPanel />
    </div>
  );
}
