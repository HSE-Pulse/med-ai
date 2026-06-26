import { useMemo } from "react";
import { Hospital, BedDouble, Activity, DoorOpen, User2 } from "lucide-react";
import type { VoyageStage } from "../lib/voyage";

interface Props {
  stages: VoyageStage[];
  discharged?: boolean;
}

interface Station {
  id: string;
  label: string;
  icon: typeof Hospital;
  color: string;
  matchType: VoyageStage["type"] | "DISCHARGED";
  /** Optional careunit hint for ward-phase station deduplication. */
  hint?: string;
}

const STATIC_STATIONS: Station[] = [
  { id: "admit", label: "Admit", icon: Hospital, color: "#3B82F6", matchType: "ADMISSION" },
  { id: "bed", label: "Bed Alloc", icon: BedDouble, color: "#6366F1", matchType: "BED_ALLOCATION" },
  { id: "discharge", label: "Discharge", icon: DoorOpen, color: "#22C55E", matchType: "DISCHARGED" },
];

export default function VoyageRail({ stages, discharged = false }: Props) {
  // Build a station list = [admit, bed, ...ward_phases, discharge]
  // The ward phases come from the patient's actual care_path.
  const stations: Station[] = useMemo(() => {
    const wards = stages
      .filter((s): s is Extract<VoyageStage, { type: "WARD_PHASE" }> => s.type === "WARD_PHASE")
      .map((s, i) => ({
        id: `ward-${i}`,
        label: s.careunit,
        icon: Activity,
        color: s.stripeColor,
        matchType: "WARD_PHASE" as const,
        hint: s.careunit,
      }));
    const list: Station[] = [STATIC_STATIONS[0], STATIC_STATIONS[1], ...wards];
    if (discharged) list.push(STATIC_STATIONS[2]);
    return list;
  }, [stages, discharged]);

  // Compute which station the patient is "currently at" — the index of the
  // furthest-progressed stage in the timeline.
  const currentIdx = useMemo(() => {
    if (!stations.length) return 0;
    if (discharged) return stations.length - 1;
    // Find the index of the LAST ward phase that's ongoing, else last completed.
    const wardCount = stations.filter((s) => s.matchType === "WARD_PHASE").length;
    if (wardCount > 0) {
      // Index of last ward station
      return stations.length - 1;
    }
    if (stages.some((s) => s.type === "BED_ALLOCATION")) return 1;
    if (stages.some((s) => s.type === "ADMISSION")) return 0;
    return 0;
  }, [stations, stages, discharged]);

  return (
    <div
      className="bg-bg-card rounded-xl border border-border p-3"
      role="region"
      aria-label="Patient voyage rail"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Voyage Rail
        </span>
        <span className="text-[10px] text-text-muted">
          Station {Math.min(currentIdx + 1, stations.length)} of {stations.length}
        </span>
      </div>
      <div className="relative pt-6 pb-2">
        {/* Rail line */}
        <div
          className="absolute top-1/2 left-6 right-6 h-1 -translate-y-1/2 rounded-full bg-border"
          aria-hidden
        />
        {/* Progress fill */}
        <div
          className="absolute top-1/2 left-6 h-1 -translate-y-1/2 rounded-full transition-all duration-700"
          style={{
            width: `calc((100% - 48px) * ${stations.length > 1 ? currentIdx / (stations.length - 1) : 0})`,
            background:
              "linear-gradient(90deg, #3B82F6 0%, #8B5CF6 50%, #22C55E 100%)",
          }}
          aria-hidden
        />
        <div className="flex justify-between relative">
          {stations.map((s, i) => {
            const Icon = s.icon;
            const reached = i <= currentIdx;
            const active = i === currentIdx;
            return (
              <div key={s.id} className="flex flex-col items-center gap-1 relative">
                <div
                  className={`w-9 h-9 rounded-full flex items-center justify-center border-2 transition-all duration-300 ${
                    active ? "scale-110 pulse-critical" : ""
                  }`}
                  style={{
                    backgroundColor: reached ? s.color + "22" : "var(--color-bg-input)",
                    borderColor: reached ? s.color : "var(--color-border)",
                    boxShadow: active ? `0 0 16px ${s.color}88` : "none",
                  }}
                  aria-label={`${s.label}${active ? " — current" : reached ? " — reached" : ""}`}
                >
                  <Icon
                    className="w-4 h-4"
                    style={{ color: reached ? s.color : "var(--color-text-muted)" }}
                  />
                </div>
                {active && (
                  <div
                    className="absolute -top-5 left-1/2 -translate-x-1/2 animate-fade-in"
                    aria-hidden
                  >
                    <User2 className="w-4 h-4 text-blue-500" />
                  </div>
                )}
                <span
                  className="text-[9px] font-medium text-center max-w-[80px] truncate"
                  style={{
                    color: reached ? "var(--color-text-primary)" : "var(--color-text-muted)",
                  }}
                  title={s.label}
                >
                  {s.label.length > 12 ? s.label.slice(0, 11) + "…" : s.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
