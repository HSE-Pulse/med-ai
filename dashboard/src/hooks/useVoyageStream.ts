import { useEffect, useReducer, useRef, useCallback } from "react";
import { useWebSocket } from "./useWebSocket";
import type { VitalKey, VitalPoint } from "../lib/voyage";

// Map MIMIC itemid → simplified vital key. Same convention as the
// backend's VITAL_ITEMID_TO_NAME but only the channels the dashboard
// shows. (Kept in sync with backend/engine/event_engine.py.)
const ITEMID_TO_VITAL: Record<number, VitalKey> = {
  220045: "hr",     // Heart Rate
  220210: "rr",     // Respiratory Rate
  220277: "spo2",   // SpO2
  220179: "sbp",    // Systolic BP
  220180: "dbp",    // Diastolic BP
  223761: "temp",   // Temperature
};

const VITAL_NAME_TO_KEY: Record<string, VitalKey> = {
  hr: "hr",
  heart_rate: "hr",
  rr: "rr",
  respiratory_rate: "rr",
  spo2: "spo2",
  sbp: "sbp",
  dbp: "dbp",
  temp: "temp",
  temperature: "temp",
};

export interface VoyageEvent {
  event: string;
  sim_time: string;
  data: Record<string, unknown>;
}

export type EventType =
  | "admission" | "transfer" | "vital" | "lab"
  | "medication" | "diagnosis" | "procedure" | "note" | "discharge";

const TRACKED_TYPES: EventType[] = [
  "admission", "transfer", "vital", "lab",
  "medication", "diagnosis", "procedure", "note", "discharge",
];

/** Rolling per-second buckets for the last RATE_WINDOW seconds. */
const RATE_WINDOW = 60;

interface State {
  /** Vital deltas filtered to the selected patient. */
  vitalsDelta: Partial<Record<VitalKey, VitalPoint[]>>;
  /** Recent events for the selected patient (cap 50). */
  patientEvents: VoyageEvent[];
  /** Recent events across the whole hospital (cap 80). */
  hospitalEvents: VoyageEvent[];
  /** Per-type counts in a rolling RATE_WINDOW window: per-event-type ring buffer of seconds. */
  rateBuckets: Record<EventType, number[]>;
  /** Total seen since mount, per type. */
  totals: Record<EventType, number>;
  /** Latest sim_time observed across any event. */
  latestSimTime: string | null;
  /** Wall-clock ms of the most recent event — drives the tick pulse. */
  lastTickWall: number;
  /** Dept-change tracker: hadm → latest careunit observed via WS. */
  recentTransfers: Record<string, { from: string | null; to: string; at: number }>;
}

type Action =
  | { type: "resetPatient" }
  | { type: "patientVital"; vital: VitalKey; point: VitalPoint }
  | { type: "patientEvent"; event: VoyageEvent }
  | { type: "hospitalEvent"; event: VoyageEvent; second: number }
  | { type: "transfer"; hadm: string; from: string | null; to: string };

const VITALS_BUFFER_CAP = 360;
const HOSPITAL_EVENTS_CAP = 80;
const PATIENT_EVENTS_CAP = 50;

function emptyBuckets(): Record<EventType, number[]> {
  return Object.fromEntries(TRACKED_TYPES.map((t) => [t, new Array(RATE_WINDOW).fill(0)])) as Record<
    EventType,
    number[]
  >;
}

function emptyTotals(): Record<EventType, number> {
  return Object.fromEntries(TRACKED_TYPES.map((t) => [t, 0])) as Record<EventType, number>;
}

const INITIAL: State = {
  vitalsDelta: {},
  patientEvents: [],
  hospitalEvents: [],
  rateBuckets: emptyBuckets(),
  totals: emptyTotals(),
  latestSimTime: null,
  lastTickWall: 0,
  recentTransfers: {},
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "resetPatient":
      return { ...state, vitalsDelta: {}, patientEvents: [] };
    case "patientVital": {
      const arr = state.vitalsDelta[action.vital] ?? [];
      const next = arr.concat(action.point).slice(-VITALS_BUFFER_CAP);
      return { ...state, vitalsDelta: { ...state.vitalsDelta, [action.vital]: next } };
    }
    case "patientEvent": {
      return {
        ...state,
        patientEvents: [action.event, ...state.patientEvents].slice(0, PATIENT_EVENTS_CAP),
      };
    }
    case "hospitalEvent": {
      const t = (action.event.event as EventType);
      const buckets = state.rateBuckets[t];
      let nextBuckets = state.rateBuckets;
      let nextTotals = state.totals;
      if (buckets) {
        const idx = action.second % RATE_WINDOW;
        const updated = buckets.slice();
        // If we've rolled over (current second > last seen), zero the new slot.
        updated[idx] = (updated[idx] ?? 0) + 1;
        nextBuckets = { ...state.rateBuckets, [t]: updated };
        nextTotals = { ...state.totals, [t]: (state.totals[t] ?? 0) + 1 };
      }
      return {
        ...state,
        hospitalEvents: [action.event, ...state.hospitalEvents].slice(0, HOSPITAL_EVENTS_CAP),
        rateBuckets: nextBuckets,
        totals: nextTotals,
        latestSimTime: action.event.sim_time || state.latestSimTime,
        lastTickWall: Date.now(),
      };
    }
    case "transfer":
      return {
        ...state,
        recentTransfers: {
          ...state.recentTransfers,
          [action.hadm]: { from: action.from, to: action.to, at: Date.now() },
        },
      };
    default:
      return state;
  }
}

interface Options {
  selectedHadm: string | null;
  refetchSnapshot?: () => void;
}

export interface VoyageStreamApi {
  status: "connecting" | "open" | "closed";
  vitalsDelta: Partial<Record<VitalKey, VitalPoint[]>>;
  patientEvents: VoyageEvent[];
  hospitalEvents: VoyageEvent[];
  rateBuckets: Record<EventType, number[]>;
  totals: Record<EventType, number>;
  latestSimTime: string | null;
  lastTickWall: number;
  recentTransfers: Record<string, { from: string | null; to: string; at: number }>;
}

/**
 * One /api/sim/ws subscription, two derived streams:
 *   • patient stream — filtered by selectedHadm (held in ref so selection
 *     changes don't reconnect)
 *   • hospital stream — all events, with per-type rolling event-rate buckets
 */
export function useVoyageStream({ selectedHadm, refetchSnapshot }: Options): VoyageStreamApi {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const hadmRef = useRef<string | null>(selectedHadm);
  hadmRef.current = selectedHadm;
  // Track per-hadm last-known dept so we can detect transfers in the WS feed.
  const deptByHadm = useRef<Record<string, string>>({});

  useEffect(() => {
    dispatch({ type: "resetPatient" });
  }, [selectedHadm]);

  const onMessage = useCallback((msg: unknown) => {
    if (!msg || typeof msg !== "object") return;
    const m = msg as VoyageEvent;
    if (!m.event || !m.data) return;

    // Hospital-wide stream (every event)
    const second = Math.floor(Date.now() / 1000);
    dispatch({ type: "hospitalEvent", event: m, second });

    // Track dept transitions per patient so we can animate the map.
    const evtHadm = (m.data.hadm_id ?? m.data.hadm_id_raw) as string | undefined;
    if (m.event === "transfer" && evtHadm) {
      const careunit = (m.data.careunit as string) || "";
      const prev = deptByHadm.current[evtHadm] ?? null;
      if (careunit && careunit !== prev) {
        dispatch({ type: "transfer", hadm: evtHadm, from: prev, to: careunit });
        deptByHadm.current[evtHadm] = careunit;
      }
    }

    // Patient-filtered stream
    if (!evtHadm || evtHadm !== hadmRef.current) return;
    if (m.event === "vital") {
      const itemid = Number(m.data.itemid);
      const valuenum = Number(m.data.valuenum);
      const charttime = (m.data.charttime as string) || m.sim_time;
      const vitalName = (m.data.vital_name as string | undefined)?.toLowerCase();
      const key = ITEMID_TO_VITAL[itemid] ?? (vitalName ? VITAL_NAME_TO_KEY[vitalName] : undefined);
      if (key && !Number.isNaN(valuenum)) {
        dispatch({ type: "patientVital", vital: key, point: { time: charttime, value: valuenum } });
      }
    }
    dispatch({ type: "patientEvent", event: m });
  }, []);

  const fallbackPoll = useCallback(async () => {
    if (hadmRef.current) refetchSnapshot?.();
  }, [refetchSnapshot]);

  const { status } = useWebSocket("/api/sim/ws", {
    onMessage,
    fallbackPoll,
    fallbackPollMs: 5000,
  });

  return {
    status,
    vitalsDelta: state.vitalsDelta,
    patientEvents: state.patientEvents,
    hospitalEvents: state.hospitalEvents,
    rateBuckets: state.rateBuckets,
    totals: state.totals,
    latestSimTime: state.latestSimTime,
    lastTickWall: state.lastTickWall,
    recentTransfers: state.recentTransfers,
  };
}
