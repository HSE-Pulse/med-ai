import { useMemo } from "react";
import { Activity, AlertTriangle, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { usePoll } from "../../hooks/usePoll";
import VitalSignChart from "../VitalSignChart";
import { formatTime } from "../../utils/format";

interface NewsScore {
  total: number;
  components?: Record<string, number>;
  any_param_eq_3?: boolean;
  recommended_response?: string;
}

interface HistoryEntry {
  hadm_id: string;
  observed_at: string;
  scoring_system: string;
  score?: NewsScore;
}

interface TrendPayload {
  slope?: number;
  is_clinically_rising?: boolean;
  rise_hours?: number;
  rise_severity?: "amber" | "red" | "green" | string;
  trend_comment?: string;
}

interface ActiveAlert {
  hadm_id: string;
  scoring_system?: string;
  department?: string;
  score?: NewsScore;
  observed_at?: string;
}

const SEVERITY_COLOR: Record<string, string> = {
  red: "border-red-500/40 text-red-400",
  amber: "border-amber-500/40 text-amber-400",
  green: "border-green-500/40 text-green-400",
};

function severityToColor(s?: string) {
  if (!s) return "border-border text-slate-400";
  return SEVERITY_COLOR[s] ?? "border-border text-slate-400";
}

function TrendIcon({ slope }: { slope?: number }) {
  if (slope === undefined || Math.abs(slope) < 0.01) {
    return <Minus className="w-3.5 h-3.5 text-slate-500" />;
  }
  return slope > 0 ? (
    <TrendingUp className="w-3.5 h-3.5 text-red-400" />
  ) : (
    <TrendingDown className="w-3.5 h-3.5 text-green-400" />
  );
}

export default function DeteriorationSummary({ hadmId }: { hadmId: string }) {
  const history = usePoll<{ data?: HistoryEntry[] }>(
    `/api/deterioration/deterioration/history/${encodeURIComponent(hadmId)}`,
    15000,
  );
  const trend = usePoll<{ data?: TrendPayload }>(
    `/api/deterioration/deterioration/trend/${encodeURIComponent(hadmId)}`,
    15000,
  );
  const activeAlerts = usePoll<{ data?: ActiveAlert[] }>(
    `/api/deterioration/deterioration/active-alerts`,
    10000,
  );

  const news2Series = useMemo(() => {
    const entries = history.data?.data ?? [];
    return entries
      .filter((e) => e.scoring_system === "news2" && e.score?.total !== undefined)
      .map((e) => ({ time: e.observed_at, value: e.score?.total ?? 0 }));
  }, [history.data]);

  const ourActiveAlert = useMemo(
    () => (activeAlerts.data?.data ?? []).find((a) => a.hadm_id === hadmId),
    [activeAlerts.data, hadmId],
  );

  const trendData = trend.data?.data;
  const latest = news2Series[news2Series.length - 1];
  const hasAnyData = news2Series.length > 0 || ourActiveAlert !== undefined;

  if (!hasAnyData && !history.loading && !activeAlerts.loading) {
    return (
      <div className="bg-bg-card border border-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-2">
          <Activity className="w-4 h-4 text-slate-400" />
          <span className="text-sm font-semibold text-white">Deterioration (NEWS2)</span>
        </div>
        <p className="text-xs text-slate-500">
          No deterioration screening recorded for this admission.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-bg-card border border-border rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Activity className="w-4 h-4 text-blue-400" />
        <span className="text-sm font-semibold text-white">Deterioration (NEWS2)</span>
        {ourActiveAlert?.score?.any_param_eq_3 && (
          <span className="ml-auto text-[10px] uppercase px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/30 font-semibold">
            Single-param ≥ 3
          </span>
        )}
      </div>

      {/* Score + trend strip */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border p-3">
          <div className="text-[10px] text-slate-500 uppercase tracking-wider">Latest score</div>
          <div className="text-2xl font-mono-clinical font-semibold text-white mt-1">
            {ourActiveAlert?.score?.total ?? latest?.value ?? "—"}
          </div>
          <div className="text-[10px] text-slate-500 mt-1">
            {ourActiveAlert?.observed_at
              ? formatTime(ourActiveAlert.observed_at)
              : latest
                ? formatTime(latest.time)
                : ""}
          </div>
        </div>
        <div className={`rounded-lg border p-3 ${severityToColor(trendData?.rise_severity)}`}>
          <div className="text-[10px] uppercase tracking-wider opacity-70 flex items-center gap-1">
            Trend <TrendIcon slope={trendData?.slope} />
          </div>
          <div className="text-sm font-medium mt-1">
            {trendData?.rise_severity?.toUpperCase() ?? "—"}
          </div>
          <div className="text-[10px] opacity-70 mt-1">
            {trendData?.rise_hours !== undefined
              ? `${trendData.rise_hours.toFixed(1)} h window`
              : "no trend computed"}
          </div>
        </div>
        <div className="rounded-lg border border-border p-3">
          <div className="text-[10px] text-slate-500 uppercase tracking-wider">Recommendation</div>
          <div className="text-xs text-slate-300 mt-1 leading-tight">
            {ourActiveAlert?.score?.recommended_response ??
              trendData?.trend_comment ??
              "No active recommendation"}
          </div>
        </div>
      </div>

      {/* Trend chart — reuse VitalSignChart with NEWS2 ranges */}
      {news2Series.length > 0 && (
        <VitalSignChart
          data={news2Series}
          label="NEWS2 trend"
          unit="pts"
          normalMin={0}
          normalMax={4}
          height={90}
        />
      )}

      {/* Component breakdown when active */}
      {ourActiveAlert?.score?.components && (
        <div>
          <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">
            Component contributions
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(ourActiveAlert.score.components)
              .filter(([, v]) => v > 0)
              .sort(([, a], [, b]) => (b as number) - (a as number))
              .map(([name, value]) => (
                <span
                  key={name}
                  className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/20"
                >
                  {name.replace(/_/g, " ")}: {value as number}
                </span>
              ))}
          </div>
        </div>
      )}

      {!ourActiveAlert && trendData?.is_clinically_rising && (
        <div className="flex items-start gap-2 text-[11px] text-amber-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            Score rising clinically — no acknowledged active alert yet. Consider escalation.
          </span>
        </div>
      )}
    </div>
  );
}
