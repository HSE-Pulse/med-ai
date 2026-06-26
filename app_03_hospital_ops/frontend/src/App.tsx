import { useState, useEffect, useCallback } from 'react';

const API = 'http://localhost:8203';
const DEPTS = ['ED', 'ED_Observation', 'Medicine', 'Med_Surg', 'Cardiology', 'Neurology', 'ICU', 'Discharge_Lounge'];
const COLORS: Record<string, string> = {
  ED: '#DC2626', ED_Observation: '#F97316', Medicine: '#3B82F6', Med_Surg: '#8B5CF6',
  Cardiology: '#EC4899', Neurology: '#14B8A6', ICU: '#EF4444', Discharge_Lounge: '#22C55E',
};

interface DeptState { patients: number; capacity: number; wait_time: number; staff: number; }
interface SimState { departments: Record<string, DeptState>; sim_time: number; total_patients: number; }

export default function App() {
  const [simState, setSimState] = useState<SimState | null>(null);
  const [running, setRunning] = useState(false);
  const [metrics, setMetrics] = useState<any>(null);

  const startSim = async () => {
    await fetch(`${API}/start`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ n_patients: 100, episode_hours: 48, algo: 'maddpg' }) });
    setRunning(true);
  };

  const fetchState = useCallback(async () => {
    try {
      const r = await fetch(`${API}/state`);
      const d = await r.json();
      if (d.data) setSimState(d.data);
    } catch {}
  }, []);

  const fetchMetrics = useCallback(async () => {
    try {
      const r = await fetch(`${API}/metrics`);
      const d = await r.json();
      if (d.data) setMetrics(d.data);
    } catch {}
  }, []);

  useEffect(() => {
    if (!running) return;
    const iv = setInterval(() => { fetchState(); fetchMetrics(); }, 2000);
    return () => clearInterval(iv);
  }, [running, fetchState, fetchMetrics]);

  return (
    <div style={{ fontFamily: 'system-ui', background: '#0F172A', color: '#E2E8F0', minHeight: '100vh', padding: 24 }}>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 8 }}>Hospital Operations - DES-MARL</h1>
      <p style={{ color: '#94A3B8', marginBottom: 24 }}>Multi-agent reinforcement learning for dynamic hospital staffing</p>

      <div style={{ marginBottom: 24 }}>
        <button onClick={startSim} disabled={running}
          style={{ padding: '10px 24px', background: running ? '#475569' : '#3B82F6', color: '#fff',
            border: 'none', borderRadius: 8, fontSize: 14, cursor: running ? 'default' : 'pointer' }}>
          {running ? 'Simulation Running...' : 'Start Simulation'}
        </button>
      </div>

      {/* Department Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
        {DEPTS.map(dept => {
          const d = simState?.departments?.[dept] || { patients: 0, capacity: 30, wait_time: 0, staff: 5 };
          const util = d.capacity > 0 ? d.patients / d.capacity : 0;
          return (
            <div key={dept} style={{ background: '#1E293B', borderRadius: 12, padding: 16,
              borderLeft: `4px solid ${COLORS[dept] || '#64748B'}` }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>{dept.replace(/_/g, ' ')}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 24, fontWeight: 700 }}>{d.patients}</div>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>Patients</div>
                </div>
                <div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: d.wait_time > 2 ? '#EF4444' : '#22C55E' }}>
                    {d.wait_time.toFixed(1)}h
                  </div>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>Wait Time</div>
                </div>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 600 }}>{d.staff}</div>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>Staff</div>
                </div>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: util > 0.9 ? '#EF4444' : util > 0.7 ? '#EAB308' : '#22C55E' }}>
                    {(util * 100).toFixed(0)}%
                  </div>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>Utilization</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Metrics Summary */}
      {metrics && (
        <div style={{ background: '#1E293B', borderRadius: 12, padding: 20 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Performance Metrics</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
            <div><div style={{ fontSize: 20, fontWeight: 700 }}>{metrics.mean_wait_time?.toFixed(1) || '-'}h</div>
              <div style={{ fontSize: 12, color: '#94A3B8' }}>Mean Wait Time</div></div>
            <div><div style={{ fontSize: 20, fontWeight: 700 }}>{metrics.throughput?.toFixed(0) || '-'}</div>
              <div style={{ fontSize: 12, color: '#94A3B8' }}>Throughput (patients/day)</div></div>
            <div><div style={{ fontSize: 20, fontWeight: 700 }}>{simState?.total_patients || 0}</div>
              <div style={{ fontSize: 12, color: '#94A3B8' }}>Total Patients</div></div>
            <div><div style={{ fontSize: 20, fontWeight: 700 }}>{(simState?.sim_time || 0).toFixed(1)}h</div>
              <div style={{ fontSize: 12, color: '#94A3B8' }}>Sim Time</div></div>
          </div>
        </div>
      )}
    </div>
  );
}
