import { useState, useEffect, useRef, useCallback } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
} from "recharts";
import {
  Loader2,
  AlertCircle,
  CheckCircle2,
  Send,
  Activity,
  AlertTriangle,
  ArrowRightLeft,
  UserPlus,
  UserMinus,
  Clock,
  Zap,
  Filter,
} from "lucide-react";
import AcuityBadge from "../components/AcuityBadge";
import { edTriagePredict, type TriagePrediction, type TriageInput } from "../lib/api";
// Sim data only — no mock data

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface SimEdPatient {
  id: string;
  hadm_id: string;
  subject_id: number;
  bed: string;
  department: string;
  acuity: 1 | 2 | 3 | 4 | 5;
  wait_minutes: number;
  status: string;
  vitals: { hr?: number; rr?: number; spo2?: number; sbp?: number; dbp?: number; temp?: number };
  primary_icd: string;
  med_count: number;
  admission_type: string;
  insurance: string;
}

interface PatientDelta {
  newArrivals: Set<string>;
  transferred: Set<string>;
  discharged: number;
  departmentChanges: Map<string, { from: string; to: string }>;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

import { ACUITY_COLORS as acuityColors, ACUITY_LABELS as acuityLabels } from "../lib/colors";

const arrivalModes = [
  "EMERGENCY ROOM",
  "PHYSICIAN REFERRAL",
  "TRANSFER FROM HOSPITAL",
  "WALK-IN/CLINIC",
  "AMBULANCE",
];

const defaultForm: TriageInput = {
  age: 55,
  gender: "M",
  hr: 92,
  rr: 20,
  spo2: 96,
  sbp: 128,
  dbp: 78,
  temperature: 37.2,
  wbc: 11.2,
  hemoglobin: 13.5,
  lactate: 1.4,
  glucose: 112,
  creatinine: 1.1,
  arrival_mode: "EMERGENCY ROOM",
};

function isEdDepartment(dept: string): boolean {
  if (!dept) return false;
  return dept === "ED" || dept === "CDU"; // Irish names from backend mapping
}

function hasVitalAlert(v: SimEdPatient["vitals"]): boolean {
  return !!(v && ((v.spo2 != null && v.spo2 < 90) || (v.hr != null && v.hr > 150) || (v.sbp != null && v.sbp < 80)));
}

function sortPatients(patients: SimEdPatient[]): SimEdPatient[] {
  return [...patients].sort((a, b) => {
    if (a.acuity !== b.acuity) return a.acuity - b.acuity; // lower ESI = more critical
    return b.wait_minutes - a.wait_minutes; // longer wait first
  });
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function EdTriage() {
  // Form & prediction state
  const [form, setForm] = useState<TriageInput>({ ...defaultForm });
  const [result, setResult] = useState<TriagePrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Live sim ED board
  const [simPatients, setSimPatients] = useState<SimEdPatient[]>([]);
  const [simTime, setSimTime] = useState<string>("");
  const [simConnected, setSimConnected] = useState(false);

  // Board filter
  // Default to "ED + CDU" so the page actually acts as an ED triage
  // board out of the box; the user can toggle to "All Depts" for a
  // hospital-wide view if they want it (see F11/F12).
  const [boardFilter, setBoardFilter] = useState<"all" | "ed">("all");

  // Patient tracking via refs (no re-render overhead)
  const prevPatientsRef = useRef<Map<string, SimEdPatient>>(new Map());
  const [delta, setDelta] = useState<PatientDelta>({
    newArrivals: new Set(),
    transferred: new Set(),
    discharged: 0,
    departmentChanges: new Map(),
  });
  const totalDischargedRef = useRef(0);

  // Custom polling with delta-tracking (new arrivals, transfers, discharges).
  // Cannot use useSimPolling hook — requires Map-based prev/next comparison.
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/sim/ed-board");
        if (res.ok && active) {
          const data = await res.json();
          const incoming: SimEdPatient[] = data.patients || [];
          const prevMap = prevPatientsRef.current;

          // Build new map
          const newMap = new Map<string, SimEdPatient>();
          incoming.forEach((p) => newMap.set(p.hadm_id, p));

          // Compute deltas
          const newArrivals = new Set<string>();
          const transferred = new Set<string>();
          const departmentChanges = new Map<string, { from: string; to: string }>();
          let discharged = 0;

          // Only compute deltas if we had a previous snapshot
          if (prevMap.size > 0) {
            // New arrivals: in new but not in old
            newMap.forEach((_, id) => {
              if (!prevMap.has(id)) newArrivals.add(id);
            });

            // Discharged/transferred out: in old but not in new
            prevMap.forEach((_, id) => {
              if (!newMap.has(id)) discharged++;
            });

            // Department changes
            newMap.forEach((p, id) => {
              const old = prevMap.get(id);
              if (old && old.department !== p.department) {
                transferred.add(id);
                departmentChanges.set(id, { from: old.department, to: p.department });
              }
            });
          }

          totalDischargedRef.current += discharged;

          prevPatientsRef.current = newMap;
          setSimPatients(incoming);
          setSimTime(data.sim_time || "");
          setSimConnected(true);
          setDelta({ newArrivals, transferred, discharged, departmentChanges });
        }
      } catch {
        if (active) setSimConnected(false);
      }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  // ML model metadata removed — all data comes from simulation only

  const handleChange = (field: keyof TriageInput, value: string | number) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handlePredict = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await edTriagePredict(form);
      if (res) {
        setResult(res);
      } else {
        setError("ED Triage model unavailable. Ensure the backend is running.");
      }
    } catch {
      setError("Prediction failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // Quick-triage: fill form from a sim patient's vitals
  const formRef = useRef<HTMLDivElement>(null);
  const fillFromPatient = useCallback((p: SimEdPatient) => {
    const v = p.vitals || {};
    if (!v.hr && !v.spo2 && !v.sbp) {
      // No vitals — can't fill form meaningfully
      return;
    }
    setForm((prev) => ({
      ...prev,
      hr: v.hr ?? prev.hr,
      rr: v.rr ?? prev.rr,
      spo2: v.spo2 ?? prev.spo2,
      sbp: v.sbp ?? prev.sbp,
      dbp: v.dbp ?? prev.dbp,
      temperature: v.temp ?? prev.temperature,
      arrival_mode: p.admission_type?.includes("EMER") ? "EMERGENCY ROOM" :
                    p.admission_type?.includes("AMBULAN") ? "AMBULANCE" :
                    p.admission_type?.includes("TRANSFER") ? "TRANSFER FROM HOSPITAL" : prev.arrival_mode,
    }));
    // Scroll to the form section
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // Compute board patients
  const isLive = simConnected && simPatients.length > 0;
  const filteredSimPatients = isLive
    ? boardFilter === "ed"
      ? simPatients.filter((p) => isEdDepartment(p.department))
      : simPatients
    : null;
  const displayPatients = filteredSimPatients ? sortPatients(filteredSimPatients) : null;

  // ESI counts from whatever is displayed
  const esiCounts = [0, 0, 0, 0, 0];
  if (displayPatients) {
    displayPatients.forEach((p) => esiCounts[(p.acuity || 3) - 1]++);
  }

  // Live acuity data for the bar chart
  const liveAcuityData = esiCounts.map((count, i) => ({
    name: `ESI-${i + 1}`,
    count,
    fill: acuityColors[i],
  }));

  // Average wait
  const avgWait = displayPatients && displayPatients.length > 0
    ? Math.round(displayPatients.reduce((s, p) => s + (p.wait_minutes || 0), 0) / displayPatients.length)
    : 0;

  // Input field helper
  const inputField = (
    label: string,
    field: keyof TriageInput,
    hint: string,
    type: "number" | "text" = "number"
  ) => (
    <div>
      <label className="text-[10px] text-text-muted block mb-0.5">
        {label} <span className="text-text-muted/60">{hint}</span>
      </label>
      <input
        type={type}
        value={form[field]}
        onChange={(e) =>
          handleChange(field, type === "number" ? parseFloat(e.target.value) || 0 : e.target.value)
        }
        className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary font-mono-clinical focus:border-blue-500 focus:outline-none transition-colors"
      />
    </div>
  );

  /* ================================================================ */
  /*  RENDER                                                           */
  /* ================================================================ */

  return (
    <div className="space-y-4">
      {/* ============================================================ */}
      {/* TOP: Census Board (full width when live)                     */}
      {/* ============================================================ */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        {/* Header row */}
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-sm font-semibold text-text-primary">
              {boardFilter === "ed" ? "ED Census Board" : "Hospital Census Board"}
              <span className="ml-2 text-[11px] font-normal text-text-muted">
                {boardFilter === "ed" ? "(ED + CDU)" : "(all departments)"}
              </span>
            </h2>
            {simConnected && (
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                <span className="text-[11px] text-green-400 font-mono-clinical">LIVE</span>
              </div>
            )}
          </div>

          {/* Sim time + filter */}
          <div className="flex items-center gap-3">
            {simTime && (
              <div className="flex items-center gap-1.5 bg-bg-primary rounded-lg px-3 py-1 border border-border/50">
                <Clock className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-xs font-mono-clinical text-text-primary font-semibold">{simTime}</span>
              </div>
            )}
            {isLive && (
              <div
                role="tablist"
                aria-label="Census filter"
                className="flex items-center gap-1 bg-bg-primary rounded-lg border border-border/50 overflow-hidden"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={boardFilter === "all"}
                  onClick={() => setBoardFilter("all")}
                  title="Show every inpatient department, not just ED"
                  className={`flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium transition-colors ${
                    boardFilter === "all"
                      ? "bg-blue-600 text-white"
                      : "text-text-muted hover:text-text-primary"
                  }`}
                >
                  <Filter className="w-3 h-3" aria-hidden="true" />
                  All Depts
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={boardFilter === "ed"}
                  onClick={() => setBoardFilter("ed")}
                  title="ED + CDU (Clinical Decision Unit) — co-located with ED in this model"
                  className={`flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium transition-colors ${
                    boardFilter === "ed"
                      ? "bg-blue-600 text-white"
                      : "text-text-muted hover:text-text-primary"
                  }`}
                >
                  ED + CDU
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Live Stats bar */}
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <div className="bg-bg-primary rounded px-2.5 py-1 border border-border/30">
            <span className="text-[10px] text-text-muted">Total: </span>
            <span className="text-xs font-bold text-text-primary font-mono-clinical">
              {displayPatients ? displayPatients.length : 0}
            </span>
          </div>
          {esiCounts.map((count, i) => (
            <div key={i} className="bg-bg-primary rounded px-2 py-1 flex items-center gap-1 border border-border/30">
              <div className="w-2 h-2 rounded-full" style={{ backgroundColor: acuityColors[i] }} />
              <span className="text-[10px] font-mono-clinical text-text-secondary">{count}</span>
            </div>
          ))}
          {isLive && (
            <>
              <div className="bg-bg-primary rounded px-2.5 py-1 border border-border/30">
                <span className="text-[10px] text-text-muted">Avg Wait: </span>
                <span className="text-xs font-bold text-text-primary font-mono-clinical">{avgWait}m</span>
              </div>
              <div className="bg-bg-primary rounded px-2.5 py-1 border border-border/30">
                <span className="text-[10px] text-text-muted">Discharged: </span>
                <span className="text-xs font-bold text-text-primary font-mono-clinical">{totalDischargedRef.current}</span>
              </div>
            </>
          )}

          {/* Delta indicators */}
          {isLive && (delta.newArrivals.size > 0 || delta.discharged > 0 || delta.transferred.size > 0) && (
            <div className="flex items-center gap-2 ml-2 text-[10px]">
              {delta.newArrivals.size > 0 && (
                <span className="flex items-center gap-0.5 text-green-400">
                  <UserPlus className="w-3 h-3" />
                  {delta.newArrivals.size} new
                </span>
              )}
              {delta.transferred.size > 0 && (
                <span className="flex items-center gap-0.5 text-yellow-400">
                  <ArrowRightLeft className="w-3 h-3" />
                  {delta.transferred.size} transferred
                </span>
              )}
              {delta.discharged > 0 && (
                <span className="flex items-center gap-0.5 text-slate-400">
                  <UserMinus className="w-3 h-3" />
                  {delta.discharged} left
                </span>
              )}
            </div>
          )}
        </div>

        {/* Patient Table */}
        <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
          <table className="w-full text-xs">
            {/* Sticky header lifted to z-20 with a solid bg + bottom shadow
                so scrolling rows can never bleed through. */}
            <thead className="sticky top-0 z-20 bg-bg-card shadow-[0_1px_0_0_var(--color-border)]">
              <tr className="text-text-muted text-left border-b border-border">
                <th className="py-1.5 px-1.5 font-medium">Dept</th>
                <th className="py-1.5 px-1.5 font-medium">Patient</th>
                <th className="py-1.5 px-1.5 font-medium">Type</th>
                <th className="py-1.5 px-1.5 font-medium">Acuity</th>
                <th className="py-1.5 px-1.5 font-medium">Wait</th>
                <th className="py-1.5 px-1.5 font-medium" title="HR · SpO₂ · SBP">Vitals</th>
                <th
                  className="py-1.5 px-1.5 font-medium"
                  title="Status comes from the simulator — currently every active patient reports as 'In Treatment'. Use Wait time as the activity indicator."
                >
                  Status
                </th>
                {isLive && <th className="py-1.5 px-1.5 font-medium w-16">Action</th>}
              </tr>
            </thead>
            <tbody>
              {displayPatients
                ? displayPatients.map((p) => {
                    const isNew = delta.newArrivals.has(p.hadm_id);
                    const isTransferred = delta.transferred.has(p.hadm_id);
                    const isCritical = p.acuity === 1;
                    const isEmergent = p.acuity === 2;
                    const vitalAlert = hasVitalAlert(p.vitals);

                    return (
                      <tr
                        key={p.hadm_id}
                        className={`border-b border-border/50 hover:bg-bg-hover transition-colors ${
                          isNew ? "animate-fade-in" : ""
                        } ${isCritical ? "pulse-critical" : ""}`}
                        style={{
                          borderLeftWidth: isCritical ? 4 : isEmergent ? 3 : 2,
                          borderLeftColor: acuityColors[(p.acuity || 3) - 1],
                          borderLeftStyle: "solid",
                        }}
                      >
                        <td
                          className="py-1.5 px-1.5 text-[10px] text-text-secondary max-w-[100px] truncate"
                          title={p.department}
                        >
                          {p.department?.replace(/\(.*\)/, "").trim().substring(0, 18)}
                        </td>
                        <td className="py-1.5 px-1.5 text-text-primary text-[10px] font-mono-clinical">
                          <span>{p.subject_id}</span>
                          {isNew && (
                            <span className="ml-1.5 inline-flex items-center px-1 py-0.5 rounded text-[8px] font-bold bg-green-500/15 text-green-400 border border-green-500/30">
                              NEW
                            </span>
                          )}
                          {isTransferred && (
                            <span className="ml-1.5 inline-flex items-center px-1 py-0.5 rounded text-[8px] font-bold bg-yellow-500/15 text-yellow-400 border border-yellow-500/30">
                              XFER
                            </span>
                          )}
                        </td>
                        <td
                          className="py-1.5 px-1.5 text-[11px] text-text-muted max-w-[110px] truncate"
                          title={p.admission_type ?? ""}
                        >
                          {p.admission_type ?? "—"}
                        </td>
                        <td className="py-1.5 px-1.5">
                          <AcuityBadge level={p.acuity as 1 | 2 | 3 | 4 | 5} size="sm" />
                        </td>
                        <td className="py-1.5 px-1.5 font-mono-clinical text-text-secondary text-[10px]">
                          {p.wait_minutes}m
                        </td>
                        <td
                          className="py-1.5 px-1.5 text-[10px] font-mono-clinical text-text-muted whitespace-nowrap"
                          title={`HR ${p.vitals?.hr ?? "—"} · SpO₂ ${p.vitals?.spo2 ?? "—"} · SBP ${p.vitals?.sbp ?? "—"}`}
                        >
                          <span className="flex items-center gap-1.5">
                            <span aria-label={`Heart rate ${p.vitals?.hr ?? "unavailable"}`}>HR&nbsp;{p.vitals?.hr ?? "—"}</span>
                            <span className="text-text-muted/50" aria-hidden="true">·</span>
                            <span aria-label={`SpO2 ${p.vitals?.spo2 ?? "unavailable"} percent`}>SpO₂&nbsp;{p.vitals?.spo2 ?? "—"}</span>
                            <span className="text-text-muted/50" aria-hidden="true">·</span>
                            <span aria-label={`Systolic BP ${p.vitals?.sbp ?? "unavailable"}`}>SBP&nbsp;{p.vitals?.sbp ?? "—"}</span>
                            {vitalAlert && (
                              <AlertTriangle className="w-3 h-3 text-red-500 shrink-0" aria-label="Vital sign alert" />
                            )}
                          </span>
                        </td>
                        <td className="py-1.5 px-1.5">
                          <span
                            className={`text-[10px] ${
                              p.status === "In Treatment"
                                ? "text-green-400"
                                : p.status === "Just Arrived"
                                ? "text-blue-400"
                                : p.status === "Waiting"
                                ? "text-yellow-400"
                                : "text-text-muted"
                            }`}
                          >
                            {p.status}
                          </span>
                        </td>
                        <td className="py-1.5 px-1.5">
                          <button
                            onClick={() => fillFromPatient(p)}
                            disabled={!p.vitals?.hr && !p.vitals?.spo2 && !p.vitals?.sbp}
                            title={p.vitals?.hr ? "Load vitals into predictor" : "No vitals available yet"}
                            className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-medium transition-colors ${
                              p.vitals?.hr || p.vitals?.spo2 || p.vitals?.sbp
                                ? "bg-blue-600/15 text-blue-400 border border-blue-600/30 hover:bg-blue-600/30 cursor-pointer"
                                : "bg-slate-500/10 text-slate-500 border border-slate-500/20 cursor-not-allowed"
                            }`}
                          >
                            <Zap className="w-2.5 h-2.5" aria-hidden="true" />
                            Use vitals
                          </button>
                        </td>
                      </tr>
                    );
                  })
                : (
                    <tr>
                      <td colSpan={8} className="py-6 text-center text-slate-500 text-sm italic">
                        No patients. Start the simulation to see live ED board data.
                      </td>
                    </tr>
                  )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ============================================================ */}
      {/* MIDDLE: Predictor Form (left 40%) + Prediction Result (60%)  */}
      {/* ============================================================ */}
      <div ref={formRef} className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* LEFT: Input Form — 2 of 5 cols (40%) */}
        <div className="lg:col-span-2 bg-bg-card rounded-xl border border-border p-4 overflow-y-auto">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Patient Assessment</h2>

          <div className="space-y-3">
            <div className="text-[10px] font-medium text-text-muted uppercase tracking-wider">
              Demographics
            </div>
            <div className="grid grid-cols-2 gap-2">
              {inputField("Age", "age", "(years)")}
              <div>
                <label className="text-[10px] text-text-muted block mb-0.5">Gender</label>
                <select
                  value={form.gender}
                  onChange={(e) => handleChange("gender", e.target.value)}
                  className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
                >
                  <option value="M">Male</option>
                  <option value="F">Female</option>
                </select>
              </div>
            </div>

            <div className="text-[10px] font-medium text-text-muted uppercase tracking-wider mt-3">
              Vital Signs
            </div>
            <div className="grid grid-cols-2 gap-2">
              {inputField("Heart Rate", "hr", "(60-100)")}
              {inputField("Resp Rate", "rr", "(12-20)")}
              {inputField("SpO2", "spo2", "(95-100%)")}
              {inputField("SBP", "sbp", "(90-140)")}
              {inputField("DBP", "dbp", "(60-90)")}
              {inputField("Temperature", "temperature", "(36.1-37.2C)")}
            </div>

            <div className="text-[10px] font-medium text-text-muted uppercase tracking-wider mt-3">
              Laboratory
            </div>
            <div className="grid grid-cols-2 gap-2">
              {inputField("WBC", "wbc", "(4.5-11 K/uL)")}
              {inputField("Hemoglobin", "hemoglobin", "(12-17 g/dL)")}
              {inputField("Lactate", "lactate", "(<2 mmol/L)")}
              {inputField("Glucose", "glucose", "(70-100 mg/dL)")}
              {inputField("Creatinine", "creatinine", "(0.7-1.3 mg/dL)")}
            </div>

            <div className="text-[10px] font-medium text-text-muted uppercase tracking-wider mt-3">
              Arrival
            </div>
            <div>
              <label className="text-[10px] text-text-muted block mb-0.5">Arrival Mode</label>
              <select
                value={form.arrival_mode}
                onChange={(e) => handleChange("arrival_mode", e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
              >
                {arrivalModes.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </div>

            <button
              onClick={handlePredict}
              disabled={loading}
              className="w-full mt-3 flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-medium py-2.5 rounded-lg transition-colors"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              Predict Triage Level
            </button>
          </div>
        </div>

        {/* RIGHT: Results — 3 of 5 cols (60%) */}
        <div className="lg:col-span-3 bg-bg-card rounded-xl border border-border p-4 overflow-y-auto">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Prediction Result</h2>

          {error && (
            <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-lg p-3 mb-3">
              <AlertCircle className="w-4 h-4 text-red-400" />
              <span className="text-xs text-red-400">{error}</span>
              <button
                onClick={handlePredict}
                className="ml-auto text-xs text-red-400 hover:text-red-300 underline"
              >
                Retry
              </button>
            </div>
          )}

          {!result && !loading && !error && (
            <div className="flex flex-col items-center justify-center h-64 text-text-muted">
              <Loader2 className="w-8 h-8 mb-3 opacity-30" />
              <p className="text-sm">Enter patient data and click Predict</p>
            </div>
          )}

          {loading && (
            <div className="space-y-3">
              <div className="skeleton h-20 w-full" />
              <div className="skeleton h-8 w-3/4" />
              <div className="skeleton h-32 w-full" />
              <div className="skeleton h-8 w-1/2" />
            </div>
          )}

          {result && !loading && (
            <div className="space-y-4 animate-fade-in">
              {/* Acuity Badge Large */}
              <div className="flex flex-col items-center py-4 bg-bg-primary rounded-lg">
                <div className="mb-2">
                  <AcuityBadge level={result.esi_level} size="lg" />
                </div>
                <div className="text-2xl font-bold text-text-primary">
                  ESI Level {result.esi_level}
                </div>
                <div className="text-xs text-text-muted mt-1">
                  {acuityLabels[result.esi_level - 1]}
                </div>
              </div>

              {/* Confidence */}
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-text-muted">Model Confidence</span>
                  <span className="font-mono-clinical text-sm font-bold text-text-primary">
                    {result.confidence.toFixed(1)}%
                  </span>
                </div>
                <div className="w-full h-2 bg-progress-bg rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${result.confidence}%`,
                      backgroundColor:
                        result.confidence > 80
                          ? "#22C55E"
                          : result.confidence > 60
                          ? "#EAB308"
                          : "#DC2626",
                    }}
                  />
                </div>
              </div>

              {/* Class Probabilities */}
              <div className="bg-bg-primary rounded-lg p-3">
                <div className="text-xs text-text-muted mb-2">Class Probabilities</div>
                <div className="space-y-2">
                  {result.probabilities.map((prob, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span
                        className="text-[10px] font-mono-clinical w-10"
                        style={{ color: acuityColors[i] }}
                      >
                        ESI-{i + 1}
                      </span>
                      <div className="flex-1 h-3 bg-progress-bg rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
                            width: `${prob * 100}%`,
                            backgroundColor: acuityColors[i],
                          }}
                        />
                      </div>
                      <span className="text-[10px] font-mono-clinical text-text-secondary w-10 text-right">
                        {(prob * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Disposition & LOS */}
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-bg-primary rounded-lg p-3 text-center">
                  <div className="text-[10px] text-text-muted mb-1">Disposition</div>
                  <div
                    className={`text-sm font-bold ${
                      result.disposition === "Admit"
                        ? "text-red-400"
                        : result.disposition === "Discharge"
                        ? "text-green-400"
                        : "text-yellow-400"
                    }`}
                  >
                    {result.disposition}
                  </div>
                </div>
                <div className="bg-bg-primary rounded-lg p-3 text-center">
                  <div className="text-[10px] text-text-muted mb-1">Est. ED LOS</div>
                  <div className="text-sm font-bold text-text-primary font-mono-clinical">
                    {result.estimated_los_hours.toFixed(1)}h
                  </div>
                </div>
              </div>

              {/* Risk Factors */}
              {result.risk_factors.length > 0 && (
                <div className="bg-bg-primary rounded-lg p-3">
                  <div className="text-xs text-text-muted mb-2">Risk Factors</div>
                  <div className="space-y-1">
                    {result.risk_factors.map((rf, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <div className="w-1.5 h-1.5 rounded-full bg-red-400 shrink-0" />
                        <span className="text-xs text-text-secondary">{rf}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ============================================================ */}
      {/* BOTTOM: Acuity Distribution (sim data only)                  */}
      {/* ============================================================ */}
      <div className="grid grid-cols-1 gap-4">
        {/* Live Acuity Distribution */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity className="w-4 h-4 text-green-400" />
            <h3 className="text-sm font-semibold text-text-primary">Acuity Distribution</h3>
          </div>

          {isLive ? (
            <div className="animate-fade-in">
              <div style={{ height: 160 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={liveAcuityData} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: "#94a3b8" }} />
                    <YAxis tick={{ fontSize: 9, fill: "#64748b" }} allowDecimals={false} width={30} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "var(--color-tooltip-bg)",
                        border: "1px solid var(--color-tooltip-border)",
                        borderRadius: 8,
                        fontSize: 11,
                      }}
                      formatter={(value: number) => [value, "Patients"]}
                    />
                    <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                      {liveAcuityData.map((d, i) => (
                        <Cell key={i} fill={acuityColors[i]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <p className="text-slate-500 text-sm italic py-8 text-center">No acuity data. Start the simulation.</p>
          )}
        </div>

        {/* Model Metrics removed — shown in Census Board and System Admin */}
      </div>
    </div>
  );
}
