// ==================== Type Definitions ====================

export interface TriageInput {
  age: number;
  gender: string;
  hr: number;
  rr: number;
  spo2: number;
  sbp: number;
  dbp: number;
  temperature: number;
  wbc: number;
  hemoglobin: number;
  lactate: number;
  glucose: number;
  creatinine: number;
  arrival_mode: string;
}

export interface TriagePrediction {
  esi_level: 1 | 2 | 3 | 4 | 5;
  confidence: number;
  probabilities: number[];
  disposition: string;
  estimated_los_hours: number;
  risk_factors: string[];
}

export interface EdStats {
  total_patients: number;
  by_esi: Record<string, number>;
  avg_wait_minutes: number;
  model_accuracy: number;
  model_f1: number;
  model_auroc: number;
}

export interface SepsisInput {
  age: number;
  gender: string;
  hr: number;
  rr: number;
  spo2: number;
  sbp: number;
  temperature: number;
  wbc: number;
  lactate: number;
  creatinine: number;
  bilirubin: number;
  platelets: number;
  gcs: number;
  fio2: number;
  urine_output: number;
}

export interface SepsisPrediction {
  risk_score: number;
  sofa_score: number;
  sofa_breakdown: Record<string, number>;
  sepsis_likely: boolean;
  recommendations: string[];
}

export interface UnitOverview {
  total_patients: number;
  high_risk_count: number;
  critical_alerts: number;
  avg_sofa: number;
  patients: Array<{
    id: string;
    age: number;
    gender: string;
    risk_score: number;
    sofa: number;
    vitals: Record<string, number>;
  }>;
}

export interface OpsSimParams {
  algorithm: string;
  speed: number;
}

export interface SimState {
  running: boolean;
  sim_time: number;
  departments: Array<{
    name: string;
    patients: number;
    capacity: number;
    wait_time: number;
    utilization: number;
  }>;
}

export interface OpsMetrics {
  mean_wait_time: number;
  total_throughput: number;
  bed_utilization: number;
  staff_efficiency: number;
  baseline_wait: number;
  marl_improvement: number;
}

export interface OncologyRiskInput {
  age: number;
  gender: string;
  cancer_type: string;
  stage: number;
  charlson_score: number;
  has_surgery: boolean;
  has_chemo: boolean;
  has_radiation: boolean;
  los_days: number;
  prior_admissions: number;
  comorbidities: string[];
}

export interface RiskPrediction {
  readmission_risk: number;
  mortality_risk: number;
  risk_level: string;
  contributing_factors: string[];
  recommendations: string[];
}

export interface PathwayInput {
  cancer_type: string;
  age: number;
  stage: number;
  charlson_score: number;
}

export interface PathwayStep {
  step: number;
  treatment: string;
  category: string;
  estimated_days: number;
  priority: string;
  description: string;
}

export interface PathwayResult {
  pathway: PathwayStep[];
  total_duration_days: number;
  urgency_score: number;
  clinical_notes: string;
}

// Patient Journey types
export interface TimelineEvent {
  timestamp: string;
  event_type: string;
  source_table: string;
  category: string;
  details: Record<string, any>;
}

export interface PatientSummary {
  subject_id: number;
  gender: string;
  anchor_age: number;
  admissions: Array<{
    hadm_id: number;
    admittime: string;
    dischtime: string;
    admission_type: string;
    discharge_location: string;
    hospital_expire_flag: number;
  }>;
}

export interface VitalSeries {
  [vitalName: string]: Array<{ time: string; value: number }>;
}

export interface LabPanel {
  [labName: string]: Array<{ time: string; value: number; unit: string; flag: string }>;
}

export interface JourneyTransfer {
  eventtype: string;
  careunit: string;
  intime: string;
  outtime: string;
}

export interface JourneyPath {
  transfers: JourneyTransfer[];
  icu_episodes: Array<{
    stay_id: number;
    intime: string;
    outtime: string;
    los: number;
    first_careunit: string;
    last_careunit: string;
  }>;
  services: Array<{
    curr_service: string;
    transfertime: string;
  }>;
}

export interface MedicationRecord {
  drug: string;
  drug_type?: string;
  category: string;
  start_time: string | null;
  stop_time: string | null;
  starttime?: string;
  endtime?: string | null;
  route: string | null;
  dose_val_rx: string | null;
  dose_unit_rx: string | null;
  prod_strength?: string | null;
  duration_hours: number | null;
}

export interface JourneyMetrics {
  total_los_hours: number;
  icu_los_hours: number;
  ed_los_hours: number;
  num_transfers: number;
  num_icu_episodes: number;
  num_procedures: number;
  num_unique_drugs: number;
  mortality: boolean;
}

// ==================== API Client ====================

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T | null> {
  try {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      console.error(`API error ${response.status}: ${url}`);
      return null;
    }
    return await response.json();
  } catch (error) {
    console.error(`API fetch failed: ${url}`, error);
    return null;
  }
}

// ED Triage API
export async function edTriagePredict(input: TriageInput): Promise<TriagePrediction | null> {
  // Map frontend field names to backend schema
  const payload = {
    age: input.age,
    gender: input.gender,
    heart_rate: input.hr,
    respiratory_rate: input.rr,
    spo2: input.spo2,
    sbp: input.sbp,
    dbp: input.dbp,
    temperature: input.temperature,
    wbc: input.wbc,
    hemoglobin: input.hemoglobin,
    lactate: input.lactate,
    glucose: input.glucose,
    creatinine: input.creatinine,
    arrival_mode: input.arrival_mode,
  };
  const res = await apiFetch<{ status: string; data: {
    acuity_level: number;
    confidence: number;
    class_probabilities: Record<string, number>;
    disposition: string;
    ed_los_estimate_hours: number;
    risk_factors: string[];
  }; error: string | null }>("/api/ed/predict", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res || !("data" in res) || !res.data) return null;
  const d = res.data;
  return {
    esi_level: d.acuity_level as 1 | 2 | 3 | 4 | 5,
    confidence: d.confidence * 100,
    probabilities: [1, 2, 3, 4, 5].map(i => d.class_probabilities[`ESI-${i}`] || 0),
    disposition: d.disposition,
    estimated_los_hours: d.ed_los_estimate_hours,
    risk_factors: d.risk_factors,
  };
}

export async function edTriageStats(): Promise<EdStats | null> {
  const res = await apiFetch<{ status: string; data: any }>("/api/ed/stats");
  if (!res || !("data" in res) || !res.data) return null;
  return res.data as EdStats;
}

export async function edTriageModelInfo(): Promise<any | null> {
  const res = await apiFetch<{ status: string; data: any }>("/api/ed/model-info");
  if (!res || !("data" in res) || !res.data) return null;
  return res.data;
}

// Sepsis API
export async function sepsisPredict(input: SepsisInput): Promise<SepsisPrediction | null> {
  return apiFetch<SepsisPrediction>("/api/sepsis/predict", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function sepsisUnitOverview(): Promise<UnitOverview | null> {
  return apiFetch<UnitOverview>("/api/sepsis/unit-overview");
}

// Hospital Operations API
export async function opsStart(params: OpsSimParams): Promise<SimState | null> {
  return apiFetch<SimState>("/api/ops/start", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function opsState(): Promise<SimState | null> {
  return apiFetch<SimState>("/api/ops/state");
}

export async function opsMetrics(): Promise<OpsMetrics | null> {
  return apiFetch<OpsMetrics>("/api/ops/metrics");
}

// ==================== Hospital Ops — live DES metrics ====================
export interface DepartmentLiveMetrics {
  name: string;
  avg_wait_time_hours: number;
  avg_service_time_hours: number;
  occupancy_ratio: number;
  throughput: number;
  queue_length: number;
}

export interface HospitalOpsMetrics {
  simulation_id: string;
  simulation_time_hours: number;
  total_discharged: number;
  mean_total_wait_hours: number;
  mean_los_hours: number;
  active_patients: number;
  departments: DepartmentLiveMetrics[];
}

export async function hospitalOpsMetrics(): Promise<HospitalOpsMetrics | null> {
  try {
    const res = await fetch("/api/ops/api/metrics");
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export interface HospitalOpsSample {
  sim_time_h: number;
  total_wait_avg_min: number;
  total_throughput: number;
  // Counterfactual series sampled from the shadow baseline DES engine —
  // same admissions, no MARL staffing actions. Optional so older backends
  // that don't emit it still parse cleanly.
  baseline_wait_avg_min?: number;
  baseline_throughput?: number;
  total_queue: number;
  active_patients: number;
  total_discharged: number;
}

export async function hospitalOpsMetricsHistory(limit = 700): Promise<HospitalOpsSample[] | null> {
  try {
    const res = await fetch(`/api/ops/api/metrics/history?limit=${limit}`);
    if (!res.ok) return null;
    const j = await res.json();
    return (j?.data?.samples ?? []) as HospitalOpsSample[];
  } catch {
    return null;
  }
}

export interface StaffingRecommendation {
  department: string;
  current_doctors: number;
  current_nurses: number;
  recommended_doctors?: number;
  recommended_nurses?: number;
  service_rate_multiplier?: number;
}

export async function opsStaffingRecommendations(): Promise<Record<string, StaffingRecommendation> | null> {
  try {
    const res = await fetch("/api/ops/staffing-recommendations");
    if (!res.ok) return null;
    const j = await res.json();
    if (j.status !== "ok") return null;
    const depts = j.data?.departments ?? j.data;
    if (Array.isArray(depts)) {
      const out: Record<string, StaffingRecommendation> = {};
      for (const r of depts) {
        const name = (r as { department?: string; name?: string }).department ?? (r as { name?: string }).name;
        if (name) out[name] = r as StaffingRecommendation;
      }
      return out;
    }
    return depts ?? null;
  } catch {
    return null;
  }
}

// Oncology API
export async function oncoRisk(input: OncologyRiskInput): Promise<RiskPrediction | null> {
  // Map frontend fields to backend PatientInput schema
  const payload = {
    age: input.age,
    gender: input.gender,
    cancer_type: input.cancer_type,
    stage_proxy: input.stage,
    drg_mortality: Math.min(4, Math.ceil(input.stage * 0.75)),
    num_procedures: (input.has_surgery ? 1 : 0) + (input.has_chemo ? 1 : 0) + (input.has_radiation ? 1 : 0),
    has_surgery: input.has_surgery ? 1 : 0,
    has_chemotherapy: input.has_chemo ? 1 : 0,
    has_radiation: input.has_radiation ? 1 : 0,
    chemo_drug_count: input.has_chemo ? 2 : 0,
    num_prior_admissions: input.prior_admissions,
    days_since_last_admission: input.prior_admissions > 0 ? 30 : 0,
    total_los_days: input.los_days,
    num_comorbidities: input.comorbidities.length,
    charlson_score: input.charlson_score,
    insurance: "Other",
    time_to_first_procedure_days: null,
  };
  const res = await apiFetch<{ status: string; data: {
    readmission_30d_risk: number;
    mortality_risk: number;
    combined_risk: number;
    risk_level: string;
    risk_color: string;
    contributing_factors: string[];
    recommendations: string[];
  }}>("/api/onco/predict-risk", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res || !res.data) return null;
  return {
    readmission_risk: res.data.readmission_30d_risk * 100,
    mortality_risk: res.data.mortality_risk * 100,
    risk_level: res.data.risk_level.charAt(0).toUpperCase() + res.data.risk_level.slice(1),
    contributing_factors: res.data.contributing_factors,
    recommendations: res.data.recommendations,
  };
}

export async function oncoPathway(input: PathwayInput): Promise<PathwayResult | null> {
  const payload = {
    cancer_type: input.cancer_type,
    age: input.age,
    stage_proxy: input.stage,
    charlson_score: input.charlson_score,
  };
  const res = await apiFetch<{ status: string; data: {
    treatment_sequence: Array<{
      step: number;
      treatment: string;
      category: string;
      estimated_days: number;
      priority: string;
    }>;
    estimated_duration_days: number;
    urgency_score: number;
    notes: string[];
  }}>("/api/onco/recommend-pathway", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res || !res.data) return null;
  return {
    pathway: res.data.treatment_sequence.map((s) => ({
      step: s.step,
      treatment: s.treatment,
      category: s.category,
      estimated_days: s.estimated_days,
      priority: s.priority,
      description: `${s.treatment} (~${s.estimated_days} days)`,
    })),
    total_duration_days: res.data.estimated_duration_days,
    urgency_score: res.data.urgency_score,
    clinical_notes: res.data.notes.join(" "),
  };
}

// oncoCohortStats removed — Oncology CohortTab now derives all analytics from /api/sim/oncology-board

// ==================== Patient Journey API ====================

export type JourneyLookupResult =
  | { ok: true; data: PatientSummary }
  | { ok: false; reason: "not_found" | "service_down" | "bad_request"; message: string };

export async function journeyPatientSummary(subjectId: number): Promise<PatientSummary | null> {
  const r = await journeyPatientSummaryDetailed(subjectId);
  return r.ok ? r.data : null;
}

export async function journeyPatientSummaryDetailed(subjectId: number): Promise<JourneyLookupResult> {
  const url = `/api/journey/patient/${subjectId}/summary`;
  try {
    const response = await fetch(url, { headers: { "Content-Type": "application/json" } });
    if (response.status === 404) {
      let message = `Patient ${subjectId} not found.`;
      try {
        const body = await response.json();
        if (body?.error) message = String(body.error);
      } catch {}
      return { ok: false, reason: "not_found", message };
    }
    if (response.status === 422 || response.status === 400) {
      return { ok: false, reason: "bad_request", message: `Invalid patient id (${response.status}).` };
    }
    if (!response.ok) {
      return {
        ok: false,
        reason: "service_down",
        message: `Patient Journey service returned HTTP ${response.status}.`,
      };
    }
    const body = await response.json();
    if (body?.status === "error") {
      return { ok: false, reason: "not_found", message: String(body.error ?? "unknown error") };
    }
    const data: PatientSummary = "status" in body && "data" in body ? body.data : body;
    return { ok: true, data };
  } catch (e) {
    return {
      ok: false,
      reason: "service_down",
      message: `Could not reach Patient Journey API on port 8205. ${e instanceof Error ? e.message : ""}`,
    };
  }
}

export async function journeyTimeline(
  subjectId: number,
  hadmId: number,
  eventTypes?: string,
  limit?: number
): Promise<{ events: TimelineEvent[] } | null> {
  const params = new URLSearchParams();
  if (eventTypes) params.set("event_types", eventTypes);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch<{ status: string; data: { events: TimelineEvent[] } } | { events: TimelineEvent[] }>(
    `/api/journey/patient/${subjectId}/admission/${hadmId}/timeline${qs}`
  );
  if (!res) return null;
  if ("status" in res && "data" in res) return res.data;
  return res as { events: TimelineEvent[] };
}

export async function journeyVitals(
  subjectId: number,
  hadmId: number,
  resample?: string
): Promise<{ vitals: VitalSeries } | null> {
  const qs = resample ? `?resample=${resample}` : "";
  const res = await apiFetch<{ status: string; data: { vitals: VitalSeries } } | { vitals: VitalSeries }>(
    `/api/journey/patient/${subjectId}/admission/${hadmId}/vitals${qs}`
  );
  if (!res) return null;
  if ("status" in res && "data" in res) return res.data;
  return res as { vitals: VitalSeries };
}

export async function journeyLabs(
  subjectId: number,
  hadmId: number,
  panels?: string
): Promise<{ panels: Record<string, LabPanel> } | null> {
  const qs = panels ? `?panels=${panels}` : "";
  const res = await apiFetch<{ status: string; data: { panels: Record<string, LabPanel> } } | { panels: Record<string, LabPanel> }>(
    `/api/journey/patient/${subjectId}/admission/${hadmId}/labs${qs}`
  );
  if (!res) return null;
  if ("status" in res && "data" in res) return res.data;
  return res as { panels: Record<string, LabPanel> };
}

export async function journeyMedications(
  subjectId: number,
  hadmId: number
): Promise<{ medications: MedicationRecord[] } | null> {
  const res = await apiFetch<{ status: string; data: { medications: MedicationRecord[] } } | { medications: MedicationRecord[] }>(
    `/api/journey/patient/${subjectId}/admission/${hadmId}/medications`
  );
  if (!res) return null;
  if ("status" in res && "data" in res) return res.data;
  return res as { medications: MedicationRecord[] };
}

export async function journeyPath(
  subjectId: number,
  hadmId: number
): Promise<JourneyPath | null> {
  const res = await apiFetch<{ status: string; data: JourneyPath } | JourneyPath>(
    `/api/journey/patient/${subjectId}/admission/${hadmId}/journey`
  );
  if (!res) return null;
  if ("status" in res && "data" in res) return res.data;
  return res as JourneyPath;
}

export async function journeyMetrics(
  subjectId: number,
  hadmId: number
): Promise<JourneyMetrics | null> {
  const res = await apiFetch<{ status: string; data: JourneyMetrics } | JourneyMetrics>(
    `/api/journey/patient/${subjectId}/admission/${hadmId}/metrics`
  );
  if (!res) return null;
  if ("status" in res && "data" in res) return res.data;
  return res as JourneyMetrics;
}

// ==================== Shared envelope unwrap ====================
// Most uplift endpoints return {status, data, error}. This helper unwraps.
async function apiEnvelope<T>(url: string, init?: RequestInit): Promise<T | null> {
  const res = await apiFetch<{ status: string; data: T; error?: string | null }>(url, init);
  if (!res) return null;
  if (res.status === "error") return null;
  return (res as any).data ?? null;
}

// ==================== Patient Journey — uplift additions ====================
export interface HighRiskFlag {
  hadm_id: string;
  subject_id?: number | string;
  flag: string;
  readmission_risk?: number;
  flagged_at: string;
}
export async function journeyHighRisk(): Promise<HighRiskFlag[] | null> {
  return apiEnvelope<HighRiskFlag[]>(`/api/journey/journey/high-risk`);
}

// ==================== Waiting List — uplift additions ====================
export interface AdmissionNotification {
  hadm_id: string;
  subject_id?: number | string;
  acuity: number;
  estimated_wait_min: number;
  pathway: string;
  received_at: string;
}
export async function waitingListNotifications(): Promise<AdmissionNotification[] | null> {
  // No dedicated endpoint — returned in-line via waiting-list admission_notifications.
  // Using existing /waiting-list endpoint as proxy for now.
  const res = await apiFetch<{ status: string; data: any[] }>(`/api/waitlist/waiting-list`);
  return res?.data ?? null;
}

// ==================== Bed Management — uplift additions ====================
export interface PriorityEscalation {
  hadm_id: string;
  bed_id: string;
  reason: string;
  bump: number;
  new_score: number;
}
export async function bedMgmtEscalatePriority(body: {
  hadm_id: string;
  reason?: string;
  bump?: number;
}): Promise<any | null> {
  return apiEnvelope(`/api/beds/escalate-bed-priority`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export async function bedMgmtEscalations(limit = 100): Promise<PriorityEscalation[] | null> {
  return apiEnvelope<PriorityEscalation[]>(`/api/beds/escalations?limit=${limit}`);
}

// Waiting List admission notifications (Bug #5 surface)
export async function waitingListAdmissionNotifications(limit = 100): Promise<AdmissionNotification[] | null> {
  return apiEnvelope<AdmissionNotification[]>(`/api/waitlist/admission-notifications?limit=${limit}`);
}

// ==================== Waiting List — per-department summary ====================
export interface WaitlistTopPatient {
  patient_id: number;
  procedure: string;
  priority_level: "urgent" | "soon" | "routine" | "planned" | string;
  composite_priority: number;
  wait_days: number;
  target_wait_days: number;
  deterioration_risk_90d: number;
  status: string;
  breach: boolean;
  source?: "live" | "demo_seed" | string;
}
export interface WaitlistSpecialtyRow {
  specialty: string;
  target_wait_weeks: number;
  inpatient_pct: number;
  total: number;
  by_priority: { urgent: number; soon: number; routine: number; planned: number };
  by_wait_bucket: { le_6w: number; "6_12w": number; "3_6m": number; "6_12m": number; gt_12m: number };
  by_status: Record<string, number>;
  mean_wait_days: number;
  median_wait_days: number;
  p90_wait_days: number;
  breach_count: number;
  breach_rate: number;
  oldest_wait_days: number;
  oldest_patient_id: number | null;
  mean_deterioration_risk_90d: number;
  high_risk_count: number;
  top_patients: WaitlistTopPatient[];
}
export interface WaitlistSummary {
  specialties: WaitlistSpecialtyRow[];
  totals: {
    grand_total: number;
    total_breaches: number;
    specialties_with_breaches: number;
    total_high_risk: number;
    breach_rate: number;
    live_count?: number;
    demo_count?: number;
    has_demo_data?: boolean;
  };
  generated_at: string;
  live_only?: boolean;
}
export async function waitingListByDepartment(liveOnly = true): Promise<WaitlistSummary | null> {
  return apiEnvelope<WaitlistSummary>(
    `/api/waitlist/waiting-list/by-department?live_only=${liveOnly}`,
  );
}
export async function waitingListSeedDemo(count = 180, clearExisting = true): Promise<any | null> {
  return apiEnvelope(`/api/waitlist/waiting-list/seed-demo?count=${count}&clear_existing=${clearExisting}`, {
    method: "POST",
  });
}
export async function waitingListPurgeDemo(): Promise<{ removed: number; remaining: number } | null> {
  return apiEnvelope(`/api/waitlist/waiting-list/purge-demo`, { method: "POST" });
}
export async function waitingListBySpecialty(specialty: string, liveOnly = true): Promise<any[] | null> {
  return apiEnvelope<any[]>(
    `/api/waitlist/waiting-list?specialty=${encodeURIComponent(specialty)}&sort_by=priority&live_only=${liveOnly}`,
  );
}

// ==================== Hospital Ops — pending actions (AI Act Art. 14) ==========
export interface PendingAction {
  action_id: string;
  action_type: string;
  reason: string;
  trigger?: Record<string, any>;
  proposed?: Record<string, any>;
  created_at: number;
}
export async function opsPendingActions(): Promise<PendingAction[] | null> {
  return apiEnvelope<PendingAction[]>(`/api/ops/ops/pending-actions`);
}
export async function opsConfirmAction(id: string, clinician?: string): Promise<any | null> {
  const q = clinician ? `?clinician=${encodeURIComponent(clinician)}` : "";
  return apiEnvelope(`/api/ops/ops/confirm-action/${id}${q}`, { method: "POST" });
}
export async function opsRejectAction(id: string): Promise<any | null> {
  return apiEnvelope(`/api/ops/ops/reject-action/${id}`, { method: "DELETE" });
}

// ==================== Hospital Ops — governance config (Art. 14) ==============
export interface GovernanceConfig {
  human_oversight_enabled: boolean;
  auto_approve_delay_seconds: number;
  require_oversight_for: string[];
  last_updated_by: string;
  last_updated_at: string | null;
  deployment_mode: "simulation" | "production";
  effective_human_oversight: boolean;
  production_mode_locked: boolean;
}
export async function opsGovernanceConfig(): Promise<GovernanceConfig | null> {
  return apiEnvelope<GovernanceConfig>(`/api/ops/ops/governance/config`);
}
export async function opsUpdateGovernanceConfig(
  patch: Partial<GovernanceConfig> & { updated_by?: string },
): Promise<GovernanceConfig | null> {
  return apiEnvelope<GovernanceConfig>(`/api/ops/ops/governance/config`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

// ==================== Clinical Chat — uplift additions ====================
export async function chatHistory(sessionId: string): Promise<any | null> {
  return apiFetch<any>(`/api/chat/chat/${sessionId}/history`);
}
export async function chatContextInject(body: {
  department: string;
  action_taken: string;
  reason: string;
  timestamp?: number;
}): Promise<any | null> {
  return apiFetch<any>(`/api/chat/context-inject`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export async function chatContextFor(department: string): Promise<any | null> {
  return apiFetch<any>(`/api/chat/context/${department}`);
}
export interface SafeguardingAlert {
  hadm_id?: string;
  subject_id?: string | number;
  reasons?: string[];
  severity?: string;
  raised_at?: string;
}
export async function chatSafeguardingAlerts(): Promise<SafeguardingAlert[] | null> {
  const res = await apiFetch<{ alerts: SafeguardingAlert[] }>(`/api/chat/safeguarding/alerts`);
  return res?.alerts ?? null;
}

// ==================== Sepsis ICU — uplift additions ====================
export interface SepsisScreen {
  hadm_id: string;
  risk_score: number;
  alert_level: string;
  sofa_total: number;
  recommended_action: string;
}
export async function sepsisRecentScreens(limit = 50): Promise<SepsisScreen[] | null> {
  return apiEnvelope<SepsisScreen[]>(`/api/sepsis/sepsis/recent-screens?limit=${limit}`);
}

// ==================== SimEngine / Digital Twin health ====================
export interface ModuleHealth {
  last_success: string | null;
  last_failure: string | null;
  failure_count: number;
  consecutive_failures: number;
  is_healthy: boolean;
  last_error?: string;
}
export interface CircuitBreakerSnapshot {
  name: string;
  state: string;
  consecutive_failures: number;
  trip_count: number;
  last_transition: number;
  opened_at: number;
}
export interface DTHealthSnapshot {
  orchestrator: {
    active_patients: number;
    disabled_modules: string[];
    sim_time: string;
    sim_running: boolean;
  };
  modules: Record<string, ModuleHealth>;
  circuit_breakers: CircuitBreakerSnapshot[];
}
export async function simDigitalTwinHealth(): Promise<DTHealthSnapshot | null> {
  return apiEnvelope<DTHealthSnapshot>(`/api/sim/sim/digital-twin/health`);
}
export async function simClockState(): Promise<any | null> {
  return apiEnvelope(`/api/sim/sim/clock`);
}
export async function simCircuitBreakerStatus(): Promise<CircuitBreakerSnapshot[] | null> {
  return apiEnvelope<CircuitBreakerSnapshot[]>(`/api/sim/sim/circuit-breaker-status`);
}
export interface RegisteredModel {
  service_name: string;
  model_path?: string;
  version: string;
  features_hash?: string;
  metrics?: Record<string, any>;
  loaded_at: string;
  registrations?: number;
}
export async function simModelsRegistry(): Promise<RegisteredModel[] | null> {
  return apiEnvelope<RegisteredModel[]>(`/api/sim/models/registry`);
}
export async function simResearchGovernanceLog(limit = 100): Promise<any[] | null> {
  return apiEnvelope<any[]>(`/api/sim/research-governance/access-log?limit=${limit}`);
}

// ==================== ERP — uplift additions ====================
export interface EWTDRow {
  department: string;
  nchd_id?: string;
  nchd_name?: string;
  hours_last_7d: number;
  breach: boolean;
  limit: number;
}
export async function erpEWTDCompliance(): Promise<{ generated_at: string; report: EWTDRow[] } | null> {
  return apiEnvelope(`/api/erp/erp/ewtd-compliance`);
}
export async function erpRegionCensus(): Promise<Record<string, { capacity: number; occupied: number }> | null> {
  return apiEnvelope(`/api/erp/erp/region-census`);
}
export async function erpActivityLog(limit = 100): Promise<any[] | null> {
  return apiEnvelope<any[]>(`/api/erp/erp/activity-log?limit=${limit}`);
}
export async function erpPatchDepartment(name: string, patch: Record<string, any>): Promise<any | null> {
  return apiEnvelope(`/api/erp/erp/departments/${name}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// ==================== Trolley Watch (8216) ====================
export interface TrolleyCount {
  ed: number;
  corridor: number;
  ward: number;
  total: number;
  observed_at: string;
}
export async function trolleyCount(): Promise<TrolleyCount | null> {
  return apiEnvelope<TrolleyCount>(`/api/trolley/trolley/count`);
}
export async function trolleyHistory(days = 7): Promise<Array<{ date: string; count: number }> | null> {
  return apiEnvelope(`/api/trolley/trolley/history?days=${days}`);
}
export async function trolleyINMOReport(date?: string): Promise<any[] | null> {
  const qs = date ? `?date=${date}` : "";
  return apiEnvelope(`/api/trolley/trolley/inmo-report${qs}`);
}
export async function trolleyCompliance(): Promise<any | null> {
  return apiEnvelope(`/api/trolley/trolley/compliance`);
}

// ==================== GDPR Compliance (8217) ====================
export async function gdprRoPA(): Promise<any[] | null> {
  return apiEnvelope<any[]>(`/api/gdpr/gdpr/ropa`);
}
export async function gdprDPIAList(): Promise<any[] | null> {
  // Convenience: call ropa + extract module names, then fetch each DPIA.
  const modules = ["ed_triage", "sepsis_icu", "oncology_ai", "hospital_ops"];
  const out: any[] = [];
  for (const m of modules) {
    const d = await apiEnvelope(`/api/gdpr/gdpr/dpia/${m}`);
    if (d) out.push(d);
  }
  return out;
}
export async function gdprAuditLog(params?: { module?: string; limit?: number }): Promise<any[] | null> {
  const qp = new URLSearchParams();
  if (params?.module) qp.set("module", params.module);
  if (params?.limit) qp.set("limit", String(params.limit));
  const qs = qp.toString() ? `?${qp.toString()}` : "";
  return apiEnvelope<any[]>(`/api/gdpr/gdpr/audit-log${qs}`);
}
export async function gdprCreateSAR(patientId: string): Promise<any | null> {
  return apiEnvelope(`/api/gdpr/gdpr/sar/${patientId}`, { method: "POST" });
}
export async function gdprPurge(patientId: string): Promise<any | null> {
  return apiEnvelope(`/api/gdpr/gdpr/purge/${patientId}`, { method: "DELETE" });
}
export async function gdprLogBreach(body: Record<string, any>): Promise<any | null> {
  return apiEnvelope(`/api/gdpr/gdpr/breach`, { method: "POST", body: JSON.stringify(body) });
}

// ==================== XAI Audit (8218) ====================
export interface XAIDecision {
  module: string;
  prediction_id: string;
  model_version?: string;
  prediction?: any;
  confidence?: number;
  shap_method?: string;
  shap_values?: Array<{ feature: string; value: number }>;
  timestamp: string;
}
export async function xaiDecisionLog(params?: { module?: string; limit?: number }): Promise<XAIDecision[] | null> {
  const qp = new URLSearchParams();
  if (params?.module) qp.set("module", params.module);
  if (params?.limit) qp.set("limit", String(params.limit));
  const qs = qp.toString() ? `?${qp.toString()}` : "";
  return apiEnvelope<XAIDecision[]>(`/api/xai/xai/decision-log${qs}`);
}
export async function xaiModelCard(module: string): Promise<any | null> {
  return apiEnvelope(`/api/xai/xai/model-card/${module}`);
}
export async function xaiOverrideStats(): Promise<any[] | null> {
  return apiEnvelope<any[]>(`/api/xai/xai/override-stats`);
}
export async function xaiLogOverride(body: Record<string, any>): Promise<any | null> {
  return apiEnvelope(`/api/xai/xai/human-override`, { method: "POST", body: JSON.stringify(body) });
}

// ==================== FHIR Gateway (8219) ====================
export async function fhirCapability(): Promise<any | null> {
  return apiFetch<any>(`/api/fhir/fhir/CapabilityStatement`);
}
export async function fhirPatient(patientId: string | number): Promise<any | null> {
  return apiFetch<any>(`/api/fhir/fhir/Patient/${patientId}`);
}
export async function fhirEncounter(hadmId: string, subjectId: string | number): Promise<any | null> {
  return apiFetch<any>(`/api/fhir/fhir/Encounter/${hadmId}?subject_id=${subjectId}`);
}
export async function fhirObservations(hadmId: string, subjectId: string | number): Promise<any | null> {
  return apiFetch<any>(`/api/fhir/fhir/Observation/${hadmId}?subject_id=${subjectId}`);
}

// ==================== Deterioration Monitor (8220) ====================
export interface DeteriorationScore {
  total: number;
  components: Record<string, number>;
  any_param_eq_3?: boolean;       // NEWS2 / PEWS
  any_pink?: boolean;              // IMEWS
  yellow_triggers?: number;        // IMEWS
  pink_triggers?: number;          // IMEWS
  age_band?: string;               // PEWS
  gestational_context?: string;    // IMEWS
  risk_band: string;
  recommended_response: string;
}
export interface DeteriorationTrend {
  current_score: number;
  prior_score: number | null;
  delta: number;
  window_minutes: number;
  num_points_in_window: number;
  slope_per_hour: number;
  trajectory: "rising" | "falling" | "stable" | "insufficient_data";
  is_clinically_rising: boolean;
}
export interface DeteriorationAlert {
  hadm_id: string;
  subject_id?: string | number;
  department?: string;
  scoring_system: "news2" | "pews" | "imews";
  score: DeteriorationScore;
  trend?: DeteriorationTrend;
  observed_at: string;
  // Back-compat alias — some older widgets read `.news2` directly
  news2?: DeteriorationScore;
  // PEWS / IMEWS-specific envelope fields
  age_months?: number;
  gestation_weeks?: number | null;
  post_partum_days?: number | null;
}
export interface DeteriorationStats {
  active_patients: number;
  high_band: number;
  medium_band: number;
  mean_score: number;
  by_department: Record<string, number>;
  by_scoring_system: Record<string, number>;
  unacknowledged_escalations: number;
  mean_time_to_ack_seconds: number;
}
export interface DeteriorationEscalation {
  escalation_id: string;
  hadm_id: string;
  scoring_system: string;
  score: DeteriorationScore;
  actions: string[];
  escalated_at: string;
  acknowledged: boolean;
  acknowledged_at?: string | null;
  acknowledged_by?: { name?: string; role?: string } | null;
  sbar?: Record<string, string> | null;
  outcome?: string | null;
  time_to_ack_seconds?: number | null;
}
export async function deteriorationScreen(body: Record<string, any>): Promise<DeteriorationAlert | null> {
  return apiEnvelope(`/api/deterioration/deterioration/screen`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export async function deteriorationPews(body: Record<string, any>): Promise<DeteriorationAlert | null> {
  return apiEnvelope(`/api/deterioration/deterioration/pews`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export async function deteriorationImews(body: Record<string, any>): Promise<DeteriorationAlert | null> {
  return apiEnvelope(`/api/deterioration/deterioration/imews`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export async function deteriorationActiveAlerts(): Promise<DeteriorationAlert[] | null> {
  return apiEnvelope<DeteriorationAlert[]>(`/api/deterioration/deterioration/active-alerts`);
}
export async function deteriorationStats(): Promise<DeteriorationStats | null> {
  return apiEnvelope(`/api/deterioration/deterioration/stats`);
}
export async function deteriorationAudit(limit = 100): Promise<DeteriorationEscalation[] | null> {
  return apiEnvelope<DeteriorationEscalation[]>(`/api/deterioration/deterioration/audit?limit=${limit}`);
}
export async function deteriorationEscalations(
  unacknowledged = false,
  limit = 200,
): Promise<DeteriorationEscalation[] | null> {
  const q = `?limit=${limit}${unacknowledged ? "&unacknowledged=true" : ""}`;
  return apiEnvelope<DeteriorationEscalation[]>(`/api/deterioration/deterioration/escalations${q}`);
}
export async function deteriorationAcknowledge(payload: {
  escalation_id: string;
  clinician?: { name?: string; role?: string };
  sbar?: Record<string, string>;
  outcome?: string;
}): Promise<DeteriorationEscalation | null> {
  return apiEnvelope(`/api/deterioration/deterioration/acknowledge`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
export async function deteriorationTrend(hadmId: string, windowMinutes = 240): Promise<DeteriorationTrend | null> {
  return apiEnvelope(`/api/deterioration/deterioration/trend/${encodeURIComponent(hadmId)}?window_minutes=${windowMinutes}`);
}

// Sim-only auto-acknowledgement governance. `effective` is the actual
// runtime flag — always false in production regardless of auto_ack_in_sim.
export interface DeteriorationGovernanceConfig {
  auto_ack_in_sim: boolean;
  auto_ack_delay_seconds: number;
  last_updated_at: string | null;
  last_updated_by: string;
  deployment_mode: string;
  effective: boolean;
}
export async function deteriorationGovernanceConfig(): Promise<DeteriorationGovernanceConfig | null> {
  return apiEnvelope(`/api/deterioration/deterioration/governance/config`);
}
export async function deteriorationGovernanceUpdate(
  body: { auto_ack_in_sim?: boolean; auto_ack_delay_seconds?: number; updated_by?: string },
): Promise<DeteriorationGovernanceConfig | null> {
  return apiEnvelope(`/api/deterioration/deterioration/governance/config`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ==================== Discharge Lounge (8221) ====================
export interface DischargeLoungeStatus {
  capacity: number;
  occupied: number;
  available: number;
  patients: Array<{
    hadm_id: string;
    subject_id?: string | number;
    source_department?: string;
    arrived_at: string;
    expected_departure_h?: number;
  }>;
  observed_at: string;
}
export async function loungeStatus(): Promise<DischargeLoungeStatus | null> {
  return apiEnvelope<DischargeLoungeStatus>(`/api/discharge-lounge/discharge-lounge/status`);
}
export async function loungeComplete(hadmId: string): Promise<any | null> {
  return apiEnvelope(`/api/discharge-lounge/discharge-lounge/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hadm_id: hadmId }),
  });
}

export async function loungeTransfer(body: Record<string, any>): Promise<any | null> {
  return apiEnvelope(`/api/discharge-lounge/discharge-lounge/transfer`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export async function loungeForecast(horizonH = 4): Promise<{ hours: number[]; capacity: number } | null> {
  return apiEnvelope(`/api/discharge-lounge/discharge-lounge/forecast?horizon_h=${horizonH}`);
}
export async function loungeMetrics(): Promise<any | null> {
  return apiEnvelope(`/api/discharge-lounge/discharge-lounge/metrics`);
}
export async function loungeCommunityQueue(): Promise<any[] | null> {
  return apiEnvelope<any[]>(`/api/discharge-lounge/community-referral/queue`);
}
