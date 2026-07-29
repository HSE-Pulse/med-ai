import { useEffect, useState } from "react";

export interface ResolvedPatient {
  hadm_id: string;
  /**
   * Id to use against /api/journey/... endpoints.
   *
   * Those routes declare `hadm_id: int` and read historical MIMIC
   * collections keyed by that integer, so a simulated id
   * ("SIM-22755188-1792857544") is rejected with a 422 — every tab on
   * the patient page failed that way. The simulated id still identifies
   * the live admission, so both are carried rather than one replaced.
   */
  journey_hadm_id: string;
  subject_id: string;
  admission_type?: string | null;
  admission_location?: string | null;
  admittime?: string | null;
  dischtime?: string | null;
  // Patient-state flags from patient_journey's /patient/{id}/summary. The
  // journey API decorates each admission row with these so the dashboard
  // can banner-mark expired/discharged patients without re-deriving the
  // state in every consumer.
  is_expired?: boolean;
  is_active?: boolean;
  state_label?: "active" | "discharged" | "expired";
  discharge_reason?: string | null;
  /** Where the resolution succeeded — useful for diagnostics in the UI. */
  resolved_via: "journey" | "alerts_search" | "deterioration_alerts" | "unresolved";
}

interface AlertsSearchHit {
  hadm_id: string;
  subject_id: string;
  /** Set when the row came from the dev stub generator, not real storage. */
  is_demo?: boolean;
  /** MIMIC admission this simulated one replays; the id journey endpoints take. */
  original_hadm_id?: number | string | null;
  /** "admitted" | "discharged" | undefined (historical MIMIC row). */
  status?: string | null;
  admission_type?: string | null;
  admission_location?: string | null;
  admittime?: string | null;
  dischtime?: string | null;
}

interface DeteriorationAlert {
  hadm_id?: string;
  subject_id?: number | string;
  department?: string;
  observed_at?: string;
}

interface JourneyAdmission {
  hadm_id: number;
  admittime?: string;
  dischtime?: string;
  admission_type?: string;
  admission_location?: string;
  is_expired?: boolean;
  is_active?: boolean;
  state_label?: "active" | "discharged" | "expired";
  discharge_reason?: string | null;
}

async function fetchOk<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

/**
 * Extract the candidate hadm_id from a SIM-style id.
 * Format observed: "SIM-{hadm_id}-{epoch_ms}"
 */
function extractSimHadmId(id: string): string | null {
  if (!id.startsWith("SIM-")) return null;
  const parts = id.split("-");
  return parts.length >= 2 ? parts[1] : null;
}

/**
 * Pick the id that /api/journey/... will accept.
 *
 * Those routes are typed `hadm_id: int`. A simulated admission carries the
 * MIMIC admission it replays in `original_hadm_id`; failing that, the id is
 * embedded in the SIM string itself. Falling back to the raw id keeps purely
 * historical lookups working unchanged.
 */
function journeyHadmId(hadmId: string, originalHadmId?: number | string | null): string {
  if (originalHadmId !== undefined && originalHadmId !== null && originalHadmId !== "") {
    return String(originalHadmId);
  }
  const fromSim = extractSimHadmId(hadmId);
  if (fromSim && /^\d+$/.test(fromSim)) return fromSim;
  return hadmId;
}

async function tryAlertsSearch(query: string): Promise<AlertsSearchHit | null> {
  const r = await fetchOk<{ results?: AlertsSearchHit[] }>(
    `/api/alerts/search/patients?q=${encodeURIComponent(query)}&limit=5`,
  );
  if (!r?.results?.length) return null;
  // Never resolve a patient page onto a stub. The search endpoint used to
  // fabricate rows for any unmatched query, and taking results[0] turned
  // /patient/SIM-22755188-1792857544 into a page for "demo-1000". The
  // endpoint no longer invents rows, but a resolver that renders whatever
  // arrives first is the other half of that failure, so it now refuses
  // anything flagged as demo data.
  const real = r.results.filter((p) => !p.is_demo);
  if (!real.length) return null;
  // Exact id wins; otherwise prefer a live admission over a closed one
  // rather than trusting arrival order.
  return (
    real.find((p) => p.hadm_id === query) ??
    real.find((p) => p.status && p.status !== "discharged") ??
    real[0]
  );
}

async function tryDeteriorationScan(hadmId: string): Promise<DeteriorationAlert | null> {
  const r = await fetchOk<{ data?: DeteriorationAlert[] }>(
    `/api/deterioration/deterioration/active-alerts`,
  );
  return r?.data?.find((a) => a.hadm_id === hadmId) ?? null;
}

async function tryJourneySummary(subjectId: string): Promise<JourneyAdmission | null> {
  const r = await fetchOk<{ data?: { admissions?: JourneyAdmission[] } }>(
    `/api/journey/patient/${encodeURIComponent(subjectId)}/summary`,
  );
  // Most recent admission = best default for "the admission this URL is about"
  const admissions = r?.data?.admissions ?? [];
  if (admissions.length === 0) return null;
  return [...admissions].sort((a, b) => {
    const ta = Date.parse((a.admittime ?? "").replace(" ", "T")) || 0;
    const tb = Date.parse((b.admittime ?? "").replace(" ", "T")) || 0;
    return tb - ta;
  })[0];
}

async function tryJourneySummaryByHadm(
  subjectId: string,
  hadmId: string,
): Promise<JourneyAdmission | null> {
  const r = await fetchOk<{ data?: { admissions?: JourneyAdmission[] } }>(
    `/api/journey/patient/${encodeURIComponent(subjectId)}/summary`,
  );
  const admissions = r?.data?.admissions ?? [];
  // Prefer the admission whose hadm_id matches the URL — falls back to the
  // most recent if not found, so a SIM-* id still gets some state context.
  const exact = admissions.find((a) => String(a.hadm_id) === hadmId);
  if (exact) return exact;
  if (admissions.length === 0) return null;
  return [...admissions].sort((a, b) => {
    const ta = Date.parse((a.admittime ?? "").replace(" ", "T")) || 0;
    const tb = Date.parse((b.admittime ?? "").replace(" ", "T")) || 0;
    return tb - ta;
  })[0];
}

/**
 * Resolve the URL param `:id` into a useful `{hadm_id, subject_id}` pair, trying
 * multiple data sources. The current page used only the alerts index, which
 * silently failed for simulation IDs (`SIM-...`). This hook handles four cases:
 *
 *   1. Numeric subject_id — directly hits journey/summary.
 *   2. Numeric hadm_id    — found via alerts search.
 *   3. SIM-{hadm}-{epoch} — extracts hadm_id, finds subject via alerts search
 *                           or deterioration active-alerts.
 *   4. Unresolved         — surfaced as a distinct state so the UI can render
 *                           an explicit "we tried X, Y, Z" message.
 */
export function useResolvePatient(id: string | undefined): {
  patient: ResolvedPatient | null;
  loading: boolean;
} {
  const [patient, setPatient] = useState<ResolvedPatient | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      setPatient(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setPatient(null);

    (async () => {
      // (1) Direct journey lookup — works when id IS a numeric subject_id
      if (/^\d+$/.test(id)) {
        const adm = await tryJourneySummary(id);
        if (cancelled) return;
        if (adm) {
          setPatient({
            hadm_id: String(adm.hadm_id),
            journey_hadm_id: journeyHadmId(String(adm.hadm_id)),
            subject_id: id,
            admission_type: adm.admission_type ?? null,
            admission_location: adm.admission_location ?? null,
            admittime: adm.admittime ?? null,
            dischtime: adm.dischtime ?? null,
            is_active: adm.is_active,
            is_expired: adm.is_expired,
            state_label: adm.state_label,
            discharge_reason: adm.discharge_reason ?? null,
            resolved_via: "journey",
          });
          setLoading(false);
          return;
        }
      }

      // (2) Alerts search — handles raw hadm_id and many other shapes
      const hit = await tryAlertsSearch(id);
      if (cancelled) return;
      if (hit) {
        // Follow-up journey lookup so the page banner can render the
        // active/discharged/expired state. Best-effort — failures don't
        // block resolution.
        const adm = await tryJourneySummaryByHadm(hit.subject_id, hit.hadm_id);
        if (cancelled) return;
        setPatient({
          ...hit,
          journey_hadm_id: journeyHadmId(hit.hadm_id, hit.original_hadm_id),
          resolved_via: "alerts_search",
          is_active: adm?.is_active,
          is_expired: adm?.is_expired,
          state_label: adm?.state_label,
          discharge_reason: adm?.discharge_reason ?? null,
        });
        setLoading(false);
        return;
      }

      // (3) SIM-style ID — extract hadm_id, try search again, then deterioration
      const simHadm = extractSimHadmId(id);
      if (simHadm) {
        const simHit = await tryAlertsSearch(simHadm);
        if (cancelled) return;
        if (simHit) {
          const adm = await tryJourneySummaryByHadm(simHit.subject_id, simHit.hadm_id);
          if (cancelled) return;
          setPatient({
            ...simHit,
            journey_hadm_id: journeyHadmId(simHit.hadm_id, simHit.original_hadm_id),
            resolved_via: "alerts_search",
            is_active: adm?.is_active,
            is_expired: adm?.is_expired,
            state_label: adm?.state_label,
            discharge_reason: adm?.discharge_reason ?? null,
          });
          setLoading(false);
          return;
        }
        const detHit = await tryDeteriorationScan(id);
        if (cancelled) return;
        if (detHit && detHit.subject_id !== undefined) {
          setPatient({
            hadm_id: id,
            journey_hadm_id: journeyHadmId(id),
            subject_id: String(detHit.subject_id),
            admission_type: null,
            admission_location: detHit.department ?? null,
            admittime: detHit.observed_at ?? null,
            dischtime: null,
            resolved_via: "deterioration_alerts",
          });
          setLoading(false);
          return;
        }
      }

      // (4) Nothing resolved — render explicit unresolved state
      setPatient({
        hadm_id: id,
        journey_hadm_id: journeyHadmId(id),
        subject_id: id,
        admission_type: null,
        admission_location: null,
        resolved_via: "unresolved",
      });
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [id]);

  return { patient, loading };
}
