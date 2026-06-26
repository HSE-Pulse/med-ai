# HSE Pulse — Real-World Hospital Deployment Readiness Assessment

**Date**: 2026-05-27
**Scope**: Can the HSE Pulse / med-ai simulation be deployed in a real Irish
hospital today, and if not, what would it take?
**Method**: three parallel deep audits — clinical / AI validity, regulatory
+ security posture, operational + infrastructure readiness — synthesised here.
**Audience**: engineering leadership, clinical sponsors, prospective HSE
partners, HIQA pre-engagement.

---

## TL;DR

**Excellent research / demonstration platform. NOT deployable in a real
Irish hospital today.** Approximately **12–18 months and €2.2–3.5 M of
focused work** to reach a controlled single-ward advisory pilot;
**24–36 months** to multi-ward production.

The architecture is sound (20 cleanly-separated microservices, a digital
twin orchestrator, observability stack, FHIR / GDPR / XAI scaffolding all
present), but every layer has gaps that are deployment-blocking in
healthcare. The blockers are foundational, not cosmetic:

- AI models trained on the wrong population (US ICU data, not Irish HSE)
- Zero API authentication on any of the 20 services
- Plaintext HTTP between services (no TLS, no mTLS)
- PHI shipped to OpenAI without a Data Processing Agreement
- No Clinical Safety Case (DCB-0129 / DCB-0160)
- Mandatory deterioration-alert acknowledgement gate is **on-by-default
  disabled** in "simulation" mode
- Single-laptop scale (RTX 4060, 30 GB RAM) versus a 500-bed hospital

---

## 1. Clinical / AI validity — **RED**

Every ML model in the platform trains on **MIMIC-IV** (US ICU data from
Beth Israel Deaconess Medical Center, Boston). Zero Irish validation.
The downstream consequences cascade across the stack:

| Concern | Reality | Severity |
|---|---|---|
| Training data provenance | MIMIC-IV only. No Irish cohort, no HSE data-access agreement, no external-validation study. | **CRITICAL** |
| ICD coding | Models emit ICD-10-**CM** (US). Irish HIPE coders use ICD-10-**AM**. Output is silently re-coded by hand — automation value destroyed. | **CRITICAL** |
| ED triage | XGBoost trained on **ESI** (US 5-level). Irish EDs use **Manchester Triage Scale (MTS)**. Current code maps ESI→MTS via "weighted random" — not a validated mapping. | **CRITICAL** |
| SOFA implementation | Respiration component uses SpO₂ only; no PaO₂ / FiO₂. **Deviates from Sepsis-3** (Vincent et al. 2016, Lancet Resp Med). | HIGH |
| Decision thresholds | Sepsis risk cutoffs 0.75 / 0.50 / 0.25 hardcoded with no published derivation, no calibration vs qSOFA / SIRS, no clinical-consensus sign-off. | HIGH |
| Reported sepsis AUROC 0.998 | Either overfit or label leakage. No external validation. | HIGH |
| MADDPG staffing | After today's retrain, live ratio 1.05–1.24 × baseline = essentially tied or slightly worse. Plateaus at reward ≈ −690. No EWTD / safe-staffing action masking. No agency-vs-permanent cost-awareness. | HIGH |
| Paediatric models | PEWS / IMEWS stubs only. ~20 % of Irish ED volume is paediatric. | **CRITICAL** |
| Sepsis-6 bundle compliance | Absent. Irish National Sepsis Programme requires bundle-compliance auditing as a national KPI. | **CRITICAL** |
| NTPF (waiting-list) integration | Not present. NTPF is the mandatory national waiting-list mechanism in Ireland. | **CRITICAL** |

### Human-in-the-loop governance bug

`app_20_deterioration/backend/app/main.py` defines
`DETERIORATION_AUTO_ACK_IN_SIM=True` as the default. In simulation mode,
the mandatory clinical-acknowledgement gate is silently disabled — NEWS2
escalations are auto-acked with no clinician review. If a production
deploy ever inherited that environment variable (or a config copy-paste
mistake), the central safety gate would vanish without warning. **This
alone would fail HIQA.**

---

## 2. Regulatory / Security — **RED**

| Area | Status | Severity |
|---|---|---|
| API authentication | **Zero auth on all 20 services.** No `Depends`, no JWT, no OAuth2, no API key. Anyone on the hospital subnet can mutate patient state, query FHIR records, or invoke AI predictions. | **CRITICAL** |
| Transport encryption | Plaintext HTTP on the dashboard (port 3010) and inter-service Docker DNS. No TLS, no mTLS. Linkerd is present in Helm but not active. | **CRITICAL** |
| OpenAI integration | `app_06_clinical_chat/chat_engine.py:743` injects patient names, vitals, and labs into prompts sent to OpenAI. **Cross-border PHI transfer to a US data controller, no DPA, no adequacy decision** → GDPR Art. 44 violation. | **CRITICAL** |
| Consent / opt-out | None. Zero matches in the codebase for `consent`, `opt_out`, `withdraw`. GDPR Arts. 7 + 21 require both. | **CRITICAL** |
| PHI in logs | `logger.info(...patient %s, hadm=%s)` patterns across services. Plaintext patient identifiers in Loki — violates GDPR Art. 5(1)(e) data-minimisation. | HIGH |
| GDPR service (`app_17`) | Dashboard tile + scaffolded DPIA, but no real consent management, no cross-service erasure verification, no DPO sign-off workflow. | HIGH |
| EU AI Act | Model cards (Art. 13) ✓, override logging (Art. 14) ✓ but optional. **No FRIA (Art. 27), no post-market monitoring (Art. 72), no CE-marking workflow, no Notified Body conformity assessment.** | HIGH |
| Audit immutability | Mongo collections, no hash-chaining, no append-only ledger. Adversary with Mongo write can re-order history. EU AI Act expects 6+ year tamper-evident retention. | HIGH |
| FHIR Gateway | 11 resource types, read-mostly. **No SMART-on-FHIR, no OAuth2, no IHI / PIX / PDQ resolution.** Cannot integrate with Cerner / Epic / iPMS as deployed. | HIGH |
| Clinical Safety Case | None. No DCB-0129 / DCB-0160 artefacts, no Hazard Log, no Clinical Safety Officer sign-off, no ISO 14971 risk-management file. HIQA registration impossible. | **CRITICAL** |
| Disaster recovery | No documented backup strategy. `.mongo-data/` unversioned, no off-site replication, no tested restore. | MEDIUM |

---

## 3. Operational / Infrastructure — **AMBER (with RED for scale)**

| Topic | Status | Notes |
|---|---|---|
| Concurrency | Single uvicorn worker per service. RTX 4060 laptop, 30 GB RAM, 16 cores. At ~100 concurrent patients today the asyncio loop hung (fixed this session by wrapping `fetch_full_journey` in `asyncio.to_thread`). | **RED** for 500-bed scale |
| State persistence | `hospital_ops`, `data_ingestion`, `ed_flow`, `bed_management` hold in-memory dicts. Snapshot to Mongo every 30 s → 30-s RPO. Restart loses recent state. | AMBER |
| HA / failover | None. No leader election. `data_ingestion` dies → entire sim halts. 10–30 min RTO. | **RED** (target < 5 min) |
| EHR integration | Broker abstraction is clean (Mongo + Kafka dual-broker pattern). **No actual EHR adapter** — ingest is from MIMIC CSV files. ~2–4 weeks of work per EHR vendor. | AMBER |
| Observability | Loki, OTel, Prometheus, Grafana wired. **No SLO rules, no AlertManager, no paging.** | AMBER |
| Test coverage | 2,885 test files, real unit + integration + E2E. **No load tests, no chaos tests, no failover tests.** | AMBER |
| Deploy story | Helm charts + Linkerd manifests present in `deploy/`. **No CI/CD, no container registry, no secrets management (SealedSecrets), no Terraform.** Currently `docker compose up`. | AMBER |
| Latent bugs | Today alone surfaced: asyncio hang from sync Mongo on loop, ed_flow snapshot 16 MiB blowup, MADDPG cutting −5 nurses in BLACK alerts, Vite WS 15 s spikes, `/stats-dashboard` datetime TypeError storm. The pattern repeats — likely 5–10 more under sustained load. | **RED** |

---

## 4. Realistic trial pathway

```
PHASE 0  (NOW — 3 mo)   Internal demo / research
         │
         ▼
PHASE 1  (3 – 9 mo)     Shadow mode pilot
                        - Read-only ingest from one real EHR feed
                        - All AI outputs logged, none surfaced to clinicians
                        - Retrain models on local data, measure drift
                        - First HIQA pre-engagement
         │
         ▼
PHASE 2  (9 – 18 mo)    Single-ward advisory pilot
                        - One service (e.g. NEWS2 deterioration only)
                        - Recommendations shown to nurses; never auto-act
                        - Clinical Safety Case + DCB-0129/0160 ready
                        - Consent system live; auto-ack disabled in prod
                        - SMART-on-FHIR auth to hospital IdP
                        - 24/7 SRE on-call
         │
         ▼
PHASE 3  (18 – 36 mo)   Multi-ward + cross-hospital
                        - CE marking under EU AI Act Class IIa
                        - HRB grant-funded RCT for one model
                        - Replicated infra, HA, DR drills
                        - NTPF / Healthlink / NIMIS integrations live
```

**Phase 1 (shadow) is realistic in 6 months with focus.**
**Phase 2 (advisory pilot) is the first deployment that touches patient
care — minimum 12 months from today.**

---

## 5. What must change before any patient touches it

Ordered by blocking severity:

1. **Re-train every model on Irish data.** HRB grant + HSE data-access
   committee + cohort assembly = roughly 9–12 months end-to-end. The
   current US-trained checkpoints become warm-starts at best.
2. **Add API auth to every service** — SMART-on-FHIR / OAuth2 /
   role-based. ~2 weeks engineering.
3. **TLS everywhere + mTLS between services.** Linkerd is already in
   Helm; just turn it on. ~1 week.
4. **Disable the OpenAI integration entirely, or sign a healthcare DPA
   with Azure OpenAI EU region.** 1 day to disable, ~3 months to procure
   a compliant DPA.
5. **Build real consent + opt-out + erasure pipelines** that actually
   verify cross-service deletion. ~3–4 weeks.
6. **Implement the Sepsis-6 bundle tracker** and replace the SpO₂-only
   SOFA with proper Sepsis-3 SOFA. ~2 weeks.
7. **Replace the "ESI → weighted-random MTS" mapping with a native MTS
   classifier** trained on Irish ED data. ~3–6 months.
8. **Add paediatric / obstetric models** (PEWS / MEOWS / paeds sepsis) —
   currently stubs. ~2–3 months.
9. **Stand up a Clinical Safety Case** with a named Clinical Safety
   Officer. DCB-0129 (manufacturer) + DCB-0160 (deployer) artefacts.
   ~2 months once a CSO is engaged.
10. **CI/CD + secrets management + EHR adapter + load / chaos tests.**
    ~6–8 weeks.
11. **Remove the `DETERIORATION_AUTO_ACK_IN_SIM=True` default** —
    auto-acking safety alerts is unacceptable even in dev modes that
    might leak to staging.

---

## 6. Cost / effort estimate

Rough Irish-market figures:

| Workstream | FTE × months | Cost (€) |
|---|---|---|
| ML retraining on Irish data + cohort access | 2 × 12 | 400 K |
| Clinical validation (shadow study + RCT for one model) | external | 500 K – 1.5 M |
| Backend hardening (auth, TLS, consent, audit, SR) | 3 × 6 | 350 K |
| FHIR / SMART / IHI integration with HSE | 2 × 6 | 250 K |
| Clinical safety + regulatory (CSO, DPO, ISO 13485, CE marking) | external | 200 K – 400 K |
| Production infra (k8s, HA Mongo, observability) | 1 × 12 + ops | 300 K |
| Pilot site readiness + change management + training | shared with site | 200 K |
| **Total to Phase 2 pilot** | | **€2.2 – 3.5 M, 12 – 18 months** |

---

## 7. Honest summary

**As a research / demonstration / RFP-response platform — outstanding.**
The Digital Twin Orchestrator, a simulation engine driving a coherent
virtual hospital, the MARL staffing experiment, the multi-service
architecture, the dashboard, the FHIR / GDPR / XAI tile coverage — this
is a more complete envisioning of an HSE AI platform than most
commercial pitches.

**As a system that can touch a real Irish patient — no, and it would be
unsafe to claim otherwise.** The clinical-validity gap (US training data
driving Irish patient decisions), the security posture (zero auth,
plaintext, PHI to OpenAI), and the missing safety case mean a deploy
today would breach GDPR, the EU AI Act, DCB-0129, and HIQA's
clinical-information-system standards on day one.

**Recommended next move**: if real-hospital deployment is the strategic
goal, treat what's built as a *reference architecture*, then start
**Phase 0 (shadow mode at one site)** with explicit ring-fencing — no
AI output ever shown to a clinician, only logged for retrospective
analysis and model retraining. That alone unlocks the data access and
the regulatory pre-engagement needed for everything that follows.

---

## Appendix A — Today's stability fixes

A subset of the issues this audit was conducted on top of were actively
patched during the assessment session (commits in the same push as this
document):

- `data_ingestion` asyncio loop hang — root cause was
  `patient_generator.fetch_full_journey` issuing 6 synchronous PyMongo
  cursor reads directly on the event loop. Wrapped in
  `asyncio.to_thread`. Validation: 50/50 `/state` probes ≤ 1 ms after
  fix (was 19/20 timing out at 5–8 s).
- `/stats-dashboard` `TypeError: can't subtract offset-naive and
  offset-aware datetimes` causing ASGI exception storms — coerced both
  datetimes to naive before subtracting.
- `ed_flow` snapshot bloat triggering Mongo 16 MiB BSON limit errors
  every 30 s — capped the `events` list at 50 per patient in memory and
  at 30 per patient at snapshot time; added a 14 MiB pre-flight check in
  `shared/integration/persistent_state.py`.
- MADDPG capacity-alert handler unclamping the trained policy's negative
  staffing deltas (the policy wanted "−3 doctors, −5 nurses" for BLACK
  alerts) — same `max(0, delta)` clamp + rule-bump fallback as the
  global sweep.
- Vital-propagation throttle: per-patient 30 s rate limit on
  `process_vital`, plus bounded fire-and-forget propagation
  (`asyncio.Semaphore(8)`, 200-task hard cap) to keep the asyncio loop
  available for HTTP request handling.

These are the kind of fragilities a Phase 1 shadow pilot will surface in
volume; budget accordingly.
