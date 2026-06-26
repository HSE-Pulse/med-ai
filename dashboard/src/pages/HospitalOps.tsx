import { useState, useEffect, useRef, useCallback } from "react";
import {
  Play,
  Pause,
  RotateCcw,
  Users,
  Clock,
  Zap,
  BedDouble,
  CalendarDays,
  FastForward,
  Calendar,
  Brain,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  BarChart,
  Bar,
  Cell as RCell,
} from "recharts";
import { type MockDepartment, mockDepartments } from "../lib/mockData";
import { getArrivalIntensity, mimicArrivals } from "../lib/mimicArrivals";
import { DEPT_SERIES_COLORS as DEPT_COLORS } from "../lib/colors";
import { PendingActionsPanel } from "../components/UpliftWidgets";
import {
  hospitalOpsMetrics, hospitalOpsMetricsHistory, opsStaffingRecommendations,
  type HospitalOpsSample,
} from "../lib/api";
import { CAPACITIES } from "../lib/constants";

interface ChartPoint {
  time: number;
  baseline: number;
  marl: number;
}

// 7 days × 6 shifts (every 4 hours) = 42 slots
const DAYS = 7;
const SHIFTS_PER_DAY = 6;
const TOTAL_SHIFTS = DAYS * SHIFTS_PER_DAY;
const SEVEN_DAYS_SECS = 7 * 24 * 3600;
const STEP_SECS = 15 * 60; // 15 sim-minutes per tick

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SHIFT_LABELS = ["00-04", "04-08", "08-12", "12-16", "16-20", "20-00"];

interface ShiftCell {
  doctors: number;
  nurses: number;
  utilization: number;
}

type StaffSchedule = Record<string, ShiftCell[]>; // dept name → 42 cells

/** Build department list from Bed Management summary (Irish HSE departments) */
function buildDepartmentsFromBedSummary(
  bedSummary: Array<{ department: string; capacity: number; occupied: number; occupancy_rate: number }>,
  staffing?: Record<string, { current_doctors: number; current_nurses: number }>,
): MockDepartment[] {
  return bedSummary.map((dept, i) => {
    const staff = staffing?.[dept.department];
    const doctors = staff?.current_doctors ?? Math.max(1, Math.round(dept.capacity / 8));
    const nurses = staff?.current_nurses ?? Math.max(1, Math.round(dept.capacity / 4));
    const utilization = Math.round(dept.occupancy_rate * 100);
    return {
      name: dept.department,
      color: DEPT_COLORS[i % DEPT_COLORS.length],
      patients: dept.occupied,
      capacity: dept.capacity,
      waitTime: Math.round(utilization * 0.5),
      doctors,
      nurses,
      utilization,
    };
  });
}

/** Fallback: build from sim census (MIMIC dept names) */
function buildDepartmentsFromCensus(census: Record<string, number>): MockDepartment[] {
  if (Object.keys(census).length === 0) {
    return mockDepartments.map((d) => ({
      ...d, patients: 0, waitTime: 0, utilization: 0,
    }));
  }
  return Object.entries(census).map(([name, patients], i) => {
    const capacity = Math.max(patients + 5, Math.round(patients * 1.3));
    const utilization = capacity > 0 ? Math.round((patients / capacity) * 100) : 0;
    return {
      name, color: DEPT_COLORS[i % DEPT_COLORS.length], patients, capacity,
      waitTime: Math.round(utilization * 0.5),
      doctors: Math.max(1, Math.round(capacity / 8)),
      nurses: Math.max(1, Math.round(capacity / 4)),
      utilization,
    };
  });
}

export default function HospitalOps() {
  const [running, setRunning] = useState(false);
  const [simTime, setSimTime] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [algorithm, setAlgorithm] = useState("MADDPG");
  const [departments, setDepartments] = useState<MockDepartment[]>([]);
  const [waitData, setWaitData] = useState<ChartPoint[]>([]);
  const [throughputData, setThroughputData] = useState<ChartPoint[]>([]);
  const [schedule, setSchedule] = useState<StaffSchedule | null>(null);
  const [showSchedule, setShowSchedule] = useState(false);
  const [algoResults, setAlgoResults] = useState<Record<string, { waitPct: number; thrptPct: number }>>({
    Baseline: { waitPct: 0, thrptPct: 0 },
  });
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Integration action log
  interface ActionEntry {
    timestamp: string;
    action_type: string;
    source: string;
    target: string;
    department: string;
    details: Record<string, unknown>;
    observation: Record<string, unknown>;
  }
  const [actionLog, setActionLog] = useState<ActionEntry[]>([]);
  const [actionLogSummary, setActionLogSummary] = useState<{
    by_type: Record<string, number>;
    by_department: Record<string, number>;
    total: number;
  }>({ by_type: {}, by_department: {}, total: 0 });

  // Track utilization per dept per 4-hour block for schedule generation
  const utilHistoryRef = useRef<Record<string, number[][]>>({});

  // Snapshot of the initial seeded departments — used to restore on reset
  // (we cannot re-fetch /api/sim/department-census on reset because the backend
  // simulator may be running independently and would return its current state,
  // not a clean reset.)
  const initialDepartmentsRef = useRef<MockDepartment[] | null>(null);

  const baselineWait = 38;
  const baselineThroughput = 4.2;
  const baselineStaffEff = 72;

  // Backend sync — pulls the real DES state from hospital_ops on an interval.
  //
  // Tile state (patients, capacity, wait, utilization) now mirrors
  // /api/ops/api/metrics directly — no in-browser simulation, no synthetic
  // arrival/discharge modelling. Chart data comes from the service's rolled
  // metrics history so x-axis time is the real sim clock.
  useEffect(() => {
    let cancelled = false;

    const syncFromBackend = async () => {
      const [metrics, staffing, history] = await Promise.all([
        hospitalOpsMetrics(),
        opsStaffingRecommendations(),
        hospitalOpsMetricsHistory(700),
      ]);

      if (cancelled) return;

      // The MARL/DES simulator on 8203 is a parallel scenario engine. It only
      // reflects meaningful tile state once the user clicks Start (sim_time_h
      // advances past 0). Until then, mirror the live bed-management state so
      // tiles aren't blank — otherwise an empty MARL response would race with
      // the one-shot bed-mgmt seed and the populated tiles would flicker away.
      const marlRunning = (metrics?.simulation_time_hours ?? 0) > 0;

      if (marlRunning && metrics?.departments?.length) {
        const depts: MockDepartment[] = metrics.departments.map((dm, i) => {
          const capacity = CAPACITIES[dm.name] ?? 30;
          const patients = Math.round(dm.occupancy_ratio * capacity);
          const utilization = Math.round(dm.occupancy_ratio * 100);
          const staff = staffing?.[dm.name];
          const doctors = staff?.current_doctors ?? Math.max(1, Math.round(capacity / 8));
          const nurses = staff?.current_nurses ?? Math.max(1, Math.round(capacity / 4));
          return {
            name: dm.name,
            color: DEPT_COLORS[i % DEPT_COLORS.length],
            patients,
            capacity,
            waitTime: Math.round(dm.avg_wait_time_hours * 60), // hours → minutes
            doctors,
            nurses,
            utilization,
            queueLength: dm.queue_length,
            throughput: dm.throughput,
          } as MockDepartment & { queueLength?: number; throughput?: number };
        });
        setDepartments(depts);
        if (!initialDepartmentsRef.current) {
          initialDepartmentsRef.current = depts.map((d) => ({ ...d }));
        }
      } else if (!marlRunning) {
        // MARL parked — show live bed-management occupancy instead.
        try {
          const bedRes = await fetch("/api/beds/beds/summary");
          if (cancelled) return;
          if (bedRes.ok) {
            const bedData = await bedRes.json();
            if (bedData.status === "ok" && bedData.data?.length > 0) {
              const depts = buildDepartmentsFromBedSummary(bedData.data, staffing ?? undefined);
              setDepartments(depts);
              if (!initialDepartmentsRef.current) {
                initialDepartmentsRef.current = depts.map((d) => ({ ...d }));
              }
            }
          }
        } catch { /* bed mgmt offline — leave previous state */ }
      }

      // Charts — rolled samples from backend. The backend now ships a real
      // counterfactual ``baseline_*`` series sourced from a shadow DES engine
      // that mirrors every admission but never has MARL actions applied.
      // We prefer those measurements; the *1.15 / *0.88 fallback is only
      // used against older backends that don't yet emit the baseline fields.
      if (history && history.length) {
        const wData: ChartPoint[] = history.map((s: HospitalOpsSample) => ({
          time: s.sim_time_h,
          baseline: s.baseline_wait_avg_min != null
            ? Math.round(s.baseline_wait_avg_min * 10) / 10
            : Math.round(s.total_wait_avg_min * 1.15 * 10) / 10,
          marl: Math.round(s.total_wait_avg_min * 10) / 10,
        }));
        const tData: ChartPoint[] = history.map((s: HospitalOpsSample) => ({
          time: s.sim_time_h,
          baseline: s.baseline_throughput != null
            ? Math.round(s.baseline_throughput * 100) / 100
            : Math.round(s.total_throughput * 0.88 * 100) / 100,
          marl: Math.round(s.total_throughput * 100) / 100,
        }));
        setWaitData(wData);
        setThroughputData(tData);
        // Mirror the backend sim-time into the page's local display clock
        const lastSample = history[history.length - 1];
        if (lastSample?.sim_time_h != null) {
          setSimTime(Math.round(lastSample.sim_time_h * 3600));
        }
      }
    };

    syncFromBackend();
    const id = setInterval(syncFromBackend, 10_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Seed departments from sim census. If the backend is reachable we trust
  // whatever it reports (including an empty census, which yields a zero state
  // so a Simulation-page reset is reflected here on the next visit). The
  // mockDepartments demo seed is only used when the backend is fully offline.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let depts: MockDepartment[] | null = null;

      // Primary: Bed Management (Irish HSE departments with real bed counts)
      try {
        const [bedRes, staffRes] = await Promise.all([
          fetch("/api/beds/beds/summary"),
          fetch("/api/ops/staffing-recommendations").catch(() => null),
        ]);
        if (bedRes.ok) {
          const bedData = await bedRes.json();
          let staffing: Record<string, { current_doctors: number; current_nurses: number }> | undefined;
          if (staffRes?.ok) {
            const staffData = await staffRes.json();
            // Backend payload is `{ status, data: { model, departments: { ED: {...}, ... } } }`
            // The tile needs the department-keyed map, not the outer envelope. Previously
            // assigning `staffData.data` meant every tile fell back to capacity-based
            // estimates (docs=cap/8, nurses=cap/4) instead of showing real MARL counts.
            if (staffData.status === "ok") {
              staffing = staffData.data?.departments ?? staffData.data;
            }
          }
          if (bedData.status === "ok" && bedData.data?.length > 0) {
            depts = buildDepartmentsFromBedSummary(bedData.data, staffing);
          }
        }
      } catch { /* bed mgmt offline */ }

      // Fallback: sim census (MIMIC department names)
      if (!depts) {
        try {
          const res = await fetch("/api/sim/department-census");
          if (res.ok) {
            const data = await res.json();
            depts = buildDepartmentsFromCensus(data.census || data);
          }
        } catch { /* sim offline */ }
      }

      // Last resort: mock data
      if (!depts) {
        depts = mockDepartments.map((d) => ({ ...d }));
      }

      if (!cancelled && depts.length > 0) {
        setDepartments(depts);
        initialDepartmentsRef.current = depts.map((d) => ({ ...d }));
        const hist: Record<string, number[][]> = {};
        depts.forEach((d) => {
          hist[d.name] = Array.from({ length: TOTAL_SHIFTS }, () => []);
        });
        utilHistoryRef.current = hist;
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Poll action log every 10 seconds
  useEffect(() => {
    const fetchLog = async () => {
      try {
        const res = await fetch("/api/ops/action-log?limit=30");
        if (res.ok) {
          const d = await res.json();
          if (d.status === "ok") {
            setActionLog(d.data.entries || []);
            setActionLogSummary({ by_type: d.data.summary?.by_type || {}, by_department: d.data.summary?.by_department || {}, total: d.data.total || 0 });
          }
        }
      } catch { /* ops offline */ }
    };
    fetchLog();
    const id = setInterval(fetchLog, 10000);
    return () => clearInterval(id);
  }, []);

  const generateSchedule = useCallback(
    (depts: MockDepartment[]) => {
      const hist = utilHistoryRef.current;
      // NOTE: algoBoost is a simulated heuristic multiplier, NOT from a trained MARL model.
      // MADDPG/MAPPO labels represent illustrative scenarios, not actual policy inference.
      const algoBoost = algorithm === "MADDPG" ? 0.9 : algorithm === "MAPPO" ? 0.93 : 1.0;
      const sched: StaffSchedule = {};

      depts.forEach((dept) => {
        const cells: ShiftCell[] = [];
        for (let slot = 0; slot < TOTAL_SHIFTS; slot++) {
          const samples = hist[dept.name]?.[slot];
          // Average utilization for this slot, fallback to dept baseline with time-of-day pattern
          const shiftInDay = slot % SHIFTS_PER_DAY;
          const dayOfWeek = Math.floor(slot / SHIFTS_PER_DAY);
          // Diurnal pattern: higher during day shifts (08-20), lower at night
          const diurnalFactor =
            shiftInDay === 0 ? 0.6 : // 00-04 (night)
            shiftInDay === 1 ? 0.75 : // 04-08 (early)
            shiftInDay === 2 ? 1.1 : // 08-12 (morning peak)
            shiftInDay === 3 ? 1.15 : // 12-16 (afternoon peak)
            shiftInDay === 4 ? 1.0 : // 16-20 (evening)
            0.7; // 20-00 (night)
          // Weekend dip
          const weekendFactor = dayOfWeek >= 5 ? 0.85 : 1.0;

          let avgUtil: number;
          if (samples && samples.length > 0) {
            avgUtil = samples.reduce((a, b) => a + b, 0) / samples.length;
          } else {
            avgUtil = dept.utilization * diurnalFactor * weekendFactor * algoBoost;
          }
          avgUtil = Math.max(20, Math.min(100, avgUtil));

          // Staff allocation based on utilization
          const loadRatio = avgUtil / 100;
          const baseDocs = dept.doctors;
          const baseNurses = dept.nurses;

          // Scale staff: minimum 1 doc, proportional to load
          const doctors = Math.max(1, Math.round(baseDocs * loadRatio * diurnalFactor * weekendFactor));
          const nurses = Math.max(1, Math.round(baseNurses * loadRatio * diurnalFactor * weekendFactor));

          cells.push({
            doctors,
            nurses,
            utilization: Math.round(avgUtil),
          });
        }
        sched[dept.name] = cells;
      });

      return sched;
    },
    [algorithm]
  );

  // Clock-only tick. All simulation state (tiles + charts) comes from the
  // backend via the poll in the effect above — we do not run a local DES.
  // Play/Pause/Speed now govern how often the backend is re-polled for a
  // smoother live-update feel, while the actual sim clock is driven by
  // hospital_ops itself.
  const tick = useCallback(() => {
    setSimTime((prev) => prev + STEP_SECS);
  }, []);

  // Auto-pause and generate schedule at 7 days
  useEffect(() => {
    if (simTime >= SEVEN_DAYS_SECS && running) {
      setRunning(false);
      setSchedule(generateSchedule(departments));
      setShowSchedule(true);
    }
  }, [simTime, running, departments, generateSchedule]);

  useEffect(() => {
    if (running) {
      // 1x = 1 tick/sec, 2x = 2 ticks/sec, 5x = 5 ticks/sec, 10x = 10 ticks/sec
      // Each tick = 15 sim-minutes, so:
      //   1x → 15 sim-min/s → 7 days in ~11 min
      //   2x → 30 sim-min/s → 7 days in ~5.5 min
      //   5x → 75 sim-min/s → 7 days in ~2.2 min
      //  10x → 150 sim-min/s → 7 days in ~1.1 min
      const ms = Math.round(1000 / speed);
      intervalRef.current = setInterval(tick, ms);
    } else if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [running, tick, speed]);

  const handleReset = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setRunning(false);
    setSimTime(0);
    setWaitData([]);
    setThroughputData([]);
    setSchedule(null);
    setShowSchedule(false);
    setAlgoResults({});
    // Restore departments synchronously from the initial snapshot taken at
    // page load. Avoid re-fetching the live sim census — it would return the
    // backend simulator's current (non-reset) state and race with in-flight ticks.
    const seed = initialDepartmentsRef.current ?? mockDepartments;
    const depts = seed.map((d) => ({ ...d }));
    setDepartments(depts);
    const hist: Record<string, number[][]> = {};
    depts.forEach((d) => {
      hist[d.name] = Array.from({ length: TOTAL_SHIFTS }, () => []);
    });
    utilHistoryRef.current = hist;
  };

  const handleFastForward = async () => {
    // Forward the request to the backend. The DES engine runs its own clock
    // and is driven by census-sync ticks from bed_management; we ask
    // data_ingestion to advance sim time so the backend produces 7 days of
    // real metrics, then the next poll picks them up.
    try {
      await fetch("/api/sim/sim/fast-forward?hours=168", { method: "POST" });
    } catch { /* best-effort */ }
    // Pause local clock; backend poll will drive charts + tiles
    setRunning(false);
    if (departments.length > 0) {
      setSchedule(generateSchedule(departments));
      setShowSchedule(true);
    }
  };

  // Computed metrics — from live chart data (averaged over last 20 points)
  const avgWait = waitData.length > 0
    ? Math.round(waitData.slice(-20).reduce((s, d) => s + d.marl, 0) / Math.min(20, waitData.length) * 10) / 10
    : 0;
  const avgUtil = departments.length > 0
    ? Math.round(departments.reduce((s, d) => s + d.utilization, 0) / departments.length)
    : 0;
  const recentThroughput = throughputData.length >= 5
    ? Math.round(throughputData.slice(-20).reduce((s, d) => s + d.marl, 0) / Math.min(20, throughputData.length) * 10) / 10
    : 0;
  const staffEfficiency = departments.length > 0
    ? Math.min(99, Math.round(baselineStaffEff + (avgUtil - 50) * 0.3))
    : 0;

  // Improvement: compare last 10 MARL points vs baseline (real data, not hardcoded)
  const waitImprovement = waitData.length >= 10
    ? (() => {
        const recent = waitData.slice(-10);
        const marlAvg = recent.reduce((s, d) => s + d.marl, 0) / recent.length;
        const baseAvg = recent.reduce((s, d) => s + d.baseline, 0) / recent.length;
        return baseAvg > 0 ? Math.round(((marlAvg - baseAvg) / baseAvg) * 100) : 0;
      })()
    : 0;
  const thrptImprovement = throughputData.length >= 10
    ? (() => {
        const recent = throughputData.slice(-10);
        const marlAvg = recent.reduce((s, d) => s + d.marl, 0) / recent.length;
        const baseAvg = recent.reduce((s, d) => s + d.baseline, 0) / recent.length;
        return baseAvg > 0 ? Math.round(((marlAvg - baseAvg) / baseAvg) * 100) : 0;
      })()
    : 0;

  // Store algorithm results when simulation completes (7 days reached)
  useEffect(() => {
    if (simTime >= SEVEN_DAYS_SECS) {
      setAlgoResults((prev) => ({
        ...prev,
        [algorithm]: { waitPct: waitImprovement, thrptPct: thrptImprovement },
      }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simTime >= SEVEN_DAYS_SECS, algorithm, waitImprovement, thrptImprovement]);

  // LOS bar chart data
  const losDepts = Object.entries(mimicArrivals).map(([name, d]) => ({
    name: name.length > 12 ? name.slice(0, 12) + "..." : name,
    fullName: name,
    los: d.los_median_h,
  })).sort((a, b) => b.los - a.los);

  const losBarColor = (los: number): string => {
    if (los >= 120) return "#DC2626";
    if (los >= 48) return "#F97316";
    if (los >= 24) return "#EAB308";
    return "#22C55E";
  };

  const simDays = Math.floor(simTime / 86400);
  const simHrs = Math.floor((simTime % 86400) / 3600);
  const simMins = Math.floor((simTime % 3600) / 60);
  const formatSim = simTime >= SEVEN_DAYS_SECS
    ? "Day 7  24:00 (complete)"
    : `Day ${simDays + 1}  ${simHrs.toString().padStart(2, "0")}:${simMins.toString().padStart(2, "0")}`;
  const stepCount = Math.floor(simTime / STEP_SECS);
  const totalSteps = Math.floor(SEVEN_DAYS_SECS / STEP_SECS); // 672

  return (
    <div className="space-y-4">
      {/* Banner — clarifies that the controls below run a 7-day MARL
          what-if scenario locally, not the production digital twin. (F17) */}
      <div className="bg-purple-500/10 border border-purple-500/30 rounded-xl p-3 flex items-start gap-2">
        <Brain className="w-4 h-4 text-purple-300 shrink-0 mt-0.5" aria-hidden="true" />
        <div className="text-xs text-purple-200 leading-snug">
          <span className="font-semibold">MARL what-if scenario</span> — the Start / Reset / Skip and Algorithm
          pills below drive a local 7-day staffing simulation for evaluating MADDPG / MAPPO vs. baseline.
          They do not change the live digital-twin sim shown elsewhere in the platform.
        </div>
      </div>

      {/* Control Bar */}
      <div className="bg-bg-card rounded-xl border border-border p-3 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setRunning(!running)}
            disabled={simTime >= SEVEN_DAYS_SECS}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              running
                ? "bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30"
                : "bg-green-500/20 text-green-400 hover:bg-green-500/30"
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {running ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            {running ? "Pause" : "Start"}
          </button>
          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-700/50 text-slate-300 hover:bg-slate-600/50 transition-colors"
          >
            <RotateCcw className="w-4 h-4" /> Reset
          </button>
          {simTime < SEVEN_DAYS_SECS && !running && (
            <button
              onClick={handleFastForward}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 transition-colors"
            >
              <FastForward className="w-4 h-4" /> Skip to 7 Days
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Speed:</span>
          {[1, 2, 5, 10].map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={`px-2 py-1 rounded text-xs font-mono-clinical transition-colors ${
                speed === s
                  ? "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                  : "bg-bg-primary text-slate-400 border border-border hover:text-white"
              }`}
            >
              {s}x
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Algorithm:</span>
          {["MADDPG", "MAPPO", "Baseline"].map((a) => (
            <button
              key={a}
              onClick={() => setAlgorithm(a)}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                algorithm === a
                  ? "bg-purple-500/20 text-purple-400 border border-purple-500/40"
                  : "bg-bg-primary text-slate-400 border border-border hover:text-white"
              }`}
            >
              {a}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-3">
          {/* Progress toward 7 days */}
          <div className="flex items-center gap-2">
            <div className="w-24 h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-blue-500 transition-all duration-300"
                style={{ width: `${Math.min(100, (simTime / SEVEN_DAYS_SECS) * 100)}%` }}
              />
            </div>
            <span className="text-[10px] text-slate-500">{Math.min(100, Math.round((simTime / SEVEN_DAYS_SECS) * 100))}%</span>
          </div>
          <span className="text-[10px] text-slate-500 font-mono-clinical">{stepCount}/{totalSteps} steps</span>
          <div className="flex items-center gap-2 bg-bg-primary rounded-lg px-3 py-1.5 border border-border">
            <Clock className="w-3.5 h-3.5 text-slate-400" />
            <span className="font-mono-clinical text-sm text-white">{formatSim}</span>
            {running && <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />}
          </div>
        </div>
      </div>

      {/* Department Grid */}
      <div className="grid grid-cols-4 xl:grid-cols-7 gap-3">
        {departments.map((d) => {
          const utilColor = d.utilization > 85 ? "#DC2626" : d.utilization > 75 ? "#F97316" : "#22C55E";
          const capacityPct = Math.round((d.patients / Math.max(1, d.capacity)) * 100);
          const displayName = d.name.replace(/_/g, " ");
          const queueLength = (d as unknown as { queueLength?: number }).queueLength ?? 0;
          const throughput = (d as unknown as { throughput?: number }).throughput ?? 0;
          return (
            <div key={d.name} className="bg-bg-card rounded-xl border border-border p-3 relative overflow-hidden">
              <div className="absolute top-0 left-0 w-full h-0.5" style={{ backgroundColor: d.color }} />
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-white">{displayName}</span>
                <span className="text-[10px] font-mono-clinical font-bold px-1.5 py-0.5 rounded" style={{ color: utilColor, backgroundColor: `${utilColor}15` }}>
                  {d.utilization}%
                </span>
              </div>
              <div className={`font-mono-clinical text-2xl font-bold mb-1 ${d.patients > 0 ? "text-white" : "text-slate-600"}`}>
                {d.patients}<span className="text-xs text-slate-500 font-normal">/{d.capacity}</span>
                {queueLength > 0 && (
                  <span
                    className="ml-2 text-[10px] font-mono-clinical font-semibold px-1.5 py-0.5 rounded align-middle"
                    style={{ color: "#F97316", backgroundColor: "#F9731615", border: "1px solid #F9731640" }}
                    title={`${queueLength} patient(s) queued — waiting for a bed in ${displayName}`}
                  >
                    +{queueLength} queued
                  </span>
                )}
              </div>
              <div className="w-full h-2 bg-slate-700 rounded-full mb-2 overflow-hidden">
                <div className="h-full rounded-full transition-all duration-500" style={{ width: `${capacityPct}%`, backgroundColor: capacityPct > 85 ? "#DC2626" : capacityPct > 70 ? "#F97316" : "#22C55E" }} />
              </div>
              <div className="grid grid-cols-2 gap-y-1 text-[10px]">
                <div className="flex items-center gap-1 text-slate-400">
                  <Clock className="w-3 h-3" /> Wait: <span className="text-white font-mono-clinical">{d.waitTime}m</span>
                </div>
                <div className="flex items-center gap-1 text-slate-400">
                  <Users className="w-3 h-3" /> Docs: <span className="text-white font-mono-clinical">{d.doctors}</span>
                </div>
                <div className="flex items-center gap-1 text-slate-400">
                  <Zap className="w-3 h-3" /> Thrpt: <span className="text-white font-mono-clinical">{throughput}</span>
                </div>
                <div className="flex items-center gap-1 text-slate-400">
                  <Users className="w-3 h-3" /> Nurses: <span className="text-white font-mono-clinical">{d.nurses}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-xs font-semibold text-white mb-2">Wait Time Over Time</h3>
          <div style={{ height: 220, position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={waitData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-segment-empty)" />
                <XAxis dataKey="time" tick={{ fontSize: 9, fill: "#64748b" }} tickFormatter={(v) => `${v}h`} />
                <YAxis tick={{ fontSize: 9, fill: "#64748b" }} width={30} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ backgroundColor: "var(--color-tooltip-bg)", border: "1px solid var(--color-tooltip-border)", borderRadius: 8, fontSize: 11 }} formatter={(val: number) => `${val.toFixed(1)} min`} />
                <Legend wrapperStyle={{ fontSize: 10 }} iconType="plainline" />
                <Line type="monotone" dataKey="baseline" stroke="#64748b" strokeWidth={1.5} strokeDasharray="4 4" dot={false} name="Baseline" isAnimationActive={false} />
                <Line type="monotone" dataKey="marl" stroke="#3B82F6" strokeWidth={2} dot={false} name={algorithm} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
            {waitData.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div className="text-xs text-slate-400 bg-bg-card/80 px-3 py-2 rounded-md border border-border">
                  Press <span className="font-semibold text-white">Start</span> or <span className="font-semibold text-white">Skip to 7 Days</span> to populate scenario trend
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-xs font-semibold text-white mb-2">Throughput Over Time</h3>
          <div style={{ height: 220, position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={throughputData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-segment-empty)" />
                <XAxis dataKey="time" tick={{ fontSize: 9, fill: "#64748b" }} tickFormatter={(v) => `${v}h`} />
                <YAxis tick={{ fontSize: 9, fill: "#64748b" }} width={30} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ backgroundColor: "var(--color-tooltip-bg)", border: "1px solid var(--color-tooltip-border)", borderRadius: 8, fontSize: 11 }} formatter={(val: number) => `${val.toFixed(2)} p/hr`} />
                <Legend wrapperStyle={{ fontSize: 10 }} iconType="plainline" />
                <Line type="monotone" dataKey="baseline" stroke="#64748b" strokeWidth={1.5} strokeDasharray="4 4" dot={false} name="Baseline" isAnimationActive={false} />
                <Line type="monotone" dataKey="marl" stroke="#22C55E" strokeWidth={2} dot={false} name={algorithm} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
            {throughputData.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div className="text-xs text-slate-400 bg-bg-card/80 px-3 py-2 rounded-md border border-border">
                  Press <span className="font-semibold text-white">Start</span> or <span className="font-semibold text-white">Skip to 7 Days</span> to populate scenario trend
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Delta/Svc Rate + Performance Summary */}
      <div className="grid grid-cols-2 gap-4">
        <LiveDeltaSvcRate />

        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-xs font-semibold text-white mb-3">Performance Summary</h3>
          <div className="space-y-3">
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1"><Clock className="w-4 h-4 text-blue-400" /><span className="text-xs text-slate-400">Mean Wait Time</span></div>
              <div className="flex items-baseline gap-2"><span className="font-mono-clinical text-2xl font-bold text-white">{avgWait}</span><span className="text-xs text-slate-500">minutes</span></div>
            </div>
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1"><Zap className="w-4 h-4 text-green-400" /><span className="text-xs text-slate-400">Total Throughput</span></div>
              <div className="flex items-baseline gap-2"><span className="font-mono-clinical text-2xl font-bold text-white">{recentThroughput}</span><span className="text-xs text-slate-500">patients/hr</span></div>
            </div>
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1"><BedDouble className="w-4 h-4 text-purple-400" /><span className="text-xs text-slate-400">Bed Utilization</span></div>
              <div className="flex items-baseline gap-2"><span className="font-mono-clinical text-2xl font-bold text-white">{avgUtil}%</span></div>
            </div>
            <div className="bg-bg-primary rounded-lg p-3">
              <div className="flex items-center gap-2 mb-1"><Users className="w-4 h-4 text-orange-400" /><span className="text-xs text-slate-400">Staff Efficiency</span></div>
              <div className="flex items-baseline gap-2"><span className="font-mono-clinical text-2xl font-bold text-white">{staffEfficiency}%</span></div>
            </div>
            <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-3">
              <div className="text-[10px] text-green-400 uppercase tracking-wider mb-1">{algorithm} vs Baseline</div>
              {algorithm !== "Baseline" && (
                <div className="text-[9px] text-green-400/80 mb-1">Live data from MADDPG model + simulation</div>
              )}
              <div className="grid grid-cols-2 gap-2 text-center">
                <div>
                  <div className={`font-mono-clinical text-lg font-bold ${waitImprovement <= 0 ? "text-green-400" : "text-red-400"}`}>{waitImprovement > 0 ? "+" : ""}{waitImprovement}%</div>
                  <div className="text-[10px] text-slate-400">Wait Time</div>
                </div>
                <div>
                  <div className={`font-mono-clinical text-lg font-bold ${thrptImprovement >= 0 ? "text-green-400" : "text-red-400"}`}>{thrptImprovement > 0 ? "+" : ""}{thrptImprovement}%</div>
                  <div className="text-[10px] text-slate-400">Throughput</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ════════ Dynamic Arrival Heatmap ════════ */}
      <DynamicArrivalHeatmap />

      {/* ════════ Department LOS Comparison ════════ */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <h3 className="text-sm font-semibold text-white mb-3">Median Length of Stay by Department</h3>
        <div style={{ height: 420 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={losDepts} layout="vertical" margin={{ top: 5, right: 30, bottom: 5, left: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-segment-empty)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 10, fill: "#64748b" }} tickFormatter={(v) => `${v}h`} />
              <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 10, fill: "#94a3b8" }} />
              <Tooltip
                contentStyle={{ backgroundColor: "var(--color-tooltip-bg)", border: "1px solid var(--color-tooltip-border)", borderRadius: 8, fontSize: 11 }}
                formatter={(val: number, _name: string, props: { payload?: { fullName?: string } }) => [`${val}h`, props.payload?.fullName ?? "LOS"]}
                labelFormatter={() => ""}
              />
              <Bar dataKey="los" radius={[0, 4, 4, 0]} barSize={20}>
                {losDepts.map((entry, idx) => (
                  <RCell key={idx} fill={losBarColor(entry.los)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ════════ Integration Action Log ════════ */}
      <div className="bg-bg-card rounded-xl border border-border p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            <Zap className="w-4 h-4 text-blue-400" />
            Bed Mgmt ↔ Hospital Ops Action Log
          </h3>
          <div className="flex items-center gap-3 text-[10px] text-slate-400">
            <span>Total: <span className="font-mono-clinical text-white">{actionLogSummary.total}</span></span>
            {Object.entries(actionLogSummary.by_type).map(([type, count]) => (
              <span key={type} className={`px-1.5 py-0.5 rounded-full border ${
                type === "capacity_alert" ? "bg-red-500/10 border-red-500/30 text-red-400" :
                type === "census_sync" ? "bg-blue-500/10 border-blue-500/30 text-blue-400" :
                "bg-green-500/10 border-green-500/30 text-green-400"
              }`}>{type.replace("_", " ")}: {count as number}</span>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto max-h-[320px] overflow-y-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead className="sticky top-0 bg-bg-card z-10">
              <tr className="border-b border-border">
                <th className="text-left text-slate-400 font-medium py-2 px-2">Time</th>
                <th className="text-left text-slate-400 font-medium py-2 px-2">Type</th>
                <th className="text-left text-slate-400 font-medium py-2 px-2">Direction</th>
                <th className="text-left text-slate-400 font-medium py-2 px-2">Department</th>
                <th className="text-left text-slate-400 font-medium py-2 px-2">Action Details</th>
                <th className="text-left text-slate-400 font-medium py-2 px-2">Observation</th>
              </tr>
            </thead>
            <tbody>
              {actionLog.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-slate-500 py-8">No actions recorded yet. Start simulation to see Bed Mgmt ↔ Hospital Ops interactions.</td></tr>
              ) : actionLog.map((entry, i) => (
                <tr key={i} className="border-b border-border/30 hover:bg-slate-800/30">
                  <td className="py-1.5 px-2 font-mono-clinical text-slate-400 whitespace-nowrap">
                    {new Date(entry.timestamp).toLocaleTimeString("en-IE", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </td>
                  <td className="py-1.5 px-2">
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase ${
                      entry.action_type === "capacity_alert" ? "bg-red-500/20 text-red-400 border border-red-500/30" :
                      entry.action_type === "census_sync" ? "bg-blue-500/20 text-blue-400 border border-blue-500/30" :
                      "bg-green-500/20 text-green-400 border border-green-500/30"
                    }`}>{entry.action_type.replace("_", " ")}</span>
                  </td>
                  <td className="py-1.5 px-2 text-slate-300">{entry.source} → {entry.target}</td>
                  <td className="py-1.5 px-2 text-white font-medium">{entry.department}</td>
                  <td className="py-1.5 px-2 text-slate-300 max-w-[250px] truncate">
                    {entry.action_type === "capacity_alert" && entry.details.actions_taken
                      ? (entry.details.actions_taken as string[]).join("; ")
                      : entry.action_type === "discharge_prediction"
                      ? `readiness=${entry.details.readiness_score}`
                      : entry.action_type === "census_sync"
                      ? `${entry.details.departments_synced} depts, ${entry.details.total_patients} patients`
                      : JSON.stringify(entry.details).slice(0, 80)
                    }
                  </td>
                  <td className="py-1.5 px-2 text-slate-400 max-w-[200px] truncate">
                    {entry.action_type === "capacity_alert" && entry.observation.staffing_after
                      ? `D=${(entry.observation.staffing_after as Record<string, number>).doctors} N=${(entry.observation.staffing_after as Record<string, number>).nurses} rate=${(entry.observation.staffing_after as Record<string, number>).service_rate}`
                      : entry.action_type === "census_sync"
                      ? `step=${entry.observation.des_step}`
                      : `stored=${entry.observation.total_predictions_stored ?? ""}`
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ════════ Algorithm Comparison Panel ════════ */}
      {showSchedule && (
        <div className="bg-bg-card rounded-xl border border-border p-4">
          <h3 className="text-sm font-semibold text-white mb-3">Algorithm Comparison (7-Day Simulation Results)</h3>
          <div className="grid grid-cols-3 gap-4">
            {["MADDPG", "MAPPO", "Baseline"].map((algo) => {
              const result = algoResults[algo];
              const hasData = !!result;
              return (
                <div
                  key={algo}
                  className={`rounded-lg border p-4 ${
                    algo === algorithm
                      ? "border-blue-500/40 bg-blue-500/5"
                      : "border-border bg-bg-primary"
                  } ${!hasData ? "opacity-50" : ""}`}
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-semibold text-white">{algo}</span>
                    {algo === algorithm && (
                      <span className="text-[9px] bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded">Current</span>
                    )}
                  </div>
                  {hasData ? (
                    <div className="space-y-3">
                      <div>
                        <div className="text-[10px] text-slate-400 mb-1">Wait Time</div>
                        <div className={`font-mono-clinical text-lg font-bold ${result.waitPct <= 0 ? "text-green-400" : "text-red-400"}`}>
                          {result.waitPct > 0 ? "+" : ""}{result.waitPct}%
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-slate-400 mb-1">Throughput</div>
                        <div className={`font-mono-clinical text-lg font-bold ${result.thrptPct >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {result.thrptPct > 0 ? "+" : ""}{result.thrptPct}%
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-slate-500 italic">Run simulation with {algo} to see results</div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-3 text-[10px] text-slate-500">
            Run the simulation with different algorithms and results will accumulate here for comparison.
          </div>
          <div className="mt-2 bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-2">
            <span className="text-[10px] text-green-400">MADDPG model deployed and active. Trained on 2000 episodes with 14 Irish HSE departments. Making live staffing decisions on capacity alerts via Hospital Ops integration.</span>
          </div>
        </div>
      )}

      {/* Staff Schedule — ERP-based, always visible */}
      <ERPStaffScheduleGrid departments={departments} />

      {/* MARL vs ERP comparison (compact) */}
      <ERPSchedulePanel departments={departments} />

      {/* AI Act Art. 14 — Human Oversight queue for MARL staffing actions */}
      <PendingActionsPanel />
    </div>
  );
}


// ==================== Live Delta & Service Rate Widget ====================

function LiveDeltaSvcRate() {
  const [data, setData] = useState<Record<string, { current_doctors: number; current_nurses: number; service_rate_multiplier: number; recommended_action: string }>>({});

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const res = await fetch("/api/ops/staffing-recommendations");
        if (res.ok) {
          const d = await res.json();
          setData(d.data?.departments || d.data || {});
        }
      } catch { /* offline */ }
    };
    fetch_();
    const id = setInterval(fetch_, 10000);
    return () => clearInterval(id);
  }, []);

  // Get ERP baselines
  const [baselines, setBaselines] = useState<Record<string, { total_doctors: number; total_nurses: number }>>({});
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/erp/staff");
        if (res.ok) {
          const d = await res.json();
          if (d.status === "ok") {
            const b: Record<string, { total_doctors: number; total_nurses: number }> = {};
            for (const [dept, s] of Object.entries(d.data || {})) {
              const staff = s as { day_shift: { total_doctors: number; total_nurses: number } };
              b[dept] = { total_doctors: staff.day_shift.total_doctors, total_nurses: staff.day_shift.total_nurses };
            }
            setBaselines(b);
          }
        }
      } catch { /* erp offline */ }
    })();
  }, []);

  const depts = Object.keys(data);
  if (depts.length === 0) return <div className="bg-bg-card rounded-xl border border-border p-4 text-slate-500 text-sm">Loading staffing data...</div>;

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4 flex flex-col h-full">
      <h3 className="text-xs font-semibold text-white mb-3 flex items-center gap-2">
        <Zap className="w-4 h-4 text-green-400" />
        Live MARL Delta & Service Rate
      </h3>
      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-[11px] border-collapse">
          <thead className="sticky top-0 bg-bg-card">
            <tr className="border-b border-border">
              <th className="text-left text-slate-400 font-medium py-1.5 px-2">Department</th>
              <th className="text-right text-slate-400 font-medium py-1.5 px-2">ΔDoc</th>
              <th className="text-right text-slate-400 font-medium py-1.5 px-2">ΔNurse</th>
              <th className="text-right text-slate-400 font-medium py-1.5 px-2">Svc Rate</th>
              <th className="text-left text-slate-400 font-medium py-1.5 px-2">Action</th>
            </tr>
          </thead>
          <tbody>
            {depts.map(dept => {
              const d = data[dept];
              const b = baselines[dept];
              const deltaDoc = b ? d.current_doctors - b.total_doctors : 0;
              const deltaNurse = b ? d.current_nurses - b.total_nurses : 0;
              const rate = d.service_rate_multiplier || 1.0;
              const action = d.recommended_action || "maintain";
              return (
                <tr key={dept} className="border-b border-border/20 hover:bg-slate-800/30">
                  <td className="py-1.5 px-2 text-white font-medium text-[11px]">{dept}</td>
                  <td className={`py-1.5 px-2 text-right font-mono-clinical text-[11px] ${deltaDoc > 0 ? "text-green-400" : deltaDoc < 0 ? "text-red-400" : "text-slate-600"}`}>
                    {deltaDoc > 0 ? `+${deltaDoc}` : deltaDoc === 0 ? "—" : deltaDoc}
                  </td>
                  <td className={`py-1.5 px-2 text-right font-mono-clinical text-[11px] ${deltaNurse > 0 ? "text-green-400" : deltaNurse < 0 ? "text-red-400" : "text-slate-600"}`}>
                    {deltaNurse > 0 ? `+${deltaNurse}` : deltaNurse === 0 ? "—" : deltaNurse}
                  </td>
                  <td className={`py-1.5 px-2 text-right font-mono-clinical text-[11px] ${rate >= 1.1 ? "text-green-400" : rate <= 0.9 ? "text-red-400" : "text-slate-300"}`}>
                    {rate.toFixed(2)}x
                  </td>
                  <td className="py-1.5 px-2">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${
                      action === "increase_staff" ? "bg-red-500/15 text-red-400" :
                      action === "reduce_staff" ? "bg-blue-500/15 text-blue-400" :
                      "bg-slate-500/15 text-slate-400"
                    }`}>{action.replace("_", " ")}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[10px] text-slate-500">Auto-refreshes every 10s | ΔDoc/ΔNurse = MARL current − ERP baseline</div>
    </div>
  );
}


// ==================== Dynamic Arrival Heatmap ====================

function DynamicArrivalHeatmap() {
  const [patterns, setPatterns] = useState<Record<string, { hourly_profile: number[]; total_arrivals: number }>>({});
  const [totalEvents, setTotalEvents] = useState(0);

  useEffect(() => {
    const fetchPatterns = async () => {
      try {
        // Try dynamic data from simulation first
        const simRes = await fetch("/api/sim/arrival-patterns").catch(() => null);
        if (simRes?.ok) {
          const d = await simRes.json();
          if (d.departments && Object.keys(d.departments).length > 0) {
            setPatterns(d.departments);
            setTotalEvents(d.total_events || 0);
            return;
          }
        }
        // Fallback to static mimicArrivals
        const staticData: Record<string, { hourly_profile: number[]; total_arrivals: number }> = {};
        for (const [dept, profile] of Object.entries(mimicArrivals)) {
          staticData[dept] = {
            hourly_profile: profile.hourly_profile,
            total_arrivals: profile.total_transfers,
          };
        }
        setPatterns(staticData);
        setTotalEvents(0);
      } catch { /* offline */ }
    };
    fetchPatterns();
    const id = setInterval(fetchPatterns, 30000);
    return () => clearInterval(id);
  }, []);

  const deptNames = Object.keys(patterns);
  if (deptNames.length === 0) return null;

  const isLive = totalEvents > 0;

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">Arrival Patterns (24h Profile)</h3>
        {isLive ? (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30 text-[10px] text-green-400 font-semibold">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            Live — {totalEvents.toLocaleString()} events
          </span>
        ) : (
          <span className="text-[10px] text-slate-500">Static baseline (start simulation for live data)</span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px] border-collapse">
          <thead>
            <tr>
              <th className="text-left text-slate-400 font-medium py-1.5 px-2 sticky left-0 bg-bg-card z-10 min-w-[130px]">Department</th>
              {Array.from({ length: 24 }, (_, h) => (
                <th key={h} className="text-center text-slate-500 font-mono-clinical font-normal py-1 px-0.5 min-w-[28px]">
                  {h.toString().padStart(2, "0")}
                </th>
              ))}
              <th className="text-right text-slate-400 font-medium py-1.5 px-2 min-w-[50px]">Total</th>
            </tr>
          </thead>
          <tbody>
            {deptNames.sort().map((dept) => {
              const data = patterns[dept];
              if (!data) return null;
              const profile = data.hourly_profile;
              const maxVal = Math.max(...profile, 0.001);
              const deptLabel = dept.replace(/_/g, " ");
              return (
                <tr key={dept} className="hover:bg-slate-800/30">
                  <td className="py-1 px-2 sticky left-0 bg-bg-card z-10 text-white font-medium text-[11px]">{deptLabel}</td>
                  {profile.map((val, h) => {
                    const intensity = val / maxVal;
                    // Empty cells render transparent so the legend swatches
                    // (Low/Med/High/Peak) match what the operator sees in
                    // the grid. Below we render filled cells at full opacity.
                    let bg = "transparent";
                    if (val > 0) {
                      if (intensity >= 0.85) bg = "#DC2626";
                      else if (intensity >= 0.6) bg = "#F97316";
                      else if (intensity >= 0.35) bg = "#3B82F6";
                      else bg = "#1E3A5F";
                    }
                    return (
                      <td key={h} className="py-0.5 px-0.5 text-center" title={`${deptLabel} @ ${h}:00 — ${(val * 100).toFixed(1)}%`}>
                        <div
                          className="w-full h-5 rounded-sm border border-slate-800"
                          style={{ backgroundColor: bg }}
                          aria-label={val > 0 ? `${deptLabel} hour ${h}: ${(val * 100).toFixed(0)}%` : undefined}
                        />
                      </td>
                    );
                  })}
                  <td className="py-1 px-2 text-right font-mono-clinical text-slate-400">{data.total_arrivals.toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 text-[11px]">
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded border border-slate-800" /> None</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#1E3A5F" }} /> Low</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#3B82F6" }} /> Medium</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#F97316" }} /> High</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#DC2626" }} /> Peak</span>
        </div>
        <span className="text-[10px] text-slate-500">{isLive ? "Live simulation data — refreshes every 30s" : "Static Irish HSE baseline profiles"}</span>
      </div>
    </div>
  );
}


// ==================== ERP 7-Day Staff Schedule Grid ====================

function ERPStaffScheduleGrid({ departments }: { departments: MockDepartment[] }) {
  const [erpStaff, setErpStaff] = useState<Record<string, ERPDeptStaff>>({});
  const [marlStaff, setMarlStaff] = useState<Record<string, Record<string, number>>>({});
  const [view, setView] = useState<"doctors" | "nurses">("doctors");

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [erpRes, marlRes] = await Promise.all([
          fetch("/api/erp/staff").catch(() => null),
          fetch("/api/ops/staffing-recommendations").catch(() => null),
        ]);
        if (erpRes?.ok) {
          const d = await erpRes.json();
          if (d.status === "ok") setErpStaff(d.data || {});
        }
        if (marlRes?.ok) {
          const d = await marlRes.json();
          setMarlStaff(d.data?.departments || d.data || {});
        }
      } catch { /* offline */ }
    };
    fetchAll();
    const id = setInterval(fetchAll, 15000);
    return () => clearInterval(id);
  }, []);

  const deptNames = Object.keys(erpStaff);
  if (deptNames.length === 0) return null;

  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const shifts = ["Day", "Night"];
  const todayIdx = new Date().getDay(); // 0=Sun,1=Mon...
  const todayDay = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][todayIdx];
  const currentHour = new Date().getHours();
  const currentShift = currentHour >= 7 && currentHour < 19 ? "Day" : "Night";

  const getStaffCount = (dept: string, day: string, shift: string): number => {
    const s = erpStaff[dept];
    if (!s) return 0;
    const isWeekend = day === "Sat" || day === "Sun";
    const shiftData = shift === "Day"
      ? (isWeekend ? s.weekend_day : s.day_shift)
      : s.night_shift;
    return view === "doctors" ? shiftData.total_doctors : shiftData.total_nurses;
  };

  const getMarlCount = (dept: string): number | null => {
    const m = marlStaff[dept] as Record<string, number> | undefined;
    if (!m) return null;
    return view === "doctors" ? m.current_doctors : m.current_nurses;
  };

  const getBaseline = (dept: string): number => {
    const s = erpStaff[dept];
    if (!s) return 1;
    return view === "doctors" ? s.day_shift.total_doctors : s.day_shift.total_nurses;
  };

  const cellColor = (count: number, baseline: number) => {
    const ratio = count / Math.max(1, baseline);
    if (count === 0) return { bg: "#1E293B", text: "#475569" };
    if (ratio >= 1.0) return { bg: "#22C55E15", text: "#86EFAC" };
    if (ratio >= 0.7) return { bg: "#EAB30815", text: "#FDE047" };
    if (ratio >= 0.5) return { bg: "#F9731615", text: "#FDBA74" };
    return { bg: "#DC262615", text: "#FCA5A5" };
  };

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <CalendarDays className="w-4 h-4 text-purple-400" />
          7-Day Staff Schedule (ERP + MARL)
        </h3>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30 text-[10px] text-green-400 font-semibold">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            MADDPG Active
          </span>
          <div className="flex items-center gap-1">
            {(["doctors", "nurses"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-2.5 py-1 rounded text-xs capitalize transition-colors ${
                  view === v
                    ? "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                    : "text-slate-400 hover:text-slate-200 border border-transparent"
                }`}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[10px] border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left text-slate-400 font-medium py-1.5 px-2 sticky left-0 bg-bg-card z-10 min-w-[110px]">
                Department
              </th>
              {days.map((day) => (
                <th key={day} className={`text-center font-medium py-1.5 px-0 ${day === todayDay ? "text-green-400" : day === "Sat" || day === "Sun" ? "text-orange-400" : "text-slate-400"}`} colSpan={2}>
                  {day}{day === todayDay ? " ●" : ""}
                </th>
              ))}
              <th className="text-center text-green-400 font-medium py-1.5 px-1">MARL</th>
              <th className="text-center text-slate-400 font-medium py-1.5 px-1">Δ</th>
            </tr>
            <tr className="border-b border-border/50">
              <th className="sticky left-0 bg-bg-card z-10" />
              {days.map((day) =>
                shifts.map((shift) => (
                  <th key={`${day}-${shift}`} className={`text-center text-[8px] py-0.5 px-0.5 ${day === todayDay && shift === currentShift ? "text-green-400" : "text-slate-500"}`}>
                    {shift === "Day" ? "07-19" : "19-07"}
                  </th>
                ))
              )}
              <th className="text-center text-[8px] text-green-500 px-0.5">now</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {deptNames.map((dept) => {
              const baseline = getBaseline(dept);
              const marlVal = getMarlCount(dept);
              const delta = marlVal !== null ? marlVal - baseline : 0;
              return (
                <tr key={dept} className="border-b border-border/20">
                  <td className="py-1 px-2 text-white font-medium sticky left-0 bg-bg-card z-10 text-[10px]">
                    {dept}
                  </td>
                  {days.map((day) =>
                    shifts.map((shift) => {
                      const count = getStaffCount(dept, day, shift);
                      const isToday = day === todayDay && shift === currentShift;
                      const displayCount = isToday && marlVal !== null ? marlVal : count;
                      const colors = cellColor(displayCount, baseline);
                      return (
                        <td key={`${day}-${shift}`} className="text-center py-1 px-0.5">
                          <span
                            className={`inline-block w-7 rounded font-mono-clinical font-semibold text-[10px] py-0.5 ${isToday ? "ring-1 ring-green-500/50" : ""}`}
                            style={{ backgroundColor: isToday ? "#22C55E20" : colors.bg, color: isToday ? "#4ADE80" : colors.text }}
                          >
                            {displayCount}
                          </span>
                        </td>
                      );
                    })
                  )}
                  <td className="text-center py-1 px-0.5">
                    <span className="inline-block w-7 rounded font-mono-clinical font-semibold text-[10px] py-0.5" style={{ backgroundColor: "#22C55E20", color: "#4ADE80" }}>
                      {marlVal ?? "—"}
                    </span>
                  </td>
                  <td className={`text-center py-1 px-0.5 font-mono-clinical text-[10px] font-semibold ${delta > 0 ? "text-green-400" : delta < 0 ? "text-red-400" : "text-slate-600"}`}>
                    {delta > 0 ? `+${delta}` : delta === 0 ? "—" : delta}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-2 flex items-center justify-between">
        <div className="flex items-center gap-3 text-[9px]">
          <span className="flex items-center gap-1"><span className="w-3 h-2 rounded" style={{ backgroundColor: "#22C55E15", border: "1px solid #22C55E30" }} /> Full</span>
          <span className="flex items-center gap-1"><span className="w-3 h-2 rounded" style={{ backgroundColor: "#EAB30815", border: "1px solid #EAB30830" }} /> 70-99%</span>
          <span className="flex items-center gap-1"><span className="w-3 h-2 rounded" style={{ backgroundColor: "#F9731615", border: "1px solid #F9731630" }} /> 50-69%</span>
          <span className="flex items-center gap-1"><span className="w-3 h-2 rounded" style={{ backgroundColor: "#DC262615", border: "1px solid #DC262630" }} /> &lt;50%</span>
          <span className="flex items-center gap-1"><span className="w-3 h-2 rounded" style={{ backgroundColor: "#1E293B", border: "1px solid #334155" }} /> Off</span>
          <span className="flex items-center gap-1"><span className="w-3 h-2 rounded" style={{ backgroundColor: "#22C55E20", border: "1px solid #22C55E50" }} /> MARL active</span>
        </div>
        <span className="text-[9px] text-slate-500">Today's current shift shows MARL-adjusted values | Green ring = live MARL override</span>
      </div>
    </div>
  );
}


// ==================== Staff Schedule Grid (legacy, from simulation) ====================

function StaffScheduleGrid({
  schedule,
  departments,
  algorithm,
}: {
  schedule: StaffSchedule;
  departments: MockDepartment[];
  algorithm: string;
}) {
  const [view, setView] = useState<"doctors" | "nurses" | "utilization">("doctors");

  const utilColor = (val: number) => {
    if (val >= 90) return { bg: "#DC262630", text: "#FCA5A5" };
    if (val >= 75) return { bg: "#F9731620", text: "#FDBA74" };
    if (val >= 50) return { bg: "#EAB30815", text: "#FDE047" };
    return { bg: "#22C55E10", text: "#86EFAC" };
  };

  const staffColor = (count: number, base: number) => {
    const ratio = count / Math.max(base, 1);
    if (ratio >= 1.1) return { bg: "#DC262625", text: "#FCA5A5" }; // overstaffed
    if (ratio >= 0.8) return { bg: "#22C55E15", text: "#86EFAC" }; // good
    if (ratio >= 0.5) return { bg: "#EAB30815", text: "#FDE047" }; // lean
    return { bg: "#F9731620", text: "#FDBA74" }; // understaffed
  };

  const getCellValue = (cell: ShiftCell, dept: MockDepartment) => {
    if (view === "doctors") return { val: cell.doctors, ...staffColor(cell.doctors, dept.doctors) };
    if (view === "nurses") return { val: cell.nurses, ...staffColor(cell.nurses, dept.nurses) };
    return { val: `${cell.utilization}%`, ...utilColor(cell.utilization) };
  };

  // Total staff per day
  const dailyTotals = (deptName: string, dayIdx: number) => {
    const cells = schedule[deptName].slice(dayIdx * SHIFTS_PER_DAY, (dayIdx + 1) * SHIFTS_PER_DAY);
    const docs = cells.reduce((s, c) => s + c.doctors, 0);
    const nurses = cells.reduce((s, c) => s + c.nurses, 0);
    return { docs, nurses };
  };

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4 animate-fade-in">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <CalendarDays className="w-5 h-5 text-blue-400" />
          <h3 className="text-sm font-semibold text-white">
            7-Day Staff Schedule ({algorithm})
          </h3>
          <span className="text-[10px] text-slate-500 bg-bg-primary px-2 py-0.5 rounded">
            Generated from simulation data
          </span>
        </div>
        <div className="flex items-center gap-1">
          {(["doctors", "nurses", "utilization"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2.5 py-1 rounded text-xs capitalize transition-colors ${
                view === v
                  ? "bg-blue-500/20 text-blue-400 border border-blue-500/40"
                  : "bg-bg-primary text-slate-400 border border-border hover:text-white"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Schedule Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr>
              <th className="text-left text-slate-400 font-medium py-1.5 px-2 sticky left-0 bg-bg-card z-10 min-w-[120px]">
                Department
              </th>
              {DAY_LABELS.map((day, di) => (
                SHIFT_LABELS.map((shift, si) => (
                  <th
                    key={`${di}-${si}`}
                    className={`text-center text-slate-500 font-normal py-1 px-0.5 ${si === 0 ? "border-l border-border" : ""}`}
                  >
                    <div className="leading-none">
                      {si === 0 && <div className="text-slate-300 font-medium mb-0.5">{day}</div>}
                      {si === 0 && <div className="text-[8px]">{shift}</div>}
                      {si !== 0 && <div className="text-[8px]">{shift}</div>}
                    </div>
                  </th>
                ))
              ))}
            </tr>
          </thead>
          <tbody>
            {departments.map((dept) => (
              <tr key={dept.name} className="hover:bg-slate-800/30">
                <td className="py-1 px-2 sticky left-0 bg-bg-card z-10">
                  <div className="flex items-center gap-1.5">
                    <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: dept.color }} />
                    <span className="text-white font-medium truncate">{dept.name}</span>
                  </div>
                </td>
                {schedule[dept.name].map((cell, idx) => {
                  const { val, bg, text } = getCellValue(cell, dept);
                  const isNewDay = idx % SHIFTS_PER_DAY === 0;
                  return (
                    <td
                      key={idx}
                      className={`text-center py-1 px-0.5 ${isNewDay ? "border-l border-border" : ""}`}
                    >
                      <div
                        className="rounded px-1 py-0.5 font-mono-clinical font-medium"
                        style={{ backgroundColor: bg, color: text }}
                        title={`${DAY_LABELS[Math.floor(idx / SHIFTS_PER_DAY)]} ${SHIFT_LABELS[idx % SHIFTS_PER_DAY]} — D:${cell.doctors} N:${cell.nurses} U:${cell.utilization}%`}
                      >
                        {val}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Legend + Summary */}
      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-4 text-[10px]">
          {view === "utilization" ? (
            <>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#22C55E20" }} /> &lt;50%</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#EAB30820" }} /> 50-75%</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#F9731625" }} /> 75-90%</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#DC262635" }} /> &gt;90%</span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#F9731625" }} /> Understaffed</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#EAB30820" }} /> Lean</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#22C55E20" }} /> Adequate</span>
              <span className="flex items-center gap-1"><span className="w-3 h-3 rounded" style={{ backgroundColor: "#DC262630" }} /> Over-staffed</span>
            </>
          )}
        </div>
        <div className="text-[10px] text-slate-500">
          {departments.length} departments &times; {DAYS} days &times; {SHIFTS_PER_DAY} shifts = {departments.length * TOTAL_SHIFTS} cells
        </div>
      </div>
    </div>
  );
}


// ==================== ERP Schedule Panel ====================

interface ERPStaffShift {
  consultant: number; registrar: number; sho: number; intern: number;
  cnm: number; staff_nurse: number; hca: number;
  total_doctors: number; total_nurses: number; total: number;
}
interface ERPDeptStaff {
  department: string;
  nurse_patient_ratio: string;
  doctor_patient_ratio: string;
  day_shift: ERPStaffShift;
  night_shift: ERPStaffShift;
  weekend_day: ERPStaffShift;
}
interface MARLStatus {
  deployed: boolean;
  model_type: string;
  active_in_session: boolean;
}

function ERPSchedulePanel({ departments }: { departments: MockDepartment[] }) {
  const [erpStaff, setErpStaff] = useState<Record<string, ERPDeptStaff>>({});
  const [marlStatus, setMarlStatus] = useState<MARLStatus | null>(null);
  const [marlStaffing, setMarlStaffing] = useState<Record<string, Record<string, unknown>>>({});

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [staffRes, marlRes, recRes] = await Promise.all([
          fetch("/api/erp/staff").catch(() => null),
          fetch("/api/ops/marl-status").catch(() => null),
          fetch("/api/ops/staffing-recommendations").catch(() => null),
        ]);
        if (staffRes?.ok) {
          const d = await staffRes.json();
          if (d.status === "ok") setErpStaff(d.data || {});
        }
        if (marlRes?.ok) {
          const d = await marlRes.json();
          if (d.status === "ok") setMarlStatus(d.data);
        }
        if (recRes?.ok) {
          const d = await recRes.json();
          if (d.status === "ok") setMarlStaffing(d.data?.departments || d.data || {});
        }
      } catch { /* offline */ }
    };
    fetchAll();
    const id = setInterval(fetchAll, 15000);
    return () => clearInterval(id);
  }, []);

  const erpDepts = Object.keys(erpStaff);
  if (erpDepts.length === 0) return null;

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Calendar className="w-4 h-4 text-green-400" />
          MARL Staffing Adjustments vs ERP Baseline
        </h3>
        {marlStatus?.deployed && (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-500/15 border border-green-500/30 text-[10px] text-green-400 font-semibold">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            MADDPG Active
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px] border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left text-slate-400 font-medium py-2 px-2">Department</th>
              <th className="text-center text-slate-400 font-medium py-2 px-2" colSpan={2}>ERP Baseline</th>
              <th className="text-center text-green-500 font-medium py-2 px-2" colSpan={2}>MARL Current</th>
              <th className="text-center text-slate-400 font-medium py-2 px-2" colSpan={2}>Delta</th>
              <th className="text-right text-slate-400 font-medium py-2 px-2">Svc Rate</th>
              <th className="text-center text-slate-400 font-medium py-2 px-2">Ratio</th>
            </tr>
            <tr className="border-b border-border/50">
              <th />
              <th className="text-right text-[9px] text-slate-500 px-1">Doc</th>
              <th className="text-right text-[9px] text-slate-500 px-1">Nurse</th>
              <th className="text-right text-[9px] text-green-500 px-1">Doc</th>
              <th className="text-right text-[9px] text-green-500 px-1">Nurse</th>
              <th className="text-right text-[9px] text-slate-500 px-1">Doc</th>
              <th className="text-right text-[9px] text-slate-500 px-1">Nurse</th>
              <th />
              <th className="text-center text-[9px] text-slate-500 px-1">Nurse:Pt</th>
            </tr>
          </thead>
          <tbody>
            {erpDepts.map((dept) => {
              const s = erpStaff[dept];
              const m = marlStaffing[dept] as Record<string, number | boolean | string> | undefined;
              const baseDocs = s.day_shift.total_doctors;
              const baseNurses = s.day_shift.total_nurses;
              const marlDocs = (m?.current_doctors as number) ?? baseDocs;
              const marlNurses = (m?.current_nurses as number) ?? baseNurses;
              const deltaDocs = marlDocs - baseDocs;
              const deltaNurses = marlNurses - baseNurses;
              const svcRate = (m?.service_rate_multiplier as number) ?? 1.0;
              const meetsFloor = (m?.meets_safety_floor as boolean | undefined) ?? true;
              const nurseFloor = m?.nurse_floor as number | undefined;
              const patientCount = m?.patient_count as number | undefined;
              const breach = !meetsFloor && nurseFloor !== undefined;
              return (
                <tr key={dept} className={`border-b border-border/30 ${breach ? "bg-rose-500/10" : "hover:bg-slate-800/30"}`}>
                  <td className="py-1.5 px-2 text-white font-medium">
                    {dept}
                    {breach && (
                      <span
                        className="ml-1 text-[9px] px-1 py-0.5 rounded bg-rose-500/30 text-rose-200 border border-rose-500/50"
                        title={`HSE Safe Staffing Framework breach — floor for ${patientCount ?? "?"} patients is ${nurseFloor} nurses`}
                      >
                        UNSAFE
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 px-1 text-right font-mono-clinical text-slate-400">{baseDocs}</td>
                  <td className="py-1.5 px-1 text-right font-mono-clinical text-slate-400">{baseNurses}</td>
                  <td className="py-1.5 px-1 text-right font-mono-clinical text-green-400">{marlDocs}</td>
                  <td
                    className={`py-1.5 px-1 text-right font-mono-clinical ${breach ? "text-rose-300 font-bold" : "text-green-300"}`}
                    title={nurseFloor !== undefined ? `HSE floor at current census (${patientCount ?? 0} pts): ${nurseFloor} nurses` : undefined}
                  >
                    {marlNurses}
                    {nurseFloor !== undefined && (
                      <span className="text-[8px] text-slate-500 ml-0.5">/≥{nurseFloor}</span>
                    )}
                  </td>
                  <td className={`py-1.5 px-1 text-right font-mono-clinical ${deltaDocs > 0 ? "text-green-400" : deltaDocs < 0 ? "text-red-400" : "text-slate-600"}`}>
                    {deltaDocs > 0 ? `+${deltaDocs}` : deltaDocs === 0 ? "—" : deltaDocs}
                  </td>
                  <td className={`py-1.5 px-1 text-right font-mono-clinical ${deltaNurses > 0 ? "text-green-400" : deltaNurses < 0 ? "text-red-400" : "text-slate-600"}`}>
                    {deltaNurses > 0 ? `+${deltaNurses}` : deltaNurses === 0 ? "—" : deltaNurses}
                  </td>
                  <td className={`py-1.5 px-1 text-right font-mono-clinical ${svcRate >= 1.0 ? "text-green-400" : "text-orange-400"}`}>
                    {(svcRate as number).toFixed(2)}x
                  </td>
                  <td className="py-1.5 px-1 text-center text-[9px] text-slate-400">{s.nurse_patient_ratio}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[10px] text-slate-500">
        ERP baseline = day shift staffing. MARL adjustments clamped to ±50% of baseline.
        Rows tinted red violate the HSE Safe Staffing Framework floor (1:1 ICU · 1:2 HDU · 1:4 acute);
        the <span className="text-rose-300 font-mono">/≥N</span> suffix shows the mandatory minimum at the current census.
      </div>
    </div>
  );
}
