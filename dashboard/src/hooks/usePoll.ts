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
 */
export function usePoll<T>(url: string, intervalMs = 5000): Result<T> {
  const [data, setData] = useState<T | null>(null);
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const tick = useCallback(async () => {
    try {
      const r = await fetch(url);
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
      if (mounted.current) setOk(false);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [url]);

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
