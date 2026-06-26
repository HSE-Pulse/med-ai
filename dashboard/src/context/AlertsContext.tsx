import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { useWebSocket } from "../hooks/useWebSocket";

export type AlertSeverity = "critical" | "high" | "medium" | "info";

export interface Alert {
  id: string;
  topic: string;
  severity: AlertSeverity;
  title: string;
  source_module: string;
  patient_id: string | null;
  payload: Record<string, unknown>;
  timestamp: string;          // first event in group
  last_timestamp?: string;    // most recent event in group
  count?: number;             // number of coalesced events (>=1)
  route_hint: string | null;
  acknowledged: boolean;
}

type Action =
  | { type: "snapshot"; alerts: Alert[] }
  | { type: "alert"; alert: Alert }
  | { type: "alert_update"; alert: Alert }
  | { type: "ack"; alert_id: string };

interface State {
  alerts: Alert[]; // newest first
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "snapshot": {
      // Replace store with server snapshot, preserving any local-only ack state
      const ackIds = new Set(state.alerts.filter((a) => a.acknowledged).map((a) => a.id));
      return {
        alerts: action.alerts.map((a) => (ackIds.has(a.id) ? { ...a, acknowledged: true } : a)),
      };
    }
    case "alert": {
      if (state.alerts.some((a) => a.id === action.alert.id)) return state;
      return { alerts: [action.alert, ...state.alerts].slice(0, 500) };
    }
    case "alert_update": {
      const idx = state.alerts.findIndex((a) => a.id === action.alert.id);
      if (idx === -1) {
        // We never saw the original (e.g. connected after it first arrived)
        return { alerts: [action.alert, ...state.alerts].slice(0, 500) };
      }
      const next = state.alerts.slice();
      // Preserve local ack state (server-side ack updates propagate via "ack")
      next[idx] = { ...action.alert, acknowledged: next[idx].acknowledged };
      // Surface the updated row at the top so repeat events bubble into view
      const [updated] = next.splice(idx, 1);
      return { alerts: [updated, ...next] };
    }
    case "ack": {
      return {
        alerts: state.alerts.map((a) =>
          a.id === action.alert_id ? { ...a, acknowledged: true } : a,
        ),
      };
    }
    default:
      return state;
  }
}

interface Ctx {
  alerts: Alert[];
  unacked: Alert[];
  connected: boolean;
  ack: (alertId: string) => Promise<void>;
  dismissAll: () => Promise<void>;
}

const AlertsCtx = createContext<Ctx | null>(null);

const ALERT_STREAM_URL = "/api/alerts/alerts/stream";
const ALERT_RECENT_URL = "/api/alerts/alerts/recent?limit=50";

export function AlertsProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, { alerts: [] });
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  const handleMessage = useCallback((msg: unknown) => {
    if (!msg || typeof msg !== "object") return;
    const m = msg as Record<string, unknown>;
    if (m.type === "snapshot" && Array.isArray(m.alerts)) {
      dispatchRef.current({ type: "snapshot", alerts: m.alerts as Alert[] });
    } else if (m.type === "alert" && m.alert) {
      dispatchRef.current({ type: "alert", alert: m.alert as Alert });
    } else if (m.type === "alert_update" && m.alert) {
      dispatchRef.current({ type: "alert_update", alert: m.alert as Alert });
    } else if (m.type === "ack" && typeof m.alert_id === "string") {
      dispatchRef.current({ type: "ack", alert_id: m.alert_id });
    }
  }, []);

  const pollOnce = useCallback(async () => {
    try {
      const res = await fetch(ALERT_RECENT_URL);
      if (!res.ok) return;
      const json = (await res.json()) as { alerts?: Alert[] };
      if (Array.isArray(json.alerts)) {
        dispatchRef.current({ type: "snapshot", alerts: json.alerts });
      }
    } catch {
      // best-effort
    }
  }, []);

  const { status } = useWebSocket(ALERT_STREAM_URL, {
    onMessage: handleMessage,
    fallbackPoll: pollOnce,
    fallbackPollMs: 5000,
  });

  const ack = useCallback(async (alertId: string) => {
    dispatchRef.current({ type: "ack", alert_id: alertId });
    try {
      await fetch(`/api/alerts/alerts/${alertId}/ack`, { method: "POST" });
    } catch {
      // optimistic: local state already updated
    }
  }, []);

  const dismissAll = useCallback(async () => {
    const ids = state.alerts.filter((a) => !a.acknowledged).map((a) => a.id);
    for (const id of ids) dispatchRef.current({ type: "ack", alert_id: id });
    await Promise.allSettled(
      ids.map((id) => fetch(`/api/alerts/alerts/${id}/ack`, { method: "POST" })),
    );
  }, [state.alerts]);

  const value = useMemo<Ctx>(() => {
    const unacked = state.alerts.filter((a) => !a.acknowledged);
    return {
      alerts: state.alerts,
      unacked,
      connected: status === "open",
      ack,
      dismissAll,
    };
  }, [state.alerts, status, ack, dismissAll]);

  return <AlertsCtx.Provider value={value}>{children}</AlertsCtx.Provider>;
}

export function useAlerts(): Ctx {
  const ctx = useContext(AlertsCtx);
  if (!ctx) {
    throw new Error("useAlerts must be used within <AlertsProvider>");
  }
  return ctx;
}
