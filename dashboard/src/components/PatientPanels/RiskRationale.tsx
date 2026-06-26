import { Info, Lightbulb } from "lucide-react";

export interface RiskPayload {
  readmission_30d_risk?: number;
  mortality_risk?: number;
  combined_risk?: number;
  risk_level?: "low" | "medium" | "high" | "critical" | string;
  risk_color?: string;
  contributing_factors?: string[];
  recommendations?: string[];
}

const LEVEL_COLOR: Record<string, string> = {
  low: "text-green-400 border-green-500/30 bg-green-500/5",
  medium: "text-amber-400 border-amber-500/30 bg-amber-500/5",
  high: "text-orange-400 border-orange-500/30 bg-orange-500/5",
  critical: "text-red-400 border-red-500/30 bg-red-500/5",
};

/**
 * Renders the rationale fields the oncology service already returns alongside
 * the risk score (`contributing_factors`, `recommendations`). This is the
 * clinician-facing "why" — preferred over wiring `/api/xai/explain` which
 * requires a prediction_id we don't have access to from this page.
 */
export default function RiskRationale({ data }: { data: RiskPayload | null }) {
  if (!data) return null;
  const factors = data.contributing_factors ?? [];
  const recs = data.recommendations ?? [];
  if (factors.length === 0 && recs.length === 0) return null;

  const level = (data.risk_level ?? "").toLowerCase();
  const levelCls = LEVEL_COLOR[level] ?? "text-slate-300 border-border bg-slate-500/5";

  return (
    <div className={`border rounded-lg p-4 ${levelCls}`}>
      <div className="flex items-center gap-2 mb-3">
        <Info className="w-4 h-4" />
        <span className="text-sm font-semibold">
          Why this risk score
        </span>
        {data.risk_level && (
          <span className="ml-auto text-[10px] uppercase tracking-wider opacity-80">
            {data.risk_level} risk
          </span>
        )}
      </div>

      {factors.length > 0 && (
        <div className="space-y-1.5 mb-3">
          <div className="text-[10px] uppercase tracking-wider opacity-70">
            Contributing factors
          </div>
          <ul className="space-y-1">
            {factors.slice(0, 6).map((f, i) => (
              <li key={i} className="text-xs leading-snug text-slate-200">
                <span className="opacity-60 mr-1.5">·</span>
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}

      {recs.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] uppercase tracking-wider opacity-70 flex items-center gap-1">
            <Lightbulb className="w-3 h-3" />
            Suggested actions
          </div>
          <ul className="space-y-1">
            {recs.slice(0, 4).map((r, i) => (
              <li key={i} className="text-xs leading-snug text-slate-200">
                <span className="opacity-60 mr-1.5">→</span>
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
