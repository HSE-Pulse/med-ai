// ==================== ED Triage Mock Data ====================

export interface MockEdPatient {
  id: string;
  bed: string;
  name: string;
  age: number;
  gender: "M" | "F";
  acuity: 1 | 2 | 3 | 4 | 5;
  chiefComplaint: string;
  waitMinutes: number;
  status: "Waiting" | "In Treatment" | "Pending Discharge" | "Admitted";
  hr: number;
  sbp: number;
  spo2: number;
}

export const mockEdPatients: MockEdPatient[] = [
  { id: "ED-001", bed: "T1", name: "Patient A", age: 72, gender: "M", acuity: 1, chiefComplaint: "Cardiac arrest", waitMinutes: 0, status: "In Treatment", hr: 42, sbp: 78, spo2: 82 },
  { id: "ED-002", bed: "T2", name: "Patient B", age: 55, gender: "F", acuity: 2, chiefComplaint: "Chest pain, diaphoresis", waitMinutes: 3, status: "In Treatment", hr: 118, sbp: 92, spo2: 91 },
  { id: "ED-003", bed: "T3", name: "Patient C", age: 34, gender: "M", acuity: 2, chiefComplaint: "Severe allergic reaction", waitMinutes: 5, status: "In Treatment", hr: 110, sbp: 88, spo2: 94 },
  { id: "ED-004", bed: "R4", name: "Patient D", age: 68, gender: "F", acuity: 3, chiefComplaint: "Abdominal pain, vomiting", waitMinutes: 22, status: "In Treatment", hr: 96, sbp: 132, spo2: 97 },
  { id: "ED-005", bed: "R5", name: "Patient E", age: 45, gender: "M", acuity: 3, chiefComplaint: "Laceration with bone exposure", waitMinutes: 18, status: "Waiting", hr: 88, sbp: 128, spo2: 98 },
  { id: "ED-006", bed: "R6", name: "Patient F", age: 29, gender: "F", acuity: 3, chiefComplaint: "Migraine with visual changes", waitMinutes: 35, status: "In Treatment", hr: 78, sbp: 118, spo2: 99 },
  { id: "ED-007", bed: "R7", name: "Patient G", age: 81, gender: "M", acuity: 3, chiefComplaint: "Fall, hip pain", waitMinutes: 42, status: "Waiting", hr: 72, sbp: 148, spo2: 96 },
  { id: "ED-008", bed: "F8", name: "Patient H", age: 23, gender: "F", acuity: 4, chiefComplaint: "Ankle sprain", waitMinutes: 68, status: "Waiting", hr: 76, sbp: 122, spo2: 99 },
  { id: "ED-009", bed: "F9", name: "Patient I", age: 42, gender: "M", acuity: 4, chiefComplaint: "UTI symptoms", waitMinutes: 55, status: "Waiting", hr: 82, sbp: 126, spo2: 98 },
  { id: "ED-010", bed: "F10", name: "Patient J", age: 37, gender: "F", acuity: 4, chiefComplaint: "Ear pain, 3 days", waitMinutes: 78, status: "Waiting", hr: 74, sbp: 118, spo2: 99 },
  { id: "ED-011", bed: "--", name: "Patient K", age: 19, gender: "M", acuity: 5, chiefComplaint: "Medication refill request", waitMinutes: 95, status: "Waiting", hr: 68, sbp: 116, spo2: 100 },
  { id: "ED-012", bed: "--", name: "Patient L", age: 56, gender: "F", acuity: 5, chiefComplaint: "Rash, 1 week", waitMinutes: 102, status: "Waiting", hr: 72, sbp: 124, spo2: 99 },
  { id: "ED-013", bed: "R8", name: "Patient M", age: 63, gender: "M", acuity: 2, chiefComplaint: "Acute stroke symptoms", waitMinutes: 2, status: "In Treatment", hr: 105, sbp: 178, spo2: 93 },
  { id: "ED-014", bed: "F11", name: "Patient N", age: 31, gender: "F", acuity: 4, chiefComplaint: "Sore throat, fever", waitMinutes: 62, status: "Waiting", hr: 88, sbp: 114, spo2: 98 },
  { id: "ED-015", bed: "R9", name: "Patient O", age: 74, gender: "M", acuity: 3, chiefComplaint: "Dyspnea, productive cough", waitMinutes: 28, status: "In Treatment", hr: 94, sbp: 136, spo2: 92 },
];

// ==================== Sepsis / ICU Mock Data ====================

function generateVitalSeries(baseValue: number, variance: number, trend: number = 0, count: number = 24): { time: string; value: number }[] {
  const now = Date.now();
  return Array.from({ length: count }, (_, i) => ({
    time: new Date(now - (count - i) * 3600000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }),
    value: Math.round((baseValue + trend * (i / count) + (Math.random() - 0.5) * variance) * 10) / 10,
  }));
}

export interface MockIcuPatient {
  id: string;
  age: number;
  gender: "M" | "F";
  riskScore: number;
  sofa: number;
  sofaBreakdown: { respiration: number; coagulation: number; liver: number; cardiovascular: number; renal: number };
  vitals: {
    hr: { time: string; value: number }[];
    rr: { time: string; value: number }[];
    spo2: { time: string; value: number }[];
    sbp: { time: string; value: number }[];
    temp: { time: string; value: number }[];
    lactate: { time: string; value: number }[];
  };
  riskTimeline: { time: string; value: number }[];
  alerts: { time: string; message: string; severity: "critical" | "warning" | "info" }[];
  admissionDays: number;
  diagnosis: string;
}

export const mockIcuPatients: MockIcuPatient[] = [
  {
    id: "ICU-001", age: 68, gender: "M", riskScore: 89, sofa: 14,
    sofaBreakdown: { respiration: 4, coagulation: 2, liver: 3, cardiovascular: 3, renal: 2 },
    vitals: { hr: generateVitalSeries(118, 15, 8), rr: generateVitalSeries(28, 6, 4), spo2: generateVitalSeries(88, 4, -3), sbp: generateVitalSeries(82, 12, -10), temp: generateVitalSeries(39.2, 0.6, 0.4), lactate: generateVitalSeries(4.8, 1.2, 1.5) },
    riskTimeline: generateVitalSeries(72, 10, 17),
    alerts: [{ time: "14:22", message: "Lactate >4.0 mmol/L - Septic shock criteria met", severity: "critical" }, { time: "13:45", message: "MAP <65 despite vasopressors", severity: "critical" }, { time: "12:10", message: "SOFA score increased by 3 points in 12h", severity: "warning" }],
    admissionDays: 3, diagnosis: "Pneumonia-induced sepsis"
  },
  {
    id: "ICU-002", age: 75, gender: "F", riskScore: 78, sofa: 11,
    sofaBreakdown: { respiration: 3, coagulation: 1, liver: 2, cardiovascular: 3, renal: 2 },
    vitals: { hr: generateVitalSeries(108, 12, 5), rr: generateVitalSeries(26, 5, 2), spo2: generateVitalSeries(90, 3, -2), sbp: generateVitalSeries(88, 10, -6), temp: generateVitalSeries(38.8, 0.5, 0.2), lactate: generateVitalSeries(3.8, 0.9, 0.8) },
    riskTimeline: generateVitalSeries(58, 12, 20),
    alerts: [{ time: "13:55", message: "Urine output <0.5 mL/kg/h for 6 hours", severity: "critical" }, { time: "11:30", message: "Positive blood culture - E. coli", severity: "warning" }],
    admissionDays: 5, diagnosis: "Urosepsis"
  },
  {
    id: "ICU-003", age: 52, gender: "M", riskScore: 65, sofa: 9,
    sofaBreakdown: { respiration: 3, coagulation: 2, liver: 1, cardiovascular: 2, renal: 1 },
    vitals: { hr: generateVitalSeries(102, 10, 3), rr: generateVitalSeries(24, 4, 1), spo2: generateVitalSeries(92, 3, -1), sbp: generateVitalSeries(95, 8, -4), temp: generateVitalSeries(38.5, 0.4, 0.1), lactate: generateVitalSeries(2.9, 0.8, 0.5) },
    riskTimeline: generateVitalSeries(45, 10, 20),
    alerts: [{ time: "14:00", message: "Increasing FiO2 requirement", severity: "warning" }, { time: "10:15", message: "Platelet count <100K", severity: "warning" }],
    admissionDays: 2, diagnosis: "Abdominal sepsis post-cholecystectomy"
  },
  {
    id: "ICU-004", age: 43, gender: "F", riskScore: 52, sofa: 7,
    sofaBreakdown: { respiration: 2, coagulation: 1, liver: 1, cardiovascular: 2, renal: 1 },
    vitals: { hr: generateVitalSeries(96, 8, 2), rr: generateVitalSeries(22, 3, 0), spo2: generateVitalSeries(94, 2, 0), sbp: generateVitalSeries(102, 10, -2), temp: generateVitalSeries(38.2, 0.3, -0.1), lactate: generateVitalSeries(2.4, 0.6, 0.2) },
    riskTimeline: generateVitalSeries(40, 8, 12),
    alerts: [{ time: "09:30", message: "Empiric antibiotics started - Meropenem", severity: "info" }],
    admissionDays: 1, diagnosis: "Cellulitis with early sepsis"
  },
  {
    id: "ICU-005", age: 61, gender: "M", riskScore: 41, sofa: 5,
    sofaBreakdown: { respiration: 2, coagulation: 0, liver: 1, cardiovascular: 1, renal: 1 },
    vitals: { hr: generateVitalSeries(88, 8, -2), rr: generateVitalSeries(20, 3, -1), spo2: generateVitalSeries(95, 2, 1), sbp: generateVitalSeries(112, 8, 3), temp: generateVitalSeries(37.8, 0.3, -0.2), lactate: generateVitalSeries(1.8, 0.4, -0.2) },
    riskTimeline: generateVitalSeries(55, 8, -14),
    alerts: [{ time: "08:00", message: "Trending improvement - consider step-down", severity: "info" }],
    admissionDays: 4, diagnosis: "Community-acquired pneumonia"
  },
  {
    id: "ICU-006", age: 79, gender: "F", riskScore: 72, sofa: 10,
    sofaBreakdown: { respiration: 3, coagulation: 2, liver: 1, cardiovascular: 2, renal: 2 },
    vitals: { hr: generateVitalSeries(104, 10, 4), rr: generateVitalSeries(25, 4, 2), spo2: generateVitalSeries(91, 3, -1), sbp: generateVitalSeries(90, 10, -5), temp: generateVitalSeries(38.9, 0.5, 0.3), lactate: generateVitalSeries(3.5, 1.0, 0.6) },
    riskTimeline: generateVitalSeries(50, 10, 22),
    alerts: [{ time: "12:45", message: "New atrial fibrillation detected", severity: "critical" }, { time: "11:00", message: "Bilirubin trending up", severity: "warning" }],
    admissionDays: 6, diagnosis: "Biliary sepsis"
  },
  {
    id: "ICU-007", age: 35, gender: "M", riskScore: 28, sofa: 3,
    sofaBreakdown: { respiration: 1, coagulation: 0, liver: 0, cardiovascular: 1, renal: 1 },
    vitals: { hr: generateVitalSeries(82, 6, -3), rr: generateVitalSeries(18, 2, -1), spo2: generateVitalSeries(97, 1, 1), sbp: generateVitalSeries(118, 6, 4), temp: generateVitalSeries(37.4, 0.2, -0.3), lactate: generateVitalSeries(1.4, 0.3, -0.3) },
    riskTimeline: generateVitalSeries(42, 6, -14),
    alerts: [{ time: "07:00", message: "Cultures negative at 48h", severity: "info" }],
    admissionDays: 2, diagnosis: "SIRS post-trauma"
  },
  {
    id: "ICU-008", age: 58, gender: "F", riskScore: 83, sofa: 12,
    sofaBreakdown: { respiration: 3, coagulation: 3, liver: 2, cardiovascular: 2, renal: 2 },
    vitals: { hr: generateVitalSeries(112, 14, 6), rr: generateVitalSeries(27, 5, 3), spo2: generateVitalSeries(89, 4, -2), sbp: generateVitalSeries(85, 12, -8), temp: generateVitalSeries(39.0, 0.6, 0.3), lactate: generateVitalSeries(4.2, 1.1, 1.2) },
    riskTimeline: generateVitalSeries(62, 12, 21),
    alerts: [{ time: "14:10", message: "DIC criteria met - PT/INR elevated", severity: "critical" }, { time: "13:00", message: "Norepinephrine dose increased", severity: "warning" }, { time: "11:20", message: "AKI Stage 2 - Creatinine 2.8", severity: "warning" }],
    admissionDays: 4, diagnosis: "Necrotizing fasciitis with septic shock"
  },
  {
    id: "ICU-009", age: 47, gender: "M", riskScore: 35, sofa: 4,
    sofaBreakdown: { respiration: 1, coagulation: 1, liver: 0, cardiovascular: 1, renal: 1 },
    vitals: { hr: generateVitalSeries(86, 6, -1), rr: generateVitalSeries(19, 2, 0), spo2: generateVitalSeries(96, 1, 0), sbp: generateVitalSeries(115, 8, 2), temp: generateVitalSeries(37.6, 0.3, -0.1), lactate: generateVitalSeries(1.6, 0.4, -0.1) },
    riskTimeline: generateVitalSeries(48, 8, -13),
    alerts: [{ time: "10:00", message: "Antibiotics de-escalated", severity: "info" }],
    admissionDays: 3, diagnosis: "Bacteremia - Staph aureus"
  },
  {
    id: "ICU-010", age: 66, gender: "F", riskScore: 58, sofa: 8,
    sofaBreakdown: { respiration: 2, coagulation: 1, liver: 2, cardiovascular: 2, renal: 1 },
    vitals: { hr: generateVitalSeries(98, 10, 2), rr: generateVitalSeries(23, 3, 1), spo2: generateVitalSeries(93, 2, 0), sbp: generateVitalSeries(98, 10, -3), temp: generateVitalSeries(38.4, 0.4, 0.1), lactate: generateVitalSeries(2.6, 0.7, 0.3) },
    riskTimeline: generateVitalSeries(44, 10, 14),
    alerts: [{ time: "13:15", message: "CT abdomen: possible abscess", severity: "warning" }, { time: "09:45", message: "WBC count rising - 18.5K", severity: "warning" }],
    admissionDays: 2, diagnosis: "Intra-abdominal abscess"
  },
  {
    id: "ICU-011", age: 82, gender: "M", riskScore: 45, sofa: 6,
    sofaBreakdown: { respiration: 2, coagulation: 1, liver: 1, cardiovascular: 1, renal: 1 },
    vitals: { hr: generateVitalSeries(90, 8, 0), rr: generateVitalSeries(21, 3, 0), spo2: generateVitalSeries(94, 2, 0), sbp: generateVitalSeries(108, 8, 0), temp: generateVitalSeries(38.0, 0.3, 0), lactate: generateVitalSeries(2.0, 0.4, 0) },
    riskTimeline: generateVitalSeries(45, 6, 0),
    alerts: [{ time: "14:30", message: "Stable - continue current management", severity: "info" }],
    admissionDays: 7, diagnosis: "Healthcare-associated pneumonia"
  },
  {
    id: "ICU-012", age: 39, gender: "F", riskScore: 15, sofa: 2,
    sofaBreakdown: { respiration: 1, coagulation: 0, liver: 0, cardiovascular: 1, renal: 0 },
    vitals: { hr: generateVitalSeries(78, 5, -2), rr: generateVitalSeries(16, 2, -1), spo2: generateVitalSeries(98, 1, 1), sbp: generateVitalSeries(122, 6, 5), temp: generateVitalSeries(37.1, 0.2, -0.2), lactate: generateVitalSeries(1.1, 0.2, -0.2) },
    riskTimeline: generateVitalSeries(30, 5, -15),
    alerts: [{ time: "06:00", message: "Ready for ICU discharge - transfer order placed", severity: "info" }],
    admissionDays: 2, diagnosis: "Resolving sepsis - endometritis"
  },
];

// ==================== Hospital Operations Mock Data ====================

export interface MockDepartment {
  name: string;
  color: string;
  patients: number;
  capacity: number;
  waitTime: number;
  doctors: number;
  nurses: number;
  utilization: number;
}

export const mockDepartments: MockDepartment[] = [
  // 14 Irish HSE departments — colors from DEPT_SERIES_COLORS
  { name: "ED",               color: "#DC2626", patients: 22, capacity: 30, waitTime: 45, doctors: 6, nurses: 14, utilization: 73 },
  { name: "MAU",              color: "#F97316", patients: 16, capacity: 24, waitTime: 25, doctors: 3, nurses: 8,  utilization: 67 },
  { name: "AMAU",             color: "#EAB308", patients: 10, capacity: 16, waitTime: 18, doctors: 2, nurses: 6,  utilization: 63 },
  { name: "SAU",              color: "#22C55E", patients: 8,  capacity: 12, waitTime: 20, doctors: 2, nurses: 4,  utilization: 67 },
  { name: "CDU",              color: "#3B82F6", patients: 5,  capacity: 8,  waitTime: 12, doctors: 1, nurses: 3,  utilization: 63 },
  { name: "Medicine",         color: "#8B5CF6", patients: 32, capacity: 40, waitTime: 30, doctors: 8, nurses: 16, utilization: 80 },
  { name: "Surgery",          color: "#EC4899", patients: 26, capacity: 36, waitTime: 28, doctors: 6, nurses: 12, utilization: 72 },
  { name: "Cardiology",       color: "#14B8A6", patients: 15, capacity: 20, waitTime: 22, doctors: 4, nurses: 8,  utilization: 75 },
  { name: "Respiratory",      color: "#6366F1", patients: 13, capacity: 18, waitTime: 24, doctors: 3, nurses: 7,  utilization: 72 },
  { name: "Orthopaedics",     color: "#F97316", patients: 17, capacity: 24, waitTime: 26, doctors: 4, nurses: 8,  utilization: 71 },
  { name: "ICU",              color: "#84CC16", patients: 10, capacity: 12, waitTime: 0,  doctors: 4, nurses: 12, utilization: 83 },
  { name: "HDU",              color: "#64748B", patients: 6,  capacity: 8,  waitTime: 0,  doctors: 2, nurses: 6,  utilization: 75 },
  { name: "Day_Ward",         color: "#F43F5E", patients: 14, capacity: 20, waitTime: 10, doctors: 3, nurses: 6,  utilization: 70 },
  { name: "Discharge_Lounge", color: "#06B6D4", patients: 6,  capacity: 10, waitTime: 8,  doctors: 1, nurses: 3,  utilization: 60 },
];

export function generateSimTimeSeries(points: number = 50) {
  const waitData: { time: number; baseline: number; marl: number }[] = [];
  const throughputData: { time: number; baseline: number; marl: number }[] = [];

  for (let i = 0; i < points; i++) {
    const t = i * 10;
    waitData.push({
      time: t,
      baseline: 35 + Math.sin(i * 0.3) * 10 + Math.random() * 5,
      marl: 22 + Math.sin(i * 0.3) * 6 + Math.random() * 3,
    });
    throughputData.push({
      time: t,
      baseline: 4.2 + Math.sin(i * 0.2) * 0.8 + Math.random() * 0.3,
      marl: 5.8 + Math.sin(i * 0.2) * 0.6 + Math.random() * 0.3,
    });
  }
  return { waitData, throughputData };
}

// ==================== Oncology Mock Data ====================

export interface MockOncologyCohort {
  totalPatients: number;
  totalAdmissions: number;
  readmissionRate: number;
  mortalityRate: number;
  cancerDistribution: { type: string; count: number }[];
  treatmentModalities: { name: string; value: number }[];
  losDistribution: { range: string; count: number }[];
}

export const mockOncologyCohort: MockOncologyCohort = {
  totalPatients: 1248,
  totalAdmissions: 2834,
  readmissionRate: 14.2,
  mortalityRate: 8.7,
  cancerDistribution: [
    { type: "Lung", count: 228 },
    { type: "Breast", count: 196 },
    { type: "Colon", count: 168 },
    { type: "Prostate", count: 144 },
    { type: "Leukemia", count: 112 },
    { type: "Lymphoma", count: 98 },
    { type: "Myeloma", count: 72 },
    { type: "Bladder", count: 64 },
    { type: "Pancreatic", count: 56 },
    { type: "Liver", count: 48 },
    { type: "Kidney", count: 34 },
    { type: "Other", count: 28 },
  ],
  treatmentModalities: [
    { name: "Surgery", value: 42 },
    { name: "Chemotherapy", value: 68 },
    { name: "Radiation", value: 35 },
    { name: "Immunotherapy", value: 18 },
    { name: "Hormonal", value: 12 },
  ],
  losDistribution: [
    { range: "1-3d", count: 312 },
    { range: "4-7d", count: 486 },
    { range: "8-14d", count: 378 },
    { range: "15-21d", count: 198 },
    { range: "22-30d", count: 124 },
    { range: "30+d", count: 88 },
  ],
};

export const mockPathwayEvents = [
  { date: "Week 1", title: "Initial Staging Workup", category: "diagnostic" as const, description: "CT chest/abdomen/pelvis, PET-CT, brain MRI, pulmonary function tests, bloodwork including tumor markers.", priority: "high" as const, estimatedDays: 7 },
  { date: "Week 2", title: "Multidisciplinary Tumor Board", category: "diagnostic" as const, description: "Case review with oncology, surgery, radiation oncology, pathology, and radiology teams.", priority: "high" as const, estimatedDays: 1 },
  { date: "Week 3-4", title: "Neoadjuvant Chemotherapy Cycle 1", category: "chemo" as const, description: "Cisplatin + Pemetrexed combination, day 1 and 8 schedule. Anti-emetics and hydration protocol.", priority: "high" as const, estimatedDays: 14 },
  { date: "Week 5-6", title: "Neoadjuvant Chemotherapy Cycle 2", category: "chemo" as const, description: "Second cycle with dose adjustment based on toxicity assessment. CBC and renal function monitoring.", priority: "high" as const, estimatedDays: 14 },
  { date: "Week 7", title: "Restaging CT Scan", category: "diagnostic" as const, description: "Response assessment per RECIST 1.1 criteria. Compare with baseline imaging.", priority: "medium" as const, estimatedDays: 3 },
  { date: "Week 8-9", title: "Surgical Resection", category: "surgery" as const, description: "Video-assisted thoracoscopic surgery (VATS) lobectomy with mediastinal lymph node dissection.", priority: "high" as const, estimatedDays: 10 },
  { date: "Week 10-11", title: "Post-operative Recovery", category: "followup" as const, description: "Chest physiotherapy, pain management, wound care. Pathology review of surgical margins.", priority: "medium" as const, estimatedDays: 14 },
  { date: "Week 12-16", title: "Adjuvant Radiation Therapy", category: "radiation" as const, description: "54 Gy in 30 fractions, 5 days/week for 6 weeks. Concurrent weekly carboplatin.", priority: "high" as const, estimatedDays: 35 },
  { date: "Week 18", title: "Follow-up Assessment", category: "followup" as const, description: "CT scan, PFTs, quality of life assessment. Transition to surveillance protocol.", priority: "medium" as const, estimatedDays: 2 },
];
