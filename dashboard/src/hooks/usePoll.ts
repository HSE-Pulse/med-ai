import { useEffect, useRef, useState, useCallback } from "react";

interface Result<T> {
  data: T | null;
  ok: boolean;
  loading: boolean;
  refresh: () => void;
}

/**
 * Poll a JSON endpoint. Returns the parsed body on success, null on failure.
 * Re-polls at `intervalMs` (default 5s). Safe against unmount races.
 *
 * Unlike useSimPolling this:
 *  - exposes a distinct `loading` state (true only on the very first attempt)
 *  - tracks `ok` separately so tiles can show a stale-but-known value when the
 *    backend hiccups instead of collapsing to "—"
 *  - keeps the previous successful payload on transient failures
 *
 * Every request is bounded by `timeoutMs` and a tick is skipped while the
 * previous one is still in flight. Both matter more than they look:
 * a hung upstream (SimEngine blocking its own event loop on a slow Mongo
 * aggregation) used to leave `fetch` pending forever, so `loading` never
 * cleared, the tile sat on a silently-stale value, and — worst — a fresh
 * request piled on every 5s from every open tab, which is what kept the
 * upstream's connection pool exhausted. Failing fast is what lets the
 * backend recover, and what lets the UI admit it doesn't know.
 */
export function usePoll<T>(
  url: string,
  intervalMs = 5000,
  timeoutMs = 8000,
): Result<T> {
  const [data, setData] = useState<T | null>(null);
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);
  const inFlight = useRef(false);

  const tick = useCallback(async () => {
    // Never let a slow endpoint queue up behind itself.
    if (inFlight.current) return;
    inFlight.current = true;

    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const r = await fetch(url, { signal: controller.signal });
      if (!r.ok) {
        if (mounted.current) setOk(false);
        return;
      }
      const json = (await r.json()) as T;
      if (mounted.current) {
        setData(json);
        setOk(true);
      }
    } catch {
      // Includes the abort — an upstream too slow to answer within
      // timeoutMs is indistinguishable from a down one, and should be
      // reported the same way rather than hung on.
      if (mounted.current) setOk(false);
    } finally {
      window.clearTimeout(timer);
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [url, timeoutMs]);

  useEffect(() => {
    mounted.current = true;
    tick();
    const id = window.setInterval(tick, intervalMs);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [tick, intervalMs]);

  return { data, ok, loading, refresh: tick };
}
