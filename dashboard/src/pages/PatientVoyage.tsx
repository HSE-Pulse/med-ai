import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Activity, Wifi, WifiOff } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import HospitalMap from "../components/HospitalMap";
import VoyageStageCard from "../components/VoyageStageCard";
import VoyageVitalsStrip from "../components/VoyageVitalsStrip";
import SimConsole from "../components/SimConsole";
import DigitalTwinPipeline from "../components/DigitalTwinPipeline";
import VoyageRail from "../components/VoyageRail";
import { useVoyageStream } from "../hooks/useVoyageStream";
import { useAlerts } from "../context/AlertsContext";
import {
  buildStages,
  formatSimTime,
  severityToClass,
  type JourneyPayload,
  type DigitalTwinSnapshot,
  type DeteriorationAlert,
  type ScribeNote,
  type VitalKey,
  type VitalPoint,
} from "../lib/voyage";

// Live patient voyage — admission → discharge cinematic timeline with
// hospital-wide context map on the left and a deep single-patient view
// on the right.
export default function PatientVoyage() {
  const [params, setParams] = useSearchParams();
  const selectedHadm = params.get("hadm");
  const setSelectedHadm = useCallback(
    (h: string | null) => {
      setParams(h ? { hadm: h } : {}, { replace: true });
    },
    [setParams],
  );

  const [journey, setJourney] = useState<JourneyPayload | null>(null);
  const [twin, setTwin] = useState<DigitalTwinSnapshot | null>(null);
  const [deter, setDeter] = useState<DeteriorationAlert[]>([]);
  const [notes, setNotes] = useState<ScribeNote[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [simSpeed, setSimSpeed] = useState<number | null>(null);

  const loadSnapshot = useCallback(async (hadm: string) => {
    setLoading(true);
    setErr(null);
    try {
      const [jRes, tRes, dRes, nRes] = await Promise.allSettled([
        fetch(`/api/sim/patient/${encodeURIComponent(hadm)}/journey`).then((r) => r.json()),
        fetch(`/api/sim/digital-twin/patient/${encodeURIComponent(hadm)}`).then((r) => r.json()),
        fetch(`/api/deterioration/deterioration/history/${encodeURIComponent(hadm)}`).then((r) => r.json()),
        fetch(`/api/scribe/notes/by-encounter/${encodeURIComponent(hadm)}`).then((r) => r.json()),
      ]);
      setJourney(jRes.status === "fulfilled" ? (jRes.value as JourneyPayload) : null);
      setTwin(
        tRes.status === "fulfilled" && tRes.value?.status === "ok"
          ? (tRes.value.data as DigitalTwinSnapshot)
          : null,
      );
      setDeter(
        dRes.status === "fulfilled" && dRes.value?.status === "ok"
          ? (dRes.value.data as DeteriorationAlert[]) || []
          : [],
      );
      setNotes(
        nRes.status === "fulfilled" && nRes.value?.status === "ok"
          ? (nRes.value.data as ScribeNote[]) || []
          : [],
      );
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedHadm) {
      setJourney(null);
      setTwin(null);
      setDeter([]);
      setNotes([]);
      return;
    }
    loadSnapshot(selectedHadm);
  }, [selectedHadm, loadSnapshot]);

  // Defensive resync every 30s while a patient is selected.
  useEffect(() => {
    if (!selectedHadm) return;
    const id = window.setInterval(() => loadSnapshot(selectedHadm), 30000);
    return () => window.clearInterval(id);
  }, [selectedHadm, loadSnapshot]);

  // Pull current sim speed once (for the SimConsole badge).
  useEffect(() => {
    fetch("/api/sim/state")
      .then((r) => r.json())
      .then((s) => {
        if (typeof s?.speed === "number") setSimSpeed(s.speed);
      })
      .catch(() => {});
  }, []);

  const refetch = useCallback(() => {
    if (selectedHadm) loadSnapshot(selectedHadm);
  }, [selectedHadm, loadSnapshot]);

  const stream = useVoyageStream({ selectedHadm, refetchSnapshot: refetch });

  const mergedVitals = useMemo<Partial<Record<VitalKey, VitalPoint[]>>>(() => {
    const base = journey?.vitals_series ?? {};
    const merged: Partial<Record<VitalKey, VitalPoint[]>> = {};
    const keys: VitalKey[] = ["hr", "rr", "spo2", "sbp", "dbp", "temp"];
    for (const k of keys) {
      const snap = (base[k] ?? []) as VitalPoint[];
      const delta = stream.vitalsDelta[k] ?? [];
      const seen = new Set(snap.map((p) => p.time));
      const extra = delta.filter((p) => !seen.has(p.time));
      merged[k] = snap.concat(extra);
    }
    return merged;
  }, [journey, stream.vitalsDelta]);

  const stages = useMemo(
    () => buildStages(journey, twin, deter, notes),
    [journey, twin, deter, notes],
  );

  const discharged = useMemo(
    () => journey?.admission?.status === "discharged" || !!journey?.admission?.sim_dischtime,
    [journey],
  );

  // Patient-specific alerts and severity overlay.
  const { alerts } = useAlerts();
  const patientAlerts = useMemo(
    () => (selectedHadm ? alerts.filter((a) => a.patient_id === selectedHadm) : []),
    [alerts, selectedHadm],
  );
  const patientSeverity = useMemo(() => {
    const m: Record<string, string> = {};
    for (const a of alerts) {
      if (a.patient_id && !a.acknowledged) m[a.patient_id] = a.severity;
    }
    return m;
  }, [alerts]);

  // For DigitalTwinPipeline pulses — recent patient event types + alert sources.
  const recentEventTypes = useMemo(
    () => stream.patientEvents.slice(0, 6).map((e) => e.event),
    [stream.patientEvents],
  );
  const recentAlertSources = useMemo(
    () => patientAlerts.slice(0, 6).map((a) => a.source_module),
    [patientAlerts],
  );

  return (
    <div className="flex flex-col gap-4 animate-fade-in">
      {/* Top band — sim console always visible */}
      <SimConsole
        wsStatus={stream.status}
        latestSimTime={stream.latestSimTime ?? journey?.sim_time ?? null}
        lastTickWall={stream.lastTickWall}
        rateBuckets={stream.rateBuckets}
        totals={stream.totals}
        hospitalEvents={stream.hospitalEvents}
        speedX={simSpeed ?? undefined}
      />

      <div className="grid grid-cols-12 gap-4">
        <aside className="col-span-12 lg:col-span-5 xl:col-span-5">
          <div className="lg:sticky lg:top-0">
            <HospitalMap
              selectedHadm={selectedHadm}
              onSelect={setSelectedHadm}
              patientSeverity={patientSeverity}
              recentTransfers={stream.recentTransfers}
            />
          </div>
        </aside>

        <section className="col-span-12 lg:col-span-7 xl:col-span-7 flex flex-col gap-3">
          <VoyageHeader
            journey={journey}
            loading={loading}
            err={err}
            selectedHadm={selectedHadm}
            wsStatus={stream.status}
          />

          {selectedHadm && stages.length > 0 && (
            <VoyageRail stages={stages} discharged={discharged} />
          )}

          {selectedHadm && (
            <VoyageVitalsStrip vitalsSeries={mergedVitals} deterAlerts={deter} />
          )}

          {selectedHadm && (
            <DigitalTwinPipeline
              pipelineResult={twin?.pipeline_results?.[0]}
              recentEventTypes={recentEventTypes}
              recentAlertSources={recentAlertSources}
            />
          )}

          {selectedHadm && patientAlerts.length > 0 && (
            <PatientAlerts alerts={patientAlerts.slice(0, 5)} />
          )}

          {selectedHadm ? (
            loading && !journey ? (
              <LoadingState />
            ) : stages.length === 0 ? (
              <EmptyState message="Snapshot returned no data for this patient." />
            ) : (
              <ol className="space-y-3">
                {stages.map((stage) => {
                  const sev = patientAlerts.find(
                    (a) => sourceMatchesStage(a.source_module, stage.type),
                  )?.severity;
                  return (
                    <li key={stage.id}>
                      <VoyageStageCard stage={stage} pulseClass={severityToClass(sev)} />
                    </li>
                  );
                })}
              </ol>
            )
          ) : (
            <EmptyState message="Select a patient from the hospital map to begin their voyage." />
          )}
        </section>
      </div>
    </div>
  );
}

function sourceMatchesStage(source: string, stageType: string): boolean {
  const s = (source || "").toLowerCase();
  switch (stageType) {
    case "ADMISSION":
      return s === "ed_flow" || s === "ed_triage";
    case "BED_ALLOCATION":
      return s === "bed_management";
    case "WARD_PHASE":
      return s === "deterioration" || s === "sepsis_icu";
    default:
      return false;
  }
}

function VoyageHeader({
  journey,
  loading,
  err,
  selectedHadm,
  wsStatus,
}: {
  journey: JourneyPayload | null;
  loading: boolean;
  err: string | null;
  selectedHadm: string | null;
  wsStatus: "connecting" | "open" | "closed";
}) {
  if (!selectedHadm) return null;
  const wsColor =
    wsStatus === "open" ? "#22C55E" : wsStatus === "connecting" ? "#EAB308" : "#F97316";
  const WsIcon = wsStatus === "open" ? Wifi : WifiOff;
  return (
    <header className="bg-bg-card rounded-xl border border-border px-4 py-3 flex items-center justify-between flex-wrap gap-2">
      <div className="flex items-center gap-3 min-w-0">
        <Activity className="w-4 h-4 text-blue-500 flex-shrink-0" />
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-text-primary truncate">
            Voyage —{" "}
            <span className="font-mono-clinical">{journey?.hadm_id ?? selectedHadm}</span>
          </h2>
          <p className="text-[11px] text-text-muted">
            {journey?.subject_id ? `Subject #${journey.subject_id} · ` : ""}
            Admitted {formatSimTime(journey?.admission?.sim_admittime)}
            {journey?.admission?.status === "discharged" && (
              <span className="text-emerald-500"> · Discharged</span>
            )}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 text-[11px] text-text-muted">
        {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
        {err && <span className="text-red-400 truncate max-w-[200px]">{err}</span>}
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold"
          style={{ borderColor: wsColor + "55", backgroundColor: wsColor + "15", color: wsColor }}
        >
          <WsIcon className="w-3 h-3" />
          {wsStatus === "open" ? "LIVE" : wsStatus === "connecting" ? "CONN" : "POLL"}
        </span>
      </div>
    </header>
  );
}

function PatientAlerts({
  alerts,
}: {
  alerts: Array<{
    id: string;
    severity: string;
    title: string;
    source_module: string;
    timestamp: string;
  }>;
}) {
  return (
    <div className="bg-bg-card rounded-xl border border-border p-3" role="region" aria-label="Patient alerts">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Active alerts
        </span>
        <span className="text-[10px] text-text-muted">{alerts.length}</span>
      </div>
      <ul className="space-y-1">
        {alerts.map((a) => {
          const sevColor =
            a.severity === "critical"
              ? "#DC2626"
              : a.severity === "high"
                ? "#F97316"
                : a.severity === "medium"
                  ? "#EAB308"
                  : "#3B82F6";
          return (
            <li
              key={a.id}
              className="flex items-start gap-2 text-[11px] py-1 border-l-2 pl-2"
              style={{ borderLeftColor: sevColor }}
            >
              <span
                className="font-semibold uppercase text-[9px] font-mono-clinical mt-0.5"
                style={{ color: sevColor }}
              >
                {a.severity}
              </span>
              <span className="text-text-primary flex-1">{a.title}</span>
              <span className="text-text-muted text-[10px]">{a.source_module}</span>
              <span className="text-text-muted text-[10px] font-mono-clinical">
                {formatSimTime(a.timestamp)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="bg-bg-card rounded-xl border border-border p-6 text-center text-text-muted text-sm flex items-center justify-center gap-2">
      <Loader2 className="w-4 h-4 animate-spin" />
      Loading patient voyage…
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="bg-bg-card rounded-xl border border-dashed border-border p-8 text-center text-text-muted text-sm">
      {message}
    </div>
  );
}
