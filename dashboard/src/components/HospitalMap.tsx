import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { getDeptColor, ACUITY_COLORS } from "../lib/colors";

interface MapPatient {
  hadm_id: string;
  subject_id: number;
  department: string;
  acuity: number;
  bed?: string;
  status?: string;
}

interface DeptCapacity {
  department: string;
  capacity: number;
  occupied: number;
  occupancy_rate: number;
  alert_level: string;
}

interface HospitalMapProps {
  selectedHadm: string | null;
  onSelect: (hadm: string) => void;
  /** hadm → severity (e.g. "critical") for pulse halos on dots. */
  patientSeverity?: Record<string, string>;
  /** Recent transfers keyed by hadm — when a transfer fires, briefly halo the destination dept. */
  recentTransfers?: Record<string, { from: string | null; to: string; at: number }>;
}

// /api/sim/ed-board has a 10s server cache + asyncio lock (thundering-herd
// guard). At ~280 patients + 1M chartevents the underlying build is 4-5s,
// so polling slightly under TTL keeps every poll a cache hit and avoids
// the AbortController-cancelling-in-flight pattern that produced 499s.
const POLL_MS = 8000;

const SEVERITY_PULSE: Record<string, string> = {
  critical: "pulse-critical",
  high: "animate-pulse-border",
};

export default function HospitalMap({
  selectedHadm,
  onSelect,
  patientSeverity = {},
  recentTransfers = {},
}: HospitalMapProps) {
  const [patients, setPatients] = useState<MapPatient[]>([]);
  const [capacity, setCapacity] = useState<Record<string, DeptCapacity>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const aborter = useRef<AbortController | null>(null);

  // Dept → most-recent transfer.at — pulse the dept group when a new
  // transfer dropped a patient there within the last ~1.5s.
  const recentDeptPing = useMemo(() => {
    const out: Record<string, number> = {};
    const now = Date.now();
    for (const t of Object.values(recentTransfers)) {
      if (!t.to) continue;
      if (now - t.at < 1500 && (out[t.to] ?? 0) < t.at) out[t.to] = t.at;
    }
    return out;
  }, [recentTransfers]);

  const load = useCallback(async () => {
    aborter.current?.abort();
    const ctrl = new AbortController();
    aborter.current = ctrl;
    try {
      const [boardRes, bedsRes] = await Promise.allSettled([
        // lite=1 skips the chartevents aggregation (heavy at 1M+ rows);
        // the map doesn't render vitals so it's free perf.
        fetch("/api/sim/ed-board?lite=1", { signal: ctrl.signal }).then((r) => r.json()),
        fetch("/api/beds/beds/summary", { signal: ctrl.signal }).then((r) => r.json()),
      ]);
      if (boardRes.status === "fulfilled") {
        const ps: MapPatient[] = (boardRes.value.patients || []).map((p: Record<string, unknown>) => ({
          hadm_id: String(p.hadm_id ?? ""),
          subject_id: Number(p.subject_id ?? 0),
          department: String(p.department ?? "Unknown"),
          acuity: Number(p.acuity ?? 3),
          bed: p.bed as string | undefined,
          status: p.status as string | undefined,
        }));
        setPatients(ps);
      }
      if (bedsRes.status === "fulfilled" && bedsRes.value?.data) {
        const cap: Record<string, DeptCapacity> = {};
        for (const d of bedsRes.value.data as DeptCapacity[]) {
          cap[d.department] = d;
        }
        setCapacity(cap);
      }
      setError(null);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      window.clearInterval(id);
      aborter.current?.abort();
    };
  }, [load]);

  const byDept = useMemo(() => {
    const m = new Map<string, MapPatient[]>();
    for (const p of patients) {
      const arr = m.get(p.department) ?? [];
      arr.push(p);
      m.set(p.department, arr);
    }
    return Array.from(m.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [patients]);

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4 h-full min-h-[600px] flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-text-primary">Hospital Map</h2>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-text-muted">
            {patients.length} active · {byDept.length} depts
          </span>
          {loading && <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />}
        </div>
      </div>

      {error && (
        <div className="text-[11px] text-red-400 mb-2">Map load failed: {error}</div>
      )}

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {byDept.map(([dept, ps]) => (
          <DeptRow
            key={dept}
            dept={dept}
            patients={ps}
            capacity={capacity[dept]}
            selectedHadm={selectedHadm}
            onSelect={onSelect}
            patientSeverity={patientSeverity}
            recentPingAt={recentDeptPing[dept]}
          />
        ))}
        {!loading && byDept.length === 0 && (
          <div className="text-[11px] text-text-muted italic py-4 text-center">
            No active patients — start the simulation from the Simulation page.
          </div>
        )}
      </div>

      <div className="mt-3 pt-2 border-t border-border flex items-center gap-3 text-[10px] text-text-muted flex-wrap">
        <span>Acuity</span>
        {ACUITY_COLORS.map((c, i) => (
          <span key={i} className="inline-flex items-center gap-1">
            <span className="w-2 h-2 rounded-full" style={{ backgroundColor: c }} />
            ESI-{i + 1}
          </span>
        ))}
      </div>
    </div>
  );
}

function DeptRow({
  dept,
  patients,
  capacity,
  selectedHadm,
  onSelect,
  patientSeverity,
  recentPingAt,
}: {
  dept: string;
  patients: MapPatient[];
  capacity?: DeptCapacity;
  selectedHadm: string | null;
  onSelect: (hadm: string) => void;
  patientSeverity: Record<string, string>;
  recentPingAt?: number;
}) {
  const deptColor = getDeptColor(dept);
  const occPct = capacity
    ? Math.round((capacity.occupied / Math.max(1, capacity.capacity)) * 100)
    : null;
  const occColor =
    occPct == null
      ? "var(--color-border)"
      : occPct >= 95
        ? "#DC2626"
        : occPct >= 85
          ? "#F97316"
          : occPct >= 75
            ? "#EAB308"
            : "#22C55E";

  // Trigger fade-in when a transfer arrives.
  const pinging = !!recentPingAt && Date.now() - recentPingAt < 1500;

  return (
    <div
      className={`rounded-lg transition-all ${pinging ? "animate-pulse-border" : ""}`}
      style={pinging ? { boxShadow: `0 0 0 1px ${deptColor}55, 0 0 12px ${deptColor}44` } : undefined}
    >
      <div className="flex items-center gap-2 mb-1">
        <span
          className="w-2 h-2 rounded-sm"
          style={{ backgroundColor: deptColor }}
          aria-hidden
        />
        <span className="text-[11px] font-medium text-text-primary">{dept}</span>
        <span className="text-[10px] text-text-muted">
          ({patients.length}
          {capacity ? `/${capacity.capacity}` : ""})
        </span>
        {occPct != null && (
          <div
            className="flex-1 h-1 ml-1 rounded-full bg-bg-input overflow-hidden"
            role="meter"
            aria-valuenow={occPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${dept} occupancy ${occPct}%`}
          >
            <div
              className="h-full transition-all duration-500"
              style={{ width: `${Math.min(100, occPct)}%`, backgroundColor: occColor }}
            />
          </div>
        )}
        {occPct != null && (
          <span
            className="text-[10px] font-mono-clinical tabular-nums"
            style={{ color: occColor }}
          >
            {occPct}%
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 pl-4">
        {patients.map((p) => {
          const isSelected = p.hadm_id === selectedHadm;
          const sevClass = SEVERITY_PULSE[patientSeverity[p.hadm_id] ?? ""] ?? "";
          const acuityColor = ACUITY_COLORS[Math.max(0, Math.min(4, p.acuity - 1))];
          return (
            <button
              key={p.hadm_id}
              onClick={() => onSelect(p.hadm_id)}
              title={`${p.hadm_id} · acuity ${p.acuity}${p.bed ? ` · ${p.bed}` : ""}`}
              aria-label={`Patient ${p.hadm_id} in ${dept}, ESI ${p.acuity}`}
              className={`w-3.5 h-3.5 rounded-full transition-all duration-200 ${sevClass} ${
                isSelected ? "ring-2 ring-offset-1 ring-blue-500 scale-125" : "hover:scale-125"
              }`}
              style={{ backgroundColor: acuityColor }}
            />
          );
        })}
      </div>
    </div>
  );
}
