import { useMemo, useState } from "react";
import { Bed, Shield, FileText, ChevronDown, ChevronRight } from "lucide-react";
import { usePoll } from "../../hooks/usePoll";
import { formatDate, formatTime } from "../../utils/format";

interface ScribeNote {
  note_id: string;
  note_type?: string;
  hadm_id?: string | number;
  patient_id?: number;
  generated_at?: string;
  status?: string;
  source?: string;
  original_note_id?: string | null;
  original_charttime?: string | null;
  approved_by?: string | null;
  soap?: { subjective?: string; objective?: string; assessment?: string; plan?: string } | null;
  full_text?: string;
  summary?: string;
  icd_codes?: Array<{ code: string; description?: string; confidence?: number; is_primary?: boolean }>;
  quality_score?: number;
}

interface LoungePatient {
  hadm_id: string;
  subject_id?: number | string;
  source_department?: string;
  arrived_at?: string;
  expected_departure_h?: number;
  initiated_by?: string;
}

interface LoungeStatus {
  capacity?: number;
  occupied?: number;
  available?: number;
  patients?: LoungePatient[];
  observed_at?: string;
}

interface EscalationRecord {
  hadm_id?: string;
  scoring_system?: string;
  rationale?: string;
  triggered_at?: string;
  acknowledged?: boolean;
  acknowledged_by?: string;
  acknowledged_at?: string;
  severity?: string;
}

/**
 * "Clinical context" panel — surfaces:
 *   - Clinical notes for this admission (Scribe-generated, MIMIC-IV-Note-derived)
 *   - Discharge-lounge status (filtered to this hadm_id from the global feed)
 *   - Recent deterioration escalations for this admission
 */
export default function ClinicalContext({ hadmId }: { hadmId: string }) {
  const lounge = usePoll<{ data?: LoungeStatus }>(
    `/api/discharge-lounge/discharge-lounge/status`,
    20000,
  );
  const escalations = usePoll<{ data?: EscalationRecord[] }>(
    `/api/deterioration/deterioration/escalations?limit=50`,
    20000,
  );
  const notes = usePoll<{ data?: ScribeNote[] }>(
    `/api/scribe/notes/by-encounter/${encodeURIComponent(hadmId)}?limit=20`,
    30000,
  );

  const myLoungeRecord = useMemo(
    () => (lounge.data?.data?.patients ?? []).find((p) => p.hadm_id === hadmId) ?? null,
    [lounge.data, hadmId],
  );

  const myEscalations = useMemo(
    () => (escalations.data?.data ?? []).filter((e) => e.hadm_id === hadmId).slice(0, 8),
    [escalations.data, hadmId],
  );

  return (
    <div className="space-y-3">
      {/* Discharge lounge */}
      <section className="bg-bg-card border border-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <Bed className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-semibold text-white">Discharge lounge</span>
          {lounge.data?.data && (
            <span className="ml-auto text-[10px] text-slate-500">
              lounge: {lounge.data.data.occupied ?? 0}/{lounge.data.data.capacity ?? 0} occupied
            </span>
          )}
        </div>
        {!myLoungeRecord ? (
          <p className="text-xs text-slate-500">
            Patient is not currently in the discharge lounge.
          </p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <div className="text-[10px] text-slate-500 uppercase">Source</div>
              <div className="text-slate-200 mt-0.5">
                {myLoungeRecord.source_department ?? "—"}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-slate-500 uppercase">Arrived</div>
              <div className="text-slate-200 mt-0.5">{formatTime(myLoungeRecord.arrived_at)}</div>
            </div>
            <div>
              <div className="text-[10px] text-slate-500 uppercase">Expected exit</div>
              <div className="text-slate-200 mt-0.5">
                {myLoungeRecord.expected_departure_h !== undefined
                  ? `${myLoungeRecord.expected_departure_h.toFixed(1)} h`
                  : "—"}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-slate-500 uppercase">Initiated by</div>
              <div className="text-slate-300 mt-0.5 truncate" title={myLoungeRecord.initiated_by}>
                {myLoungeRecord.initiated_by ?? "—"}
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Deterioration escalations */}
      <section className="bg-bg-card border border-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <Shield className="w-4 h-4 text-amber-400" />
          <span className="text-sm font-semibold text-white">Deterioration escalations</span>
          <span className="ml-auto text-[10px] text-slate-500">{myEscalations.length} recorded</span>
        </div>
        {myEscalations.length === 0 ? (
          <p className="text-xs text-slate-500">No escalations recorded for this admission.</p>
        ) : (
          <ul className="space-y-2">
            {myEscalations.map((e, i) => (
              <li
                key={i}
                className="flex items-start gap-3 text-xs border-l-2 pl-3 py-0.5"
                style={{
                  borderColor:
                    e.severity === "critical"
                      ? "#dc2626"
                      : e.severity === "high"
                        ? "#f97316"
                        : "#f59e0b",
                }}
              >
                <div className="flex-1">
                  <div className="text-slate-200">{e.rationale ?? "Escalation triggered"}</div>
                  <div className="text-slate-500 text-[10px] mt-0.5 flex gap-3">
                    <span>{formatDate(e.triggered_at)}</span>
                    {e.scoring_system && <span>· {e.scoring_system.toUpperCase()}</span>}
                    {e.acknowledged ? (
                      <span className="text-green-400">
                        ✓ ack {e.acknowledged_by ? `by ${e.acknowledged_by}` : ""}
                      </span>
                    ) : (
                      <span className="text-amber-400">unacknowledged</span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Notes — populated from Scribe (MIMIC-IV-Note-derived where available) */}
      <NotesSection notes={notes.data?.data ?? []} loading={notes.loading} />
    </div>
  );
}

function NotesSection({ notes, loading }: { notes: ScribeNote[]; loading: boolean }) {
  return (
    <section className="bg-bg-card border border-border rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <FileText className="w-4 h-4 text-blue-400" />
        <span className="text-sm font-semibold text-white">Clinical notes</span>
        <span className="ml-auto text-[10px] text-slate-500">{notes.length} for this admission</span>
      </div>
      {loading && notes.length === 0 ? (
        <p className="text-xs text-slate-500">Loading notes...</p>
      ) : notes.length === 0 ? (
        <p className="text-xs text-slate-500">No clinical notes recorded for this admission yet.</p>
      ) : (
        <ul className="space-y-2">
          {notes.map((n) => (
            <NoteCard key={n.note_id} note={n} />
          ))}
        </ul>
      )}
    </section>
  );
}

function NoteCard({ note }: { note: ScribeNote }) {
  const [open, setOpen] = useState(false);
  const isMimic = (note.source ?? "synthetic") === "mimic_iv_note";
  const ts = note.original_charttime || note.generated_at || "";
  const niceType = (note.note_type || "note").replace(/_/g, " ");
  return (
    <li className="border border-border rounded-md overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-800/40"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
        <span className="capitalize text-slate-200 font-medium">{niceType}</span>
        {isMimic ? (
          <span className="px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">MIMIC-IV-Note</span>
        ) : (
          <span className="px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider bg-slate-500/15 text-slate-400 border border-slate-500/30">synthetic</span>
        )}
        <span className={`px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider ${
          note.status === "approved"
            ? "bg-blue-500/15 text-blue-300 border border-blue-500/30"
            : "bg-amber-500/10 text-amber-300 border border-amber-500/30"
        }`}>{note.status || "draft"}</span>
        <span className="ml-auto text-[10px] text-slate-500 font-mono">{ts ? formatDate(ts) : ""}</span>
      </button>
      {open && (
        <div className="px-3 py-3 border-t border-border space-y-3 text-xs">
          {note.soap?.subjective && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Subjective</div>
              <p className="text-slate-200 leading-relaxed whitespace-pre-wrap">{note.soap.subjective}</p>
            </div>
          )}
          {note.soap?.objective && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Objective</div>
              <p className="text-slate-200 leading-relaxed whitespace-pre-wrap">{note.soap.objective}</p>
            </div>
          )}
          {note.soap?.assessment && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Assessment</div>
              <p className="text-slate-200 leading-relaxed whitespace-pre-wrap">{note.soap.assessment}</p>
            </div>
          )}
          {note.soap?.plan && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Plan</div>
              <p className="text-slate-200 leading-relaxed whitespace-pre-wrap">{note.soap.plan}</p>
            </div>
          )}
          {note.icd_codes && note.icd_codes.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">ICD codes</div>
              <div className="flex flex-wrap gap-1.5">
                {note.icd_codes.slice(0, 8).map((c, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded text-[10px] bg-slate-700/40 text-slate-300 font-mono">
                    {c.code}
                    {c.is_primary ? <span className="ml-1 text-emerald-400">★</span> : null}
                  </span>
                ))}
              </div>
            </div>
          )}
          {note.original_note_id && (
            <div className="text-[10px] text-slate-500 pt-1 border-t border-border">
              Source MIMIC note: <span className="font-mono">{note.original_note_id}</span>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
