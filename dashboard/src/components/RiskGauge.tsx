interface RiskGaugeProps {
  value: number;
  label: string;
  size?: number;
}

export default function RiskGauge({ value, label, size = 180 }: RiskGaugeProps) {
  const clampedValue = Math.min(100, Math.max(0, value));

  const getColor = (v: number) => {
    if (v <= 25) return "#22C55E";
    if (v <= 50) return "#EAB308";
    if (v <= 75) return "#F97316";
    return "#DC2626";
  };

  const getRiskLabel = (v: number) => {
    if (v <= 25) return "Low";
    if (v <= 50) return "Moderate";
    if (v <= 75) return "High";
    return "Critical";
  };

  const color = getColor(clampedValue);

  // Arc geometry
  const strokeW = 8;
  const r = 50;
  const cx = 90;
  const cy = 66;
  const vw = 180;
  const vh = 80;

  const pct = clampedValue / 100;
  const valAngle = Math.PI * (1 - pct);

  const px = (a: number) => cx + r * Math.cos(a);
  const py = (a: number) => cy - r * Math.sin(a);

  const bgArc = `M ${px(Math.PI)} ${py(Math.PI)} A ${r} ${r} 0 1 0 ${px(0)} ${py(0)}`;

  let valArc = "";
  if (pct > 0) {
    const large = pct > 0.5 ? 1 : 0;
    valArc = `M ${px(Math.PI)} ${py(Math.PI)} A ${r} ${r} 0 ${large} 0 ${px(valAngle)} ${py(valAngle)}`;
  }

  const h = Math.round(size * vh / vw);

  return (
    <div className="flex flex-col items-center" style={{ width: size }}>
      <div className="relative" style={{ width: size, height: h, isolation: "isolate" }}>
        {/* Arc layer - z-0 */}
        <svg
          width="100%"
          height="100%"
          viewBox={`0 0 ${vw} ${vh}`}
          className="absolute inset-0"
          style={{ zIndex: 0 }}
        >
          <path d={bgArc} fill="none" stroke="var(--color-segment-border)" strokeWidth={strokeW} strokeLinecap="round" />
          {valArc && (
            <path d={valArc} fill="none" stroke={color} strokeWidth={strokeW} strokeLinecap="round" />
          )}
        </svg>
        {/* Text layer - z-10, with background to punch through the arc */}
        <div
          className="absolute inset-x-0 flex flex-col items-center"
          style={{ zIndex: 10, top: "16%" }}
        >
          <span
            className="font-mono-clinical font-bold text-[22px] leading-none px-1"
            style={{
              color,
              backgroundColor: "var(--color-bg-card, #1E293B)",
              borderRadius: 4,
            }}
          >
            {clampedValue.toFixed(1)}%
          </span>
          <span className="text-[11px] text-slate-400 mt-1">
            {getRiskLabel(clampedValue)}
          </span>
        </div>
      </div>
      <span className="text-xs text-slate-400">{label}</span>
    </div>
  );
}
