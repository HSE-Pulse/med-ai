import { useEffect, useMemo, useState } from "react";
import { HeartPulse, BellRing, RefreshCw, CheckCircle2, TrendingUp, TrendingDown, Minus, Baby, Activity, Bot } from "lucide-react";
import {
  deteriorationActiveAlerts,
  deteriorationStats,
  deteriorationAudit,
  deteriorationScreen,
  deteriorationPews,
  deteriorationImews,
  deteriorationEscalations,
  deteriorationAcknowledge,
  deteriorationGovernanceConfig,
  deteriorationGovernanceUpdate,
  type DeteriorationAlert,
  type DeteriorationStats,
  type DeteriorationEscalation,
  type DeteriorationGovernanceConfig,
} from "../lib/api";

type ScoringSystem = "news2" | "pews" | "imews";

export default function DeteriorationMonitor() {
  const [alerts, setAlerts] = useState<DeteriorationAlert[]>([]);
  const [stats, setStats] = useState<DeteriorationStats | null>(null);
  const [audit, setAudit] = useState<DeteriorationEscalation[]>([]);
  const [unackOnly, setUnackOnly] = useState(true);
  const [screenResult, setScreenResult] = useState<DeteriorationAlert | null>(null);
  const [activeSystem, setActiveSystem] = useState<ScoringSystem>("news2");

  // Shared vitals
  const [hadmId, setHadmId] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [department, setDepartment] = useState("Medicine");
  const [rr, setRr] = useState(18);
  const [spo2, setSpo2] = useState(96);
  const [supplO2, setSupplO2] = useState(false);
  const [temp, setTemp] = useState(37.0);
  const [sbp, setSbp] = useState(120);
  const [dbp, setDbp] = useState(75);
  const [hr, setHr] = useState(80);
  const [consciousness, setConsciousness] = useState("A");

  // PEWS-specific
  const [ageMonths, setAgeMonths] = useState(60);
  const [behaviour, setBehaviour] = useState("Alert");
  const [respEffort, setRespEffort] = useState<string>("none");
  const [crt, setCrt] = useState(2);

  // IMEWS-specific
  const [gestWeeks, setGestWeeks] = useState<number | "">(30);
  const [ppDays, setPpDays] = useState<number | "">("");
  const [proteinuria, setProteinuria] = useState<string>("negative");

  const [governance, setGovernance] = useState<DeteriorationGovernanceConfig | null>(null);

  async function refresh() {
    const [a, s, au, g] = await Promise.all([
      deteriorationActiveAlerts(),
      deteriorationStats(),
      deteriorationEscalations(unackOnly, 50),
      deteriorationGovernanceConfig(),
    ]);
    setAlerts(a || []);
    setStats(s);
    setAudit(au || []);
    setGovernance(g);
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unackOnly]);

  async function toggleAutoAck() {
    if (!governance) return;
    const next = !governance.auto_ack_in_sim;
    const warn = next
      ? "Enable sim-mode auto-acknowledgement?\n\n" +
        "Every new NEWS2/PEWS/IMEWS escalation will be auto-acked after " +
        `${governance.auto_ack_delay_seconds}s with a synthetic SBAR note flagged ` +
        "'sim_autoack'. This only runs when DEPLOYMENT_MODE=simulation — " +
        "production deployments will ignore the flag.\n\nContinue?"
      : "Disable auto-ack? All new escalations will require manual clinician review.";
    if (!confirm(warn)) return;
    const updated = await deteriorationGovernanceUpdate({
      auto_ack_in_sim: next,
      updated_by: "dashboard",
    });
    if (updated) setGovernance(updated);
  }

  async function runScreen() {
    const common = {
      hadm_id: hadmId || `screen-${Date.now()}`,
      subject_id: subjectId || undefined,
      department,
      vitals: {
        respiratory_rate: rr,
        spo2,
        on_supplemental_o2: supplO2,
        temperature: temp,
        systolic_bp: sbp,
        diastolic_bp: dbp,
        heart_rate: hr,
        consciousness,
      },
    };
    let res: DeteriorationAlert | null = null;
    if (activeSystem === "pews") {
      res = await deteriorationPews({
        ...common,
        age_months: ageMonths,
        vitals: {
          ...common.vitals,
          behaviour,
          respiratory_effort: respEffort === "none" ? undefined : respEffort,
          capillary_refill_s: crt,
        },
      });
    } else if (activeSystem === "imews") {
      res = await deteriorationImews({
        ...common,
        gestation_weeks: gestWeeks === "" ? undefined : Number(gestWeeks),
        post_partum_days: ppDays === "" ? undefined : Number(ppDays),
        vitals: {
          ...common.vitals,
          proteinuria,
        },
      });
    } else {
      res = await deteriorationScreen(common);
    }
    setScreenResult(res);
    refresh();
  }

  async function acknowledge(id: string) {
    await deteriorationAcknowledge({
      escalation_id: id,
      clinician: { name: "Duty Clinician", role: "Registrar" },
      sbar: {
        situation: "Alert reviewed from dashboard.",
        background: "Automated review via Dashboard UI.",
        assessment: "Clinician has seen the alert.",
        recommendation: "Continue monitoring per local protocol.",
      },
      outcome: "reviewed",
    });
    refresh();
  }

  const bandColour = (band: string) => {
    switch (band) {
      case "critical": return "text-rose-200 bg-rose-600/30 border border-rose-400/60";
      case "high":     return "text-rose-300 bg-rose-500/20 border border-rose-500/40";
      case "medium":   return "text-amber-300 bg-amber-500/20 border border-amber-500/40";
      case "low":      return "text-yellow-300 bg-yellow-500/20 border border-yellow-500/40";
      default:         return "text-emerald-300 bg-emerald-500/15 border border-emerald-500/30";
    }
  };

  const systemColour = (sys: string) => {
    if (sys === "pews")  return "bg-pink-500/20 text-pink-300 border border-pink-500/40";
    if (sys === "imews") return "bg-purple-500/20 text-purple-300 border border-purple-500/40";
    return "bg-blue-500/20 text-blue-300 border border-blue-500/40";
  };

  const trajectoryIcon = (trajectory?: string) => {
    if (trajectory === "rising") return <TrendingUp className="inline w-3 h-3 text-rose-400" />;
    if (trajectory === "falling") return <TrendingDown className="inline w-3 h-3 text-emerald-400" />;
    if (trajectory === "stable") return <Minus className="inline w-3 h-3 text-slate-400" />;
    return null;
  };

  const systemDist = stats?.by_scoring_system ?? {};
  const totalBySystem = useMemo(() => {
    return Object.entries(systemDist).reduce((acc, [, v]) => acc + (v as number), 0);
  }, [systemDist]);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-start flex-wrap gap-2">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <HeartPulse className="w-5 h-5 text-rose-400" /> Predictive Deterioration Monitor
          </h2>
          <p className="text-sm text-slate-400">
            Port 8220 — routes adult to <b>NEWS2</b>, paediatric to <b>PEWS</b> (NCEC NCG #1),
            maternal to <b>IMEWS</b> (NCEC NCG #4). Fed by the Digital Twin on every vital
            event for non-ICU inpatients.
          </p>
        </div>
        <button onClick={refresh} className="px-3 py-1.5 text-sm rounded-lg bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 flex items-center gap-2">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <Stat label="Active patients" value={stats?.active_patients ?? 0} />
        <Stat label="High band (≥7)" value={stats?.high_band ?? 0} colour="text-rose-300" />
        <Stat label="Medium (5-6)" value={stats?.medium_band ?? 0} colour="text-amber-300" />
        <Stat label="Mean score" value={stats?.mean_score ?? 0} />
        <Stat label="Unack. escalations" value={stats?.unacknowledged_escalations ?? 0} colour="text-rose-300" />
        <Stat label="Mean TTACK (s)" value={stats?.mean_time_to_ack_seconds ?? 0} />
      </div>

      {/* Scoring-system distribution */}
      {totalBySystem > 0 && (
        <div className="bg-bg-card border border-border rounded-xl p-3 flex gap-2 items-center flex-wrap text-xs">
          <span className="text-slate-400">Active screens by system:</span>
          {Object.entries(systemDist).map(([sys, n]) => (
            <span key={sys} className={`px-2 py-1 rounded ${systemColour(sys)}`}>
              {sys.toUpperCase()}: {n as number}
            </span>
          ))}
        </div>
      )}

      {/* Run screen */}
      <section className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <h3 className="font-semibold text-white">Run screen</h3>
          <div className="flex gap-1 ml-2">
            {(["news2", "pews", "imews"] as ScoringSystem[]).map((s) => (
              <button
                key={s}
                onClick={() => setActiveSystem(s)}
                className={`px-2 py-1 text-xs rounded ${activeSystem === s ? systemColour(s) : "bg-bg-primary text-slate-400 border border-border hover:text-white"}`}
              >
                {s.toUpperCase()}
              </button>
            ))}
          </div>
          <span className="text-[10px] text-slate-500 ml-2">
            {activeSystem === "news2" && "Adult — HSE iNEWS2"}
            {activeSystem === "pews" && "Paediatric — NCEC NCG #1"}
            {activeSystem === "imews" && "Maternal — NCEC NCG #4"}
          </span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <Input label="hadm_id" value={hadmId} onChange={setHadmId} />
          <Input label="subject_id" value={subjectId} onChange={setSubjectId} />
          <Input label="department" value={department} onChange={setDepartment} />
          <Input label="RR" value={String(rr)} onChange={(v) => setRr(Number(v) || 0)} />
          <Input label="SpO2" value={String(spo2)} onChange={(v) => setSpo2(Number(v) || 0)} />
          <Input label="Temp (°C)" value={String(temp)} onChange={(v) => setTemp(Number(v) || 0)} />
          <Input label="SBP" value={String(sbp)} onChange={(v) => setSbp(Number(v) || 0)} />
          <Input label="HR" value={String(hr)} onChange={(v) => setHr(Number(v) || 0)} />
          {activeSystem === "imews" && (
            <Input label="DBP" value={String(dbp)} onChange={(v) => setDbp(Number(v) || 0)} />
          )}
        </div>

        <div className="flex gap-3 mt-3 items-center flex-wrap">
          <label className="text-xs text-slate-400 flex items-center gap-2">
            <input type="checkbox" checked={supplO2} onChange={(e) => setSupplO2(e.target.checked)} />
            On supplemental O₂
          </label>
          <select value={consciousness} onChange={(e) => setConsciousness(e.target.value)} className="bg-bg-primary border border-border rounded px-2 py-1 text-xs text-white">
            <option value="A">Alert</option>
            <option value="V">Voice</option>
            <option value="P">Pain</option>
            <option value="U">Unresponsive</option>
            <option value="Confusion">Confusion (new)</option>
          </select>

          {activeSystem === "pews" && (
            <>
              <Input label="age (months)" value={String(ageMonths)} onChange={(v) => setAgeMonths(Number(v) || 0)} />
              <select value={behaviour} onChange={(e) => setBehaviour(e.target.value)} className="bg-bg-primary border border-border rounded px-2 py-1 text-xs text-white">
                <option value="Alert">Behaviour: Alert</option>
                <option value="Irritable">Irritable</option>
                <option value="Lethargic">Lethargic</option>
                <option value="Unresponsive">Unresponsive</option>
              </select>
              <select value={respEffort} onChange={(e) => setRespEffort(e.target.value)} className="bg-bg-primary border border-border rounded px-2 py-1 text-xs text-white">
                <option value="none">Resp effort: none</option>
                <option value="mild">mild</option>
                <option value="moderate">moderate</option>
                <option value="severe">severe</option>
              </select>
              <Input label="CRT (s)" value={String(crt)} onChange={(v) => setCrt(Number(v) || 0)} />
            </>
          )}

          {activeSystem === "imews" && (
            <>
              <Input label="gestation (wks)" value={String(gestWeeks)} onChange={(v) => setGestWeeks(v === "" ? "" : Number(v))} />
              <Input label="post-partum (days)" value={String(ppDays)} onChange={(v) => setPpDays(v === "" ? "" : Number(v))} />
              <select value={proteinuria} onChange={(e) => setProteinuria(e.target.value)} className="bg-bg-primary border border-border rounded px-2 py-1 text-xs text-white">
                <option value="negative">proteinuria: neg</option>
                <option value="trace">trace</option>
                <option value="1+">1+</option>
                <option value="2+">2+</option>
                <option value="3+">3+</option>
                <option value="4+">4+</option>
              </select>
            </>
          )}

          <button onClick={runScreen} className="ml-auto px-3 py-1.5 text-sm rounded-lg bg-rose-500/20 text-rose-300 hover:bg-rose-500/30">
            Compute {activeSystem.toUpperCase()}
          </button>
        </div>

        {screenResult && (
          <div className="mt-3 bg-bg-primary rounded p-3">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`px-2 py-0.5 rounded text-xs font-semibold ${systemColour(screenResult.scoring_system)}`}>
                {screenResult.scoring_system?.toUpperCase()}
              </span>
              <span className={`px-2 py-0.5 rounded text-xs font-semibold ${bandColour(screenResult.score.risk_band)}`}>
                Band: {screenResult.score.risk_band}
              </span>
              <span className="text-white text-sm">Total: <strong>{screenResult.score.total}</strong></span>
              {screenResult.score.age_band && (
                <span className="text-xs text-slate-400"><Baby className="inline w-3 h-3 mr-1" />Age band: {screenResult.score.age_band}</span>
              )}
              {screenResult.score.gestational_context && (
                <span className="text-xs text-slate-400"><Activity className="inline w-3 h-3 mr-1" />{screenResult.score.gestational_context}</span>
              )}
              {screenResult.trend && (
                <span className="text-xs text-slate-400">
                  {trajectoryIcon(screenResult.trend.trajectory)} {screenResult.trend.trajectory} (Δ {screenResult.trend.delta})
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-2">{screenResult.score.recommended_response}</p>
            <details className="mt-2">
              <summary className="text-xs text-slate-500 cursor-pointer">Components</summary>
              <pre className="text-xs text-slate-400 mt-1 overflow-x-auto">{JSON.stringify(screenResult.score.components, null, 2)}</pre>
            </details>
          </div>
        )}
      </section>

      {/* Active alerts */}
      <section className="bg-bg-card border border-border rounded-xl p-4">
        <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
          <BellRing className="w-4 h-4 text-amber-400" /> Active alerts ({alerts.length})
        </h3>
        {alerts.length === 0 ? (
          <p className="text-sm text-slate-500">No active alerts. Simulation vitals will populate this when any NEWS2 ≥ 5 / PEWS ≥ 3 / IMEWS pink fires.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-slate-400">
                <tr className="border-b border-border">
                  <th className="text-left py-1.5 px-2">HADM</th>
                  <th className="text-left py-1.5 px-2">System</th>
                  <th className="text-left py-1.5 px-2">Dept</th>
                  <th className="text-right py-1.5 px-2">Score</th>
                  <th className="text-left py-1.5 px-2">Band</th>
                  <th className="text-left py-1.5 px-2">Trend</th>
                  <th className="text-left py-1.5 px-2">Observed</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a, i) => (
                  <tr key={`${a.hadm_id}-${i}`} className="border-t border-border/30 hover:bg-slate-800/30">
                    <td className="py-1 px-2 font-mono-clinical text-[11px]">{a.hadm_id}</td>
                    <td className="py-1 px-2">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${systemColour(a.scoring_system)}`}>
                        {(a.scoring_system || "news2").toUpperCase()}
                      </span>
                    </td>
                    <td className="py-1 px-2 text-slate-300">{a.department || "—"}</td>
                    <td className="py-1 px-2 text-right font-semibold text-white">{a.score?.total ?? "—"}</td>
                    <td className="py-1 px-2">
                      <span className={`px-2 py-0.5 rounded text-[10px] ${bandColour(a.score?.risk_band || "")}`}>
                        {a.score?.risk_band || "—"}
                      </span>
                    </td>
                    <td className="py-1 px-2 text-slate-400 text-xs">
                      {a.trend ? (
                        <>{trajectoryIcon(a.trend.trajectory)} {a.trend.trajectory}{a.trend.is_clinically_rising && " ⚠️"}</>
                      ) : "—"}
                    </td>
                    <td className="py-1 px-2 text-xs font-mono-clinical text-slate-500">{a.observed_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Escalation audit + acknowledge */}
      <section className="bg-bg-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h3 className="font-semibold text-white">Escalation audit</h3>
            {/* Sim-only auto-ack toggle — shows only when the backend reports
                DEPLOYMENT_MODE=simulation; in prod the button is hidden so
                clinicians can't accidentally flip it. */}
            {governance?.deployment_mode === "simulation" && (
              <button
                onClick={toggleAutoAck}
                className={`text-[11px] px-2.5 py-1 rounded-full border flex items-center gap-1.5 transition-colors ${
                  governance.effective
                    ? "bg-purple-500/20 border-purple-500/60 text-purple-300 hover:bg-purple-500/30"
                    : "bg-slate-800 border-slate-600 text-slate-400 hover:text-slate-200"
                }`}
                title={
                  governance.effective
                    ? `Auto-ack ON — synthetic SBAR after ${governance.auto_ack_delay_seconds}s. Sim-mode only.`
                    : "Auto-ack OFF — escalations require manual clinician review."
                }
              >
                <Bot className="w-3 h-3" />
                Auto-ack (sim): {governance.effective ? "ON" : "OFF"}
              </button>
            )}
          </div>
          <label className="text-xs text-slate-400 flex items-center gap-2">
            <input type="checkbox" checked={unackOnly} onChange={(e) => setUnackOnly(e.target.checked)} />
            Unacknowledged only
          </label>
        </div>
        {audit.length === 0 ? (
          <p className="text-sm text-slate-500">
            {unackOnly ? "No unacknowledged escalations." : "No escalations yet."}
          </p>
        ) : (
          <div className="max-h-96 overflow-auto">
            <ul className="space-y-1 text-sm">
              {audit.map((a) => (
                <li key={a.escalation_id} className="border-b border-border/30 py-2 flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${systemColour(a.scoring_system)}`}>
                        {(a.scoring_system || "news2").toUpperCase()}
                      </span>
                      <span className="font-mono-clinical text-xs text-slate-400">{a.escalated_at}</span>
                      <span className="text-slate-200 font-medium">HADM {a.hadm_id}</span>
                      <span className="text-slate-300">
                        score=<strong>{a.score?.total ?? "?"}</strong>
                      </span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${bandColour(a.score?.risk_band || "")}`}>
                        {a.score?.risk_band || "—"}
                      </span>
                      {a.acknowledged ? (
                        <>
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                            ACKED {typeof a.time_to_ack_seconds === "number" ? `in ${Math.round(a.time_to_ack_seconds)}s` : ""}
                          </span>
                          {(a as DeteriorationEscalation & { auto_ack?: boolean }).auto_ack && (
                            <span
                              className="px-1.5 py-0.5 rounded text-[10px] bg-purple-500/20 text-purple-300 border border-purple-500/40 flex items-center gap-1"
                              title="Synthetic sim-mode acknowledgement (flagged for audit filters)"
                            >
                              <Bot className="w-2.5 h-2.5" /> SIM AUTO
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/40">PENDING</span>
                      )}
                    </div>
                    <div className="text-xs text-slate-500 mt-0.5 break-words">
                      {Array.isArray(a.actions) ? a.actions.join(" · ") : ""}
                    </div>
                  </div>
                  {!a.acknowledged && (
                    <button
                      onClick={() => acknowledge(a.escalation_id)}
                      className="shrink-0 px-2 py-1 text-[11px] rounded bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 border border-emerald-500/40 flex items-center gap-1"
                    >
                      <CheckCircle2 className="w-3 h-3" /> Ack
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, colour }: { label: string; value: number | string; colour?: string }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-3">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${colour || "text-white"}`}>{value}</div>
    </div>
  );
}

function Input({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] text-slate-400">{label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} className="bg-bg-primary border border-border rounded px-2 py-1 text-white text-sm" />
    </label>
  );
}
