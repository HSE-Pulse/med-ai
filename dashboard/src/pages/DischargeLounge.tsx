import { useEffect, useState } from "react";
import { DoorOpen, RefreshCw, ArrowRightLeft, CheckCircle2, Search, LogOut, Loader2, AlertTriangle } from "lucide-react";
import {
  loungeStatus,
  loungeForecast,
  loungeMetrics,
  loungeCommunityQueue,
  loungeTransfer,
  loungeComplete,
  type DischargeLoungeStatus,
} from "../lib/api";

export default function DischargeLounge() {
  const [status, setStatus] = useState<DischargeLoungeStatus | null>(null);
  const [forecast, setForecast] = useState<number[]>([]);
  const [metrics, setMetrics] = useState<any>(null);
  const [community, setCommunity] = useState<any[]>([]);
  const [hadmId, setHadmId] = useState("");
  const [srcDept, setSrcDept] = useState("Medicine");
  // Track whether the most recent /status poll succeeded so we can
  // surface a clear "service unreachable" banner instead of silently
  // showing zeroed cards when the lounge backend is offline or slow.
  const [lastFetchOk, setLastFetchOk] = useState<boolean>(true);
  const [lastFetchAt, setLastFetchAt] = useState<Date | null>(null);
  // Tick every second so the per-row remaining-time pills tick down live
  // between data polls (polling is every 15s, which is too coarse for a
  // clinician watching the lounge).
  const [, setNowTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setNowTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  async function refresh() {
    const [s, f, m, cq] = await Promise.all([
      loungeStatus(),
      loungeForecast(4),
      loungeMetrics(),
      loungeCommunityQueue(),
    ]);
    // Treat /status returning a non-null payload as the success signal —
    // the other three endpoints can be empty by design (no community queue
    // is normal). Only when /status itself fails is the service truly
    // unreachable from this page's perspective.
    setLastFetchOk(s !== null);
    setLastFetchAt(new Date());
    if (s !== null) setStatus(s);
    setForecast(f?.hours || []);
    if (m !== null) setMetrics(m);
    setCommunity(cq || []);
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 15000); return () => clearInterval(t); }, []);

  const [busyHadm, setBusyHadm] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  async function transfer() {
    if (!hadmId) return;
    setBusyHadm(`transfer:${hadmId}`);
    const r = await loungeTransfer({ hadm_id: hadmId, source_department: srcDept });
    setBusyHadm(null);
    if (r && typeof r === "object" && (r as any).duplicate) {
      setToast({ kind: "ok", text: `#${hadmId} was already in the lounge — no change` });
    } else if (r) {
      setToast({ kind: "ok", text: `Transferred #${hadmId} from ${srcDept}` });
    } else {
      setToast({ kind: "err", text: `Transfer failed for #${hadmId}` });
    }
    setHadmId("");
    refresh();
    window.setTimeout(() => setToast(null), 3000);
  }

  async function complete(id: string) {
    setBusyHadm(`complete:${id}`);
    const r = await loungeComplete(id);
    setBusyHadm(null);
    if (r) {
      setToast({ kind: "ok", text: `Discharged #${id} — bed released` });
    } else {
      setToast({ kind: "err", text: `Complete failed for #${id}` });
    }
    refresh();
    window.setTimeout(() => setToast(null), 3000);
  }

  function openSearch() {
    // Fire ⌘K to open the global command palette (searches real MIMIC patients)
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", metaKey: true, ctrlKey: true, bubbles: true }),
    );
  }

  const utilization = status ? Math.round((status.occupied / Math.max(1, status.capacity)) * 100) : 0;

  return (
    <div className="space-y-4">
      {!lastFetchOk && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
          <div className="flex-1">
            <div className="text-sm font-semibold text-amber-300">
              Discharge Lounge service unreachable
            </div>
            <div className="text-xs text-amber-300/70 mt-0.5">
              Last poll failed{lastFetchAt ? ` at ${lastFetchAt.toLocaleTimeString()}` : ""}.
              Stats below are from the most recent successful poll, or zero. Click
              Refresh to retry.
            </div>
          </div>
        </div>
      )}
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <DoorOpen className="w-5 h-5 text-emerald-400" /> Discharge Lounge Coordinator
          </h2>
          <p className="text-sm text-slate-400">Port 8221 — Automated ward→lounge transfers to free inpatient beds earlier.</p>
        </div>
        <button onClick={refresh} className="px-3 py-1.5 text-sm rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Stat label="Capacity" value={status?.capacity ?? 0} />
        <Stat label="Occupied" value={status?.occupied ?? 0} />
        <Stat label="Available" value={status?.available ?? 0} colour="text-emerald-400" />
        <Stat label="Utilisation" value={`${utilization}%`} colour={utilization > 80 ? "text-amber-400" : "text-white"} />
      </div>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <ArrowRightLeft className="w-4 h-4 text-blue-400" /> Transfer patient into lounge
          <span className="ml-auto text-[10px] text-slate-500 font-normal">
            Auto-transfer fires for readiness ≥ 0.85 · auto-discharge on expected-hours timeout
          </span>
        </h3>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <input
              value={hadmId}
              onChange={(e) => setHadmId(e.target.value)}
              placeholder="hadm_id"
              className="w-full bg-bg-primary border border-border rounded pl-3 pr-9 py-1.5 text-sm text-white font-mono-clinical"
            />
            <button
              onClick={openSearch}
              title="Search patient (⌘K)"
              className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
            >
              <Search className="w-3.5 h-3.5" />
            </button>
          </div>
          <input
            value={srcDept}
            onChange={(e) => setSrcDept(e.target.value)}
            placeholder="source dept"
            className="w-48 bg-bg-primary border border-border rounded px-3 py-1.5 text-sm text-white"
          />
          <button
            onClick={transfer}
            disabled={!hadmId || busyHadm === `transfer:${hadmId}`}
            className="px-3 py-1.5 text-sm rounded-lg bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-50 flex items-center gap-1.5"
          >
            {busyHadm === `transfer:${hadmId}` ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : null}
            Transfer
          </button>
        </div>
        {toast && (
          <div
            className={`mt-2 text-xs px-2.5 py-1.5 rounded ${
              toast.kind === "ok"
                ? "bg-emerald-500/10 text-emerald-300 border border-emerald-500/30"
                : "bg-red-500/10 text-red-300 border border-red-500/30"
            }`}
          >
            {toast.text}
          </div>
        )}
      </section>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3">Current occupants</h3>
        {(status?.patients || []).length === 0 ? (
          <p className="text-sm text-slate-500">No patients currently in the lounge.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-slate-400">
              <tr>
                <th className="text-left py-1.5">HADM</th>
                <th
                  className="text-left py-1.5"
                  title="Originating ward / department before transfer to the lounge"
                >
                  Source ward
                </th>
                <th className="text-left py-1.5">Arrived</th>
                <th
                  className="text-left py-1.5"
                  title="Who triggered the transfer — `auto` for readiness-driven, otherwise the user"
                >
                  Initiated by
                </th>
                <th
                  className="text-right py-1.5"
                  title="Top number = expected lounge LOS (hours). Bottom number = countdown to auto-discharge."
                >
                  Expected · time left
                </th>
                <th className="text-right py-1.5 w-28">Action</th>
              </tr>
            </thead>
            <tbody>
              {status!.patients.map((p, i) => {
                const auto = typeof (p as any).initiated_by === "string" &&
                  (p as any).initiated_by.startsWith("auto:");
                const arrived = p.arrived_at ? Date.parse(p.arrived_at) : NaN;
                const hours = Number(p.expected_departure_h ?? 0);
                const departBy = Number.isFinite(arrived) ? arrived + hours * 3600_000 : NaN;
                const remainingMs = Number.isFinite(departBy) ? departBy - Date.now() : NaN;
                let remainingLabel = "—";
                let remainingTone = "text-slate-400";
                if (Number.isFinite(remainingMs)) {
                  if (remainingMs <= 0) {
                    remainingLabel = "expiring";
                    remainingTone = "text-amber-400 animate-pulse";
                  } else {
                    const secTotal = Math.floor(remainingMs / 1000);
                    const min = Math.floor(secTotal / 60);
                    const sec = secTotal % 60;
                    if (min < 2) {
                      remainingLabel = `${min}m ${String(sec).padStart(2, "0")}s`;
                    } else if (min < 60) {
                      remainingLabel = `${min}m`;
                    } else {
                      const h = Math.floor(min / 60);
                      const mm = min % 60;
                      remainingLabel = `${h}h ${String(mm).padStart(2, "0")}m`;
                    }
                    remainingTone = min < 15 ? "text-amber-400" : "text-slate-300";
                  }
                }
                return (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1 font-mono-clinical">{p.hadm_id}</td>
                    <td className="py-1">{p.source_department || "—"}</td>
                    <td className="py-1 text-xs font-mono-clinical text-slate-400">{p.arrived_at}</td>
                    <td className="py-1 text-xs">
                      {auto ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-blue-500/15 text-blue-300 text-[10px]">
                          auto
                        </span>
                      ) : (
                        <span className="text-slate-400">{(p as any).initiated_by ?? "manual"}</span>
                      )}
                    </td>
                    <td className="py-1 text-right font-mono-clinical">
                      <div>{p.expected_departure_h ?? "—"}</div>
                      <div className={`text-[10px] ${remainingTone}`} title="Time until auto-discharge">
                        {remainingLabel}
                      </div>
                    </td>
                    <td className="py-1 text-right">
                      <button
                        onClick={() => complete(p.hadm_id)}
                        disabled={busyHadm === `complete:${p.hadm_id}`}
                        className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-lg bg-blue-500/15 text-blue-300 hover:bg-blue-500/25 disabled:opacity-50"
                        title="Manual override — auto-expiry will fire when the remaining timer hits 0"
                      >
                        {busyHadm === `complete:${p.hadm_id}` ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <LogOut className="w-3 h-3" />
                        )}
                        Complete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-3">4-hour Forecast</h3>
          <div className="flex items-end gap-2 h-28">
            {forecast.map((f, i) => (
              <div key={i} className="flex-1 flex flex-col items-center justify-end">
                <div className="bg-emerald-500/60 w-full rounded-t" style={{ height: `${(f / (status?.capacity || 10)) * 100}%` }} />
                <span className="text-xs text-slate-400 mt-1">+{i + 1}h</span>
                <span className="text-xs text-slate-300 font-mono-clinical">{f}</span>
              </div>
            ))}
            {forecast.length === 0 && <p className="text-sm text-slate-500">No forecast.</p>}
          </div>
        </section>
        <section className="bg-bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-white mb-3">Metrics</h3>
          {metrics ? (
            <div className="text-sm space-y-1">
              <div><span className="text-slate-400">Mean LOS:</span> <span className="text-white">{metrics.mean_los_h} h</span></div>
              <div><span className="text-slate-400">Throughput (24h):</span> <span className="text-white">{metrics.throughput_24h ?? metrics.throughput}</span></div>
              {typeof metrics.samples_count === "number" && (
                <div><span className="text-slate-400">Samples in buffer:</span> <span className="text-slate-500">{metrics.samples_count}</span></div>
              )}
              <div><span className="text-slate-400">Occupancy:</span> <span className="text-white">{metrics.current_occupancy}/{metrics.capacity}</span></div>
            </div>
          ) : <p className="text-sm text-slate-500">—</p>}
        </section>
      </div>

      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-blue-400" /> Sláintecare community-referral queue
        </h3>
        {community.length === 0 ? (
          <p className="text-sm text-slate-500">No referrals queued.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {community.map((c, i) => (
              <li key={i} className="border-b border-border pb-1">
                <span className="font-mono-clinical text-xs text-slate-400">{c.received_at}</span>{" "}
                <span className="text-slate-200">HADM {c.hadm_id}</span>{" "}
                <span className="text-slate-500">({c.reason})</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, colour }: { label: string; value: any; colour?: string }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${colour || "text-white"}`}>{value}</div>
    </div>
  );
}
