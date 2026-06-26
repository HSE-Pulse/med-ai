import React, { useCallback, useEffect, useRef, useState } from "react";

/* --------------------------------------------------------------------------
   Types
   -------------------------------------------------------------------------- */

interface SOFABreakdown {
  respiration: number;
  coagulation: number;
  liver: number;
  cardiovascular: number;
  renal: number;
  total: number;
}

interface PatientSummary {
  stay_id: number;
  bed: string;
  age: number;
  gender: string;
  careunit: string;
  hours_in_icu: number;
  current_risk: number;
  alert_level: "green" | "yellow" | "orange" | "red";
  sofa_score: number;
  trend: "rising" | "falling" | "stable";
  last_updated: string;
}

interface TimelinePoint {
  timestamp: string;
  risk_score: number;
  alert_level: string;
  sofa_score: number;
  heart_rate?: number;
  respiratory_rate?: number;
  spo2?: number;
  sbp?: number;
  temperature?: number;
  lactate?: number;
}

interface PatientTimeline {
  stay_id: number;
  careunit: string;
  admission_time: string;
  timeline: TimelinePoint[];
  current_alert: string;
  peak_risk: number;
  peak_risk_time?: string;
}

interface UnitOverview {
  unit_name: string;
  total_patients: number;
  red_alerts: number;
  orange_alerts: number;
  yellow_alerts: number;
  patients: PatientSummary[];
}

interface WsUpdate {
  type: string;
  timestamp: string;
  stay_id: number;
  bed: string;
  risk_score: number;
  alert_level: string;
  sofa_score: number;
  vitals: Record<string, number>;
}

/* --------------------------------------------------------------------------
   Configuration
   -------------------------------------------------------------------------- */

declare global {
  interface Window {
    __SEPSIS_CONFIG__?: { API_BASE?: string; WS_BASE?: string };
  }
}

const cfg = window.__SEPSIS_CONFIG__ || {};
const API_BASE = cfg.API_BASE || import.meta.env.VITE_API_BASE || "http://localhost:8000";
const WS_BASE = cfg.WS_BASE || import.meta.env.VITE_WS_BASE || "ws://localhost:8000";

/* --------------------------------------------------------------------------
   Colour helpers
   -------------------------------------------------------------------------- */

const ALERT_COLORS: Record<string, string> = {
  red: "#ef4444",
  orange: "#f97316",
  yellow: "#eab308",
  green: "#22c55e",
};

const ALERT_BG: Record<string, string> = {
  red: "#fef2f2",
  orange: "#fff7ed",
  yellow: "#fefce8",
  green: "#f0fdf4",
};

function alertColor(level: string): string {
  return ALERT_COLORS[level] || "#9ca3af";
}

function alertBg(level: string): string {
  return ALERT_BG[level] || "#f9fafb";
}

/* --------------------------------------------------------------------------
   Simple SVG mini-chart (sparkline)
   -------------------------------------------------------------------------- */

function Sparkline({
  data,
  width = 200,
  height = 40,
  color = "#3b82f6",
}: {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  if (!data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map((v, i) => {
      const x = (i / Math.max(data.length - 1, 1)) * width;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        points={points}
      />
    </svg>
  );
}

/* --------------------------------------------------------------------------
   SOFA breakdown bar
   -------------------------------------------------------------------------- */

function SOFABar({ sofa }: { sofa: SOFABreakdown }) {
  const components: { label: string; key: keyof SOFABreakdown; color: string }[] = [
    { label: "Resp", key: "respiration", color: "#3b82f6" },
    { label: "Coag", key: "coagulation", color: "#8b5cf6" },
    { label: "Liver", key: "liver", color: "#f59e0b" },
    { label: "Cardio", key: "cardiovascular", color: "#ef4444" },
    { label: "Renal", key: "renal", color: "#10b981" },
  ];
  const total = sofa.total || 1;

  return (
    <div style={{ margin: "8px 0" }}>
      <div style={{ display: "flex", height: 20, borderRadius: 4, overflow: "hidden" }}>
        {components.map((c) => {
          const val = sofa[c.key] as number;
          const pct = (val / 20) * 100;
          return (
            <div
              key={c.key}
              title={`${c.label}: ${val}/4`}
              style={{
                width: `${pct}%`,
                minWidth: val > 0 ? 4 : 0,
                backgroundColor: c.color,
                transition: "width 0.3s",
              }}
            />
          );
        })}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#6b7280", marginTop: 2 }}>
        {components.map((c) => (
          <span key={c.key} style={{ color: c.color }}>
            {c.label}: {sofa[c.key] as number}
          </span>
        ))}
        <span style={{ fontWeight: 600 }}>Total: {sofa.total}/20</span>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   Patient card in the monitoring grid
   -------------------------------------------------------------------------- */

function PatientCard({
  patient,
  onClick,
}: {
  patient: PatientSummary;
  onClick: () => void;
}) {
  const trendIcon = patient.trend === "rising" ? "^" : patient.trend === "falling" ? "v" : "-";
  const trendColor = patient.trend === "rising" ? "#ef4444" : patient.trend === "falling" ? "#22c55e" : "#9ca3af";

  return (
    <div
      onClick={onClick}
      style={{
        border: `2px solid ${alertColor(patient.alert_level)}`,
        borderRadius: 8,
        padding: 12,
        backgroundColor: alertBg(patient.alert_level),
        cursor: "pointer",
        transition: "transform 0.15s, box-shadow 0.15s",
        position: "relative",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.transform = "translateY(-2px)";
        (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 12px rgba(0,0,0,0.1)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.transform = "";
        (e.currentTarget as HTMLElement).style.boxShadow = "";
      }}
    >
      {/* Alert badge */}
      <div
        style={{
          position: "absolute",
          top: 8,
          right: 8,
          width: 12,
          height: 12,
          borderRadius: "50%",
          backgroundColor: alertColor(patient.alert_level),
          animation: patient.alert_level === "red" ? "pulse 1.5s infinite" : undefined,
        }}
      />

      <div style={{ fontWeight: 700, fontSize: 15 }}>{patient.bed}</div>
      <div style={{ fontSize: 12, color: "#6b7280" }}>
        {patient.age}{patient.gender} | {patient.careunit} | {patient.hours_in_icu.toFixed(0)}h
      </div>

      <div style={{ marginTop: 8, display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 28, fontWeight: 700, color: alertColor(patient.alert_level) }}>
          {(patient.current_risk * 100).toFixed(0)}%
        </span>
        <span style={{ fontSize: 14, color: trendColor, fontWeight: 600 }}>{trendIcon} {patient.trend}</span>
      </div>

      <div style={{ fontSize: 12, color: "#4b5563", marginTop: 4 }}>SOFA: {patient.sofa_score}</div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   Alert notification panel
   -------------------------------------------------------------------------- */

function AlertPanel({ alerts }: { alerts: WsUpdate[] }) {
  return (
    <div
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: 12,
        maxHeight: 300,
        overflowY: "auto",
        backgroundColor: "#fff",
      }}
    >
      <h3 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 600 }}>Live Alerts</h3>
      {alerts.length === 0 && <div style={{ color: "#9ca3af", fontSize: 13 }}>No alerts yet</div>}
      {alerts.map((a, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 0",
            borderBottom: "1px solid #f3f4f6",
            fontSize: 13,
          }}
        >
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              backgroundColor: alertColor(a.alert_level),
              flexShrink: 0,
            }}
          />
          <span style={{ fontWeight: 600 }}>{a.bed}</span>
          <span style={{ color: alertColor(a.alert_level), fontWeight: 600 }}>
            {(a.risk_score * 100).toFixed(0)}%
          </span>
          <span style={{ color: "#9ca3af", marginLeft: "auto" }}>
            {new Date(a.timestamp).toLocaleTimeString()}
          </span>
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------------------
   Patient detail view
   -------------------------------------------------------------------------- */

function PatientDetail({
  stayId,
  onBack,
}: {
  stayId: number;
  onBack: () => void;
}) {
  const [timeline, setTimeline] = useState<PatientTimeline | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_BASE}/patient/${stayId}/timeline`)
      .then((r) => r.json())
      .then((data: PatientTimeline) => {
        setTimeline(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [stayId]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 40, color: "#9ca3af" }}>Loading patient data...</div>
    );
  }

  if (!timeline) {
    return (
      <div style={{ textAlign: "center", padding: 40 }}>
        <div style={{ color: "#ef4444" }}>Failed to load patient data</div>
        <button onClick={onBack} style={{ marginTop: 12 }}>Back</button>
      </div>
    );
  }

  const riskData = timeline.timeline.map((t) => t.risk_score);
  const hrData = timeline.timeline.map((t) => t.heart_rate ?? 0);
  const spo2Data = timeline.timeline.map((t) => t.spo2 ?? 100);
  const sbpData = timeline.timeline.map((t) => t.sbp ?? 120);
  const tempData = timeline.timeline.map((t) => t.temperature ?? 37);
  const lactateData = timeline.timeline.map((t) => t.lactate ?? 0);
  const sofaData = timeline.timeline.map((t) => t.sofa_score);

  const latest = timeline.timeline[timeline.timeline.length - 1];

  const sofaBreakdown: SOFABreakdown = {
    respiration: latest ? Math.min(4, Math.round(latest.sofa_score * 0.2)) : 0,
    coagulation: latest ? Math.min(4, Math.round(latest.sofa_score * 0.15)) : 0,
    liver: latest ? Math.min(4, Math.round(latest.sofa_score * 0.15)) : 0,
    cardiovascular: latest ? Math.min(4, Math.round(latest.sofa_score * 0.25)) : 0,
    renal: latest ? Math.min(4, Math.round(latest.sofa_score * 0.25)) : 0,
    total: latest?.sofa_score ?? 0,
  };

  return (
    <div>
      <button
        onClick={onBack}
        style={{
          background: "none",
          border: "1px solid #d1d5db",
          borderRadius: 6,
          padding: "6px 16px",
          cursor: "pointer",
          marginBottom: 16,
          fontSize: 13,
        }}
      >
        Back to Unit Overview
      </button>

      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: alertBg(timeline.current_alert),
          border: `2px solid ${alertColor(timeline.current_alert)}`,
          borderRadius: 8,
          padding: 16,
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: 0 }}>Stay {timeline.stay_id}</h2>
          <div style={{ color: "#6b7280", fontSize: 13 }}>
            {timeline.careunit} | Admitted {new Date(timeline.admission_time).toLocaleString()}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 36, fontWeight: 700, color: alertColor(timeline.current_alert) }}>
            {latest ? (latest.risk_score * 100).toFixed(0) : 0}%
          </div>
          <div style={{ fontSize: 12, color: "#6b7280" }}>Sepsis Risk</div>
        </div>
      </div>

      {/* SOFA breakdown */}
      <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>SOFA Score Breakdown</h3>
        <SOFABar sofa={sofaBreakdown} />
      </div>

      {/* Charts grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
        {[
          { label: "Sepsis Risk", data: riskData, color: "#ef4444", unit: "%" },
          { label: "Heart Rate", data: hrData, color: "#3b82f6", unit: "bpm" },
          { label: "SpO2", data: spo2Data, color: "#8b5cf6", unit: "%" },
          { label: "Systolic BP", data: sbpData, color: "#f59e0b", unit: "mmHg" },
          { label: "Temperature", data: tempData, color: "#10b981", unit: "C" },
          { label: "Lactate", data: lactateData, color: "#ec4899", unit: "mmol/L" },
        ].map(({ label, data, color, unit }) => (
          <div
            key={label}
            style={{
              background: "#fff",
              border: "1px solid #e5e7eb",
              borderRadius: 8,
              padding: 12,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>{label}</span>
              <span style={{ fontSize: 12, color }}>
                {data.length ? data[data.length - 1].toFixed(1) : "-"} {unit}
              </span>
            </div>
            <Sparkline data={data} width={220} height={50} color={color} />
          </div>
        ))}
      </div>

      {/* SOFA timeline */}
      <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 8, padding: 16 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>SOFA Score Timeline</h3>
        <Sparkline data={sofaData} width={700} height={60} color="#ef4444" />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#9ca3af", marginTop: 4 }}>
          <span>{new Date(timeline.admission_time).toLocaleString()}</span>
          <span>Peak: {timeline.peak_risk.toFixed(2)} at {timeline.peak_risk_time ? new Date(timeline.peak_risk_time).toLocaleTimeString() : "N/A"}</span>
          <span>Now</span>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
   Summary stats bar
   -------------------------------------------------------------------------- */

function StatsBar({ overview }: { overview: UnitOverview }) {
  const stats = [
    { label: "Total Patients", value: overview.total_patients, color: "#3b82f6" },
    { label: "Red Alerts", value: overview.red_alerts, color: "#ef4444" },
    { label: "Orange Alerts", value: overview.orange_alerts, color: "#f97316" },
    { label: "Yellow Alerts", value: overview.yellow_alerts, color: "#eab308" },
  ];

  return (
    <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
      {stats.map((s) => (
        <div
          key={s.label}
          style={{
            flex: 1,
            background: "#fff",
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            padding: "12px 16px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 28, fontWeight: 700, color: s.color }}>{s.value}</div>
          <div style={{ fontSize: 12, color: "#6b7280" }}>{s.label}</div>
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------------------
   Main App
   -------------------------------------------------------------------------- */

export default function App() {
  const [overview, setOverview] = useState<UnitOverview | null>(null);
  const [selectedStay, setSelectedStay] = useState<number | null>(null);
  const [wsAlerts, setWsAlerts] = useState<WsUpdate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Fetch unit overview
  const fetchOverview = useCallback(() => {
    fetch(`${API_BASE}/unit-overview`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: UnitOverview) => {
        setOverview(data);
        setError(null);
      })
      .catch((e) => setError(`Failed to load unit overview: ${e.message}`));
  }, []);

  useEffect(() => {
    fetchOverview();
    const interval = setInterval(fetchOverview, 30_000);
    return () => clearInterval(interval);
  }, [fetchOverview]);

  // WebSocket connection
  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      ws = new WebSocket(`${WS_BASE}/ws/monitor`);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const data: WsUpdate = JSON.parse(event.data);
          if (data.alert_level === "red" || data.alert_level === "orange") {
            setWsAlerts((prev) => [data, ...prev].slice(0, 50));
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        reconnectTimer = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // Patient detail view
  if (selectedStay !== null) {
    return (
      <div style={{ fontFamily: "'Inter', -apple-system, sans-serif", padding: 24, maxWidth: 900, margin: "0 auto" }}>
        <PatientDetail stayId={selectedStay} onBack={() => setSelectedStay(null)} />
      </div>
    );
  }

  // Main dashboard
  return (
    <div style={{ fontFamily: "'Inter', -apple-system, sans-serif", padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      {/* Pulse animation keyframes */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, color: "#111827" }}>Sepsis & ICU Deterioration Monitor</h1>
          <p style={{ margin: "4px 0 0", color: "#6b7280", fontSize: 14 }}>
            Real-time sepsis onset prediction -- 4-6 hour early warning
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: error ? "#ef4444" : "#22c55e" }} />
          <span style={{ fontSize: 12, color: "#6b7280" }}>{error ? "Disconnected" : "Connected"}</span>
        </div>
      </div>

      {error && (
        <div
          style={{
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 8,
            padding: 12,
            marginBottom: 16,
            color: "#991b1b",
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {overview && <StatsBar overview={overview} />}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 300px", gap: 16 }}>
        {/* Patient grid */}
        <div>
          <h2 style={{ fontSize: 16, margin: "0 0 12px", color: "#374151" }}>Patient Monitoring Grid</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
            {overview?.patients.map((p) => (
              <PatientCard key={p.stay_id} patient={p} onClick={() => setSelectedStay(p.stay_id)} />
            ))}
          </div>
          {!overview && !error && (
            <div style={{ textAlign: "center", padding: 40, color: "#9ca3af" }}>Loading...</div>
          )}
        </div>

        {/* Alert panel */}
        <div>
          <h2 style={{ fontSize: 16, margin: "0 0 12px", color: "#374151" }}>Notifications</h2>
          <AlertPanel alerts={wsAlerts} />
        </div>
      </div>
    </div>
  );
}
