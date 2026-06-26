import { useEffect, useRef, useState, useCallback } from "react";

type Status = "connecting" | "open" | "closed";

interface Options {
  /** Called on every incoming parsed JSON message. */
  onMessage?: (msg: unknown) => void;
  /** Fallback interval (ms) if WS never opens — swap to polling via the REST cousin. */
  fallbackPollMs?: number;
  /** Fallback polling function invoked if WS fails to connect. */
  fallbackPoll?: () => Promise<void>;
  /** Disable auto-connect. */
  disabled?: boolean;
}

/**
 * Reconnecting WebSocket hook with exponential backoff and graceful fallback to polling.
 *
 * Pass a relative URL like "/api/alerts/alerts/stream" — the hook converts it to
 * a fully-qualified ws:// or wss:// based on the current origin.
 */
export function useWebSocket(url: string, options: Options = {}) {
  const { onMessage, fallbackPoll, fallbackPollMs = 5000, disabled = false } = options;
  const [status, setStatus] = useState<Status>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const pollTimerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const fallbackPollRef = useRef(fallbackPoll);
  fallbackPollRef.current = fallbackPoll;

  const wsUrl = useCallback(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${url}`;
  }, [url]);

  const startPolling = useCallback(() => {
    if (!fallbackPollRef.current || pollTimerRef.current !== null) return;
    const tick = async () => {
      try {
        await fallbackPollRef.current?.();
      } catch {
        // swallow — polling is best-effort
      }
    };
    tick();
    pollTimerRef.current = window.setInterval(tick, fallbackPollMs);
  }, [fallbackPollMs]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    setStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl());
    } catch {
      startPolling();
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      retryRef.current = 0;
      setStatus("open");
      stopPolling();
    };

    ws.onmessage = (ev) => {
      if (!mountedRef.current) return;
      try {
        const parsed = JSON.parse(ev.data);
        onMessageRef.current?.(parsed);
      } catch {
        // ignore non-JSON frames
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setStatus("closed");
      startPolling();
      scheduleReconnect();
    };

    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        // ignore
      }
    };
  }, [wsUrl, startPolling, stopPolling]);

  const scheduleReconnect = useCallback(() => {
    const delay = Math.min(30000, 1000 * Math.pow(2, retryRef.current));
    retryRef.current += 1;
    window.setTimeout(() => {
      if (mountedRef.current) connect();
    }, delay);
  }, [connect]);

  useEffect(() => {
    if (disabled) return;
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      stopPolling();
      try {
        wsRef.current?.close();
      } catch {
        // ignore
      }
    };
  }, [connect, stopPolling, disabled]);

  const send = useCallback((data: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(typeof data === "string" ? data : JSON.stringify(data));
      return true;
    }
    return false;
  }, []);

  return { status, send };
}
