import { useEffect, useState } from "react";
import { Link2, Search, RefreshCw, AlertCircle } from "lucide-react";
import {
  fhirCapability,
  fhirPatient,
  fhirEncounter,
  fhirObservations,
} from "../lib/api";

type CapState = "loading" | "ready" | "offline";

export default function FHIRGateway() {
  const [cap, setCap] = useState<any>(null);
  const [capState, setCapState] = useState<CapState>("loading");
  const [patientId, setPatientId] = useState("");
  const [hadmId, setHadmId] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [patient, setPatient] = useState<any>(null);
  const [encounter, setEncounter] = useState<any>(null);
  const [obs, setObs] = useState<any>(null);

  useEffect(() => {
    let cancelled = false;
    // Hard timeout — if the gateway hasn't responded in 8 s we flip to
    // an offline state so the operator isn't staring at a perpetual
    // "Loading…" string.
    const timeout = setTimeout(() => {
      if (!cancelled) setCapState((s) => (s === "loading" ? "offline" : s));
    }, 8000);
    fhirCapability()
      .then((c) => {
        if (cancelled) return;
        if (c) {
          setCap(c);
          setCapState("ready");
        } else {
          setCapState("offline");
        }
      })
      .catch(() => {
        if (!cancelled) setCapState("offline");
      });
    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, []);

  const refetchCap = () => {
    setCapState("loading");
    setCap(null);
    fhirCapability()
      .then((c) => (c ? (setCap(c), setCapState("ready")) : setCapState("offline")))
      .catch(() => setCapState("offline"));
  };

  async function lookup() {
    if (patientId) setPatient(await fhirPatient(patientId));
    if (hadmId && subjectId) {
      setEncounter(await fhirEncounter(hadmId, subjectId));
      setObs(await fhirObservations(hadmId, subjectId));
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold text-white flex items-center gap-2">
          <Link2 className="w-5 h-5 text-cyan-400" /> National EHR Gateway — HL7 FHIR R4
        </h2>
        <p className="text-sm text-slate-400">Port 8219 — HSE Shared Care Record interoperability. PPSN pseudonymised in simulation mode.</p>
      </div>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white">CapabilityStatement</h3>
          {capState === "offline" && (
            <button
              type="button"
              onClick={refetchCap}
              className="px-2 py-1 text-xs rounded bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-1"
            >
              <RefreshCw className="w-3 h-3" aria-hidden="true" /> Retry
            </button>
          )}
        </div>
        {capState === "ready" && cap ? (
          <div className="text-sm">
            <div className="mb-2"><span className="text-slate-400">FHIR Version:</span> <span className="text-white font-mono-clinical">{cap.fhirVersion}</span></div>
            <div className="flex flex-wrap gap-2">
              {(cap.rest?.[0]?.resource || []).map((r: any) => (
                <span key={r.type} className="px-2 py-0.5 text-xs rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                  {r.type} ({r.interaction.map((i: any) => i.code).join(", ")})
                </span>
              ))}
            </div>
          </div>
        ) : capState === "offline" ? (
          <div className="flex items-center gap-2 text-sm text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
            <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
            <span>FHIR gateway is unreachable. Resource lookup below may also be unavailable.</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <RefreshCw className="w-3 h-3 animate-spin" aria-hidden="true" />
            <span>Loading capability statement…</span>
          </div>
        )}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <Search className="w-4 h-4 text-blue-400" /> Resource Lookup
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-3">
          <input value={patientId} onChange={(e) => setPatientId(e.target.value)} placeholder="Patient ID (subject_id)" className="bg-bg-primary border border-border rounded px-3 py-1.5 text-sm text-white" />
          <input value={hadmId} onChange={(e) => setHadmId(e.target.value)} placeholder="Encounter / HADM ID" className="bg-bg-primary border border-border rounded px-3 py-1.5 text-sm text-white" />
          <input value={subjectId} onChange={(e) => setSubjectId(e.target.value)} placeholder="Subject ID (for encounter)" className="bg-bg-primary border border-border rounded px-3 py-1.5 text-sm text-white" />
        </div>
        <button onClick={lookup} className="px-3 py-1.5 text-sm rounded-lg bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Fetch
        </button>
      </section>

      {patient && (
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-2">Patient Resource</h3>
          <pre className="text-xs text-slate-300 bg-bg-primary rounded p-3 overflow-auto max-h-72">{JSON.stringify(patient, null, 2)}</pre>
        </section>
      )}
      {encounter && (
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-2">Encounter Resource</h3>
          <pre className="text-xs text-slate-300 bg-bg-primary rounded p-3 overflow-auto max-h-72">{JSON.stringify(encounter, null, 2)}</pre>
        </section>
      )}
      {obs && (
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-2">Observations Bundle ({(obs.entry || []).length})</h3>
          <pre className="text-xs text-slate-300 bg-bg-primary rounded p-3 overflow-auto max-h-96">{JSON.stringify(obs, null, 2)}</pre>
        </section>
      )}
    </div>
  );
}
