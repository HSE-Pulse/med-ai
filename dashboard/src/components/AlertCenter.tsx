import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, X, Check, AlertCircle, AlertTriangle, Info, Wifi, WifiOff } from "lucide-react";
import { useAlerts, type Alert, type AlertSeverity } from "../context/AlertsContext";

const SEVERITY_ORDER: AlertSeverity[] = ["critical", "high", "medium", "info"];

const SEVERITY_STYLE: Record<AlertSeverity, { ring: string; text: string; dot: string; icon: typeof AlertCircle }> = {
  critical: { ring: "ring-red-500/40", text: "text-red-400", dot: "bg-red-500", icon: AlertCircle },
  high: { ring: "ring-orange-500/40", text: "text-orange-400", dot: "bg-orange-500", icon: AlertTriangle },
  medium: { ring: "ring-amber-500/40", text: "text-amber-400", dot: "bg-amber-500", icon: AlertTriangle },
  info: { ring: "ring-slate-500/40", text: "text-slate-400", dot: "bg-slate-500", icon: Info },
};

function timeAgo(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const delta = Math.max(0, Date.now() - then);
    const sec = Math.floor(delta / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.floor(hr / 24)}d ago`;
  } catch {
    return "";
  }
}

function AlertRow({
  alert,
  onAck,
  onNavigate,
}: {
  alert: Alert;
  onAck: (id: string) => void;
  onNavigate: (route: string | null) => void;
}) {
  const style = SEVERITY_STYLE[alert.severity];
  const Icon = style.icon;
  return (
    <div
      className={`group p-3 border-b border-border last:border-b-0 hover:bg-slate-700/30 ${
        alert.acknowledged ? "opacity-50" : ""
      }`}
      role="listitem"
      aria-live={alert.severity === "critical" ? "assertive" : "polite"}
    >
      <div className="flex items-start gap-2.5">
        <Icon className={`w-4 h-4 shrink-0 mt-0.5 ${style.text}`} />
        <div className="flex-1 min-w-0">
          <button
            onClick={() => onNavigate(alert.route_hint)}
            className="block text-left w-full"
            disabled={!alert.route_hint}
          >
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-medium text-white truncate">{alert.title}</span>
              {alert.count && alert.count > 1 ? (
                <span
                  className="shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-slate-700 text-slate-300"
                  title={`${alert.count} events coalesced`}
                >
                  ×{alert.count}
                </span>
              ) : null}
            </div>
            <div className="text-xs text-slate-400 mt-0.5 flex items-center gap-2">
              <span className="font-mono-clinical">{alert.source_module || alert.topic}</span>
              {alert.patient_id && (
                <span className="text-blue-400">· #{alert.patient_id}</span>
              )}
              <span>·</span>
              <span>
                {alert.count && alert.count > 1 && alert.last_timestamp
                  ? `latest ${timeAgo(alert.last_timestamp)}`
                  : timeAgo(alert.timestamp)}
              </span>
            </div>
          </button>
        </div>
        {!alert.acknowledged && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onAck(alert.id);
            }}
            className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-slate-600/60 text-slate-400 hover:text-slate-200"
            title="Acknowledge"
            aria-label="Acknowledge alert"
          >
            <Check className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

export default function AlertCenter() {
  const { alerts, unacked, connected, ack, dismissAll } = useAlerts();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<AlertSeverity | "all">("all");
  const navigate = useNavigate();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const unackCount = unacked.length;
  const criticalCount = unacked.filter((a) => a.severity === "critical").length;

  const filtered = filter === "all" ? alerts : alerts.filter((a) => a.severity === filter);
  const grouped: Record<AlertSeverity, Alert[]> = {
    critical: [],
    high: [],
    medium: [],
    info: [],
  };
  for (const a of filtered) grouped[a.severity].push(a);

  const handleNavigate = (route: string | null) => {
    if (!route) return;
    navigate(route);
    setOpen(false);
  };

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative flex items-center justify-center w-9 h-9 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
        title="Alert center"
        aria-label={`Alert center: ${unackCount} unacknowledged`}
        aria-expanded={open}
      >
        <Bell className="w-5 h-5" />
        {unackCount > 0 && (
          <span
            className={`absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold flex items-center justify-center ${
              criticalCount > 0
                ? "bg-red-500 text-white animate-pulse"
                : "bg-orange-500 text-white"
            }`}
            aria-hidden
          >
            {unackCount > 99 ? "99+" : unackCount}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-2 w-[380px] max-h-[min(640px,80vh)] bg-bg-card border border-border rounded-lg shadow-xl flex flex-col z-50"
          role="dialog"
          aria-label="Alert center"
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-white">Alerts</span>
              <span
                className="text-[10px] flex items-center gap-1 text-slate-500"
                title={connected ? "Live stream" : "Disconnected (polling)"}
              >
                {connected ? (
                  <>
                    <Wifi className="w-3 h-3 text-green-500" />
                    Live
                  </>
                ) : (
                  <>
                    <WifiOff className="w-3 h-3 text-amber-500" />
                    Polling
                  </>
                )}
              </span>
            </div>
            <div className="flex items-center gap-1">
              {unackCount > 0 && (
                <button
                  onClick={() => dismissAll()}
                  className="text-[11px] text-slate-400 hover:text-slate-200 px-2 py-1 rounded"
                  title="Acknowledge all"
                >
                  Ack all
                </button>
              )}
              <button
                onClick={() => setOpen(false)}
                className="p-1 rounded text-slate-500 hover:text-slate-200"
                aria-label="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="flex items-center gap-1 px-3 py-2 border-b border-border text-[11px]">
            {(["all", ...SEVERITY_ORDER] as const).map((s) => {
              const count = s === "all" ? alerts.length : (grouped[s]?.length ?? 0);
              const active = filter === s;
              return (
                <button
                  key={s}
                  onClick={() => setFilter(s)}
                  className={`px-2 py-1 rounded capitalize transition-colors ${
                    active
                      ? "bg-blue-500/20 text-blue-300"
                      : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/40"
                  }`}
                >
                  {s} <span className="text-slate-500">{count}</span>
                </button>
              );
            })}
          </div>

          <div className="flex-1 overflow-y-auto" role="list">
            {filtered.length === 0 ? (
              <div className="p-6 text-center text-sm text-slate-500">
                No alerts. All clear.
              </div>
            ) : filter === "all" ? (
              SEVERITY_ORDER.flatMap((sev) =>
                grouped[sev].length === 0
                  ? []
                  : [
                      <div
                        key={`h-${sev}`}
                        className="sticky top-0 z-10 px-3 py-1 bg-bg-card/95 backdrop-blur border-b border-border text-[10px] uppercase tracking-wide text-slate-500 flex items-center gap-2"
                      >
                        <span className={`w-1.5 h-1.5 rounded-full ${SEVERITY_STYLE[sev].dot}`} />
                        {sev}
                        <span className="text-slate-600">({grouped[sev].length})</span>
                      </div>,
                      ...grouped[sev].map((a) => (
                        <AlertRow
                          key={a.id}
                          alert={a}
                          onAck={ack}
                          onNavigate={handleNavigate}
                        />
                      )),
                    ],
              )
            ) : (
              filtered.map((a) => (
                <AlertRow key={a.id} alert={a} onAck={ack} onNavigate={handleNavigate} />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
