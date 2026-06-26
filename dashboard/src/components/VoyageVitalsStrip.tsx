import { useMemo } from "react";
import { Heart, Activity, Thermometer, Wind, Droplets } from "lucide-react";
import type { VitalKey, VitalPoint, DeteriorationAlert } from "../lib/voyage";
import { pctToColor } from "../lib/voyage";

interface Props {
  vitalsSeries: Partial<Record<VitalKey, VitalPoint[]>>;
  deterAlerts: DeteriorationAlert[];
}

interface VitalSpec {
  key: VitalKey;
  label: string;
  unit: string;
  normal: [number, number];
  icon: typeof Heart;
}

const VITALS: VitalSpec[] = [
  { key: "hr", label: "HR", unit: "bpm", normal: [60, 100], icon: Heart },
  { key: "rr", label: "RR", unit: "/min", normal: [12, 20], icon: Wind },
  { key: "spo2", label: "SpO₂", unit: "%", normal: [94, 100], icon: Activity },
  { key: "sbp", label: "SBP", unit: "mmHg", normal: [90, 140], icon: Droplets },
  { key: "dbp", label: "DBP", unit: "mmHg", normal: [60, 90], icon: Droplets },
  { key: "temp", label: "Temp", unit: "°C", normal: [36.0, 37.5], icon: Thermometer },
];

export default function VoyageVitalsStrip({ vitalsSeries, deterAlerts }: Props) {
  const latestNews = useMemo(() => {
    if (!deterAlerts.length) return null;
    const sorted = [...deterAlerts].sort(
      (a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at),
    );
    return sorted[0];
  }, [deterAlerts]);

  return (
    <div
      className="bg-bg-card rounded-xl border border-border p-3 sticky top-0 z-10"
      role="region"
      aria-label="Live vital signs"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Live Vitals
        </span>
        {latestNews && (
          <NewsChip
            score={latestNews.score?.total ?? 0}
            band={latestNews.score?.risk_band ?? "—"}
            trajectory={latestNews.trend?.trajectory}
            system={latestNews.scoring_system ?? "news2"}
          />
        )}
      </div>
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
        {VITALS.map((v) => (
          <Sparkline
            key={v.key}
            spec={v}
            data={vitalsSeries[v.key] ?? []}
          />
        ))}
      </div>
    </div>
  );
}

function Sparkline({ spec, data }: { spec: VitalSpec; data: VitalPoint[] }) {
  const Icon = spec.icon;
  const last = data.length ? data[data.length - 1] : null;
  const [lo, hi] = spec.normal;
  const outOfRange = last != null && (last.value < lo || last.value > hi);
  const valueColor = outOfRange ? "#DC2626" : "#22C55E";
  const path = useMemo(() => buildPath(data, 100, 30), [data]);

  // HR-only: pulse the icon at the actual heart rate.
  const isHR = spec.key === "hr";
  const beatDurationS = isHR && last && last.value > 30 && last.value < 220 ? 60 / last.value : null;

  return (
    <div className="bg-bg-input/40 rounded-lg p-2 flex flex-col gap-1 min-w-0">
      <div className="flex items-center justify-between gap-1">
        <span className="inline-flex items-center gap-1 text-[10px] text-text-muted">
          <span
            className={isHR ? "animate-heartbeat inline-block" : "inline-block"}
            style={beatDurationS ? { animationDuration: `${beatDurationS}s`, color: valueColor } : undefined}
          >
            <Icon className="w-3 h-3" />
          </span>
          {spec.label}
        </span>
        <span className="text-[9px] text-text-muted/70 font-mono-clinical">
          {lo}-{hi}
        </span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="font-mono-clinical text-base font-bold" style={{ color: valueColor }}>
          {last ? last.value.toFixed(1) : "—"}
        </span>
        <span className="text-[9px] text-text-muted">{spec.unit}</span>
      </div>
      <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="w-full h-6">
        {/* normal-range guide */}
        <line x1="0" x2="100" y1="15" y2="15" stroke="var(--color-segment-border)" strokeDasharray="2 2" strokeOpacity="0.4" />
        {path && (
          <path d={path} fill="none" stroke={valueColor} strokeWidth="1.5" />
        )}
      </svg>
      <span className="text-[9px] text-text-muted/70 font-mono-clinical text-right">
        {data.length} pts
      </span>
    </div>
  );
}

function NewsChip({
  score,
  band,
  trajectory,
  system,
}: {
  score: number;
  band: string;
  trajectory?: string;
  system: string;
}) {
  const color = pctToColor((score / 20) * 100);
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px]"
      style={{ borderColor: color + "55", backgroundColor: color + "15", color }}
    >
      <span className="text-text-muted uppercase text-[9px] font-mono-clinical">{system}</span>
      <span className="font-mono-clinical font-bold">{score}</span>
      <span className="text-[10px]">({band})</span>
      {trajectory && trajectory !== "insufficient_data" && (
        <span className="text-[10px] text-text-muted">· {trajectory}</span>
      )}
    </span>
  );
}

function buildPath(data: VitalPoint[], w: number, h: number): string {
  if (data.length < 2) return "";
  const values = data.map((d) => d.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const stepX = w / (data.length - 1);
  return data
    .map((d, i) => {
      const x = i * stepX;
      const y = h - ((d.value - min) / range) * (h - 2) - 1;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}
