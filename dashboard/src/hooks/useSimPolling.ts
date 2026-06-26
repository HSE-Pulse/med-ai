import { useState, useEffect, useRef, useCallback } from "react";

/**
 * Generic hook for polling a simulation endpoint at a fixed interval.
 *
 * Consolidates the repeated useEffect + setInterval + cleanup pattern
 * used in EdTriage, SepsisIcu, and other simulation-connected pages.
 *
 * @param url        The API endpoint to poll (e.g. "/api/sim/ed-board")
 * @param interval   Polling interval in ms (default 3000)
 * @param transform  Optional function to transform the raw JSON response
 */
export function useSimPolling<T>(
  url: string,
  interval = 3000,
  transform?: (data: Record<string, unknown>) => T,
): { data: T | null; connected: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const activeRef = useRef(true);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (activeRef.current) {
        setData(transform ? transform(json) : (json as T));
        setConnected(true);
      }
    } catch {
      if (activeRef.current) {
        setConnected(false);
      }
    }
  }, [url, transform]);

  useEffect(() => {
    activeRef.current = true;
    poll();
    const id = setInterval(poll, interval);
    return () => {
      activeRef.current = false;
      clearInterval(id);
    };
  }, [poll, interval]);

  return { data, connected, refresh: poll };
}
