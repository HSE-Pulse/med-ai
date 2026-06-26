/**
 * Centralized color constants and label mappings for the dashboard.
 *
 * Consolidates color/label definitions that were duplicated across
 * EdTriage, SepsisIcu, PatientJourney, HospitalOps, and EDFlow pages.
 */

// ---------------------------------------------------------------------------
// Risk / alert levels
// ---------------------------------------------------------------------------

export const RISK_COLORS = {
  critical: "#DC2626", // red-600
  high: "#F97316",     // orange-500
  moderate: "#EAB308", // yellow-500
  low: "#22C55E",      // green-500
  info: "#3B82F6",     // blue-500
} as const;

export function riskColor(pct: number): string {
  if (pct >= 75) return RISK_COLORS.critical;
  if (pct >= 50) return RISK_COLORS.high;
  if (pct >= 30) return RISK_COLORS.moderate;
  return RISK_COLORS.low;
}

// ---------------------------------------------------------------------------
// ED Triage acuity (ESI 1-5)
// ---------------------------------------------------------------------------

export const ACUITY_COLORS = [
  "#DC2626", // ESI-1 Resuscitation
  "#F97316", // ESI-2 Emergent
  "#EAB308", // ESI-3 Urgent
  "#22C55E", // ESI-4 Less Urgent
  "#3B82F6", // ESI-5 Non-Urgent
];

export const ACUITY_LABELS = [
  "Resuscitation",
  "Emergent",
  "Urgent",
  "Less Urgent",
  "Non-Urgent",
];

// ---------------------------------------------------------------------------
// SOFA score
// ---------------------------------------------------------------------------

export const SOFA_COMPONENT_COLORS = [
  "#3B82F6", // Respiration
  "#8B5CF6", // Coagulation
  "#F97316", // Liver
  "#DC2626", // Cardiovascular
  "#14B8A6", // Renal
];

export const SOFA_COMPONENT_LABELS = [
  "Respiration",
  "Coagulation",
  "Liver",
  "Cardiovascular",
  "Renal",
];

export function sofaTotalColor(total: number): string {
  if (total >= 10) return "#DC2626";
  if (total >= 7) return "#F97316";
  if (total >= 4) return "#EAB308";
  return "#22C55E";
}

export function sofaTotalLabel(total: number): string {
  if (total >= 10) return "Very High \u2014 Likely Sepsis";
  if (total >= 7) return "High Risk";
  if (total >= 4) return "Moderate Risk";
  return "Low Risk";
}

// ---------------------------------------------------------------------------
// MTS (Manchester Triage Scale) — used in ED Flow
// ---------------------------------------------------------------------------

export const MTS_COLORS: Record<string, string> = {
  Immediate: "#DC2626",
  "Very Urgent": "#F97316",
  Urgent: "#EAB308",
  Standard: "#22C55E",
  "Non-Urgent": "#3B82F6",
};

// ---------------------------------------------------------------------------
// Department colors
// ---------------------------------------------------------------------------

export const DEPT_COLORS: Record<string, string> = {
  "Emergency Department": "#EF4444",
  "Medical ICU": "#F97316",
  "Surgical ICU": "#F59E0B",
  "Cardiac ICU": "#EC4899",
  "Neuro ICU": "#8B5CF6",
  "Medicine": "#3B82F6",
  "Surgery": "#14B8A6",
  "Oncology": "#10B981",
  "Medical Intensive Care Unit (MICU)": "#F97316",
  "Surgical Intensive Care Unit (SICU)": "#F59E0B",
  "Coronary Care Unit (CCU)": "#EC4899",
  "Hematology/Oncology": "#10B981",
  "General Medicine": "#3B82F6",
};

export const DEPT_SERIES_COLORS = [
  "#DC2626", "#F97316", "#EAB308", "#22C55E", "#3B82F6",
  "#8B5CF6", "#EC4899", "#14B8A6", "#6366F1", "#F97316",
  "#84CC16", "#64748B", "#F43F5E", "#06B6D4", "#A855F7",
];

export function getDeptColor(dept: string, fallback = "#6B7280"): string {
  if (DEPT_COLORS[dept]) return DEPT_COLORS[dept];
  const lower = dept.toLowerCase();
  for (const [key, color] of Object.entries(DEPT_COLORS)) {
    if (lower.includes(key.toLowerCase())) return color;
  }
  return fallback;
}

// ---------------------------------------------------------------------------
// Crowding (NEDOCS)
// ---------------------------------------------------------------------------

export const CROWDING_COLORS: Record<string, string> = {
  normal: "#22C55E",
  busy: "#F59E0B",
  crowded: "#F97316",
  severe: "#DC2626",
};
