import { useEffect, useMemo, useState } from "react";
import {
  Stethoscope,
  Workflow,
  BedDouble,
  HeartPulse,
  AlertTriangle,
  FileText,
  Microscope,
} from "lucide-react";
import type { PipelineResult } from "../lib/voyage";

interface Props {
  pipelineResult?: PipelineResult;
  /** Recent patient events from the live stream — used to pulse the matching node. */
  recentEventTypes?: string[];
  /** Recent alert source_modules for the patient — also pulse those nodes. */
  recentAlertSources?: string[];
}

interface ModuleSpec {
  id: string;
  label: string;
  icon: typeof Stethoscope;
  color: string;
  /** Predicate: did this module fire based on the pipeline_results snapshot? */
  firedFromPipeline: (pr?: PipelineResult) => boolean;
  /** Recent event types in the patient stream that imply this module fired. */
  eventTypes: string[];
  /** Backend module names that map to this node (for alert source_module match). */
  sourceMatches: string[];
}

const MODULES: ModuleSpec[] = [
  {
    id: "ed_triage",
    label: "ED Triage",
    icon: Stethoscope,
    color: "#3B82F6",
    firedFromPipeline: (pr) => !!pr?.ed_triage,
    eventTypes: ["admission"],
    sourceMatches: ["ed_triage"],
  },
  {
    id: "ed_flow",
    label: "ED Flow",
    icon: Workflow,
    color: "#8B5CF6",
    firedFromPipeline: (pr) => !!pr?.ed_flow,
    eventTypes: ["vital", "lab", "transfer", "discharge"],
    sourceMatches: ["ed_flow"],
  },
  {
    id: "bed_management",
    label: "Bed Mgmt",
    icon: BedDouble,
    color: "#6366F1",
    firedFromPipeline: (pr) => !!pr?.bed_management || !!pr?.bed_allocation,
    eventTypes: ["vital", "transfer", "discharge"],
    sourceMatches: ["bed_management"],
  },
  {
    id: "sepsis_icu",
    label: "Sepsis ICU",
    icon: HeartPulse,
    color: "#DC2626",
    firedFromPipeline: (pr) => !!pr?.sepsis_icu,
    eventTypes: ["vital", "lab"],
    sourceMatches: ["sepsis_icu"],
  },
  {
    id: "deterioration",
    label: "Deterioration",
    icon: AlertTriangle,
    color: "#F97316",
    firedFromPipeline: () => false, // deterioration fires from vitals not from pipeline_results
    eventTypes: ["vital"],
    sourceMatches: ["deterioration"],
  },
  {
    id: "oncology_ai",
    label: "Oncology AI",
    icon: Microscope,
    color: "#10B981",
    firedFromPipeline: (pr) => !!pr?.oncology_ai,
    eventTypes: [],
    sourceMatches: ["oncology_ai"],
  },
  {
    id: "clinical_scribe",
    label: "Clinical Scribe",
    icon: FileText,
    color: "#14B8A6",
    firedFromPipeline: (pr) => !!pr?.clinical_scribe,
    eventTypes: ["note", "vital"],
    sourceMatches: ["clinical_scribe"],
  },
];

export default function DigitalTwinPipeline({
  pipelineResult,
  recentEventTypes = [],
  recentAlertSources = [],
}: Props) {
  // Per-module pulse — true briefly when a matching event arrives.
  const [pulsing, setPulsing] = useState<Record<string, number>>({});

  // Watch for new event-type / alert-source triggers and bump pulses.
  const triggerKey = useMemo(
    () => recentEventTypes.slice(0, 5).join(",") + "|" + recentAlertSources.slice(0, 5).join(","),
    [recentEventTypes, recentAlertSources],
  );

  useEffect(() => {
    const now = Date.now();
    const next: Record<string, number> = { ...pulsing };
    let changed = false;
    for (const m of MODULES) {
      const evMatch = recentEventTypes.some((t) => m.eventTypes.includes(t));
      const srcMatch = recentAlertSources.some((s) => m.sourceMatches.includes(s));
      if (evMatch || srcMatch) {
        next[m.id] = now;
        changed = true;
      }
    }
    if (changed) setPulsing(next);
    // Decay pulses after 1.5s
    const t = window.setTimeout(() => {
      setPulsing((prev) => {
        const cutoff = Date.now() - 1500;
        const out: Record<string, number> = {};
        for (const [k, v] of Object.entries(prev)) if (v > cutoff) out[k] = v;
        return out;
      });
    }, 1600);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerKey]);

  return (
    <div
      className="bg-bg-card rounded-xl border border-border p-3"
      role="region"
      aria-label="Digital twin AI pipeline"
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Digital Twin Pipeline
        </span>
        <span className="text-[9px] text-text-muted">
          AI modules fire as events arrive for this patient
        </span>
      </div>
      <div className="relative">
        {/* Connector line behind nodes */}
        <div
          className="absolute top-7 left-0 right-0 h-px bg-border"
          aria-hidden
        />
        <div className="grid grid-cols-7 gap-1 relative">
          {MODULES.map((m) => {
            const Icon = m.icon;
            const fired = m.firedFromPipeline(pipelineResult);
            const isPulsing = !!pulsing[m.id];
            const opacity = fired || isPulsing ? 1 : 0.4;
            const ring = isPulsing
              ? `0 0 0 4px ${m.color}33, 0 0 18px ${m.color}88`
              : fired
                ? `0 0 0 2px ${m.color}55`
                : "none";
            return (
              <div key={m.id} className="flex flex-col items-center gap-1 min-w-0">
                <div
                  className={`w-14 h-14 rounded-full flex items-center justify-center transition-all duration-300 ${
                    isPulsing ? "scale-110" : ""
                  }`}
                  style={{
                    backgroundColor: m.color + "22",
                    boxShadow: ring,
                    opacity,
                  }}
                  aria-label={`${m.label}${fired ? " — fired" : ""}${isPulsing ? " — active" : ""}`}
                >
                  <Icon className="w-6 h-6" style={{ color: m.color }} />
                </div>
                <span
                  className="text-[10px] font-medium text-center truncate w-full"
                  style={{ color: fired || isPulsing ? "var(--color-text-primary)" : "var(--color-text-muted)" }}
                >
                  {m.label}
                </span>
                <PipelineSummary id={m.id} pr={pipelineResult} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function PipelineSummary({ id, pr }: { id: string; pr?: PipelineResult }) {
  if (!pr) return null;
  if (id === "ed_triage" && pr.ed_triage) {
    return (
      <span className="text-[9px] text-text-muted font-mono-clinical">
        ESI-{pr.ed_triage.acuity_level}
      </span>
    );
  }
  if (id === "ed_flow" && pr.ed_flow) {
    const pet = pr.ed_flow.pet_breach_risk;
    if (pet != null) {
      return (
        <span className="text-[9px] text-text-muted font-mono-clinical">
          PET {Math.round((pet > 1 ? pet : pet * 100))}%
        </span>
      );
    }
  }
  if (id === "bed_management" && pr.bed_management) {
    const d = pr.bed_management.discharge_probability_24h;
    if (d != null) {
      return (
        <span className="text-[9px] text-text-muted font-mono-clinical">
          D24 {Math.round((d > 1 ? d : d * 100))}%
        </span>
      );
    }
  }
  if (id === "sepsis_icu" && pr.sepsis_icu?.alert_level) {
    return (
      <span className="text-[9px] text-text-muted font-mono-clinical">
        {pr.sepsis_icu.alert_level}
      </span>
    );
  }
  if (id === "clinical_scribe" && pr.clinical_scribe?.queued != null) {
    return (
      <span className="text-[9px] text-text-muted font-mono-clinical">
        {pr.clinical_scribe.queued} queued
      </span>
    );
  }
  return null;
}
