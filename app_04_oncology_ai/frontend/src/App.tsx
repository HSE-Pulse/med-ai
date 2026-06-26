import { useState, useEffect } from 'react';

const API = 'http://localhost:8204';

interface RiskResult { readmission_30d_risk: number; mortality_risk: number; combined_risk: number;
  risk_level: string; risk_color: string; contributing_factors: string[]; recommendations: string[]; }
interface PathwayResult { cancer_type: string; treatment_sequence: any[]; estimated_duration_days: number;
  urgency_score: number; notes: string[]; }
interface CohortData { total_patients: number; total_admissions: number; readmission_rate: number;
  mortality_rate: number; cancer_type_distribution: Record<string, number>; }

export default function App() {
  const [tab, setTab] = useState<'risk' | 'pathway' | 'cohort'>('risk');
  const [risk, setRisk] = useState<RiskResult | null>(null);
  const [pathway, setPathway] = useState<PathwayResult | null>(null);
  const [cohort, setCohort] = useState<CohortData | null>(null);
  const [form, setForm] = useState({ age: 65, gender: 'M', cancer_type: 'Lung', stage_proxy: 2,
    num_procedures: 1, has_surgery: 0, has_chemotherapy: 1, charlson_score: 2, total_los_days: 7,
    num_prior_admissions: 1, num_comorbidities: 3 });

  const predictRisk = async () => {
    const r = await fetch(`${API}/predict-risk`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
    const d = await r.json();
    if (d.data) setRisk(d.data);
  };

  const getPathway = async () => {
    const r = await fetch(`${API}/recommend-pathway`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cancer_type: form.cancer_type, age: form.age, stage_proxy: form.stage_proxy,
        charlson_score: form.charlson_score }) });
    const d = await r.json();
    if (d.data) setPathway(d.data);
  };

  useEffect(() => {
    fetch(`${API}/cohort-stats`).then(r => r.json()).then(d => { if (d.data) setCohort(d.data); }).catch(() => {});
  }, []);

  const Tab = ({ id, label }: { id: typeof tab; label: string }) => (
    <button onClick={() => setTab(id)} style={{ padding: '8px 20px', borderRadius: 6,
      background: tab === id ? '#3B82F6' : 'transparent', color: tab === id ? '#fff' : '#94A3B8',
      border: 'none', cursor: 'pointer', fontSize: 14 }}>{label}</button>
  );

  const CANCERS = ['Lung', 'Breast', 'Colon', 'Colorectal', 'Prostate', 'Leukemia (Myeloid)',
    'Non-Hodgkin Lymphoma', 'Multiple Myeloma', 'Brain', 'Pancreatic', 'Liver', 'Bladder'];

  return (
    <div style={{ fontFamily: 'system-ui', background: '#0F172A', color: '#E2E8F0', minHeight: '100vh', padding: 24 }}>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Oncology AI</h1>
      <p style={{ color: '#94A3B8', marginBottom: 20 }}>Cancer risk prediction and treatment pathway optimization</p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
        <Tab id="risk" label="Risk Prediction" />
        <Tab id="pathway" label="Treatment Pathway" />
        <Tab id="cohort" label="Cohort Analytics" />
      </div>

      {tab === 'risk' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
          <div style={{ background: '#1E293B', borderRadius: 12, padding: 20 }}>
            <h3 style={{ marginBottom: 16 }}>Patient Information</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {[{k:'age',l:'Age',t:'number'},{k:'gender',l:'Gender',t:'select',o:['M','F']},
                {k:'cancer_type',l:'Cancer Type',t:'select',o:CANCERS},
                {k:'stage_proxy',l:'Stage (1-4)',t:'number'},
                {k:'charlson_score',l:'Charlson Score',t:'number'},
                {k:'num_prior_admissions',l:'Prior Admissions',t:'number'},
                {k:'has_chemotherapy',l:'Has Chemo (0/1)',t:'number'},
                {k:'has_surgery',l:'Has Surgery (0/1)',t:'number'},
                {k:'total_los_days',l:'LOS (days)',t:'number'},
                {k:'num_comorbidities',l:'Comorbidities',t:'number'},
              ].map(f => (
                <div key={f.k}>
                  <label style={{ fontSize: 12, color: '#94A3B8' }}>{f.l}</label>
                  {f.t === 'select' ? (
                    <select value={(form as any)[f.k]} onChange={e => setForm({...form, [f.k]: e.target.value})}
                      style={{ width: '100%', padding: 8, background: '#0F172A', color: '#E2E8F0',
                        border: '1px solid #334155', borderRadius: 6 }}>
                      {f.o!.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input type="number" value={(form as any)[f.k]}
                      onChange={e => setForm({...form, [f.k]: Number(e.target.value)})}
                      style={{ width: '100%', padding: 8, background: '#0F172A', color: '#E2E8F0',
                        border: '1px solid #334155', borderRadius: 6 }} />
                  )}
                </div>
              ))}
            </div>
            <button onClick={predictRisk} style={{ marginTop: 16, padding: '10px 24px',
              background: '#3B82F6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer' }}>
              Predict Risk
            </button>
          </div>

          {risk && (
            <div style={{ background: '#1E293B', borderRadius: 12, padding: 20 }}>
              <h3 style={{ marginBottom: 16 }}>Risk Assessment</h3>
              <div style={{ textAlign: 'center', marginBottom: 20 }}>
                <div style={{ fontSize: 48, fontWeight: 700, color: risk.risk_color }}>
                  {(risk.combined_risk * 100).toFixed(0)}%
                </div>
                <div style={{ fontSize: 18, fontWeight: 600, color: risk.risk_color, textTransform: 'uppercase' }}>
                  {risk.risk_level} Risk
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
                <div style={{ background: '#0F172A', padding: 12, borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>30-Day Readmission</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{(risk.readmission_30d_risk * 100).toFixed(1)}%</div>
                </div>
                <div style={{ background: '#0F172A', padding: 12, borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>Mortality</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{(risk.mortality_risk * 100).toFixed(1)}%</div>
                </div>
              </div>
              {risk.contributing_factors.length > 0 && (<>
                <h4 style={{ fontSize: 14, marginBottom: 8 }}>Risk Factors</h4>
                <ul style={{ paddingLeft: 16, marginBottom: 16 }}>
                  {risk.contributing_factors.map((f, i) => <li key={i} style={{ fontSize: 13, marginBottom: 4 }}>{f}</li>)}
                </ul>
              </>)}
              <h4 style={{ fontSize: 14, marginBottom: 8 }}>Recommendations</h4>
              <ul style={{ paddingLeft: 16 }}>
                {risk.recommendations.map((r, i) => <li key={i} style={{ fontSize: 13, color: '#22C55E', marginBottom: 4 }}>{r}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === 'pathway' && (
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'end' }}>
            <div>
              <label style={{ fontSize: 12, color: '#94A3B8' }}>Cancer Type</label>
              <select value={form.cancer_type} onChange={e => setForm({...form, cancer_type: e.target.value})}
                style={{ display: 'block', padding: 8, background: '#1E293B', color: '#E2E8F0', border: '1px solid #334155', borderRadius: 6 }}>
                {CANCERS.map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            <button onClick={getPathway} style={{ padding: '10px 20px', background: '#8B5CF6',
              color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer' }}>Get Pathway</button>
          </div>
          {pathway && (
            <div style={{ background: '#1E293B', borderRadius: 12, padding: 20 }}>
              <h3>{pathway.cancer_type} Treatment Pathway</h3>
              <p style={{ color: '#94A3B8', marginBottom: 16 }}>
                Estimated duration: {pathway.estimated_duration_days} days | Urgency: {(pathway.urgency_score * 100).toFixed(0)}%
              </p>
              {pathway.treatment_sequence.map((s: any) => (
                <div key={s.step} style={{ display: 'flex', gap: 16, padding: 12, borderBottom: '1px solid #334155', alignItems: 'center' }}>
                  <div style={{ width: 32, height: 32, borderRadius: '50%', background: '#3B82F6',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, flexShrink: 0 }}>
                    {s.step}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600 }}>{s.treatment}</div>
                    <div style={{ fontSize: 12, color: '#94A3B8' }}>{s.category} | ~{s.estimated_days} days | {s.priority}</div>
                  </div>
                </div>
              ))}
              {pathway.notes.map((n, i) => (
                <p key={i} style={{ fontSize: 13, color: '#EAB308', marginTop: 8 }}>* {n}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'cohort' && cohort && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
          <div style={{ background: '#1E293B', borderRadius: 12, padding: 20 }}>
            <h3 style={{ marginBottom: 16 }}>Cohort Overview</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {[
                { l: 'Total Patients', v: cohort.total_patients?.toLocaleString() },
                { l: 'Total Admissions', v: cohort.total_admissions?.toLocaleString() },
                { l: 'Readmission Rate', v: `${(cohort.readmission_rate * 100).toFixed(1)}%` },
                { l: 'Mortality Rate', v: `${(cohort.mortality_rate * 100).toFixed(1)}%` },
              ].map(s => (
                <div key={s.l} style={{ background: '#0F172A', padding: 12, borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: '#94A3B8' }}>{s.l}</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{s.v}</div>
                </div>
              ))}
            </div>
          </div>
          <div style={{ background: '#1E293B', borderRadius: 12, padding: 20 }}>
            <h3 style={{ marginBottom: 16 }}>Cancer Type Distribution</h3>
            {cohort.cancer_type_distribution && Object.entries(cohort.cancer_type_distribution)
              .sort(([,a],[,b]) => (b as number) - (a as number)).slice(0, 12).map(([type, count]) => (
              <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <div style={{ width: 120, fontSize: 12, color: '#94A3B8' }}>{type}</div>
                <div style={{ flex: 1, background: '#0F172A', borderRadius: 4, height: 20 }}>
                  <div style={{ width: `${((count as number) / cohort.total_admissions) * 100}%`,
                    background: '#3B82F6', borderRadius: 4, height: 20, minWidth: 2 }} />
                </div>
                <div style={{ width: 50, textAlign: 'right', fontSize: 12 }}>{(count as number).toLocaleString()}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
