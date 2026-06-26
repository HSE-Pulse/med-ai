/**
 * Patient Voyage helpers — pure functions and type definitions for
 * turning a /journey snapshot + digital-twin pipeline_results into an
 * ordered list of cinematic stages, plus the reducer that mutates the
 * stages from WebSocket deltas.
 *
 *
 * design intent.
 */

import { getDeptColor, RISK_COLORS, ACUITY_COLORS, riskColor } from "./colors";

// --------------------------------------------------------------------------
// Backend payload types — only the fields we actually consume
// --------------------------------------------------------------------------

export interface VitalPoint {
  time: string;
  value: number;
  flag?: string;
}

export type VitalKey = "hr" | "rr" | "spo2" | "sbp" | "dbp" | "temp";

export interface JourneyAdmission {
  hadm_id: string;
  subject_id: number;
  sim_admittime: string;
  admission_type?: string | null;
  admission_location?: string | null;
  insurance?: string | null;
  race?: string | null;
  status?: string;
  sim_dischtime?: string | null;
  discharge_location?: string | null;
  hospital_expire_flag?: number | null;
  discharge_reason?: string | null;
}

export interface CarePathSegment {
  careunit: string;
  eventtype: string;
  intime: string;
  outtime: string | null;
  ongoing: boolean;
}

export interface JourneyTimelineEntry {
  time: string | null;
  event: string;
  detail: string;
}

export interface JourneyPayload {
  hadm_id: string;
  subject_id: number;
  sim_time: string;
  admission: JourneyAdmission;
  vitals: Partial<Record<VitalKey, number>>;
  vitals_series: Partial<Record<VitalKey, VitalPoint[]>>;
  labs: Record<string, number>;
  labs_series: Record<string, VitalPoint[]>;
  medications: Array<{
    drug: string;
    dose_val_rx?: string | number;
    dose_unit_rx?: string;
    route?: string;
    action?: string;
    sim_time?: string;
  }>;
  diagnoses: Array<{ icd_code: string; long_title: string; seq_num: number }>;
  procedures: Array<{ icd_code: string; long_title: string; seq_num: number }>;
  transfers: Array<{ careunit: string; eventtype: string; intime: string }>;
  care_path: CarePathSegment[];
  timeline: JourneyTimelineEntry[];
}

export interface EdTriageResult {
  acuity_level: number;
  acuity_label: string;
  confidence?: number;
  disposition?: string;
  ed_los_estimate_hours?: number;
  risk_factors?: string[];
}

export interface EdFlowResult {
  mts_category?: number;
  mts_name?: string;
  mts_color?: string;
  pet_breach_risk?: number;
  lwbs_risk?: number;
  admission_probability?: number;
  predicted_ed_los_minutes?: number;
  pet_remaining_minutes?: number;
}

export interface BedManagementResult {
  current_los_hours?: number;
  predicted_discharge_time?: string;
  discharge_probability_24h?: number;
  discharge_probability_48h?: number;
  discharge_readiness_score?: number;
  key_factors?: string[];
  barriers_to_discharge?: string[];
}

export interface BedAllocationResult {
  recommended_bed?: string;
  recommended_department?: string;
  priority_score?: number;
  wait_time_estimate_minutes?: number;
  allocation_reason?: string;
}

export interface OncologyResult {
  readmission_30d_risk?: number;
  cancer_type?: string;
}

export interface SepsisResult {
  alert_level?: string;
  sofa_total?: number;
}

export interface PipelineResult {
  hadm_id: string;
  timestamp: string;
  ed_triage?: EdTriageResult;
  ed_flow?: EdFlowResult;
  bed_management?: BedManagementResult;
  bed_allocation?: BedAllocationResult;
  oncology_ai?: OncologyResult;
  sepsis_icu?: SepsisResult;
  clinical_scribe?: { source?: string; queued?: number };
  waiting_list_notified?: boolean;
}

export interface DigitalTwinSnapshot {
  context?: Record<string, unknown>;
  pipeline_results: PipelineResult[];
}

export interface DeteriorationAlert {
  hadm_id: string;
  department?: string;
  scoring_system?: string;
  score?: {
    total?: number;
    risk_band?: string;
    recommended_response?: string;
  };
  observed_at: string;
  trend?: {
    current_score?: number;
    prior_score?: number | null;
    trajectory?: string;
    slope_per_hour?: number;
  };
}

export interface ScribeNote {
  note_id?: string;
  note_type?: string;
  content?: string;
  generated_at?: string;
  status?: string;
}

// --------------------------------------------------------------------------
// Voyage stage discriminated union
// --------------------------------------------------------------------------

export type VoyageStageType = "ADMISSION" | "BED_ALLOCATION" | "WARD_PHASE" | "TRANSFER" | "DISCHARGE";

export interface BaseStage {
  id: string;
  type: VoyageStageType;
  sim_time: string;
  title: string;
  stripeColor: string;
}

export interface AdmissionStage extends BaseStage {
  type: "ADMISSION";
  admission: JourneyAdmission;
  aiTriage?: EdTriageResult;
  aiFlow?: EdFlowResult;
  aiOncology?: OncologyResult;
}

export interface BedAllocationStage extends BaseStage {
  type: "BED_ALLOCATION";
  bedManagement?: BedManagementResult;
  bedAllocation?: BedAllocationResult;
}

export interface WardPhaseStage extends BaseStage {
  type: "WARD_PHASE";
  careunit: string;
  intime: string;
  outtime: string | null;
  ongoing: boolean;
  vitalsCount: number;
  medCount: number;
  abnormalLabs: Array<{ name: string; value: number; flag?: string }>;
  sepsis?: SepsisResult;
  deterAlert?: DeteriorationAlert;
}

export interface TransferStage extends BaseStage {
  type: "TRANSFER";
  from: string;
  to: string;
  fromColor: string;
  toColor: string;
}

export interface DischargeStage extends BaseStage {
  type: "DISCHARGE";
  discharge_location?: string | null;
  hospital_expire_flag?: number | null;
  discharge_reason?: string | null;
  dischargeNote?: ScribeNote;
}

export type VoyageStage =
  | AdmissionStage
  | BedAllocationStage
  | WardPhaseStage
  | TransferStage
  | DischargeStage;

// --------------------------------------------------------------------------
// Stage builder — snapshot → ordered stages
// --------------------------------------------------------------------------

export function buildStages(
  journey: JourneyPayload | null,
  twin: DigitalTwinSnapshot | null,
  deter: DeteriorationAlert[],
  notes: ScribeNote[],
): VoyageStage[] {
  if (!journey) return [];
  const stages: VoyageStage[] = [];
  const pipeline = twin?.pipeline_results?.[0]; // latest pipeline run

  // 1. ADMISSION
  const triage = pipeline?.ed_triage;
  const flow = pipeline?.ed_flow;
  const onco = pipeline?.oncology_ai;
  const acuity = triage?.acuity_level ?? 3;
  stages.push({
    id: `admission-${journey.admission.hadm_id}`,
    type: "ADMISSION",
    sim_time: journey.admission.sim_admittime,
    title: "Admission",
    stripeColor: ACUITY_COLORS[Math.max(0, Math.min(4, acuity - 1))],
    admission: journey.admission,
    aiTriage: triage,
    aiFlow: flow,
    aiOncology: onco,
  });

  // 2. BED ALLOCATION (if pipeline has it)
  if (pipeline?.bed_management || pipeline?.bed_allocation) {
    const deptHint = pipeline?.bed_allocation?.recommended_department ?? "";
    stages.push({
      id: `bed-${journey.admission.hadm_id}`,
      type: "BED_ALLOCATION",
      sim_time: pipeline.timestamp,
      title: "Bed Allocation",
      stripeColor: getDeptColor(deptHint, "#6366F1"),
      bedManagement: pipeline.bed_management,
      bedAllocation: pipeline.bed_allocation,
    });
  }

  // 3. WARD PHASES — one per care_path segment, with TRANSFER markers between
  const sortedDeter = [...deter].sort(
    (a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at),
  );
  let prevSeg: CarePathSegment | null = null;
  journey.care_path.forEach((seg, idx) => {
    if (prevSeg && prevSeg.careunit !== seg.careunit) {
      stages.push({
        id: `transfer-${idx}-${journey.admission.hadm_id}`,
        type: "TRANSFER",
        sim_time: seg.intime,
        title: "Transfer",
        stripeColor: getDeptColor(seg.careunit, "#6B7280"),
        from: prevSeg.careunit,
        to: seg.careunit,
        fromColor: getDeptColor(prevSeg.careunit, "#6B7280"),
        toColor: getDeptColor(seg.careunit, "#6B7280"),
      });
    }
    const intimeMs = Date.parse(seg.intime);
    const outtimeMs = seg.outtime ? Date.parse(seg.outtime) : Number.POSITIVE_INFINITY;
    const vitalsCount = countVitalsInWindow(journey.vitals_series, intimeMs, outtimeMs);
    const medCount = journey.medications.filter((m) => {
      if (!m.sim_time) return false;
      const t = Date.parse(m.sim_time);
      return t >= intimeMs && t <= outtimeMs;
    }).length;
    const abnormalLabs = collectAbnormalLabs(journey.labs_series, intimeMs, outtimeMs);
    const deterAlert = sortedDeter
      .reverse()
      .find((a) => {
        const t = Date.parse(a.observed_at);
        return t >= intimeMs && t <= outtimeMs;
      });
    sortedDeter.reverse(); // restore order for next iteration
    stages.push({
      id: `ward-${idx}-${journey.admission.hadm_id}`,
      type: "WARD_PHASE",
      sim_time: seg.intime,
      title: seg.careunit,
      stripeColor: getDeptColor(seg.careunit, "#6B7280"),
      careunit: seg.careunit,
      intime: seg.intime,
      outtime: seg.outtime,
      ongoing: seg.ongoing,
      vitalsCount,
      medCount,
      abnormalLabs,
      sepsis: pipeline?.sepsis_icu,
      deterAlert,
    });
    prevSeg = seg;
  });

  // 4. DISCHARGE (if status === "discharged" or admission has dischtime)
  const discharged =
    journey.admission.status === "discharged" || !!journey.admission.sim_dischtime;
  if (discharged) {
    const expired = (journey.admission.hospital_expire_flag ?? 0) > 0;
    const dischargeNote = notes.find(
      (n) => (n.note_type || "").toLowerCase().includes("discharge"),
    );
    stages.push({
      id: `discharge-${journey.admission.hadm_id}`,
      type: "DISCHARGE",
      sim_time: journey.admission.sim_dischtime ?? journey.sim_time,
      title: expired ? "Deceased" : "Discharge",
      stripeColor: expired ? RISK_COLORS.critical : RISK_COLORS.low,
      discharge_location: journey.admission.discharge_location,
      hospital_expire_flag: journey.admission.hospital_expire_flag,
      discharge_reason: journey.admission.discharge_reason,
      dischargeNote,
    });
  }

  return stages;
}

function countVitalsInWindow(
  series: Partial<Record<VitalKey, VitalPoint[]>>,
  startMs: number,
  endMs: number,
): number {
  let n = 0;
  for (const arr of Object.values(series) as VitalPoint[][]) {
    if (!Array.isArray(arr)) continue;
    for (const p of arr) {
      const t = Date.parse(p.time);
      if (t >= startMs && t <= endMs) n++;
    }
  }
  return n;
}

function collectAbnormalLabs(
  series: Record<string, VitalPoint[]>,
  startMs: number,
  endMs: number,
): Array<{ name: string; value: number; flag?: string }> {
  const out: Array<{ name: string; value: number; flag?: string }> = [];
  for (const [name, arr] of Object.entries(series ?? {})) {
    if (!Array.isArray(arr)) continue;
    for (const p of arr) {
      const t = Date.parse(p.time);
      if (t >= startMs && t <= endMs && p.flag && p.flag !== "normal") {
        out.push({ name, value: p.value, flag: p.flag });
      }
    }
  }
  return out.slice(0, 8); // cap visual noise
}

// --------------------------------------------------------------------------
// Severity → CSS class + color helpers
// --------------------------------------------------------------------------

export function severityToClass(severity?: string): string {
  switch ((severity ?? "").toLowerCase()) {
    case "critical":
      return "pulse-critical";
    case "high":
    case "medium":
      return "animate-pulse-border";
    default:
      return "";
  }
}

export function pctToColor(p: number | undefined): string {
  if (p == null) return RISK_COLORS.info;
  // Argument may be 0-1 or 0-100; normalise to 0-100.
  return riskColor(p > 1 ? p : p * 100);
}

export function formatPct(p: number | undefined): string {
  if (p == null) return "—";
  const v = p > 1 ? p : p * 100;
  return `${Math.round(v)}%`;
}

export function formatSimTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function durationLabel(start?: string | null, end?: string | null): string {
  if (!start) return "";
  const s = Date.parse(start);
  const e = end ? Date.parse(end) : Date.now();
  if (!isFinite(s) || !isFinite(e)) return "";
  const mins = Math.max(0, Math.round((e - s) / 60000));
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${h}h ${m}m`;
}
