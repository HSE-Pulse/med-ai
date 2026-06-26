import { useState, useEffect, useCallback } from "react";
import {
  Database, Users, BedDouble, Clock, Calendar,
} from "lucide-react";
import { EWTDPanel, RegionCensusPanel, ERPActivityLogPanel, ERPDepartmentEditor } from "../components/UpliftWidgets";

/* ---------- types ---------- */
interface StaffRoleCount {
  consultant: number; registrar: number; sho: number; intern: number;
  cnm: number; staff_nurse: number; hca: number;
  total_doctors: number; total_nurses: number; total: number;
}
interface DeptConfig {
  name: string; full_name: string; type: string; capacity: number;
  bed_types: Record<string, number>; isolation_beds: number;
  los: { median_h: number; mean_h: number; p90_h: number };
  cleaning_minutes: number;
}
interface DeptStaff {
  department: string; nurse_patient_ratio: string; doctor_patient_ratio: string;
  day_shift: StaffRoleCount; night_shift: StaffRoleCount; weekend_day: StaffRoleCount;
}
interface BedCfg {
  bed_id: string; department: string; bed_type: string;
  is_isolation: boolean; cleaning_minutes: number;
}
interface HospitalCfg {
  name: string; total_beds: number; pet_target_hours: number;
  nedocs_thresholds: Record<string, number>;
  mts_categories: Record<string, { name: string; color: string; target_minutes: number }>;
  alert_levels: string[]; ewtd_max_weekly_hours: number;
}

const TYPE_COLORS: Record<string, string> = {
  emergency: "bg-red-500/15 text-red-400 border-red-500/30",
  assessment: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  observation: "bg-cyan-500/15 text-cyan-400 border-cyan-500/30",
  inpatient: "bg-green-500/15 text-green-400 border-green-500/30",
  critical: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  high_dependency: "bg-purple-500/15 text-purple-400 border-purple-500/30",
  day_case: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
  discharge: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

const BED_COLORS: Record<string, string> = {
  standard: "#3B82F6", isolation: "#EAB308", monitored: "#22C55E",
  trolley: "#6B7280", resuscitation: "#DC2626",
};

export default function HospitalERP() {
  const [depts, setDepts] = useState<DeptConfig[]>([]);
  const [staff, setStaff] = useState<Record<string, DeptStaff>>({});
  const [beds, setBeds] = useState<BedCfg[]>([]);
  const [config, setConfig] = useState<HospitalCfg | null>(null);
  const [selDept, setSelDept] = useState("ED");
  const [selBedDept, setSelBedDept] = useState("ED");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Safely fetch + parse JSON: an empty body or a 5xx upstream returns
  // null instead of throwing, so a single offline endpoint can't take
  // down the whole page (or surface a raw "Unexpected end of JSON input"
  // error to the operator).
  const safeFetchJson = async (url: string): Promise<any | null> => {
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      const text = await res.text();
      if (!text) return null;
      try {
        return JSON.parse(text);
      } catch {
        return null;
      }
    } catch {
      return null;
    }
  };

  const load = useCallback(async () => {
    try {
      const [d, s, b, c] = await Promise.all([
        safeFetchJson("/api/erp/departments"),
        safeFetchJson("/api/erp/staff"),
        safeFetchJson("/api/erp/beds"),
        safeFetchJson("/api/erp/config"),
      ]);
      if (d?.status === "ok") setDepts(d.data || []);
      if (s?.status === "ok") setStaff(s.data || {});
      if (b?.status === "ok") setBeds(b.data || []);
      if (c?.status === "ok") setConfig(c.data);
      const downCount = [d, s, b, c].filter((x) => x === null).length;
      if (downCount > 0 && downCount === 4) {
        setError("ERP service is unavailable. Live master data cannot be loaded.");
      } else if (downCount > 0) {
        setError("Some ERP endpoints are unavailable — partial data shown below.");
      } else {
        setError(null);
      }
    } catch {
      setError("ERP service is unavailable. Live master data cannot be loaded.");
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const selectedStaff = staff[selDept];
  const deptBeds = beds.filter(b => b.department === selBedDept);

  if (loading) return (
    <div className="space-y-4">
      {[1,2,3].map(i => <div key={i} className="skeleton h-24 w-full rounded-xl" />)}
    </div>
  );

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-2 rounded-lg text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300 ml-4">&times;</button>
        </div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 text-slate-400 mb-2"><Database className="w-4 h-4" /><span className="text-xs uppercase">Hospital</span></div>
          <div className="font-mono-clinical text-xl font-bold text-white">{config?.name || "HSE Model Hospital"}</div>
          <div className="text-[10px] text-slate-400 mt-1">EWTD: {config?.ewtd_max_weekly_hours}h/week max</div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 text-slate-400 mb-2"><BedDouble className="w-4 h-4" /><span className="text-xs uppercase">Total Beds</span></div>
          <div className="font-mono-clinical text-2xl font-bold text-blue-400">{config?.total_beds || beds.length}</div>
          <div className="text-[10px] text-slate-400 mt-1">{depts.length} departments</div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 text-slate-400 mb-2"><Users className="w-4 h-4" /><span className="text-xs uppercase">Total Staff (Day)</span></div>
          <div className="font-mono-clinical text-2xl font-bold text-green-400">
            {Object.values(staff).reduce((s, d) => s + d.day_shift.total, 0)}
          </div>
          <div className="text-[10px] text-slate-400 mt-1">
            {Object.values(staff).reduce((s, d) => s + d.day_shift.total_doctors, 0)} doctors, {Object.values(staff).reduce((s, d) => s + d.day_shift.total_nurses, 0)} nurses
          </div>
        </div>
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 text-slate-400 mb-2"><Clock className="w-4 h-4" /><span className="text-xs uppercase">PET Target</span></div>
          <div className="font-mono-clinical text-2xl font-bold text-purple-400">{config?.pet_target_hours || 6}h</div>
          <div className="text-[10px] text-slate-400 mt-1">Irish HSE standard</div>
        </div>
      </div>

      {/* Department Overview Table */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h2 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
          <Database className="w-4 h-4 text-blue-400" /> Department Configuration (14 Irish HSE Departments)
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-slate-400 font-medium py-2 px-2">Department</th>
                <th className="text-left text-slate-400 font-medium py-2 px-2">Type</th>
                <th className="text-right text-slate-400 font-medium py-2 px-2">Beds</th>
                <th className="text-right text-slate-400 font-medium py-2 px-2">Isolation</th>
                <th className="text-right text-slate-400 font-medium py-2 px-2">LOS Median</th>
                <th className="text-right text-slate-400 font-medium py-2 px-2">LOS Mean</th>
                <th className="text-right text-slate-400 font-medium py-2 px-2">LOS P90</th>
                <th className="text-right text-slate-400 font-medium py-2 px-2">Clean (min)</th>
              </tr>
            </thead>
            <tbody>
              {depts.map(d => (
                <tr key={d.name} className="border-b border-border/30 hover:bg-slate-800/30">
                  <td className="py-2 px-2 text-white font-medium">{d.full_name}</td>
                  <td className="py-2 px-2">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded border ${TYPE_COLORS[d.type] || "text-slate-400"}`}>
                      {d.type.replace("_", " ")}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right font-mono-clinical text-white">{d.capacity}</td>
                  <td className="py-2 px-2 text-right font-mono-clinical text-yellow-400">{d.isolation_beds}</td>
                  <td className="py-2 px-2 text-right font-mono-clinical text-slate-300">{d.los.median_h}h</td>
                  <td className="py-2 px-2 text-right font-mono-clinical text-slate-400">{d.los.mean_h}h</td>
                  <td className="py-2 px-2 text-right font-mono-clinical text-slate-500">{d.los.p90_h}h</td>
                  <td className="py-2 px-2 text-right font-mono-clinical text-slate-400">{d.cleaning_minutes}</td>
                </tr>
              ))}
              <tr className="border-t border-border font-semibold">
                <td className="py-2 px-2 text-white">TOTAL</td>
                <td className="py-2 px-2" />
                <td className="py-2 px-2 text-right font-mono-clinical text-blue-400">{depts.reduce((s, d) => s + d.capacity, 0)}</td>
                <td className="py-2 px-2 text-right font-mono-clinical text-yellow-400">{depts.reduce((s, d) => s + d.isolation_beds, 0)}</td>
                <td colSpan={3} />
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Staff Roster + Bed Config */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Staff Roster */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              <Users className="w-4 h-4 text-green-400" /> Staff Roster
            </h2>
            <select value={selDept} onChange={e => setSelDept(e.target.value)}
              className="bg-bg-input border border-border rounded px-2 py-1 text-[11px] text-text-primary focus:border-blue-500 focus:outline-none">
              {depts.map(d => <option key={d.name} value={d.name}>{d.name}</option>)}
            </select>
          </div>
          {selectedStaff ? (
            <div>
              <div className="flex gap-2 mb-3 text-[10px]">
                <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/30">
                  Nurse ratio: {selectedStaff.nurse_patient_ratio}
                </span>
                <span className="px-2 py-0.5 rounded bg-green-500/10 text-green-400 border border-green-500/30">
                  Doctor ratio: {selectedStaff.doctor_patient_ratio}
                </span>
              </div>
              <table className="w-full text-[11px] border-collapse">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left text-slate-400 font-medium py-1.5 px-2">Role</th>
                    <th className="text-right text-slate-400 font-medium py-1.5 px-2">Day</th>
                    <th className="text-right text-slate-400 font-medium py-1.5 px-2">Night</th>
                    <th className="text-right text-slate-400 font-medium py-1.5 px-2">Weekend</th>
                  </tr>
                </thead>
                <tbody>
                  {(["consultant", "registrar", "sho", "intern", "cnm", "staff_nurse", "hca"] as const).map(role => (
                    <tr key={role} className="border-b border-border/30">
                      <td className="py-1.5 px-2 text-white capitalize">{role.replace("_", " ")}</td>
                      <td className="py-1.5 px-2 text-right font-mono-clinical text-blue-400">{selectedStaff.day_shift[role]}</td>
                      <td className="py-1.5 px-2 text-right font-mono-clinical text-purple-400">{selectedStaff.night_shift[role]}</td>
                      <td className="py-1.5 px-2 text-right font-mono-clinical text-orange-400">{selectedStaff.weekend_day[role]}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-border font-semibold">
                    <td className="py-1.5 px-2 text-white">Total</td>
                    <td className="py-1.5 px-2 text-right font-mono-clinical text-blue-400">{selectedStaff.day_shift.total}</td>
                    <td className="py-1.5 px-2 text-right font-mono-clinical text-purple-400">{selectedStaff.night_shift.total}</td>
                    <td className="py-1.5 px-2 text-right font-mono-clinical text-orange-400">{selectedStaff.weekend_day.total}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : <div className="text-slate-500 text-[11px] py-4 text-center">Select a department</div>}
        </div>

        {/* Bed Configuration */}
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              <BedDouble className="w-4 h-4 text-blue-400" /> Bed Configuration
            </h2>
            <select value={selBedDept} onChange={e => setSelBedDept(e.target.value)}
              className="bg-bg-input border border-border rounded px-2 py-1 text-[11px] text-text-primary focus:border-blue-500 focus:outline-none">
              {depts.map(d => <option key={d.name} value={d.name}>{d.name} ({d.capacity})</option>)}
            </select>
          </div>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {Object.entries(BED_COLORS).map(([type, color]) => {
              const count = deptBeds.filter(b => b.bed_type === type).length;
              if (count === 0) return null;
              return (
                <span key={type} className="text-[9px] px-2 py-0.5 rounded-full border border-slate-700 flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                  {type}: {count}
                </span>
              );
            })}
            {deptBeds.filter(b => b.is_isolation).length > 0 && (
              <span className="text-[9px] px-2 py-0.5 rounded-full border border-yellow-500/30 text-yellow-400">
                Isolation: {deptBeds.filter(b => b.is_isolation).length}
              </span>
            )}
          </div>
          <div className="grid grid-cols-6 gap-1 max-h-[280px] overflow-y-auto">
            {deptBeds.map(b => (
              <div key={b.bed_id}
                className={`text-[9px] font-mono-clinical px-1.5 py-1 rounded text-center border ${
                  b.is_isolation ? "border-yellow-500/50 bg-yellow-500/10 text-yellow-400" : "border-slate-700 bg-slate-800/40 text-slate-300"
                }`}
                title={`${b.bed_id} | ${b.bed_type}${b.is_isolation ? " | isolation" : ""}`}
              >
                <div style={{ borderLeft: `3px solid ${BED_COLORS[b.bed_type] || "#6B7280"}`, paddingLeft: 4 }}>
                  {b.bed_id.split("-")[1]}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Schedule Overview */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h2 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
          <Calendar className="w-4 h-4 text-purple-400" /> Shift Schedule Overview (Irish 12h Pattern)
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-slate-400 font-medium py-2 px-2">Department</th>
                <th className="text-center text-slate-400 font-medium py-2 px-2" colSpan={2}>Day Shift (07:00-19:00)</th>
                <th className="text-center text-slate-400 font-medium py-2 px-2" colSpan={2}>Night Shift (19:00-07:00)</th>
                <th className="text-center text-slate-400 font-medium py-2 px-2">Pattern</th>
              </tr>
              <tr className="border-b border-border/50">
                <th />
                <th className="text-right text-[9px] text-slate-500 px-2">Doctors</th>
                <th className="text-right text-[9px] text-slate-500 px-2">Nurses</th>
                <th className="text-right text-[9px] text-slate-500 px-2">Doctors</th>
                <th className="text-right text-[9px] text-slate-500 px-2">Nurses</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {Object.entries(staff).map(([dept, s]) => (
                <tr key={dept} className="border-b border-border/30 hover:bg-slate-800/30">
                  <td className="py-1.5 px-2 text-white font-medium">{dept}</td>
                  <td className="py-1.5 px-2 text-right font-mono-clinical text-blue-400">{s.day_shift.total_doctors}</td>
                  <td className="py-1.5 px-2 text-right font-mono-clinical text-blue-300">{s.day_shift.total_nurses}</td>
                  <td className="py-1.5 px-2 text-right font-mono-clinical text-purple-400">{s.night_shift.total_doctors}</td>
                  <td className="py-1.5 px-2 text-right font-mono-clinical text-purple-300">{s.night_shift.total_nurses}</td>
                  <td className="py-1.5 px-2 text-center text-[9px] text-slate-400">12h</td>
                </tr>
              ))}
              <tr className="border-t border-border font-semibold">
                <td className="py-1.5 px-2 text-white">TOTAL</td>
                <td className="py-1.5 px-2 text-right font-mono-clinical text-blue-400">
                  {Object.values(staff).reduce((s, d) => s + d.day_shift.total_doctors, 0)}
                </td>
                <td className="py-1.5 px-2 text-right font-mono-clinical text-blue-300">
                  {Object.values(staff).reduce((s, d) => s + d.day_shift.total_nurses, 0)}
                </td>
                <td className="py-1.5 px-2 text-right font-mono-clinical text-purple-400">
                  {Object.values(staff).reduce((s, d) => s + d.night_shift.total_doctors, 0)}
                </td>
                <td className="py-1.5 px-2 text-right font-mono-clinical text-purple-300">
                  {Object.values(staff).reduce((s, d) => s + d.night_shift.total_nurses, 0)}
                </td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Uplift — EWTD compliance, HSE regions, activity log, runtime PATCH */}
      <EWTDPanel />
      <RegionCensusPanel />
      <ERPActivityLogPanel />
      <ERPDepartmentEditor />
    </div>
  );
}
