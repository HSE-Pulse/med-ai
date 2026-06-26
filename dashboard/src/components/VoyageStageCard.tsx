import { useMemo } from "react";
import {
  Hospital,
  BedDouble,
  Activity,
  ArrowRightLeft,
  DoorOpen,
  Stethoscope,
  AlertTriangle,
} from "lucide-react";
import type { VoyageStage } from "../lib/voyage";
import {
  formatSimTime,
  durationLabel,
  formatPct,
  pctToColor,
} from "../lib/voyage";
import { RISK_COLORS } from "../lib/colors";
import AcuityBadge from "./AcuityBadge";

interface Props {
  stage: VoyageStage;
  /** Apply a transient pulse class when a freshly-arrived event maps to this stage. */
  pulseClass?: string;
}

const STAGE_ICON = {
  ADMISSION: Hospital,
  BED_ALLOCATION: BedDouble,
  WARD_PHASE: Activity,
  TRANSFER: ArrowRightLeft,
  DISCHARGE: DoorOpen,
} as const;

export default function VoyageStageCard({ stage, pulseClass = "" }: Props) {
  const Icon = STAGE_ICON[stage.type] ?? Stethoscope;
  const headerRight = useMemo(() => {
    if (stage.type === "WARD_PHASE") {
      return durationLabel(stage.intime, stage.outtime);
    }
    return formatSimTime(stage.sim_time);
  }, [stage]);

  return (
    <article
      className={`relative bg-bg-card rounded-xl border border-border overflow-hidden animate-fade-in ${pulseClass}`}
    >
      <div
        className="absolute left-0 top-0 bottom-0 w-1.5"
        style={{ backgroundColor: stage.stripeColor }}
        aria-hidden
      />
      <header className="flex items-center justify-between gap-2 px-4 py-2 border-b border-border pl-5">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="w-4 h-4 text-text-muted flex-shrink-0" />
          <h3 className="text-sm font-semibold text-text-primary truncate">{stage.title}</h3>
          <span className="text-[10px] text-text-muted uppercase tracking-wider font-mono-clinical">
            {stage.type.replace("_", " ")}
          </span>
        </div>
        <span className="text-[11px] text-text-muted font-mono-clinical flex-shrink-0">
          {headerRight}
        </span>
      </header>
      <div className="px-4 py-3 pl-5 space-y-2">{renderBody(stage)}</div>
    </article>
  );
}

function renderBody(s: VoyageStage) {
  switch (s.type) {
    case "ADMISSION":
      return <AdmissionBody stage={s} />;
    case "BED_ALLOCATION":
      return <BedAllocationBody stage={s} />;
    case "WARD_PHASE":
      return <WardPhaseBody stage={s} />;
    case "TRANSFER":
      return <TransferBody stage={s} />;
    case "DISCHARGE":
      return <DischargeBody stage={s} />;
  }
}

function Chip({
  label,
  value,
  color,
}: {
  label: string;
  value: React.ReactNode;
  color?: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] border"
      style={{
        borderColor: (color ?? "#94a3b8") + "55",
        backgroundColor: (color ?? "#94a3b8") + "15",
        color: color ?? "var(--color-text-primary)",
      }}
    >
      <span className="text-text-muted">{label}</span>
      <span className="font-mono-clinical font-semibold">{value}</span>
    </span>
  );
}

function AdmissionBody({ stage }: { stage: Extract<VoyageStage, { type: "ADMISSION" }> }) {
  const { admission, aiTriage, aiFlow, aiOncology } = stage;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {aiTriage && (
          <AcuityBadge
            level={Math.max(1, Math.min(5, aiTriage.acuity_level)) as 1 | 2 | 3 | 4 | 5}
            size="sm"
          />
        )}
        {admission.admission_type && (
          <Chip label="Type" value={admission.admission_type} color="#3B82F6" />
        )}
        {admission.admission_location && (
          <Chip label="From" value={admission.admission_location} color="#94a3b8" />
        )}
        {aiTriage?.disposition && (
          <Chip
            label="Disposition"
            value={aiTriage.disposition.replace(/_/g, " ")}
            color="#8B5CF6"
          />
        )}
      </div>
      {(aiFlow || aiOncology || aiTriage) && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {aiFlow?.pet_breach_risk != null && (
            <Chip
              label="PET risk"
              value={formatPct(aiFlow.pet_breach_risk)}
              color={pctToColor(aiFlow.pet_breach_risk)}
            />
          )}
          {aiFlow?.lwbs_risk != null && (
            <Chip
              label="LWBS risk"
              value={formatPct(aiFlow.lwbs_risk)}
              color={pctToColor(aiFlow.lwbs_risk)}
            />
          )}
          {aiFlow?.predicted_ed_los_minutes != null && aiFlow.predicted_ed_los_minutes > 0 && (
            <Chip
              label="ED LOS"
              value={`${Math.round(aiFlow.predicted_ed_los_minutes)} min`}
              color="#14B8A6"
            />
          )}
          {aiOncology?.readmission_30d_risk != null && (
            <Chip
              label="Readmit 30d"
              value={formatPct(aiOncology.readmission_30d_risk)}
              color={pctToColor(aiOncology.readmission_30d_risk)}
            />
          )}
        </div>
      )}
      {aiTriage?.risk_factors && aiTriage.risk_factors.length > 0 && (
        <ul className="text-[11px] text-text-muted pt-1">
          {aiTriage.risk_factors.slice(0, 4).map((r, i) => (
            <li key={i} className="flex items-start gap-1">
              <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0 text-amber-500" />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function BedAllocationBody({ stage }: { stage: Extract<VoyageStage, { type: "BED_ALLOCATION" }> }) {
  const bm = stage.bedManagement;
  const ba = stage.bedAllocation;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {ba?.recommended_bed && (
          <Chip label="Bed" value={ba.recommended_bed} color="#6366F1" />
        )}
        {bm?.discharge_probability_24h != null && (
          <Chip
            label="Discharge 24h"
            value={formatPct(bm.discharge_probability_24h)}
            color={pctToColor(bm.discharge_probability_24h)}
          />
        )}
        {bm?.discharge_probability_48h != null && (
          <Chip
            label="Discharge 48h"
            value={formatPct(bm.discharge_probability_48h)}
            color={pctToColor(bm.discharge_probability_48h)}
          />
        )}
        {bm?.discharge_readiness_score != null && (
          <Chip
            label="Readiness"
            value={formatPct(bm.discharge_readiness_score)}
            color={pctToColor(bm.discharge_readiness_score)}
          />
        )}
      </div>
      {ba?.allocation_reason && (
        <p className="text-[11px] text-text-muted italic pt-0.5">{ba.allocation_reason}</p>
      )}
      {bm?.key_factors && bm.key_factors.length > 0 && (
        <ul className="text-[11px] text-text-muted pt-0.5 list-disc pl-4">
          {bm.key_factors.slice(0, 3).map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function WardPhaseBody({ stage }: { stage: Extract<VoyageStage, { type: "WARD_PHASE" }> }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Chip label="Vitals" value={stage.vitalsCount} color="#22C55E" />
        <Chip label="Meds" value={stage.medCount} color="#3B82F6" />
        {stage.abnormalLabs.length > 0 && (
          <Chip
            label="Flagged labs"
            value={stage.abnormalLabs.length}
            color={RISK_COLORS.high}
          />
        )}
        {stage.ongoing && (
          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-500 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Ongoing
          </span>
        )}
      </div>
      {stage.deterAlert && (
        <div className="flex items-center gap-2 text-[11px]">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
          <span className="text-text-muted">
            {(stage.deterAlert.scoring_system ?? "NEWS2").toUpperCase()}
          </span>
          <span
            className="font-mono-clinical font-semibold"
            style={{ color: pctToColor(((stage.deterAlert.score?.total ?? 0) / 20) * 100) }}
          >
            {stage.deterAlert.score?.total ?? "?"} ({stage.deterAlert.score?.risk_band ?? "—"})
          </span>
        </div>
      )}
      {stage.sepsis?.alert_level && (
        <div className="text-[11px]">
          <span className="text-text-muted">Sepsis screen: </span>
          <span
            className="font-mono-clinical font-semibold"
            style={{ color: RISK_COLORS[(stage.sepsis.alert_level.toLowerCase() as keyof typeof RISK_COLORS)] ?? RISK_COLORS.info }}
          >
            {stage.sepsis.alert_level}
          </span>
          {stage.sepsis.sofa_total != null && (
            <span className="text-text-muted"> · SOFA {stage.sepsis.sofa_total}</span>
          )}
        </div>
      )}
      {stage.abnormalLabs.length > 0 && (
        <ul className="text-[10px] text-text-muted flex flex-wrap gap-x-2">
          {stage.abnormalLabs.map((l, i) => (
            <li key={i}>
              <span className="font-mono-clinical">{l.name}</span> {l.value} ({l.flag})
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function TransferBody({ stage }: { stage: Extract<VoyageStage, { type: "TRANSFER" }> }) {
  return (
    <div className="flex items-center gap-3 text-[12px]">
      <span
        className="rounded px-2 py-0.5"
        style={{ backgroundColor: stage.fromColor + "25", color: stage.fromColor }}
      >
        {stage.from}
      </span>
      <ArrowRightLeft className="w-3.5 h-3.5 text-text-muted" />
      <span
        className="rounded px-2 py-0.5"
        style={{ backgroundColor: stage.toColor + "25", color: stage.toColor }}
      >
        {stage.to}
      </span>
    </div>
  );
}

function DischargeBody({ stage }: { stage: Extract<VoyageStage, { type: "DISCHARGE" }> }) {
  const expired = (stage.hospital_expire_flag ?? 0) > 0;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {stage.discharge_location && (
          <Chip
            label="To"
            value={stage.discharge_location}
            color={expired ? RISK_COLORS.critical : RISK_COLORS.low}
          />
        )}
        {stage.discharge_reason && (
          <Chip label="Reason" value={stage.discharge_reason} color="#94a3b8" />
        )}
      </div>
      {stage.dischargeNote?.content && (
        <p className="text-[11px] text-text-muted italic line-clamp-3 pt-1">
          {stage.dischargeNote.content.slice(0, 220)}
          {stage.dischargeNote.content.length > 220 ? "…" : ""}
        </p>
      )}
    </>
  );
}
