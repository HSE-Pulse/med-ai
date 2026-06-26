import { useState, useMemo, useEffect } from "react";
import { SepsisRecentScreensPanel } from "../components/UpliftWidgets";
import {
  AlertTriangle,
  ArrowLeft,
  Users,
  Activity,
  Bell,
  ThermometerSun,
  Award,
  Radio,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import VitalSignChart from "../components/VitalSignChart";
import {
  calcRespiratorySOFA,
  calcCoagulationSOFA,
  calcLiverSOFA,
  calcCardioSOFA,
  calcRenalSOFA,
  calcCnsSOFA,
  type SofaInputs,
} from "../lib/sofa";
// Sim data only — no mock data

import {
  sofaTotalColor as totalSofaColor,
  sofaTotalLabel as totalSofaLabel,
  SOFA_COMPONENT_COLORS as sofaColors,
  SOFA_COMPONENT_LABELS as sofaLabels,
  riskColor as _riskColor,
} from "../lib/colors";

const SOFA_PRESETS: Record<string, SofaInputs> = {
  "Normal Patient": { spo2: 98, platelets: 250, bilirubin: 0.6, meanBp: 85, creatinine: 0.9, gcs: 15 },
  Deteriorating: { spo2: 94, platelets: 120, bilirubin: 2.5, meanBp: 68, creatinine: 1.8, gcs: 13 },
  "Septic Shock": { spo2: 88, platelets: 30, bilirubin: 8.0, meanBp: 55, creatinine: 4.2, gcs: 9 },
};

const riskColor = _riskColor;

interface SimIcuPatient {
  hadm_id: number;
  subject_id: number;
  department: string;
  sofa_total: number;
  // Backend (`/api/sim/icu-board`) emits the long names; older code used
  // four-letter aliases. Keep both keys typed as optional so the UI is
  // tolerant of either shape — the read sites below pick whichever side
  // is present.
  sofa_components: {
    respiration?: number; coagulation?: number; liver?: number; cardiovascular?: number; renal?: number;
    resp?: number; coag?: number; cardio?: number;
  };
  vitals: { hr: number; rr: number; spo2: number; sbp: number; dbp: number; temp: number };
  labs: { platelets: number; bilirubin: number; creatinine: number };
  risk_level: string;
  alerts: Array<{ severity: string; type: string; message?: string; value?: number }>;
}

const simSofaColor = totalSofaColor;

function LiveBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30">
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
      </span>
      <span className="text-[10px] font-semibold text-green-400 uppercase">LIVE</span>
    </span>
  );
}

import { useSimPolling } from "../hooks/useSimPolling";

// Per-vital rolling history buffer cap. ~30 min at the 5 s board poll.
const MAX_POINTS = 360;

export default function SepsisIcu() {
  const [selectedSimPatient, setSelectedSimPatient] = useState<SimIcuPatient | null>(null);

  const { data: simData, connected: simConnected } = useSimPolling<{
    patients: SimIcuPatient[];
    icu_capacity?: number;
    hdu_capacity?: number;
  }>("/api/sim/icu-board", 5000);
  const simIcuPatients = simData?.patients ?? [];

  // Refresh the selected patient from the latest board poll so vitals update
  // each cycle. Falls back to the last-known object if the patient drops out
  // of the board (discharged) so the detail view doesn't flicker to empty.
  const livePatient = useMemo<SimIcuPatient | null>(() => {
    if (!selectedSimPatient) return null;
    return (
      simIcuPatients.find((p) => p.hadm_id === selectedSimPatient.hadm_id) ??
      selectedSimPatient
    );
  }, [selectedSimPatient, simIcuPatients]);

  // Rolling per-vital history accumulated from successive snapshots.
  const [simHistory, setSimHistory] = useState<
    Record<string, { t: number; v: number }[]>
  >({});

  // Reset the buffer when the selection switches to a different patient.
  useEffect(() => {
    setSimHistory({});
  }, [selectedSimPatient?.hadm_id]);

  // Append the latest vitals snapshot to the buffer on each board update.
  useEffect(() => {
    if (!livePatient?.vitals) return;
    const v = livePatient.vitals as Record<string, number>;
    const now = Date.now();
    setSimHistory((prev) => {
      const next = { ...prev };
      for (const [k, val] of Object.entries(v)) {
        if (typeof val !== "number" || !Number.isFinite(val)) continue;
        const series = (next[k] ?? []).slice();
        const last = series[series.length - 1];
        if (!last || last.v !== val || now - last.t > 1000) {
          series.push({ t: now, v: val });
        }
        if (series.length > MAX_POINTS) {
          series.splice(0, series.length - MAX_POINTS);
        }
        next[k] = series;
      }
      return next;
    });
  }, [livePatient]);

  // Authoritative time-series seed from the sim API — fired ONCE per
  // patient selection. Pre-fills the buffer with the full
  // MIMIC_SIM.chartevents history (since admission → current sim time)
  // so charts have real depth from the first paint. After the seed, the
  // 5 s board snapshot-append effect (above) keeps the chart rolling
  // without re-fetching the full series each cycle (which used to wipe
  // appended snapshots and made the chart look static between sparse
  // sim chartevent emissions).
  useEffect(() => {
    const hid = selectedSimPatient?.hadm_id;
    if (!hid) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/sim/patient/${hid}/journey`);
        if (!res.ok) return;
        const data = await res.json();
        const series:
          | Record<string, Array<{ time: string; value: number }>>
          | undefined = data?.vitals_series || data?.data?.vitals_series;
        if (cancelled || !series || Object.keys(series).length === 0) return;
        setSimHistory((prev) => {
          // Only seed if the buffer is empty for this key — the
          // snapshot-append effect may have already added a board point
          // before this fetch returned, and we don't want to clobber it.
          const next: Record<string, { t: number; v: number }[]> = { ...prev };
          for (const [name, points] of Object.entries(series)) {
            if (next[name] && next[name].length > 1) continue;
            const mapped = (points || [])
              .map((p) => ({ t: Date.parse(p.time), v: Number(p.value) }))
              .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v));
            if (mapped.length > MAX_POINTS) {
              mapped.splice(0, mapped.length - MAX_POINTS);
            }
            next[name] = mapped;
          }
          return next;
        });
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSimPatient?.hadm_id]);
  const icuCapacity = simData?.icu_capacity ?? 12;
  const hduCapacity = simData?.hdu_capacity ?? 8;

  // Bed occupancy from Bed Management
  const [bedPressure, setBedPressure] = useState<{icu_occ: number; icu_cap: number; hdu_occ: number; hdu_cap: number; icu_alert: string; hdu_alert: string}>({icu_occ: 0, icu_cap: 12, hdu_occ: 0, hdu_cap: 8, icu_alert: "green", hdu_alert: "green"});
  const [staffInfo, setStaffInfo] = useState<{docs: number; nurses: number; ratio: string}>({docs: 4, nurses: 13, ratio: "1:1"});

  useEffect(() => {
    const fetchBedStaff = async () => {
      try {
        const [bedRes, staffRes] = await Promise.all([
          fetch("/api/beds/beds/summary").catch(() => null),
          fetch("/api/erp/staff/ICU").catch(() => null),
        ]);
        if (bedRes?.ok) {
          const d = await bedRes.json();
          if (d.status === "ok") {
            const icu = d.data?.find((x: Record<string, unknown>) => x.department === "ICU");
            const hdu = d.data?.find((x: Record<string, unknown>) => x.department === "HDU");
            setBedPressure({
              icu_occ: icu?.occupied ?? 0, icu_cap: icu?.capacity ?? 12, icu_alert: icu?.alert_level ?? "green",
              hdu_occ: hdu?.occupied ?? 0, hdu_cap: hdu?.capacity ?? 8, hdu_alert: hdu?.alert_level ?? "green",
            });
          }
        }
        if (staffRes?.ok) {
          const d = await staffRes.json();
          if (d.status === "ok") {
            setStaffInfo({
              docs: d.data?.day_shift?.total_doctors ?? 4,
              nurses: d.data?.day_shift?.total_nurses ?? 13,
              ratio: d.data?.nurse_patient_ratio ?? "1:1",
            });
          }
        }
      } catch { /* offline */ }
    };
    fetchBedStaff();
    const id = setInterval(fetchBedStaff, 10000);
    return () => clearInterval(id);
  }, []);

  const useSimData = simConnected && simIcuPatients.length > 0;

  const totalPatients = useSimData ? simIcuPatients.length : 0;
  const highRiskCount = useSimData
    ? simIcuPatients.filter((p) => p.sofa_total >= 10).length
    : 0;
  const criticalAlerts = useSimData
    ? simIcuPatients.reduce(
        (sum, p) => sum + (p.alerts?.filter((a) => a.severity === "critical").length || 0),
        0
      )
    : 0;
  const avgSofa = useSimData && totalPatients > 0
    ? (simIcuPatients.reduce((sum, p) => sum + p.sofa_total, 0) / totalPatients).toFixed(1)
    : "—";

  /* ── SOFA Calculator state (must be before any early return) ── */
  const [sofaInputs, setSofaInputs] = useState<SofaInputs>(SOFA_PRESETS["Normal Patient"]);

  const sofaScores = useMemo(() => {
    const resp = calcRespiratorySOFA(sofaInputs.spo2);
    const coag = calcCoagulationSOFA(sofaInputs.platelets);
    const liver = calcLiverSOFA(sofaInputs.bilirubin);
    const cardio = calcCardioSOFA(sofaInputs.meanBp);
    const renal = calcRenalSOFA(sofaInputs.creatinine);
    const cns = calcCnsSOFA(sofaInputs.gcs);
    return {
      resp,
      coag,
      liver,
      cardio,
      renal,
      cns,
      total: resp + coag + liver + cardio + renal + cns,
    };
  }, [sofaInputs]);

  // Sim patient detail view
  if (selectedSimPatient) {
    const sp = livePatient ?? selectedSimPatient;
    const sofaTotal = sp.sofa_total;
    const color = simSofaColor(sofaTotal);
    const simSofaData = [
      { name: "Respiration", value: sp.sofa_components.resp, fill: sofaColors[0] },
      { name: "Coagulation", value: sp.sofa_components.coag, fill: sofaColors[1] },
      { name: "Liver", value: sp.sofa_components.liver, fill: sofaColors[2] },
      { name: "Cardiovascular", value: sp.sofa_components.cardio, fill: sofaColors[3] },
      { name: "Renal", value: sp.sofa_components.renal, fill: sofaColors[4] },
    ];
    // Rolling history: each board poll appends to a per-vital buffer
    // (capped at MAX_POINTS). The buffer represents real observed snapshots
    // accumulated since the patient was selected — not a synthesised trend
    // (F28 fix: the previous implementation faked history with Math.random;
    // here every point is a real sim emission tagged with wall-clock time).
    const seriesFor = (key: string, current: number) => {
      const buf = simHistory[key];
      if (buf && buf.length > 0) {
        return buf.map((p) => ({
          time: new Date(p.t).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
          value: p.v,
        }));
      }
      return [{ time: "now", value: current }];
    };
    const historyPoints = simHistory.hr?.length ?? 0;

    return (
      <div className="space-y-4 animate-fade-in">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setSelectedSimPatient(null)}
            className="flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" /> Back to Unit
          </button>
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold"
              style={{ backgroundColor: `${color}20`, color, border: `2px solid ${color}` }}
            >
              {sofaTotal}
            </div>
            <div>
              <div className="text-white font-semibold flex items-center gap-2">
                Patient #{sp.subject_id}
                <LiveBadge />
              </div>
              <div className="text-xs text-slate-400">{sp.department} | HADM #{sp.hadm_id} | {sp.risk_level}</div>
            </div>
          </div>
          <div className="ml-auto bg-bg-card rounded-lg px-3 py-1.5 border border-border">
            <span className="text-xs text-slate-400">SOFA Score: </span>
            <span className="font-mono-clinical font-bold text-white">{sofaTotal}</span>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div className="col-span-2 space-y-3">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <h3 className="text-sm font-semibold text-white">Vital Signs — rolling</h3>
              <span className="text-[11px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 rounded-full px-2 py-0.5">
                live trend · {historyPoints} pts
              </span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <VitalSignChart data={seriesFor("hr", sp.vitals.hr)} label="Heart Rate" unit="bpm" normalMin={60} normalMax={100} />
              <VitalSignChart data={seriesFor("rr", sp.vitals.rr)} label="Respiratory Rate" unit="br/min" normalMin={12} normalMax={20} />
              <VitalSignChart data={seriesFor("spo2", sp.vitals.spo2)} label="SpO2" unit="%" normalMin={94} normalMax={100} />
              <VitalSignChart data={seriesFor("sbp", sp.vitals.sbp)} label="Systolic BP" unit="mmHg" normalMin={90} normalMax={140} />
              <VitalSignChart data={seriesFor("temp", sp.vitals.temp)} label="Temperature" unit="C" normalMin={36.1} normalMax={37.8} />
              <VitalSignChart data={seriesFor("dbp", sp.vitals.dbp)} label="Diastolic BP" unit="mmHg" normalMin={60} normalMax={90} />
            </div>
          </div>

          <div className="space-y-3">
            <div className="bg-bg-card rounded-xl border border-border p-3">
              <h4 className="text-xs font-semibold text-white mb-2">SOFA Breakdown</h4>
              <div className="space-y-2">
                {simSofaData.map((item) => (
                  <div key={item.name} className="flex items-center gap-2">
                    <span className="text-[10px] text-slate-400 w-24 shrink-0">{item.name}</span>
                    <div className="flex-1 flex gap-0.5">
                      {[0, 1, 2, 3].map((seg) => (
                        <div
                          key={seg}
                          className="flex-1 h-3 rounded-sm"
                          style={{
                            backgroundColor: seg < item.value ? item.fill : "var(--color-segment-empty)",
                            border: "1px solid var(--color-segment-border)",
                          }}
                        />
                      ))}
                    </div>
                    <span className="text-[10px] font-mono-clinical text-slate-300 w-4 text-right">{item.value}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-bg-card rounded-xl border border-border p-3">
              <h4 className="text-xs font-semibold text-white mb-2">Lab Values</h4>
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">Platelets</span>
                  <span className={`font-mono-clinical ${sp.labs.platelets < 150 ? "text-red-400" : "text-slate-300"}`}>{sp.labs.platelets} K/uL</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">Bilirubin</span>
                  <span className={`font-mono-clinical ${sp.labs.bilirubin > 1.2 ? "text-red-400" : "text-slate-300"}`}>{sp.labs.bilirubin} mg/dL</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">Creatinine</span>
                  <span className={`font-mono-clinical ${sp.labs.creatinine > 1.2 ? "text-red-400" : "text-slate-300"}`}>{sp.labs.creatinine} mg/dL</span>
                </div>
              </div>
            </div>

            {sp.alerts.length > 0 && (
              <div className="bg-bg-card rounded-xl border border-border p-3">
                <h4 className="text-xs font-semibold text-white mb-2">Alerts</h4>
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {sp.alerts.map((alert, i) => (
                    <div
                      key={i}
                      className={`flex items-start gap-2 rounded-lg p-2 ${
                        alert.severity === "critical"
                          ? "bg-red-500/10 border border-red-500/20"
                          : alert.severity === "warning"
                          ? "bg-yellow-500/10 border border-yellow-500/20"
                          : "bg-blue-500/10 border border-blue-500/20"
                      }`}
                    >
                      <AlertTriangle className={`w-3 h-3 shrink-0 mt-0.5 ${
                        alert.severity === "critical" ? "text-red-400" : alert.severity === "warning" ? "text-yellow-400" : "text-blue-400"
                      }`} />
                      <p className="text-[11px] text-slate-300">{alert.message || `${alert.type}: ${alert.value}`}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // UNIT OVERVIEW
  return (
    <div className="space-y-4">
      {/* Top Stats */}
      <div className="grid grid-cols-6 gap-3">
        <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center">
            <Users className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <div className="text-[10px] text-slate-400 uppercase">Patients</div>
            <div className="font-mono-clinical text-xl font-bold text-white">{totalPatients}<span className="text-sm text-slate-500">/{icuCapacity + hduCapacity}</span></div>
          </div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-red-500/10 flex items-center justify-center">
            <AlertTriangle className="w-5 h-5 text-red-400" />
          </div>
          <div>
            <div className="text-[10px] text-slate-400 uppercase">High Risk</div>
            <div className="font-mono-clinical text-xl font-bold text-red-400">{highRiskCount}</div>
          </div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-orange-500/10 flex items-center justify-center">
            <Bell className="w-5 h-5 text-orange-400" />
          </div>
          <div>
            <div className="text-[10px] text-slate-400 uppercase">Alerts</div>
            <div className="font-mono-clinical text-xl font-bold text-orange-400">{criticalAlerts}</div>
          </div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
            <Activity className="w-5 h-5 text-purple-400" />
          </div>
          <div>
            <div className="text-[10px] text-slate-400 uppercase">Avg SOFA</div>
            <div className="font-mono-clinical text-xl font-bold text-white">{avgSofa}</div>
          </div>
        </div>
        {/* Bed Pressure */}
        <div className={`bg-bg-card rounded-xl border p-3 ${bedPressure.icu_alert === "black" ? "border-red-500/50" : bedPressure.icu_alert === "red" ? "border-red-500/30" : "border-border"}`}>
          <div className="text-[10px] text-slate-400 uppercase mb-1">Bed Pressure</div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-300">ICU</span>
              <span className={`font-mono-clinical text-sm font-bold ${bedPressure.icu_alert === "black" ? "text-red-400" : bedPressure.icu_alert === "red" ? "text-orange-400" : "text-green-400"}`}>{bedPressure.icu_occ}/{bedPressure.icu_cap}</span>
            </div>
            <div className="w-full h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full rounded-full" style={{width: `${Math.min(100, bedPressure.icu_occ/bedPressure.icu_cap*100)}%`, backgroundColor: bedPressure.icu_alert === "black" ? "#DC2626" : bedPressure.icu_alert === "red" ? "#F97316" : "#22C55E"}} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-slate-300">HDU</span>
              <span className={`font-mono-clinical text-sm font-bold ${bedPressure.hdu_alert === "black" ? "text-red-400" : "text-green-400"}`}>{bedPressure.hdu_occ}/{bedPressure.hdu_cap}</span>
            </div>
            <div className="w-full h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full rounded-full" style={{width: `${Math.min(100, bedPressure.hdu_occ/bedPressure.hdu_cap*100)}%`, backgroundColor: bedPressure.hdu_alert === "black" ? "#DC2626" : "#22C55E"}} />
            </div>
          </div>
        </div>
        {/* Staffing */}
        <div className="bg-bg-card rounded-xl border border-border p-3">
          <div className="text-[10px] text-slate-400 uppercase mb-1">ICU Staff</div>
          <div className="flex items-baseline gap-1">
            <span className="font-mono-clinical text-lg font-bold text-blue-400">{staffInfo.docs}</span>
            <span className="text-[9px] text-slate-500">doc</span>
            <span className="font-mono-clinical text-lg font-bold text-green-400 ml-1">{staffInfo.nurses}</span>
            <span className="text-[9px] text-slate-500">nurse</span>
          </div>
          <div className="text-[9px] text-slate-400 mt-1">Ratio: {staffInfo.ratio}</div>
        </div>
      </div>

      {/* Patient Grid */}
      {useSimData ? (
        <>
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-sm font-semibold text-white">ICU Patients</h3>
            <LiveBadge />
            <span className="text-xs text-slate-500">({simIcuPatients.length} patients from simulation)</span>
          </div>
          <div className="grid grid-cols-4 gap-3">
            {simIcuPatients.map((sp) => {
              const color = simSofaColor(sp.sofa_total);
              return (
                <div
                  key={sp.hadm_id}
                  className="bg-bg-card rounded-xl border border-border p-3 hover:border-slate-500 transition-all cursor-pointer group"
                  onClick={() => setSelectedSimPatient(sp)}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-white">#{sp.subject_id}</span>
                    <span className="text-[10px] text-slate-500">{sp.department}</span>
                  </div>

                  <div className="flex items-center gap-3 mb-2">
                    <div
                      className="w-12 h-12 rounded-full flex items-center justify-center text-base font-bold shrink-0 transition-shadow"
                      style={{
                        backgroundColor: `${color}20`,
                        color,
                        border: `2px solid ${color}`,
                        boxShadow: sp.sofa_total >= 10 ? `0 0 12px ${color}40` : "none",
                      }}
                    >
                      {sp.sofa_total}
                    </div>
                    <div className="flex-1">
                      <div className="text-[10px] text-slate-400 mb-0.5">{sp.risk_level}</div>
                      <div
                        className="inline-block text-[10px] px-1.5 py-0.5 rounded font-medium"
                        style={{ backgroundColor: `${color}15`, color }}
                      >
                        SOFA: {sp.sofa_total}
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-4 gap-1 text-center">
                    <div>
                      <div className={`font-mono-clinical text-[11px] font-bold ${(sp.vitals?.hr ?? 80) > 100 || (sp.vitals?.hr ?? 80) < 60 ? "text-red-400" : "text-slate-300"}`}>
                        {sp.vitals?.hr != null ? Math.round(sp.vitals.hr) : "—"}
                      </div>
                      <div className="text-[8px] text-slate-500">HR</div>
                    </div>
                    <div>
                      <div className={`font-mono-clinical text-[11px] font-bold ${(sp.vitals?.spo2 ?? 98) < 94 ? "text-red-400" : "text-slate-300"}`}>
                        {sp.vitals?.spo2 != null ? Math.round(sp.vitals.spo2) : "—"}
                      </div>
                      <div className="text-[8px] text-slate-500">SpO2</div>
                    </div>
                    <div>
                      <div className={`font-mono-clinical text-[11px] font-bold ${(sp.vitals?.sbp ?? 120) < 90 ? "text-red-400" : "text-slate-300"}`}>
                        {sp.vitals?.sbp != null ? Math.round(sp.vitals.sbp) : "—"}
                      </div>
                      <div className="text-[8px] text-slate-500">SBP</div>
                    </div>
                    <div>
                      <div className={`font-mono-clinical text-[11px] font-bold ${(sp.vitals?.temp ?? 37) > 38.3 ? "text-red-400" : "text-slate-300"}`}>
                        {sp.vitals?.temp != null ? sp.vitals.temp.toFixed(1) : "—"}
                      </div>
                      <div className="text-[8px] text-slate-500">Temp</div>
                    </div>
                  </div>

                  {sp.alerts.some((a) => a.severity === "critical") && (
                    <div className="mt-2 flex items-center gap-1 text-[10px] text-red-400">
                      <AlertTriangle className="w-3 h-3" />
                      {sp.alerts.filter((a) => a.severity === "critical").length} critical alert(s)
                    </div>
                  )}

                  <div className="mt-2 text-center opacity-0 group-hover:opacity-100 transition-opacity">
                    <span className="text-[10px] text-blue-400">Click for details</span>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <div className="bg-bg-card rounded-xl border border-border p-8 text-center">
          <Users className="w-8 h-8 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-500 text-sm">No ICU patients. Start the simulation to see live data.</p>
        </div>
      )}

      {/* ════════ Interactive SOFA Score Calculator ════════ */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h3 className="text-sm font-semibold text-white mb-3">Interactive SOFA Score Calculator</h3>

        {/* Preset buttons */}
        <div className="flex items-center gap-2 mb-4">
          <span className="text-xs text-slate-400">Presets:</span>
          {Object.entries(SOFA_PRESETS).map(([label, preset]) => (
            <button
              key={label}
              onClick={() => setSofaInputs(preset)}
              className="px-2.5 py-1 rounded text-xs bg-bg-primary text-slate-300 border border-border hover:text-white hover:border-slate-500 transition-colors"
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-6">
          {/* Left: Input fields */}
          <div className="space-y-3">
            {([
              { key: "spo2" as const, label: "SpO2 (%)", min: 50, max: 100, step: 1 },
              { key: "platelets" as const, label: "Platelets (K/uL)", min: 0, max: 500, step: 5 },
              { key: "bilirubin" as const, label: "Bilirubin (mg/dL)", min: 0, max: 20, step: 0.1 },
              { key: "meanBp" as const, label: "Mean BP (mmHg)", min: 30, max: 130, step: 1 },
              { key: "creatinine" as const, label: "Creatinine (mg/dL)", min: 0, max: 10, step: 0.1 },
              { key: "gcs" as const, label: "GCS (CNS)", min: 3, max: 15, step: 1 },
            ]).map(({ key, label, min, max, step }) => {
              const sliderId = `sofa-${key}`;
              return (
                <div key={key} className="flex items-center gap-3">
                  <label
                    htmlFor={sliderId}
                    className="text-xs text-slate-400 w-36 shrink-0"
                  >
                    {label}
                  </label>
                  <input
                    id={sliderId}
                    type="range"
                    min={min}
                    max={max}
                    step={step}
                    value={sofaInputs[key]}
                    aria-label={label}
                    aria-valuetext={String(sofaInputs[key])}
                    onChange={(e) => setSofaInputs((prev) => ({ ...prev, [key]: parseFloat(e.target.value) }))}
                    className="flex-1 accent-blue-500 h-1.5"
                  />
                  <span className="font-mono-clinical text-sm text-white w-14 text-right">
                    {typeof sofaInputs[key] === "number" && sofaInputs[key] % 1 !== 0
                      ? sofaInputs[key].toFixed(1)
                      : sofaInputs[key]}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Right: Component scores + total */}
          <div className="space-y-3">
            {([
              { label: "Respiratory", score: sofaScores.resp, color: "#3B82F6" },
              { label: "Coagulation", score: sofaScores.coag, color: "#8B5CF6" },
              { label: "Liver", score: sofaScores.liver, color: "#F97316" },
              { label: "Cardiovascular", score: sofaScores.cardio, color: "#DC2626" },
              { label: "Renal", score: sofaScores.renal, color: "#14B8A6" },
              { label: "CNS (GCS)", score: sofaScores.cns, color: "#EC4899" },
            ]).map(({ label, score, color }) => (
              <div key={label} className="flex items-center gap-2">
                <span className="text-xs text-slate-400 w-28 shrink-0">{label}</span>
                <div className="flex-1 flex gap-0.5">
                  {[0, 1, 2, 3].map((seg) => (
                    <div
                      key={seg}
                      className="flex-1 h-5 rounded-sm transition-colors"
                      style={{
                        backgroundColor: seg < score ? color : "var(--color-segment-empty)",
                        border: "1px solid var(--color-segment-border)",
                      }}
                    />
                  ))}
                </div>
                <span className="font-mono-clinical text-sm text-white w-6 text-right">{score}</span>
              </div>
            ))}

            {/* Total SOFA */}
            <div className="mt-2 rounded-lg p-3 text-center" style={{ backgroundColor: `${totalSofaColor(sofaScores.total)}15`, border: `1px solid ${totalSofaColor(sofaScores.total)}40` }}>
              <div className="text-xs text-slate-400 mb-1">Total SOFA Score</div>
              <div className="font-mono-clinical text-4xl font-bold" style={{ color: totalSofaColor(sofaScores.total) }}>
                {sofaScores.total}
              </div>
              <div className="text-xs font-medium mt-1" style={{ color: totalSofaColor(sofaScores.total) }}>
                {totalSofaLabel(sofaScores.total)}
              </div>
            </div>
          </div>
        </div>
        <p className="mt-3 text-[11px] text-slate-500 leading-snug">
          Cardiovascular sub-score is MAP-only — this calculator does not account for vasopressor doses
          (dopamine, noradrenaline) which would normally lift the cardio component to 2–4 in shock states.
          Treat the total as a lower-bound screening number, not a clinical SOFA.
        </p>
      </div>

      {/* ════════ Model Performance Cards ════════ */}
      <div>
        <h3 className="text-sm font-semibold text-white mb-3">Trained Model Performance</h3>
        <div className="grid grid-cols-2 gap-4">
          {/* LightGBM */}
          <div className="bg-bg-card rounded-xl border border-border p-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-xs font-semibold text-white">LightGBM</h4>
              <span className="text-[10px] text-slate-500 bg-bg-primary px-2 py-0.5 rounded">Gradient Boosting</span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-bg-primary rounded-lg p-3 text-center">
                <div className="text-[10px] text-slate-400 mb-1">AUROC</div>
                <div className="font-mono-clinical text-xl font-bold text-blue-400">0.994</div>
              </div>
              <div className="bg-bg-primary rounded-lg p-3 text-center">
                <div className="text-[10px] text-slate-400 mb-1">Sensitivity</div>
                <div className="font-mono-clinical text-xl font-bold text-green-400">1.000</div>
                <div className="text-[9px] text-slate-500">@95% Spec</div>
              </div>
              <div className="bg-bg-primary rounded-lg p-3 text-center">
                <div className="text-[10px] text-slate-400 mb-1">Accuracy</div>
                <div className="font-mono-clinical text-xl font-bold text-purple-400">0.988</div>
              </div>
            </div>
          </div>

          {/* LSTM-Attention */}
          <div className="bg-bg-card rounded-xl border border-border p-4 relative">
            <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
              <div className="flex items-center gap-2 min-w-0">
                <h4 className="text-xs font-semibold text-white truncate">LSTM-Attention</h4>
                {/* Best Model badge — kept inline with the title row so
                    it never overlaps the subtitle pill on the right. */}
                <span className="inline-flex items-center gap-1 bg-yellow-500/15 border border-yellow-500/30 text-yellow-400 text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0">
                  <Award className="w-3 h-3" aria-hidden="true" /> Best Model
                </span>
              </div>
              <span className="text-[10px] text-slate-500 bg-bg-primary px-2 py-0.5 rounded shrink-0">Deep Sequence</span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-bg-primary rounded-lg p-3 text-center">
                <div className="text-[10px] text-slate-400 mb-1">AUROC</div>
                <div className="font-mono-clinical text-xl font-bold text-blue-400">0.998</div>
              </div>
              <div className="bg-bg-primary rounded-lg p-3 text-center">
                <div className="text-[10px] text-slate-400 mb-1">Sensitivity</div>
                <div className="font-mono-clinical text-xl font-bold text-green-400">0.882</div>
              </div>
              <div className="bg-bg-primary rounded-lg p-3 text-center">
                <div className="text-[10px] text-slate-400 mb-1">Specificity</div>
                <div className="font-mono-clinical text-xl font-bold text-purple-400">0.997</div>
              </div>
            </div>
          </div>
        </div>
      </div>
      {/* Live screens from Rule 3 cascade + admission Step 7 */}
      <SepsisRecentScreensPanel />
    </div>
  );
}
