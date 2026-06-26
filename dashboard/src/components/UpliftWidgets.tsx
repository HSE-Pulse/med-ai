// Small, drop-in panels surfacing Phase B/C/D backend endpoints on
// existing dashboard pages. Each widget self-polls (15-30s) and is safe to
// render before its upstream service is online — it just shows an empty
// state instead of crashing.

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ShieldAlert, XCircle, Activity, Briefcase, MapPin, Flag, Gavel, BookLock, BellRing, MessageCircle, Save, RefreshCw } from "lucide-react";
import {
  opsPendingActions,
  opsConfirmAction,
  opsRejectAction,
  opsGovernanceConfig,
  opsUpdateGovernanceConfig,
  simDigitalTwinHealth,
  simCircuitBreakerStatus,
  simModelsRegistry,
  simResearchGovernanceLog,
  erpEWTDCompliance,
  erpRegionCensus,
  erpActivityLog,
  erpPatchDepartment,
  journeyHighRisk,
  chatSafeguardingAlerts,
  chatHistory,
  sepsisRecentScreens,
  bedMgmtEscalations,
  waitingListAdmissionNotifications,
  type PendingAction,
  type GovernanceConfig,
  type DTHealthSnapshot,
  type CircuitBreakerSnapshot,
  type RegisteredModel,
  type EWTDRow,
  type HighRiskFlag,
  type SafeguardingAlert,
  type SepsisScreen,
  type PriorityEscalation,
  type AdmissionNotification,
} from "../lib/api";

// --------------------------------------------------------------------------- Pending Actions (AI Act Art. 14)
export function PendingActionsPanel() {
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [config, setConfig] = useState<GovernanceConfig | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toggleBusy, setToggleBusy] = useState(false);

  async function refresh() {
    const [list, cfg] = await Promise.all([opsPendingActions(), opsGovernanceConfig()]);
    setActions(list || []);
    setConfig(cfg || null);
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 10_000); return () => clearInterval(t); }, []);

  async function confirm(id: string) {
    setBusy(id); await opsConfirmAction(id); setBusy(null); refresh();
  }
  async function reject(id: string) {
    setBusy(id); await opsRejectAction(id); setBusy(null); refresh();
  }

  async function toggleOversight() {
    if (!config || config.production_mode_locked) return;
    setToggleBusy(true);
    const next = await opsUpdateGovernanceConfig({
      human_oversight_enabled: !config.human_oversight_enabled,
      updated_by: "ops_admin",
    });
    if (next) setConfig(next);
    setToggleBusy(false);
    refresh();
  }

  const oversightOn = config?.effective_human_oversight ?? false;
  const locked = config?.production_mode_locked ?? false;
  const modeLabel = oversightOn ? "Human oversight ON" : "Auto-approve ON";
  const modeClass = oversightOn
    ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
    : "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";

  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <Gavel className="w-4 h-4 text-amber-400" />
          Pending AI actions — EU AI Act Art. 14 (Human Oversight)
        </h3>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] px-2 py-0.5 rounded border ${modeClass}`}>
            {modeLabel}
          </span>
          {locked ? (
            <span className="text-[10px] px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/40">
              Locked ON (production)
            </span>
          ) : (
            <button
              disabled={toggleBusy}
              onClick={toggleOversight}
              className="text-[10px] px-2 py-0.5 rounded bg-slate-700/40 text-slate-200 border border-slate-600 hover:bg-slate-700/60 disabled:opacity-50 inline-flex items-center gap-1"
              title="Flip Art. 14 oversight mode (simulation only)"
            >
              <RefreshCw className={`w-3 h-3 ${toggleBusy ? "animate-spin" : ""}`} />
              {oversightOn ? "Disable oversight (auto-approve)" : "Require human review"}
            </button>
          )}
        </div>
      </div>

      {config && (
        <p className="text-[11px] text-slate-500 mb-2">
          {oversightOn ? (
            <>
              Every MARL / staffing recommendation is queued here for a clinician or
              operations manager to confirm or reject. No action is applied without a click.
            </>
          ) : (
            <>
              Recommendations apply automatically in simulation mode. Flip the toggle to require
              human review. Production mode (<code>DEPLOYMENT_MODE=production</code>) always enforces
              Art. 14 oversight regardless of this setting.
            </>
          )}
          {config.last_updated_by !== "system" && (
            <span className="ml-1 text-slate-600">
              · last changed by {config.last_updated_by}
            </span>
          )}
        </p>
      )}

      {oversightOn ? (
        actions.length === 0 ? (
          <p className="text-sm text-slate-500">No AI recommendations awaiting confirmation.</p>
        ) : (
          <ul className="space-y-2">
            {actions.map((a) => (
              <li key={a.action_id} className="bg-bg-primary border border-border rounded p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs px-2 py-0.5 rounded bg-amber-500/20 text-amber-300">{a.action_type}</span>
                      <span className="text-xs text-slate-400">{a.reason}</span>
                    </div>
                    <pre className="text-xs text-slate-300 bg-bg-card rounded p-2 overflow-auto max-h-32">{JSON.stringify(a.proposed, null, 2)}</pre>
                  </div>
                  <div className="flex flex-col gap-1">
                    <button
                      disabled={busy === a.action_id}
                      onClick={() => confirm(a.action_id)}
                      className="px-3 py-1 text-xs rounded bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-50 flex items-center gap-1"
                    >
                      <CheckCircle2 className="w-3 h-3" /> Confirm
                    </button>
                    <button
                      disabled={busy === a.action_id}
                      onClick={() => reject(a.action_id)}
                      className="px-3 py-1 text-xs rounded bg-rose-500/20 text-rose-300 hover:bg-rose-500/30 disabled:opacity-50 flex items-center gap-1"
                    >
                      <XCircle className="w-3 h-3" /> Reject
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )
      ) : (
        <div className="bg-emerald-500/5 border border-emerald-500/20 rounded p-3 text-xs text-emerald-200/80">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            <span className="font-medium text-emerald-300">Auto-approve active</span>
          </div>
          <p>
            AI recommendations apply immediately. Every auto-approval is written to the action
            log with <code className="text-[11px] text-emerald-200">source=auto_approved</code> so
            the audit trail still captures the decision path.
          </p>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Digital Twin health
export function DigitalTwinHealthPanel() {
  const [snap, setSnap] = useState<DTHealthSnapshot | null>(null);
  useEffect(() => {
    const load = async () => setSnap(await simDigitalTwinHealth());
    load(); const t = setInterval(load, 15_000); return () => clearInterval(t);
  }, []);
  if (!snap) return null;
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <Activity className="w-4 h-4 text-cyan-400" /> Digital Twin — Orchestrator Health
      </h3>
      <div className="text-xs text-slate-400 mb-2">
        Sim time: <span className="font-mono-clinical text-slate-200">{snap.orchestrator.sim_time}</span>{" "}
        · Running: {snap.orchestrator.sim_running ? "yes" : "no"}{" "}
        · Active patients: {snap.orchestrator.active_patients}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {Object.entries(snap.modules).map(([name, m]) => (
          <div key={name} className="bg-bg-primary border border-border rounded p-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-mono-clinical text-slate-200">{name}</span>
              <span className={`text-xs ${m.is_healthy ? "text-emerald-400" : "text-rose-400"}`}>
                {m.is_healthy ? "healthy" : "unhealthy"}
              </span>
            </div>
            <div className="text-xs text-slate-500 mt-1">
              last success: {m.last_success || "—"}{" "}
              · fails: {m.failure_count} (consec: {m.consecutive_failures})
            </div>
          </div>
        ))}
        {Object.keys(snap.modules).length === 0 && (
          <p className="text-xs text-slate-500 col-span-2">No module health recorded yet — run the sim to populate.</p>
        )}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- Circuit breakers
export function CircuitBreakerPanel() {
  const [data, setData] = useState<CircuitBreakerSnapshot[]>([]);
  useEffect(() => {
    const load = async () => setData((await simCircuitBreakerStatus()) || []);
    load(); const t = setInterval(load, 15_000); return () => clearInterval(t);
  }, []);
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <ShieldAlert className="w-4 h-4 text-amber-400" /> Circuit Breakers
      </h3>
      {data.length === 0 ? <p className="text-sm text-slate-500">No call activity yet.</p> : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {data.map((b) => (
            <div key={b.name} className="bg-bg-primary border border-border rounded p-2 text-xs">
              <div className="font-mono-clinical text-slate-200">{b.name}</div>
              <div className={`mt-0.5 ${b.state === "closed" ? "text-emerald-400" : b.state === "half_open" ? "text-amber-400" : "text-rose-400"}`}>
                {b.state} · trips {b.trip_count}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Model registry
export function ModelsRegistryPanel() {
  const [rows, setRows] = useState<RegisteredModel[]>([]);
  useEffect(() => { simModelsRegistry().then((r) => setRows(r || [])); }, []);
  if (rows.length === 0) return null;
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <Briefcase className="w-4 h-4 text-indigo-400" /> Model Registry
      </h3>
      <table className="w-full text-sm">
        <thead className="text-slate-400">
          <tr>
            <th className="text-left py-1.5">Service</th>
            <th className="text-left py-1.5">Version</th>
            <th className="text-left py-1.5">Features hash</th>
            <th className="text-left py-1.5">Loaded at</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              <td className="py-1 font-mono-clinical">{r.service_name}</td>
              <td className="py-1">{r.version}</td>
              <td className="py-1 text-xs text-slate-400">{r.features_hash || "—"}</td>
              <td className="py-1 text-xs text-slate-400">{r.loaded_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// --------------------------------------------------------------------------- EWTD compliance
export function EWTDPanel() {
  const [rows, setRows] = useState<EWTDRow[]>([]);
  useEffect(() => {
    let cancelled = false;
    erpEWTDCompliance().then((r) => {
      if (!cancelled) setRows(r?.report || []);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  const breaches = rows.filter((r) => r.breach);
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-400" /> EWTD Compliance — 48h NCHD cap
      </h3>
      <div className="text-xs text-slate-400 mb-2">
        {rows.length} NCHDs tracked · <span className={breaches.length > 0 ? "text-rose-400" : "text-emerald-400"}>{breaches.length}</span> breach(es)
      </div>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No roster data.</p> : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <thead className="text-slate-400 sticky top-0 bg-bg-card">
              <tr>
                <th className="text-left py-1.5">Department</th>
                <th className="text-left py-1.5">NCHD</th>
                <th className="text-right py-1.5">Hours (7d)</th>
                <th className="text-left py-1.5">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1">{r.department}</td>
                  <td className="py-1 font-mono-clinical text-xs">{r.nchd_name || r.nchd_id || "—"}</td>
                  <td className={`py-1 text-right font-mono-clinical ${r.breach ? "text-rose-400" : "text-slate-200"}`}>{r.hours_last_7d}</td>
                  <td className="py-1 text-xs">
                    {r.breach ? <span className="text-rose-400">BREACH (&gt;{r.limit}h)</span> : <span className="text-emerald-400">OK</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- HSE region census
export function RegionCensusPanel() {
  const [data, setData] = useState<Record<string, { capacity: number; occupied: number }> | null>(null);
  useEffect(() => {
    let cancelled = false;
    erpRegionCensus().then((r) => {
      if (!cancelled) setData(r);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  if (!data) return null;
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <MapPin className="w-4 h-4 text-emerald-400" /> HSE Health Regions — 6-Region Census (2024)
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {Object.entries(data).map(([region, v]) => {
          const util = v.capacity > 0 ? Math.round((v.occupied / v.capacity) * 100) : 0;
          const bar = util > 90 ? "bg-rose-500" : util > 80 ? "bg-amber-500" : "bg-emerald-500";
          return (
            <div key={region} className="bg-bg-primary border border-border rounded p-2">
              <div className="text-xs text-slate-200 font-semibold">{region}</div>
              <div className="text-xs text-slate-400 mt-0.5">{v.occupied}/{v.capacity} beds ({util}%)</div>
              <div className="h-1.5 bg-slate-700 rounded mt-1 overflow-hidden">
                <div className={`h-full ${bar}`} style={{ width: `${Math.min(100, util)}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- ERP activity log
export function ERPActivityLogPanel() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    let cancelled = false;
    erpActivityLog(100).then((r) => {
      if (!cancelled) setRows(r || []);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3">Clinical Activity Log</h3>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No activities logged yet.</p> : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <thead className="text-slate-400 sticky top-0 bg-bg-card">
              <tr>
                <th className="text-left py-1.5">Time</th>
                <th className="text-left py-1.5">Dept</th>
                <th className="text-left py-1.5">Note type</th>
                <th className="text-left py-1.5">ICD codes</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1 font-mono-clinical text-xs text-slate-400">{r.timestamp}</td>
                  <td className="py-1 text-xs">{r.department || "—"}</td>
                  <td className="py-1 text-xs">{r.note_type || "—"}</td>
                  <td className="py-1 text-xs">{Array.isArray(r.icd_codes) ? r.icd_codes.join(", ") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Patient Journey high-risk flags
export function HighRiskPanel() {
  const [rows, setRows] = useState<HighRiskFlag[]>([]);
  useEffect(() => { journeyHighRisk().then((r) => setRows(r || [])); }, []);
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <Flag className="w-4 h-4 text-rose-400" /> High-Risk Admissions
      </h3>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No high-risk flags yet.</p> : (
        <table className="w-full text-sm">
          <thead className="text-slate-400">
            <tr>
              <th className="text-left py-1.5">HADM</th>
              <th className="text-left py-1.5">Subject</th>
              <th className="text-left py-1.5">Flag</th>
              <th className="text-right py-1.5">Readmit risk</th>
              <th className="text-left py-1.5">Flagged</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-border">
                <td className="py-1 font-mono-clinical">{r.hadm_id}</td>
                <td className="py-1 font-mono-clinical text-xs">{r.subject_id}</td>
                <td className="py-1 text-xs">{r.flag}</td>
                <td className="py-1 text-right font-mono-clinical text-rose-400">
                  {r.readmission_risk != null ? (r.readmission_risk * 100).toFixed(0) + "%" : "—"}
                </td>
                <td className="py-1 text-xs font-mono-clinical text-slate-400">{r.flagged_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Safeguarding alerts (Children First)
export function SafeguardingPanel() {
  const [rows, setRows] = useState<SafeguardingAlert[]>([]);
  useEffect(() => { chatSafeguardingAlerts().then((r) => setRows(r || [])); const t = setInterval(async () => setRows((await chatSafeguardingAlerts()) || []), 30_000); return () => clearInterval(t); }, []);
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <ShieldAlert className="w-4 h-4 text-amber-400" /> Children First Act 2015 — Safeguarding Alerts
      </h3>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No safeguarding alerts.</p> : (
        <ul className="space-y-1 text-sm">
          {rows.map((r, i) => (
            <li key={i} className="border-b border-border pb-1">
              <span className={`text-xs px-2 py-0.5 rounded mr-2 ${r.severity === "urgent" ? "bg-rose-500/20 text-rose-300" : "bg-amber-500/20 text-amber-300"}`}>
                {r.severity}
              </span>
              <span className="font-mono-clinical text-slate-200">HADM {r.hadm_id}</span>
              <span className="text-slate-400 ml-2">{(r.reasons || []).join(" · ")}</span>
              <span className="ml-2 text-xs text-slate-500 font-mono-clinical">{r.raised_at}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Sepsis recent screens
export function SepsisRecentScreensPanel() {
  const [rows, setRows] = useState<SepsisScreen[]>([]);
  // Only poll while the user is actually on this page — if the tab is
  // hidden (or the panel has unmounted) we cancel the next tick instead
  // of letting an in-flight request resolve into the dead component's
  // setState. This is what was bleeding 500s onto every other page.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (cancelled) return;
      const r = await sepsisRecentScreens(50);
      if (cancelled) return;
      setRows(r || []);
      timer = setTimeout(tick, 10_000);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);
  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3">Recent /sepsis-screen calls (Rule 3 cascade)</h3>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No screens yet.</p> : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <thead className="text-slate-400 sticky top-0 bg-bg-card">
              <tr>
                <th className="text-left py-1.5">HADM</th>
                <th className="text-left py-1.5">Alert</th>
                <th className="text-right py-1.5">Risk</th>
                <th className="text-right py-1.5">SOFA</th>
                <th className="text-left py-1.5">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice().reverse().map((s, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1 font-mono-clinical">{s.hadm_id}</td>
                  <td className="py-1">
                    <span className={`text-xs px-2 py-0.5 rounded ${s.alert_level === "RED" ? "bg-rose-500/20 text-rose-300" : s.alert_level === "ORANGE" ? "bg-orange-500/20 text-orange-300" : s.alert_level === "YELLOW" ? "bg-yellow-500/20 text-yellow-300" : "bg-emerald-500/20 text-emerald-300"}`}>
                      {s.alert_level}
                    </span>
                  </td>
                  <td className="py-1 text-right font-mono-clinical">{s.risk_score.toFixed(2)}</td>
                  <td className="py-1 text-right">{s.sofa_total}</td>
                  <td className="py-1 text-xs text-slate-400">{s.recommended_action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Research Governance access log (DPA 2018 s.45-54)
export function ResearchGovernancePanel() {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    setRows((await simResearchGovernanceLog(100)) || []);
    setLoading(false);
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 30_000); return () => clearInterval(t); }, []);

  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <BookLock className="w-4 h-4 text-emerald-400" />
          Research Governance — MIMIC-IV Access Log (DPA 2018 s.45-54)
        </h3>
        <button onClick={refresh} className="px-2 py-1 text-xs rounded bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </div>
      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-slate-500">
          No MIMIC-IV access events recorded yet. Events appear here whenever a service queries MIMIC-IV through the ResearchGovernance wrapper.
        </p>
      ) : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <thead className="text-slate-400 sticky top-0 bg-bg-card">
              <tr>
                <th className="text-left py-1.5">Time</th>
                <th className="text-left py-1.5">Service</th>
                <th className="text-left py-1.5">Collection</th>
                <th className="text-left py-1.5">Purpose</th>
                <th className="text-right py-1.5">Rows</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1 text-xs font-mono-clinical text-slate-400">{r.timestamp}</td>
                  <td className="py-1 text-xs">{r.service || "—"}</td>
                  <td className="py-1 text-xs">{r.collection || "—"}</td>
                  <td className="py-1 text-xs text-slate-300">{r.purpose || "—"}</td>
                  <td className="py-1 text-right font-mono-clinical">{r.row_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Bed Priority Escalations log (Integration 5)
export function BedPriorityEscalationsPanel() {
  const [rows, setRows] = useState<PriorityEscalation[]>([]);
  async function refresh() { setRows((await bedMgmtEscalations(200)) || []); }
  useEffect(() => { refresh(); const t = setInterval(refresh, 15_000); return () => clearInterval(t); }, []);

  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <BellRing className="w-4 h-4 text-rose-400" />
          Bed-Priority Escalations
        </h3>
        <button onClick={refresh} className="px-2 py-1 text-xs rounded bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">No priority escalations recorded yet.</p>
      ) : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <thead className="text-slate-400 sticky top-0 bg-bg-card">
              <tr>
                <th className="text-left py-1.5">HADM</th>
                <th className="text-left py-1.5">Bed</th>
                <th className="text-left py-1.5">Reason</th>
                <th className="text-right py-1.5">Bump</th>
                <th className="text-right py-1.5">New score</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice().reverse().map((e, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1 font-mono-clinical">{e.hadm_id}</td>
                  <td className="py-1 font-mono-clinical text-xs">{e.bed_id}</td>
                  <td className="py-1 text-xs">
                    <span className={`px-2 py-0.5 rounded ${String(e.reason).includes("sepsis") ? "bg-rose-500/20 text-rose-300" : String(e.reason).includes("news2") ? "bg-amber-500/20 text-amber-300" : "bg-slate-500/20 text-slate-300"}`}>
                      {e.reason}
                    </span>
                  </td>
                  <td className="py-1 text-right font-mono-clinical text-emerald-400">+{e.bump.toFixed(2)}</td>
                  <td className="py-1 text-right font-mono-clinical">{e.new_score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Waiting List admission notifications (Bug #5 surface)
export function WaitingListAdmissionsPanel() {
  const [rows, setRows] = useState<AdmissionNotification[]>([]);
  async function refresh() { setRows((await waitingListAdmissionNotifications(200)) || []); }
  useEffect(() => { refresh(); const t = setInterval(refresh, 15_000); return () => clearInterval(t); }, []);

  const counts = rows.reduce<Record<number, number>>((acc, r) => {
    acc[r.acuity] = (acc[r.acuity] || 0) + 1; return acc;
  }, {});

  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <Activity className="w-4 h-4 text-blue-400" />
          Admission Notifications from Digital Twin
        </h3>
        <button onClick={refresh} className="px-2 py-1 text-xs rounded bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </div>
      <div className="flex gap-2 text-xs mb-3 flex-wrap">
        {[1, 2, 3, 4, 5].map((acuity) => (
          <span key={acuity} className="px-2 py-0.5 rounded bg-bg-primary border border-border">
            Acuity {acuity}: <span className="font-mono-clinical text-slate-200">{counts[acuity] || 0}</span>
          </span>
        ))}
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">No admission notifications received yet.</p>
      ) : (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-sm">
            <thead className="text-slate-400 sticky top-0 bg-bg-card">
              <tr>
                <th className="text-left py-1.5">Time</th>
                <th className="text-left py-1.5">HADM</th>
                <th className="text-left py-1.5">Subject</th>
                <th className="text-right py-1.5">Acuity</th>
                <th className="text-right py-1.5">Wait (min)</th>
                <th className="text-left py-1.5">Pathway</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice().reverse().map((n, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1 text-xs font-mono-clinical text-slate-400">{n.received_at}</td>
                  <td className="py-1 font-mono-clinical">{n.hadm_id}</td>
                  <td className="py-1 font-mono-clinical text-xs">{n.subject_id ?? "—"}</td>
                  <td className="py-1 text-right">{n.acuity}</td>
                  <td className="py-1 text-right font-mono-clinical">{n.estimated_wait_min}</td>
                  <td className="py-1 text-xs text-slate-300">{n.pathway}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Chat history viewer (Bug #6 surface)
export function ChatHistoryViewer({ defaultSessionId = "default" }: { defaultSessionId?: string }) {
  const [sessionId, setSessionId] = useState(defaultSessionId);
  const [turns, setTurns] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true); setErr(null);
    const res = await chatHistory(sessionId);
    if (!res) { setErr("Failed to fetch history."); setTurns([]); }
    else { setTurns(res.turns || []); }
    setLoading(false);
  }

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <MessageCircle className="w-4 h-4 text-indigo-400" />
          Session History (ConversationBuffer)
        </h3>
        <div className="flex gap-2 items-center">
          <input
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            placeholder="session_id"
            className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-white w-48"
          />
          <button onClick={load} className="px-3 py-1 text-xs rounded bg-indigo-500/20 text-indigo-300 hover:bg-indigo-500/30 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> Load
          </button>
        </div>
      </div>
      {err && <p className="text-sm text-rose-400">{err}</p>}
      {loading ? <p className="text-sm text-slate-500">Loading…</p> : turns.length === 0 ? (
        <p className="text-sm text-slate-500">No history for this session.</p>
      ) : (
        <div className="max-h-72 overflow-auto space-y-2">
          {turns.map((t, i) => (
            <div
              key={i}
              className={`text-sm p-2 rounded border ${
                t.role === "user" ? "bg-blue-500/10 border-blue-500/20"
                : t.role === "assistant" ? "bg-emerald-500/10 border-emerald-500/20"
                : "bg-slate-500/10 border-slate-500/20"
              }`}
            >
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-0.5">
                <span className="uppercase font-semibold">{t.role}</span>
                <span className="font-mono-clinical">{t.timestamp}</span>
              </div>
              <div className="text-slate-200 whitespace-pre-wrap">{t.content}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- ERP department editor (Bug #7 — runtime PATCH)
export function ERPDepartmentEditor() {
  const [name, setName] = useState("ED");
  const [json, setJson] = useState<string>('{"capacity": 32}');
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function apply() {
    setErr(null); setBusy(true);
    let patch: Record<string, any>;
    try { patch = JSON.parse(json); }
    catch { setErr("Invalid JSON"); setBusy(false); return; }
    const res = await erpPatchDepartment(name, patch);
    if (res == null) setErr("PATCH failed — see server logs."); else setResult(res);
    setBusy(false);
  }

  return (
    <section className="bg-bg-card border border-border rounded-xl p-4">
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
        <Save className="w-4 h-4 text-amber-400" />
        Department Master-Data Editor (PATCH /erp/departments/&lt;name&gt;)
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-400">Department name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-white"
          />
        </label>
        <label className="flex flex-col gap-1 md:col-span-2">
          <span className="text-xs text-slate-400">Patch (JSON)</span>
          <textarea
            value={json}
            onChange={(e) => setJson(e.target.value)}
            rows={3}
            className="bg-bg-primary border border-border rounded px-2 py-1 text-sm text-white font-mono-clinical"
          />
        </label>
      </div>
      <div className="flex items-center gap-2 mt-3">
        <button
          onClick={apply}
          disabled={busy || !name}
          className="px-3 py-1.5 text-sm rounded-lg bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 disabled:opacity-50 flex items-center gap-2"
        >
          <Save className="w-4 h-4" /> {busy ? "Applying…" : "Apply PATCH"}
        </button>
        {err && <span className="text-xs text-rose-400">{err}</span>}
      </div>
      <p className="text-xs text-slate-500 mt-2">
        Overrides persist in <code className="font-mono-clinical">hospital_erp.departments</code> and win at read-time over the static defaults in <code className="font-mono-clinical">data.py</code>.
      </p>
      {result && (
        <div className="mt-3">
          <div className="text-xs text-slate-400 mb-1">Merged department config</div>
          <pre className="text-xs text-slate-300 bg-bg-primary rounded p-2 overflow-auto max-h-56">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </section>
  );
}
