import { useEffect, useState, useMemo } from "react";
import { Radio, Wifi, WifiOff, Activity } from "lucide-react";
import type { EventType, VoyageEvent } from "../hooks/useVoyageStream";
import { formatSimTime } from "../lib/voyage";

interface Props {
  wsStatus: "connecting" | "open" | "closed";
  latestSimTime: string | null;
  lastTickWall: number;
  rateBuckets: Record<EventType, number[]>;
  totals: Record<EventType, number>;
  hospitalEvents: VoyageEvent[];
  /** Optional engine speed for the badge. */
  speedX?: number;
}

const TYPES: EventType[] = [
  "admission", "transfer", "vital", "lab",
  "medication", "diagnosis", "procedure", "discharge",
];

const TYPE_COLOR: Record<string, string> = {
  admission: "#3B82F6",
  transfer: "#8B5CF6",
  vital: "#EC4899",
  lab: "#F59E0B",
  medication: "#14B8A6",
  diagnosis: "#EF4444",
  procedure: "#F97316",
  note: "#6366F1",
  discharge: "#22C55E",
};

export default function SimConsole({
  wsStatus,
  latestSimTime,
  lastTickWall,
  rateBuckets,
  totals,
  hospitalEvents,
  speedX,
}: Props) {
  // Tick pulse — turns on briefly each time a new event lands.
  const [tick, setTick] = useState(false);
  useEffect(() => {
    if (!lastTickWall) return;
    setTick(true);
    const t = window.setTimeout(() => setTick(false), 350);
    return () => window.clearTimeout(t);
  }, [lastTickWall]);

  const wsLabel = wsStatus === "open" ? "LIVE" : wsStatus === "connecting" ? "CONN" : "POLL";
  const wsColor =
    wsStatus === "open" ? "#22C55E" : wsStatus === "connecting" ? "#EAB308" : "#F97316";
  const WsIcon = wsStatus === "open" ? Wifi : WifiOff;

  return (
    <div
      className="bg-bg-card rounded-xl border border-border px-4 py-3"
      role="region"
      aria-label="Simulation console"
    >
      <div className="grid grid-cols-12 gap-3 items-center">
        {/* Sim clock + tick */}
        <div className="col-span-12 md:col-span-4 flex items-center gap-3 min-w-0">
          <div
            className={`relative w-10 h-10 rounded-xl flex items-center justify-center transition-all duration-300 ${
              tick ? "scale-110" : ""
            }`}
            style={{
              backgroundColor: tick ? wsColor + "33" : wsColor + "15",
              boxShadow: tick ? `0 0 20px ${wsColor}88` : "none",
            }}
          >
            <Radio className="w-5 h-5" style={{ color: wsColor }} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-wider text-text-muted font-mono-clinical">
                Sim Time
              </span>
              {speedX != null && (
                <span
                  className="text-[10px] font-mono-clinical font-semibold px-1.5 py-0.5 rounded"
                  style={{ backgroundColor: "#8B5CF622", color: "#A78BFA" }}
                >
                  {speedX}×
                </span>
              )}
              <span
                className="inline-flex items-center gap-1 text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded"
                style={{ backgroundColor: wsColor + "22", color: wsColor }}
              >
                <WsIcon className="w-2.5 h-2.5" />
                {wsLabel}
              </span>
            </div>
            <div
              className="text-base md:text-lg font-mono-clinical font-bold text-text-primary truncate"
              aria-live="polite"
            >
              {formatSimTime(latestSimTime) || "—"}
            </div>
          </div>
        </div>

        {/* Per-type event-rate sparklines */}
        <div className="col-span-12 md:col-span-8 grid grid-cols-4 lg:grid-cols-8 gap-1.5">
          {TYPES.map((t) => (
            <EventTypeRateTile
              key={t}
              type={t}
              buckets={rateBuckets[t] ?? []}
              total={totals[t] ?? 0}
            />
          ))}
        </div>
      </div>

      {hospitalEvents.length > 0 && (
        <HospitalTicker events={hospitalEvents.slice(0, 6)} />
      )}
    </div>
  );
}

function EventTypeRateTile({
  type,
  buckets,
  total,
}: {
  type: EventType;
  buckets: number[];
  total: number;
}) {
  const color = TYPE_COLOR[type] ?? "#94a3b8";
  // Roll the buffer so the current second is rightmost. The reducer stores
  // counts indexed by (epoch_seconds % WINDOW); to render in time-order we
  // align to the present second.
  const path = useMemo(() => buildSparkPath(buckets), [buckets]);
  // Sum the last 10 buckets for an "events/10s" view.
  const recent = buckets.slice(-10).reduce((a, b) => a + b, 0);

  return (
    <div className="bg-bg-input/40 rounded p-1.5 flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center justify-between gap-1">
        <span
          className="text-[9px] font-mono-clinical uppercase font-semibold truncate"
          style={{ color }}
        >
          {type}
        </span>
        <span className="text-[9px] text-text-muted font-mono-clinical">{total}</span>
      </div>
      <svg viewBox="0 0 60 14" preserveAspectRatio="none" className="w-full h-3.5">
        {path && <path d={path} stroke={color} fill={color + "33"} strokeWidth="1" />}
      </svg>
      <span className="text-[9px] text-text-muted font-mono-clinical text-right">
        {recent}/10s
      </span>
    </div>
  );
}

function HospitalTicker({ events }: { events: VoyageEvent[] }) {
  return (
    <div className="mt-2 pt-2 border-t border-border flex items-center gap-2 text-[10px] overflow-hidden">
      <Activity className="w-3 h-3 text-text-muted flex-shrink-0" />
      <span className="text-text-muted font-mono-clinical uppercase text-[9px] flex-shrink-0">
        Live
      </span>
      <div className="flex gap-2 overflow-x-auto">
        {events.map((e, i) => {
          const color = TYPE_COLOR[e.event] ?? "#94a3b8";
          const hadm = (e.data?.hadm_id as string) ?? "—";
          return (
            <span
              key={`${e.sim_time}-${i}`}
              className="inline-flex items-center gap-1 flex-shrink-0 animate-fade-in"
            >
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ backgroundColor: color }}
                aria-hidden
              />
              <span className="font-mono-clinical text-text-muted uppercase">
                {e.event}
              </span>
              <span className="font-mono-clinical text-text-primary truncate max-w-[120px]">
                {hadm.slice(-12)}
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function buildSparkPath(buckets: number[]): string {
  if (!buckets.length) return "";
  const w = 60;
  const h = 14;
  const max = Math.max(1, ...buckets);
  const step = w / buckets.length;
  let d = `M0,${h}`;
  buckets.forEach((v, i) => {
    const x = i * step;
    const y = h - (v / max) * (h - 1) - 0.5;
    d += ` L${x.toFixed(1)},${y.toFixed(1)}`;
  });
  d += ` L${w},${h} Z`;
  return d;
}
