import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, User, LayoutDashboard, X, Loader2 } from "lucide-react";

interface PatientHit {
  hadm_id: string;
  subject_id: string;
  admission_type?: string | null;
  admission_location?: string | null;
  admittime?: string | null;
  dischtime?: string | null;
}

interface NavHit {
  kind: "nav";
  label: string;
  route: string;
}

const NAV_TARGETS: NavHit[] = [
  { kind: "nav", label: "Dashboard", route: "/" },
  { kind: "nav", label: "ED Triage", route: "/ed-triage" },
  { kind: "nav", label: "ED Flow", route: "/ed-flow" },
  { kind: "nav", label: "Sepsis & ICU", route: "/sepsis" },
  { kind: "nav", label: "Hospital Ops", route: "/hospital-ops" },
  { kind: "nav", label: "Bed Management", route: "/bed-management" },
  { kind: "nav", label: "Oncology AI", route: "/oncology" },
  { kind: "nav", label: "Waiting List", route: "/waiting-list" },
  { kind: "nav", label: "Patient Journey", route: "/patient-journey" },
  { kind: "nav", label: "Clinical Scribe", route: "/clinical-scribe" },
  { kind: "nav", label: "Deterioration (NEWS2)", route: "/deterioration" },
  { kind: "nav", label: "Discharge Lounge", route: "/discharge-lounge" },
  { kind: "nav", label: "Trolley Watch", route: "/trolley" },
  { kind: "nav", label: "FHIR Gateway", route: "/fhir" },
  { kind: "nav", label: "XAI Audit", route: "/xai" },
  { kind: "nav", label: "GDPR Compliance", route: "/gdpr" },
  { kind: "nav", label: "Clinical Chat", route: "/chat" },
  { kind: "nav", label: "Simulation", route: "/simulation" },
  { kind: "nav", label: "Hospital ERP", route: "/erp" },
  { kind: "nav", label: "System Admin", route: "/system" },
];

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [patients, setPatients] = useState<PatientHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setPatients([]);
      setActive(0);
      return;
    }
    const t = setTimeout(() => inputRef.current?.focus(), 20);
    return () => clearTimeout(t);
  }, [open]);

  // Patient search depends on the Alerts service (8222). When that
  // service is offline the search silently returned no rows, which
  // contradicted the placeholder copy ("Search patient ID, admission,
  // or jump to page…"). Track the failure separately so we can render
  // a clear "patient search offline" hint instead. (F22)
  const [searchOffline, setSearchOffline] = useState(false);

  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length === 0) {
      setPatients([]);
      setSearchOffline(false);
      return;
    }
    setLoading(true);
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/alerts/search/patients?q=${encodeURIComponent(q)}&limit=8`,
          { signal: ctrl.signal },
        );
        if (!res.ok) throw new Error(String(res.status));
        const json = (await res.json()) as { results?: PatientHit[] };
        setPatients(Array.isArray(json.results) ? json.results : []);
        setSearchOffline(false);
      } catch {
        setPatients([]);
        setSearchOffline(true);
      } finally {
        setLoading(false);
      }
    }, 180);
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [query, open]);

  const navMatches = query.trim()
    ? NAV_TARGETS.filter((n) =>
        n.label.toLowerCase().includes(query.toLowerCase()),
      ).slice(0, 5)
    : NAV_TARGETS.slice(0, 6);

  type Row =
    | { kind: "nav"; item: NavHit }
    | { kind: "patient"; item: PatientHit };

  const rows: Row[] = [
    ...navMatches.map((n) => ({ kind: "nav" as const, item: n })),
    ...patients.map((p) => ({ kind: "patient" as const, item: p })),
  ];

  const go = useCallback(
    (row: Row) => {
      if (row.kind === "nav") {
        navigate(row.item.route);
      } else {
        navigate(`/patient/${row.item.hadm_id}`);
      }
      setOpen(false);
    },
    [navigate],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(rows.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = rows[active];
      if (row) go(row);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm flex items-start justify-center pt-[15vh] animate-fade-in"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-label="Command palette"
      aria-modal="true"
    >
      <div
        className="w-[90%] max-w-xl bg-bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search className="w-5 h-5 text-slate-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Search patient ID, admission, or jump to page..."
            className="flex-1 bg-transparent outline-none text-sm text-white placeholder:text-slate-500"
            aria-label="Search"
          />
          {loading ? (
            <Loader2 className="w-4 h-4 text-slate-500 animate-spin" />
          ) : (
            <kbd className="text-[10px] px-1.5 py-0.5 rounded border border-border text-slate-500">
              ESC
            </kbd>
          )}
          <button
            onClick={() => setOpen(false)}
            className="p-1 rounded text-slate-500 hover:text-slate-200"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="max-h-[50vh] overflow-y-auto">
          {searchOffline && rows.length === navMatches.length && query.trim() && (
            <div className="px-4 py-2 text-[11px] text-amber-300 bg-amber-500/10 border-b border-amber-500/30">
              Patient search service is offline — only page navigation results are shown.
            </div>
          )}
          {rows.length === 0 ? (
            <div className="p-6 text-center text-sm text-slate-500">
              {query.trim()
                ? searchOffline
                  ? "No page matches and the patient search service is offline."
                  : "No matches. Try a patient ID like 20001."
                : "Start typing to search patients or pages."}
            </div>
          ) : (
            <div>
              {navMatches.length > 0 && (
                <div className="px-3 pt-3 pb-1 text-[10px] uppercase tracking-wide text-slate-500">
                  Pages
                </div>
              )}
              {navMatches.map((n, i) => {
                const idx = i;
                const isActive = idx === active;
                return (
                  <button
                    key={`nav-${n.route}`}
                    onClick={() => go({ kind: "nav", item: n })}
                    onMouseEnter={() => setActive(idx)}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${
                      isActive
                        ? "bg-blue-500/15 text-blue-200"
                        : "text-slate-300 hover:bg-slate-700/30"
                    }`}
                  >
                    <LayoutDashboard className="w-4 h-4 shrink-0 text-slate-500" />
                    <span className="text-sm flex-1">{n.label}</span>
                    <span className="text-[11px] text-slate-500 font-mono-clinical">
                      {n.route}
                    </span>
                  </button>
                );
              })}

              {patients.length > 0 && (
                <div className="px-3 pt-3 pb-1 text-[10px] uppercase tracking-wide text-slate-500">
                  Patients
                </div>
              )}
              {patients.map((p, i) => {
                const idx = navMatches.length + i;
                const isActive = idx === active;
                return (
                  <button
                    key={`p-${p.hadm_id}-${i}`}
                    onClick={() => go({ kind: "patient", item: p })}
                    onMouseEnter={() => setActive(idx)}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-left ${
                      isActive
                        ? "bg-blue-500/15 text-blue-200"
                        : "text-slate-300 hover:bg-slate-700/30"
                    }`}
                  >
                    <User className="w-4 h-4 shrink-0 text-slate-500" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm">
                        Patient{" "}
                        <span className="font-mono-clinical text-blue-300">
                          {p.hadm_id}
                        </span>
                        <span className="text-slate-500 ml-2">
                          (subject {p.subject_id})
                        </span>
                      </div>
                      {p.admission_type && (
                        <div className="text-[11px] text-slate-500">
                          {p.admission_type}
                          {p.admission_location && ` · ${p.admission_location}`}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between px-4 py-2 border-t border-border text-[10px] text-slate-500">
          <div className="flex items-center gap-3">
            <span>
              <kbd className="px-1 py-0.5 rounded border border-border">↑↓</kbd> navigate
            </span>
            <span>
              <kbd className="px-1 py-0.5 rounded border border-border">↵</kbd> open
            </span>
          </div>
          <span>
            <kbd className="px-1 py-0.5 rounded border border-border">⌘K</kbd> toggle
          </span>
        </div>
      </div>
    </div>
  );
}
