import { useEffect, useState } from "react";
import { Shield, ListChecks, Trash2, FileText, AlertTriangle, RefreshCw, CheckCircle2 } from "lucide-react";
import {
  gdprRoPA,
  gdprDPIAList,
  gdprAuditLog,
  gdprCreateSAR,
  gdprPurge,
} from "../lib/api";

type Toast =
  | { kind: "ok"; title: string; detail?: string }
  | { kind: "err"; title: string; detail?: string };

export default function GDPRCompliance() {
  const [ropa, setRopa] = useState<any[]>([]);
  const [dpia, setDpia] = useState<any[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [sarId, setSarId] = useState("");
  const [purgeId, setPurgeId] = useState("");
  const [lastAction, setLastAction] = useState<any>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [submitting, setSubmitting] = useState<null | "sar" | "purge">(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    const [r, d, a] = await Promise.all([gdprRoPA(), gdprDPIAList(), gdprAuditLog({ limit: 100 })]);
    setRopa(r || []);
    setDpia(d || []);
    setAudit(a || []);
    setLoading(false);
  }
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  // Auto-dismiss toast after 6s so it doesn't linger.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const handleSar = async () => {
    if (!sarId) return;
    setSubmitting("sar");
    setToast(null);
    try {
      const res = await gdprCreateSAR(sarId);
      setLastAction(res);
      if (res && (res as any).request_id) {
        setToast({ kind: "ok", title: "SAR queued", detail: `request_id ${(res as any).request_id}` });
        refresh();
      } else {
        setToast({ kind: "err", title: "GDPR service did not confirm the SAR", detail: "Check System Admin and retry." });
      }
    } catch (e) {
      setToast({ kind: "err", title: "Could not queue SAR", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setSubmitting(null);
    }
  };

  const handlePurge = async () => {
    if (!purgeId) return;
    if (!confirm(`Cascade delete for patient ${purgeId}? Creates a tombstone across every service.`)) return;
    setSubmitting("purge");
    setToast(null);
    try {
      const res = await gdprPurge(purgeId);
      setLastAction(res);
      if (res) {
        setToast({ kind: "ok", title: "Erasure requested", detail: `Tombstone fan-out queued for ${purgeId}` });
        refresh();
      } else {
        setToast({ kind: "err", title: "GDPR service did not confirm the erasure", detail: "Check System Admin and retry." });
      }
    } catch (e) {
      setToast({ kind: "err", title: "Could not request erasure", detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <Shield className="w-5 h-5 text-emerald-400" /> GDPR Compliance Engine
          </h2>
          <p className="text-sm text-slate-400">
            Port 8217 — RoPA (Art. 30), DPIA (Art. 35), SAR (Art. 15), Right to Erasure (Art. 17), breach log (Art. 33).
          </p>
        </div>
        <button onClick={refresh} className="px-3 py-1.5 text-sm rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
            <ListChecks className="w-4 h-4 text-blue-400" /> Subject Access Request (Art. 15)
          </h3>
          <div className="flex gap-2">
            <input value={sarId} onChange={(e) => setSarId(e.target.value)} placeholder="patient_id" className="flex-1 bg-bg-primary border border-border rounded px-3 py-1.5 text-sm text-white" />
            <button
              type="button"
              onClick={handleSar}
              disabled={!sarId || submitting === "sar"}
              className="px-3 py-1.5 text-sm rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 disabled:opacity-50 disabled:cursor-not-allowed border border-blue-500/30"
            >
              {submitting === "sar" ? "Queuing…" : "Queue SAR"}
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-2">Returns a request_id trackable via /gdpr/sar/&lt;id&gt;/status.</p>
        </section>

        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
            <Trash2 className="w-4 h-4 text-rose-400" /> Right to Erasure (Art. 17)
          </h3>
          <div className="flex gap-2">
            <input value={purgeId} onChange={(e) => setPurgeId(e.target.value)} placeholder="patient_id" className="flex-1 bg-bg-primary border border-border rounded px-3 py-1.5 text-sm text-white" />
            <button
              type="button"
              onClick={handlePurge}
              disabled={!purgeId || submitting === "purge"}
              className="px-3 py-1.5 text-sm rounded-lg bg-rose-500/20 text-rose-300 hover:bg-rose-500/30 disabled:opacity-50 disabled:cursor-not-allowed border border-rose-500/30"
            >
              {submitting === "purge" ? "Purging…" : "Purge"}
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-2">72-hour SLA — breach is auto-logged if any service fails.</p>
        </section>
      </div>

      {toast && (
        <div
          role="status"
          className={`flex items-start gap-2 rounded-lg p-3 text-sm border ${
            toast.kind === "ok"
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
              : "bg-red-500/10 border-red-500/30 text-red-300"
          }`}
        >
          {toast.kind === "ok"
            ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />
            : <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" />}
          <div className="flex-1">
            <div className="font-medium">{toast.title}</div>
            {toast.detail && <div className="text-xs opacity-80">{toast.detail}</div>}
          </div>
          <button
            type="button"
            onClick={() => setToast(null)}
            className="text-current opacity-60 hover:opacity-100"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}

      {lastAction && (
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-2">Last Action Result</h3>
          <pre className="text-xs text-slate-300 overflow-auto max-h-64 bg-bg-primary rounded p-3">{JSON.stringify(lastAction, null, 2)}</pre>
        </section>
      )}

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <FileText className="w-4 h-4 text-amber-400" /> DPIAs (Art. 35 — high-risk modules)
        </h3>
        {loading ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : dpia.length === 0 ? (
          <p className="text-sm text-slate-500">
            No DPIA records returned. The GDPR service may be offline, or no modules are currently flagged
            high-risk. Check System Admin for the service status.
          </p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {dpia.map((d) => (
              <div key={d.module} className="bg-bg-primary border border-border rounded p-3">
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-white capitalize">{String(d.module).replace(/_/g, " ")}</div>
                  <span className={`text-xs px-2 py-0.5 rounded ${d.risk_level === "high" ? "bg-rose-500/20 text-rose-300" : "bg-amber-500/20 text-amber-300"}`}>{d.risk_level}</span>
                </div>
                <p className="text-xs text-slate-400 mt-1">{d.purpose}</p>
                <p className="text-xs text-slate-500 mt-1"><span className="text-slate-400">Basis:</span> {d.lawful_basis}</p>
                {Array.isArray(d.mitigating_measures) && (
                  <ul className="mt-2 space-y-0.5 list-disc list-inside text-xs text-slate-400">
                    {d.mitigating_measures.map((m: string, i: number) => <li key={i}>{m}</li>)}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <ListChecks className="w-4 h-4 text-slate-400" /> RoPA — Processing Activities
        </h3>
        {ropa.length === 0 ? <p className="text-sm text-slate-500">No activities registered.</p> : (
          <table className="w-full text-sm">
            <thead className="text-slate-400">
              <tr>
                <th className="text-left py-1.5">Service</th>
                <th className="text-left py-1.5">Legal basis</th>
                <th className="text-left py-1.5">Retention</th>
              </tr>
            </thead>
            <tbody>
              {ropa.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1.5 font-mono-clinical text-slate-300">{r.service}</td>
                  <td className="py-1.5 text-xs">{r.privacy_notice?.legal_basis || "—"}</td>
                  <td className="py-1.5 text-xs">{r.privacy_notice?.retention_period || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400" /> Recent Audit Events
        </h3>
        {audit.length === 0 ? <p className="text-sm text-slate-500">No events yet.</p> : (
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-sm">
              <thead className="text-slate-400 sticky top-0 bg-bg-card">
                <tr>
                  <th className="text-left py-1.5">Time</th>
                  <th className="text-left py-1.5">Module</th>
                  <th className="text-left py-1.5">Purpose</th>
                  <th className="text-left py-1.5">Patient</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((a, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1 font-mono-clinical text-xs text-slate-400">{a.timestamp}</td>
                    <td className="py-1">{a.module}</td>
                    <td className="py-1 text-xs text-slate-300">{a.purpose}</td>
                    <td className="py-1 font-mono-clinical text-xs">{a.patient_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
