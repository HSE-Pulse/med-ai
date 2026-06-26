import { useState } from "react";
import {
  FileText, Code, Search, Sparkles, Loader2, Send,
  CheckCircle, AlertCircle,
} from "lucide-react";

/* ---------- types ---------- */
interface NoteResult {
  note_id: string; note_type: string; quality_score: number;
  faithfulness_score: number; model_used: string; status: string;
  soap?: { subjective: string; objective: string; assessment: string; plan: string };
  icd_codes?: Array<{ code: string; confidence: number; is_primary: boolean }>;
}
interface ICDCode { code: string; description: string; confidence: number; is_primary: boolean; }
interface CodingResult { icd10am_codes: ICDCode[]; coding_confidence: number; }
interface EntityResult {
  medications: Array<{ drug: string }>; diagnoses: Array<{ term: string; icd_code: string }>;
  symptoms: Array<{ symptom: string }>;
}

const SOAP_SECTIONS = [
  { key: "subjective", label: "S - Subjective", color: "blue" },
  { key: "objective",  label: "O - Objective",  color: "green" },
  { key: "assessment", label: "A - Assessment", color: "purple" },
  { key: "plan",       label: "P - Plan",       color: "amber" },
] as const;

export default function ClinicalScribe() {
  const [tab, setTab] = useState<"note" | "code" | "ner">("note");

  const [noteText, setNoteText] = useState("");
  const [noteType, setNoteType] = useState("progress_note");
  const [noteRes, setNoteRes] = useState<NoteResult | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);

  const [codeText, setCodeText] = useState("");
  const [codeRes, setCodeRes] = useState<CodingResult | null>(null);
  const [codeLoading, setCodeLoading] = useState(false);

  const [nerText, setNerText] = useState("");
  const [nerRes, setNerRes] = useState<EntityResult | null>(null);
  const [nerLoading, setNerLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const genNote = async () => {
    setNoteLoading(true);
    try {
      const r = await fetch("/api/scribe/generate-note/from-text", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clinical_text: noteText, note_type: noteType }),
      });
      const d = await r.json();
      if (d.status === "ok") setNoteRes(d.data);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setNoteLoading(false);
  };

  const codeNote = async () => {
    setCodeLoading(true);
    try {
      const r = await fetch("/api/scribe/code", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note_text: codeText }),
      });
      const d = await r.json();
      if (d.status === "ok") setCodeRes(d.data);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setCodeLoading(false);
  };

  const extractNER = async () => {
    setNerLoading(true);
    try {
      const r = await fetch("/api/scribe/extract-entities", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: nerText }),
      });
      const d = await r.json();
      if (d.status === "ok") setNerRes(d.data);
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    setNerLoading(false);
  };

  const tabs = [
    { id: "note" as const, label: "Note Generation", icon: FileText },
    { id: "code" as const, label: "ICD Coding", icon: Code },
    { id: "ner"  as const, label: "Entity Extraction", icon: Search },
  ];

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-2 rounded-lg text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300 ml-4">&times;</button>
        </div>
      )}
      {/* Tab bar */}
      <div className="flex gap-1 bg-bg-card rounded-xl border border-border p-1">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === id ? "bg-blue-500/20 text-blue-400" : "text-slate-400 hover:text-white hover:bg-slate-700/50"
            }`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {/* ───── Note Generation ───── */}
      {tab === "note" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-bg-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-blue-400" /> Generate Clinical Note
            </h2>
            <div className="mb-2">
              <label htmlFor="scribe-note-type" className="text-[11px] text-text-muted block mb-0.5">Note Type</label>
              <select id="scribe-note-type" value={noteType} onChange={e => setNoteType(e.target.value)}
                className="w-full bg-bg-input border border-border rounded px-2 py-1.5 text-sm text-text-primary focus:border-blue-500 focus:outline-none">
                <option value="progress_note">Progress Note</option>
                <option value="admission_note">Admission Note</option>
                <option value="discharge_summary">Discharge Summary</option>
                <option value="ed_note">ED Note</option>
                <option value="consultant_letter">Consultant Letter</option>
              </select>
            </div>
            <label htmlFor="scribe-note-text" className="sr-only">Clinical encounter text</label>
            <textarea id="scribe-note-text" value={noteText} onChange={e => setNoteText(e.target.value)}
              rows={16}
              aria-label="Clinical encounter text"
              placeholder="Enter clinical encounter text, transcript, or notes..."
              className="w-full min-h-[300px] bg-bg-input border border-border rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder-slate-600 focus:border-blue-500 focus:outline-none transition-colors resize-none font-mono-clinical" />
            <button onClick={genNote} disabled={noteLoading || !noteText.trim()}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white font-medium py-2.5 rounded-lg transition-colors text-sm mt-3">
              {noteLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {noteLoading ? "Generating..." : "Generate SOAP Note"}
            </button>
          </div>

          <div className="bg-bg-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-semibold text-text-primary mb-3">Generated Note</h2>
            {noteRes ? (() => {
              // The "Quality: X%" pill comes straight from the backend and
              // can read 100 % even when the model is a fallback keyword
              // extractor that left O/A/P as placeholder strings. Detect
              // the placeholder pattern and downgrade the badge so the
              // operator isn't misled into trusting the output.
              const PLACEHOLDER_RE = /^(examination findings to be documented|clinical assessment pending|plan to be confirmed)\.?$/i;
              const placeholderCount = noteRes.soap
                ? (Object.values(noteRes.soap) as string[]).filter((v) => v && PLACEHOLDER_RE.test(String(v).trim())).length
                : 0;
              const isFallback = (noteRes.model_used || "").toLowerCase().includes("keyword") || placeholderCount >= 2;
              const effectiveQuality = isFallback
                ? Math.min(noteRes.quality_score, 0.4)
                : noteRes.quality_score;
              return (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-[11px] flex-wrap gap-2">
                  <span className="text-text-muted">ID: <span className="font-mono-clinical">{noteRes.note_id}</span></span>
                  <span className="flex items-center gap-1">
                    {effectiveQuality > 0.7
                      ? <CheckCircle className="w-3.5 h-3.5 text-green-400" aria-hidden="true" />
                      : <AlertCircle className="w-3.5 h-3.5 text-yellow-400" aria-hidden="true" />}
                    <span className="text-text-secondary">
                      Quality: {(effectiveQuality * 100).toFixed(0)}%
                      {isFallback && <span className="ml-1 text-yellow-400">\u00b7 fallback</span>}
                    </span>
                  </span>
                </div>

                {isFallback && (
                  <div className="text-[11px] bg-yellow-500/10 border border-yellow-500/30 text-yellow-300 rounded-lg p-2 leading-snug">
                    Heads-up \u2014 this note was produced by a keyword-extraction fallback, not a clinical LLM. Treat
                    the Subjective copy as the source text and re-write Objective / Assessment / Plan manually.
                  </div>
                )}

                {noteRes.soap && SOAP_SECTIONS.map(({ key, label, color }) => {
                  const v = (noteRes.soap as Record<string, string>)[key] ?? "";
                  const isPlaceholder = PLACEHOLDER_RE.test(v.trim());
                  return (
                    <div key={key} className={`bg-${color}-500/10 border border-${color}-500/20 rounded-lg p-3`}>
                      <div className={`text-[11px] font-bold text-${color}-400 mb-1 flex items-center gap-1`}>
                        {label}
                        {isPlaceholder && <span className="text-yellow-400 font-normal">(template \u2014 needs author)</span>}
                      </div>
                      <div className="text-[11px] text-text-secondary leading-relaxed">
                        {v || "\u2014"}
                      </div>
                    </div>
                  );
                })}

                {noteRes.icd_codes && noteRes.icd_codes.length > 0 && (
                  <div className="bg-bg-primary rounded-lg p-3 space-y-1">
                    <div className="text-[11px] font-semibold text-text-muted mb-1">Suggested ICD codes</div>
                    {noteRes.icd_codes.map((c, i) => (
                      <div key={i} className="flex items-baseline gap-2 text-[11px]">
                        <span className="font-mono-clinical text-text-primary w-14 shrink-0">{c.code}</span>
                        <span className="flex-1 text-text-secondary truncate">
                          {(c as { description?: string }).description ?? "\u2014"}
                        </span>
                        <span className={c.confidence < 0.3 ? "text-yellow-400" : "text-text-muted"}>
                          {(c.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                    ))}
                    <div className="text-[10px] text-yellow-400/80 pt-1">
                      Suggestions only \u2014 confirm against the source note before submitting.
                    </div>
                  </div>
                )}

                <div className="text-[10px] text-text-muted">
                  Model: <span className="font-mono-clinical">{noteRes.model_used}</span> | {noteRes.status}
                </div>
              </div>
              );
            })() : (
              <div className="flex items-center justify-center py-20 text-text-muted text-[11px]">
                Enter clinical text and generate a structured SOAP note
              </div>
            )}
          </div>
        </div>
      )}

      {/* ───── ICD Coding ───── */}
      {tab === "code" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-bg-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
              <Code className="w-4 h-4 text-green-400" /> Auto ICD-10 Coding
            </h2>
            <label htmlFor="scribe-code-text" className="sr-only">Discharge summary or clinical note</label>
            <textarea id="scribe-code-text" value={codeText} onChange={e => setCodeText(e.target.value)}
              rows={16}
              aria-label="Discharge summary or clinical note for ICD coding"
              placeholder="Paste discharge summary or clinical note for ICD coding..."
              className="w-full min-h-[300px] bg-bg-input border border-border rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder-slate-600 focus:border-blue-500 focus:outline-none transition-colors resize-none" />
            <button onClick={codeNote} disabled={codeLoading || !codeText.trim()}
              className="w-full flex items-center justify-center gap-2 bg-green-600 hover:bg-green-500 disabled:bg-green-800 text-white font-medium py-2.5 rounded-lg transition-colors text-sm mt-3">
              {codeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Code className="w-4 h-4" />}
              {codeLoading ? "Coding..." : "Generate ICD Codes"}
            </button>
          </div>

          <div className="bg-bg-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-semibold text-text-primary mb-3">ICD-10 Code Suggestions</h2>
            {codeRes && codeRes.icd10am_codes.length > 0 ? (
              <div className="space-y-2">
                {codeRes.icd10am_codes.map((c, i) => (
                  <div key={i} className="flex items-center justify-between bg-bg-primary rounded-lg p-3 border border-border/30">
                    <div className="flex items-center gap-3">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        c.is_primary ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                                     : "bg-bg-input text-text-muted"
                      }`}>
                        {c.is_primary ? "PRIMARY" : `#${i + 1}`}
                      </span>
                      <div>
                        <div className="font-mono-clinical text-sm font-bold text-text-primary">{c.code}</div>
                        <div className="text-[10px] text-text-muted">{c.description}</div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono-clinical text-sm font-bold text-text-primary">
                        {(c.confidence * 100).toFixed(0)}%
                      </div>
                      <div className="w-16 h-1.5 bg-progress-bg rounded-full overflow-hidden mt-1">
                        <div className="h-full rounded-full bg-green-500" style={{ width: `${c.confidence * 100}%` }} />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-center justify-center py-20 text-text-muted text-[11px]">
                Paste clinical text to get ICD-10 code suggestions
              </div>
            )}
          </div>
        </div>
      )}

      {/* ───── NER ───── */}
      {tab === "ner" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-bg-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
              <Search className="w-4 h-4 text-purple-400" /> Clinical Entity Extraction
            </h2>
            <label htmlFor="scribe-ner-text" className="sr-only">Clinical text for entity extraction</label>
            <textarea id="scribe-ner-text" value={nerText} onChange={e => setNerText(e.target.value)}
              rows={16}
              aria-label="Clinical text for entity extraction"
              placeholder="Enter clinical text to extract medications, diagnoses, symptoms..."
              className="w-full min-h-[300px] bg-bg-input border border-border rounded-lg px-3 py-2.5 text-sm text-text-primary placeholder-slate-600 focus:border-blue-500 focus:outline-none transition-colors resize-none" />
            <button onClick={extractNER} disabled={nerLoading || !nerText.trim()}
              className="w-full flex items-center justify-center gap-2 bg-purple-600 hover:bg-purple-500 disabled:bg-purple-800 text-white font-medium py-2.5 rounded-lg transition-colors text-sm mt-3">
              {nerLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              {nerLoading ? "Extracting..." : "Extract Entities"}
            </button>
          </div>

          <div className="bg-bg-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-semibold text-text-primary mb-3">Extracted Entities</h2>
            {nerRes ? (
              <div className="space-y-3">
                {nerRes.medications.length > 0 && (
                  <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3">
                    <div className="text-[10px] font-bold text-blue-400 mb-2">
                      Medications ({nerRes.medications.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {nerRes.medications.map((m, i) => (
                        <span key={i} className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-blue-500/15 text-blue-400 border border-blue-500/30 font-medium">
                          {m.drug}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {nerRes.diagnoses && nerRes.diagnoses.length > 0 && (
                  <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
                    <div className="text-[10px] font-bold text-red-400 mb-2">
                      Diagnoses ({nerRes.diagnoses.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {nerRes.diagnoses.map((d, i) => (
                        <span key={i} className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-red-500/15 text-red-400 border border-red-500/30 font-medium">
                          {d.term} <span className="ml-1 font-mono-clinical text-[9px] opacity-70">({d.icd_code})</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {nerRes.symptoms.length > 0 && (
                  <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3">
                    <div className="text-[10px] font-bold text-yellow-400 mb-2">
                      Symptoms ({nerRes.symptoms.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {nerRes.symptoms.map((s, i) => (
                        <span key={i} className="inline-flex items-center text-[11px] px-2.5 py-1 rounded-full bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 font-medium">
                          {s.symptom}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {!nerRes.medications.length && !nerRes.symptoms.length && !(nerRes.diagnoses?.length) && (
                  <div className="text-text-muted text-[11px] py-8 text-center">No entities found</div>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center py-20 text-text-muted text-[11px]">
                Enter clinical text to extract entities
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
