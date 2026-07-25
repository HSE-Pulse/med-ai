import { useState, useEffect, useRef, useCallback } from "react";

/**
 * Generic hook for polling a simulation endpoint at a fixed interval.
 *
 * Consolidates the repeated useEffect + setInterval + cleanup pattern
 * used in EdTriage, SepsisIcu, and other simulation-connected pages.
 *
 * Requests are bounded by `timeoutMs` and never overlap — see the note in
 * usePoll: an unbounded 3s poll against a stalled sim endpoint pins the
 * upstream's connection pool open and keeps `connected` stuck true on data
 * that stopped updating.
 *
 * @param url        The API endpoint to poll (e.g. "/api/sim/ed-board")
 * @param interval   Polling interval in ms (default 3000)
 * @param transform  Optional function to transform the raw JSON response
 * @param timeoutMs  Per-request timeout (default 8000)
 */
export function useSimPolling<T>(
  url: string,
  interval = 3000,
  transform?: (data: Record<string, unknown>) => T,
  timeoutMs = 8000,
): { data: T | null; connected: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const activeRef = useRef(true);
  const inFlight = useRef(false);

  const poll = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;

    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { signal: controller.signal });
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
    } finally {
      window.clearTimeout(timer);
      inFlight.current = false;
    }
  }, [url, transform, timeoutMs]);

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
