import {
  ResponsiveContainer,
  AreaChart,
  Area,
  ReferenceLine,
  YAxis,
} from "recharts";

interface DataPoint {
  time: string;
  value: number;
}

interface VitalSignChartProps {
  data: DataPoint[];
  label: string;
  unit: string;
  normalMin: number;
  normalMax: number;
  height?: number;
}

export default function VitalSignChart({
  data,
  label,
  unit,
  normalMin,
  normalMax,
  height = 80,
}: VitalSignChartProps) {
  const currentValue = data.length > 0 ? data[data.length - 1].value : 0;
  const minVal = Math.min(...data.map((d) => d.value));
  const maxVal = Math.max(...data.map((d) => d.value));
  const isOutOfRange = currentValue < normalMin || currentValue > normalMax;
  const color = isOutOfRange ? "#DC2626" : "#22C55E";
  const yMin = Math.min(minVal, normalMin) - 5;
  const yMax = Math.max(maxVal, normalMax) + 5;

  return (
    <div className="bg-bg-card rounded-lg border border-border p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-slate-400 font-medium">{label}</span>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500">
            {normalMin}-{normalMax} {unit}
          </span>
        </div>
      </div>
      <div className="flex items-baseline gap-1 mb-1">
        <span
          className="font-mono-clinical text-xl font-bold"
          style={{ color }}
        >
          {currentValue.toFixed(1)}
        </span>
        <span className="text-xs text-slate-500">{unit}</span>
        {isOutOfRange && (
          <span className="text-[10px] text-red-400 font-medium ml-1">OUT OF RANGE</span>
        )}
      </div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
            <YAxis domain={[yMin, yMax]} hide />
            <ReferenceLine y={normalMax} stroke="var(--color-segment-border)" strokeDasharray="3 3" />
            <ReferenceLine y={normalMin} stroke="var(--color-segment-border)" strokeDasharray="3 3" />
            <defs>
              <linearGradient id={`grad-${label}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.3} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="value"
              stroke={color}
              strokeWidth={1.5}
              fill={`url(#grad-${label})`}
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex justify-between text-[10px] text-slate-500 mt-1">
        <span>Min: {minVal.toFixed(1)}</span>
        <span>Max: {maxVal.toFixed(1)}</span>
      </div>
    </div>
  );
}
