import { type ReactNode } from "react";
import { TrendingUp, TrendingDown } from "lucide-react";

interface StatCardProps {
  icon: ReactNode;
  label: string;
  value: string | number;
  trend?: { direction: "up" | "down"; value: string };
  accentColor?: string;
  subtitle?: string;
  children?: ReactNode;
}

export default function StatCard({
  icon,
  label,
  value,
  trend,
  accentColor = "#3B82F6",
  subtitle,
  children,
}: StatCardProps) {
  const trendIsPositive =
    (trend?.direction === "up" && !label.toLowerCase().includes("wait")) ||
    (trend?.direction === "down" && label.toLowerCase().includes("wait"));

  return (
    <div className="bg-bg-card rounded-xl border border-border p-4 hover:border-slate-600 transition-colors relative overflow-hidden">
      <div
        className="absolute top-0 left-0 w-1 h-full rounded-l-xl"
        style={{ backgroundColor: accentColor }}
      />
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 text-slate-400">
          {icon}
          <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
        </div>
        {trend && (
          <div
            className={`flex items-center gap-1 text-xs font-medium px-1.5 py-0.5 rounded ${
              trendIsPositive
                ? "text-green-400 bg-green-400/10"
                : "text-red-400 bg-red-400/10"
            }`}
          >
            {trend.direction === "up" ? (
              <TrendingUp className="w-3 h-3" />
            ) : (
              <TrendingDown className="w-3 h-3" />
            )}
            {trend.value}
          </div>
        )}
      </div>
      <div className="font-mono-clinical text-2xl font-bold text-white mb-1">{value}</div>
      {subtitle && <p className="text-xs text-slate-400">{subtitle}</p>}
      {children}
    </div>
  );
}
