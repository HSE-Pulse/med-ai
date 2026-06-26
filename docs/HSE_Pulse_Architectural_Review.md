# MedAI Platform (HSE Pulse) — Architectural Gap Analysis & Implementation Roadmap

**Reviewer model:** an internal AI architectural review
**Scope:** v3.0 system design (18 backend microservices, shared integration layer, React 19 dashboard, MongoDB persistence, MIMIC-IV simulation pool)
**Review lens:** Irish HSE Model 4 hospital deployment readiness
**Date:** 23 April 2026

---

## 0. Executive Summary

The platform is architecturally coherent and far more mature than typical academic prototypes — shared constants for Irish department mappings, MARL clamping against ERP baselines, circuit breakers, GDPR/EU AI Act scaffolding, and a Digital Twin orchestrator are all present. The research foundation (DES-MARL with 92.9% wait-time reduction, AUROC 0.994 sepsis, 0.897 mortality) is strong.

However, **seven structural gaps** stand between the current v3.0 design and a system HIQA would be willing to register, the HSE would procure, or a clinical safety officer would sign off on:

1. **Data origin mismatch.** Every model is trained on MIMIC-IV (Beth Israel Deaconess, Boston). Drug formularies, ICD-10-CM vs ICD-10-AM/IRDG coding, triage behaviour, and care pathway structures differ materially from Irish hospitals. Without an Irish-data transfer-learning pathway, models will suffer distributional shift on day one.
2. **EventBus is in-process.** The design explicitly notes this. Cross-service communication therefore falls back to synchronous HTTP with circuit breakers — but there is no durable message broker, no event replay, no at-least-once delivery. A single service restart silently drops the in-flight event graph for ~8 of the 18 services.
3. **State persistence is fragile.** Bed allocations, ED Flow patient state, DES state, active NEWS2 alerts, discharge plans, and metrics history are all in-memory. A pod restart loses operational truth until Digital Twin events rehydrate it.
4. **FHIR Gateway is a stub.** Four resource types (Patient, Encounter, Observation, CapabilityStatement) is insufficient for any real HSE integration. HSE's national messaging (Healthlink, MN-CMS, NIMIS) needs at minimum Condition, MedicationRequest, DiagnosticReport, DocumentReference, ServiceRequest, AllergyIntolerance, Immunization — plus SMART-on-FHIR auth and Subscription support.
5. **EU AI Act operational gaps.** The `AIActInfo` dataclass is metadata. Missing: a post-market monitoring pipeline (Art. 72), conformity assessment artefacts (Art. 43), CE-marking workflow, EU database registration, and — critically for HSE as a public body — a Fundamental Rights Impact Assessment (FRIA, Art. 27).
6. **No clinical safety case.** There is no Hazard Log, no Clinical Safety Officer sign-off workflow, no DCB-0129/0160-equivalent artefacts. HIQA's National Standards for Safer Better Healthcare expect explicit clinical risk evidence; the platform currently has none.
7. **Missing modules blocking clinical value.** Pharmacy / medication reconciliation, e-prescribing (HPRA), lab/radiology ordering, NIMIS integration, Healthlink GP messaging, NAS transport, NTPF waiting-list feed, Individual Health Identifier (IHI) service, paediatric PEWS, and obstetric MEOWS are absent. Any one of these is a blocker for a Model 4 site.

The remainder of this document walks through every module, identifies per-module gaps with Irish-context impact, maps cross-module data-flow seamlessness, prioritises issues P0/P1/P2/P3, and proposes a 12-month phased implementation plan.

---

## 1. Module-by-Module Review

For each service: **current state → gaps → Irish impact → specific improvements**. I've kept entries dense; each is intended as a backlog source.

### 1.1 ED Triage (app_01, :8201)

**Current state.** XGBoost + PyTorch NN for ESI 1–5 prediction with 50+ features and missingness indicators. AUROC 0.728 (XGB), Wt-F1 0.577 (NN). Rule-based fallback.

**Gaps.**
- Trained on ESI but Irish EDs use **Manchester Triage Scale (MTS)** operationally; MTS is only applied downstream in ED Flow (:8214) as a weighted random mapping — not a trained model.
- No concordance metric between predicted ESI and MTS applied by triage nurse (the "clinician override" loop is untracked).
- No paediatric sub-model — Irish EDs see significant paediatric volume and triage differs.
- No pregnancy-aware routing (obstetric early-warning differs).
- No `limitations` field exposing known failure modes (e.g., low-frequency presentations, non-English-speaking patients).
- Feature importance exposed via XAI (:8218) but not embedded in the prediction response for clinician transparency (EU AI Act Art. 13).

**Irish impact.** Ireland runs MTS in ~all public EDs; an ESI-only model is immediately challenged by triage nurses and will be overridden off the floor. Paediatric under-6 presentations and pregnancy triage misses are clinical-harm vectors HIQA would flag.

**Improvements.**
- Add a **native MTS classifier** trained on the same feature set with an MTS-aligned loss; expose both ESI and MTS with agreement score.
- Introduce a **paediatric sub-model** (features: age-adjusted vitals, Paediatric Assessment Triangle inputs).
- Return `prediction_envelope`: `{confidence_interval, applicability_flags, override_this_if}`.
- Log every clinician override to a dedicated `override_log` collection; feed into drift monitoring.
- Expose top-5 SHAP features inline (not just via /explain).

### 1.2 Sepsis ICU (app_02, :8202)

**Current state.** LightGBM + LSTM-Attention. AUROC 0.998 (LSTM). 4-hour window, SOFA components, RED/ORANGE/YELLOW/GREEN.

**Gaps.**
- AUROC 0.998 is extraordinary — likely overfit or data-leakage. Needs external validation on a non-MIMIC cohort before any HSE conversation.
- No linkage to **Irish National Sepsis Programme** bundle (Sepsis Six pathway); current output is a risk score, not a bundle-compliance checklist.
- No explicit **qSOFA / SIRS** output — Irish wards and MAUs often run qSOFA first.
- Alert escalation uses the 5-minute NEWS2 debouncer but sepsis alerts have different urgency semantics.
- No paediatric sepsis (Ireland's Sepsis 2.0 includes paediatric screening).

**Irish impact.** The National Clinical Effectiveness Committee (NCEC) mandates sepsis screening protocols; a system that produces a risk score but doesn't trigger the Sepsis Six bundle checklist is providing less value than HSE's existing paper forms. Also, AUROC 0.998 will not survive scrutiny by the HSE Acute Hospitals Division.

**Improvements.**
- External validation on MIMIC-III, eICU-CRD, or — better — an anonymised Irish dataset (e.g., via an HRB-funded collaboration) before publishing metrics in marketing.
- Replace or augment the score with a **Sepsis Six bundle tracker**: fluids, cultures, lactate, antibiotics, urine output, oxygen — each with time-to-compliance.
- Add qSOFA and SIRS parallel outputs for transparency.
- Separate sepsis escalation path (not NEWS2 debouncer) with its own cooldown and acknowledgement workflow.
- Add a paediatric module (Sepsis Trust UK / NICE NG51-aligned).

### 1.3 Hospital Ops (app_03, :8203)

**Current state.** DES + MADDPG staffing optimisation, 12-dim observation, clamped ±50% ERP baseline, action log.

**Gaps.**
- MADDPG policy is trained in simulation; no evidence of **sim-to-real transfer validation**. Real hospital dynamics have staff leave, sickness, locum constraints, EWTD caps — likely not in the simulator fidelity.
- The 12-dim observation is documented generically; what's actually in it? If `occupancy, wait, staffing ratio, ...` is the full list, this is thin for a Model 4 hospital with 14 departments.
- No **EWTD enforcement** inside the MARL action space — in principle the agent could recommend a staffing configuration that breaches 48h/week.
- No **locum/agency cost** in reward — MARL optimises operational metrics but cost-blind recommendations are unusable for HSE procurement.
- No uncertainty quantification on MARL actions; clinicians need "MARL suggests +2 nurses to ICU with 62% confidence vs rule-based: +2 nurses with 95% confidence."

**Irish impact.** HSE staffing is rigidly constrained by EWTD, nurse:patient ratios mandated by the Safe Staffing Framework (Department of Health, 2018), and consultant contracts (Sláintecare consultant contract 2023). A MARL agent blind to these rules will produce unrealisable recommendations and lose clinician trust permanently.

**Improvements.**
- Introduce **action masking** for EWTD, safe-staffing floors, and consultant contract constraints.
- Add **cost-aware reward**: agency vs permanent rate differentials, overtime premium.
- Publish **counterfactual evaluation** of MARL policy vs rule-based on held-out sim episodes (already your DES-MARL paper work — formalise it here).
- Add **uncertainty output**: bootstrap ensemble of MADDPG agents, report std of action distribution.
- Separate **hourly** (short-horizon) from **shift-planning** (12-24h horizon) policies; current setup conflates them.

### 1.4 Oncology AI (app_04, :8204)

**Current state.** XGBoost + Transformer for 30-day readmission (AUROC 0.734) and mortality (AUROC 0.897). 16 features including Charlson comorbidity.

**Gaps.**
- Cancer pathways in Ireland are governed by **National Cancer Control Programme (NCCP)** — pathway logic should align.
- No integration with **Rapid Access Clinics** (breast, lung, prostate) or **symptomatic clinic** referral routing.
- No stage-specific modelling; "stage proxy" is brittle.
- Readmission at 30 days is US-centric; Ireland tracks also 28-day emergency readmission as HSE KPI.
- No integration with **NCRI** (National Cancer Registry Ireland) for population comparison.
- Oncology endocrine/immunotherapy adverse events not modelled.

**Irish impact.** NCCP Rapid Access Clinic routing is where Irish cancer outcomes are won or lost. A risk model that doesn't feed the RAC triage workflow is a research artefact, not a clinical tool.

**Improvements.**
- Add a **pathway-aware** component: given a cancer ICD + stage proxy, output the NCCP-recommended next step (MDT date, imaging, biopsy, systemic therapy).
- 28-day emergency readmission flag alongside 30-day.
- Immunotherapy toxicity screen (irAE risk) using medication + vitals signature.
- Link to NCRI incidence rates for population-calibrated risk.

### 1.5 Patient Journey (app_05, :8205)

**Current state.** Timeline/vitals/labs/meds/cohort view. Per-patient endpoints.

**Gaps.**
- No **longitudinal view across admissions** — if the same patient has 3 admissions across a year, can the clinician see trends?
- Cohort comparison uses MIMIC population as reference; Irish clinical review needs Irish peer-group comparison.
- No annotation/handover note feature.
- No **patient-facing view** (GDPR Art. 15 SAR is wired, but there's no patient portal consuming the data).

**Irish impact.** Sláintecare's integrated care agenda depends on cross-encounter continuity. Siloed-by-admission views reinforce the existing fragmentation problem that Sláintecare is trying to solve.

**Improvements.**
- Add `/patient/{id}/lifetime-journey` joining admissions, GP-referral metadata, community contacts.
- Handover annotations with structured SBAR template.
- Patient portal stub (read-only, consent-gated, HSE IHI-keyed) — even as a placeholder, it positions the platform for Sláintecare procurement.

### 1.6 Clinical Chat (app_06, :8206)

**Current state.** LLM assistant via Ollama / OpenAI. `/chat, /models, /context`.

**Gaps.**
- Which models? If OpenAI, **cross-border data transfer** to US — requires SCCs, TIAs, and DPIA supplement. If Ollama with Meditron-7B (your existing work) — local, fine. The design doesn't declare this; it must.
- No **guardrails layer**: hallucination detection, dose-range validation, drug-drug interaction check.
- No **audit trail** of what the LLM said — critical for clinical defensibility.
- No **citation enforcement** — LLM answers must be grounded in retrieved clinical evidence (RAG pattern) with source links.
- No **Irish-specific knowledge grounding**: HSE protocols, NCEC NCGs, national formulary.

**Irish impact.** An LLM without HSE NCG/NCEC grounding will confidently state guidance inconsistent with Irish practice. GDPR-wise, routing health-data-containing prompts to OpenAI is a major compliance finding.

**Improvements.**
- Mandatory **on-prem LLM** (Meditron-7B via Ollama is correct; document this).
- RAG layer over HSE NCGs, NCEC NCGs, and BNF-equivalent formulary.
- Guardrails: `llm-guard` or custom — dose validation, DDI, red-flag triage symptoms force escalation.
- Every response persisted with prompt, retrieved context, tokens, latency, clinician feedback.

### 1.7 Data Ingestion (app_07, :8207)

**Current state.** ETL, CSV/Parquet upload, schema endpoint.

**Gaps.**
- No **schema versioning** — if MIMIC-IV 3.x arrives, or an Irish dataset joins, no migration path documented.
- No **data quality dashboard** — null rates, drift, distribution shifts.
- No **PHI scrubbing pipeline** at ingestion boundary; relies on MIMIC being pre-scrubbed.
- No **Delta Lake / medallion bronze-silver-gold** structure visible (you've built this on D2D — why not reuse?).

**Irish impact.** For any Irish deployment, a DPIA-grade ingestion pipeline is mandatory. HIQA will ask: "Where does data enter, who sees it, where is it encrypted, who has access logs."

**Improvements.**
- Port your D2D medallion foundation here as-is — bronze (raw), silver (cleaned + PHI-scrubbed), gold (feature store).
- Great Expectations or Soda for data quality.
- Presidio or Irish-tuned NER for PHI scrubbing at ingestion, not at egress.
- Schema registry (Confluent Schema Registry or similar).

### 1.8 Bed Management (app_08, :8208)

**Current state.** Real-time bed tracking, multi-objective allocation, discharge prediction (XGBoost + LOS regressor), capacity forecast.

**Gaps.**
- State is **in-memory**. A restart loses every allocation until Digital Twin events rehydrate — and some events (e.g., beds occupied pre-start) may never arrive.
- Forecast is **rule-based**; described as "exponential convergence to 80%" — this is a placeholder, not a forecast.
- No **surge protocol**: when NEDOCS > 180, what's the automated response?
- "Substring match for SIM-prefixed IDs" is brittle — risk of cross-patient bed assignment under ID collision.
- No **ring-fenced bed categories** (e.g., isolation beds, bariatric, paediatric, stroke-thrombolysis).

**Irish impact.** Ireland's bed crisis is *the* operational problem HSE cares about. A bed management system that loses state on restart is a non-starter. Missing isolation-bed tracking is an infection-control liability (post-COVID, HIQA scrutinises this).

**Improvements.**
- Persist bed allocations to MongoDB with CDC (change data capture).
- Replace rule-based forecast with an **ARIMA / Prophet / LightGBM forecaster** using historical arrival patterns; expose confidence intervals.
- Bed taxonomy: `{isolation, bariatric, paediatric, acute_stroke, cardiac_monitored, observation, inpatient, dayward}` as attributes not just "capacity".
- Replace substring ID match with strict UUID-based identifiers; use SIM-hash only as display prefix.

### 1.9 Waiting List (app_09, :8209)

**Current state.** Priority scoring, NLP triage, scheduling.

**Gaps.**
- Not integrated with **NTPF** (National Treatment Purchase Fund) — the most important Irish waiting-list mechanism.
- No **chronological validation** per SDU Clinical Validation.
- No **clinical urgency + chronological** dual queue (current Irish practice).
- No **P1/P2/P3 prioritisation** per National Inpatient and Daycase Waiting List.

**Irish impact.** In Ireland, waiting-list management without NTPF awareness is genuinely not useful. Scheduled-Care Transformation Programme alignment is the entry criterion.

**Improvements.**
- NTPF data feed (at minimum, CSV import of outsourcing pipeline).
- P1/P2/P3 surgical priority codes.
- Chronological validation workflow (SDU).
- Clinical validation + failure-to-attend analytics.

### 1.10 Clinical Scribe (app_10, :8210)

**Current state.** Auto-transcription, NER, ICD coding.

**Gaps.**
- Which ICD version? Irish HIPE coding uses **ICD-10-AM (Australian Modification)** + **ACHI** + **ACS**. If the service produces ICD-10-CM (US), HIPE coders will manually re-code, destroying the time-saving.
- No **SNOMED-CT** output (HSE is on SNOMED journey).
- Transcription language model not specified — English-only? Irish-English accent coverage? Multilingual patients?
- No **clinician sign-off workflow** (legally required — auto-generated notes are a draft only).

**Irish impact.** HIPE coding is how Irish hospital activity is paid for. Generating the wrong ICD variant is not a bug, it's a revenue-cycle failure.

**Improvements.**
- Dual output: ICD-10-CM (for research/MIMIC compatibility) + **ICD-10-AM** (for HIPE), mapped via crosswalk.
- SNOMED-CT concept extraction alongside ICD.
- Speech model: Whisper-large-v3 fine-tuned on Irish-accented medical speech (or at minimum, documented accuracy metrics on Irish English).
- Mandatory clinician sign-off before any note leaves draft state; audit trail of edits.

### 1.11 ED Flow (app_14, :8214)

**Current state.** PET compliance, NEDOCS, bottleneck detection, MTS classification.

**Gaps.**
- PET tracking is per-patient — good. But no **PET escalation** when a patient approaches breach (e.g., at 5h30min, alert ED consultant).
- NEDOCS alone is narrow; **ANTECIP**, **EDWIN**, **READI** are alternative crowding metrics — Irish hospitals often look at multiple.
- No **LWBS** (left without being seen) forecasting, only detection.
- Bottleneck detection approach not specified — rule-based? ML?

**Irish impact.** PET 6-hour compliance is a ministerial reporting metric; approaching-breach alerts are operationally the most valuable signal you could surface. Current implementation tracks but doesn't act.

**Improvements.**
- 5h warning (green), 5h30min amber, 5h45min red — escalate to ED consultant, then site manager.
- Multi-metric crowding dashboard: NEDOCS + EDWIN + ANTECIP + local Irish "trolleys waiting > 9h" INMO metric.
- LWBS predictive model (wait time + acuity + time-of-day).

### 1.12 Hospital ERP (app_15, :8215)

**Current state.** Master data in static Python dicts — departments, staff, schedule, beds, config.

**Gaps.**
- **Static Python dicts** means changes require code redeploy. Not usable for operational master-data management.
- No **audit trail** on master-data changes — HIQA requires traceability.
- No **approval workflow** — who signs off staff roster changes?
- No **integration** with SAP HR / HSE payroll / SAP Ariba procurement which HSE actually runs.

**Irish impact.** ERP data is the single source of truth; if it's unversioned static code, every downstream calculation (staffing ratios, MARL clamping bounds) is un-auditable.

**Improvements.**
- Move master data to MongoDB with **CDC + audit log**.
- Role-based edit workflow (Operations Manager → Director of Nursing → CFO sign-off).
- Adapter stubs for SAP (at minimum, CSV import scheduled jobs).
- Versioned config with rollback.

### 1.13 Trolley Watch (app_16, :8216)

**Current state.** IoT bed/trolley movement tracking, INMO-compatible metrics.

**Gaps.**
- "IoT" is aspirational unless specified. Which hardware? RFID? BLE? UWB?
- `TrolleyMetrics` class is good start — but does it produce the **daily INMO 08:00 Trolley Count** in exact INMO format?
- No integration with **HSE TrolleyGAR** (if it exists in the target hospital).
- No **escalation protocol** when trolley count > threshold.

**Irish impact.** INMO's daily trolley count is the single most politically sensitive healthcare metric in Ireland. A system that produces slightly-different numbers will destroy trust overnight.

**Improvements.**
- Match INMO counting methodology **exactly** (08:00 snapshot, defined inclusions/exclusions).
- Publish daily count to an internal endpoint with reconciliation report against actual INMO daily reports.
- Escalation: trolley count > hospital threshold → site manager + HSE Performance Management.

### 1.14 GDPR Compliance (app_17, :8217)

**Current state.** SAR, erasure, ROPA, breach, DPIA, audit log. Privacy notice on every service.

**Gaps.**
- **Erasure and ML models**: if a patient requests erasure (Art. 17), what happens to models that were trained on their data? No mention of model retraining pipeline or legitimate-interests assessment for retained model weights.
- **DPIA per module** — but no **cross-module DPIA** showing cumulative risk.
- No **Consent Management** service — implied consent under Art. 9(2)(h) is declared, but no mechanism for explicit consent where required (e.g., research, AI training).
- No **DPO dashboard/console**.
- No **DPC breach reporting automation** (72-hour clock).
- No **data retention policy** enforcement.
- Cross-border data transfer not documented (Azure EU North is Ireland — fine; but if any service calls US-hosted API, SCCs needed).

**Irish impact.** Data Protection Commission (DPC) is the most active EU DPA. A hospital deploying this without cross-module DPIA will fail the first DPC audit.

**Improvements.**
- Model-erasure policy: (a) flag patient data as excluded from future training, (b) document legitimate-interests-based retention of historical weights, (c) scheduled model retraining cadence.
- Unified cross-module DPIA generated by joining per-module DPIAs.
- Consent Management Service as 19th microservice (see §3 Missing Modules).
- DPO console with dashboards for SARs, breaches, erasure requests, DPIAs, ROPA.
- DPC breach reporting automated via structured `/gdpr/breach/submit-to-dpc` with 72h countdown.
- Retention enforcement job (TTL indices on MongoDB).

### 1.15 XAI (app_18, :8218)

**Current state.** SHAP / LIME, feature importance, decision tree.

**Gaps.**
- Explanations are **global + local** but no **counterfactual** explanations ("If HR was 90 instead of 130, risk would drop to 0.3").
- No **clinician-readable narrative** — SHAP values are not lay-readable.
- No **explanation consistency check** (two similar patients should receive similar explanations).
- Not wired into FHIR as `Observation.interpretation` or `RiskAssessment.reason`.

**Irish impact.** EU AI Act Art. 13 requires explanations **appropriate for the deployer** — a clinician, not a data scientist. SHAP plots don't meet this bar.

**Improvements.**
- Counterfactual explanations via DiCE or similar.
- Narrative generation via LLM: "High risk driven by: elevated lactate (↑3× normal), tachycardia (HR 130), falling SpO2. If SpO2 stabilised above 94%, risk would drop by ~40%."
- Consistency monitoring (Lipschitz-style).
- FHIR `RiskAssessment.prediction.rationale` mapping.

### 1.16 FHIR Gateway (app_19, :8219)

**Current state.** 4 resource types (Patient, Encounter, Observation, CapabilityStatement). R4.

**Gaps (large).**
- **Missing resources**: Condition, Procedure, MedicationRequest, MedicationAdministration, AllergyIntolerance, DiagnosticReport, ServiceRequest, DocumentReference, Immunization, CarePlan, CareTeam, RiskAssessment, Subscription.
- No **SMART-on-FHIR** auth.
- No **IHE profiles** (PIX, PDQ, XDS, ATNA).
- No **Subscription** (webhook-style notifications).
- No **HSE IHI (Individual Health Identifier)** handling — Health Identifiers Act 2014 makes this mandatory for any cross-provider exchange.
- No **HL7 v2** bridge (legacy Irish systems still run v2).
- No **Healthlink** integration (Irish GP messaging — runs on HL7 v2).
- No **NIMIS** integration (radiology).

**Irish impact.** The FHIR Gateway is the single most important integration point for HSE. Its current scope makes it a demo, not a gateway. Without IHI handling, it is non-compliant with the Health Identifiers Act.

**Improvements.** (this is a dedicated project)
- Resource expansion: minimum viable HSE set is Patient + Encounter + Observation + Condition + MedicationRequest + DiagnosticReport + DocumentReference + AllergyIntolerance.
- SMART-on-FHIR app launch.
- IHE profile support starting with PIX/PDQ for IHI resolution.
- HL7 v2 adapter (Mirth Connect or NextGen Connect Integration Engine).
- Healthlink adapter.
- IHI handling service integrated with Consent Management.

### 1.17 Deterioration Monitor (app_20, :8220)

**Current state.** NEWS2 + iNEWS escalation. 4 risk bands. 5-minute debouncer.

**Gaps.**
- **Adult only.** Ireland uses **PEWS** (paediatric) and **IMEWS** (Irish Maternity Early Warning Score) — both are NCEC NCGs (No. 1 and No. 4). Missing both.
- No **trended NEWS2** — rising NEWS2 from 3→5 over 1 hour is more alarming than static NEWS2 of 5; current system doesn't differentiate.
- **Debouncer as alert-fatigue mitigation is primitive** — a 5-minute cooldown blocks a NEWS2 that went from 5 → 7 within 4 minutes. Should be score-change aware.
- No **escalation acknowledgement** loop — who received the alert, did they respond, time-to-review.
- No integration with **Critical Care Outreach Team (CCOT)**.

**Irish impact.** Missing IMEWS in an Irish hospital is a clinical-safety category issue — IMEWS is mandatory per NCEC NCG No. 4 for all pregnant and post-partum patients.

**Improvements.**
- Implement PEWS (per NCEC NCG No. 1) and IMEWS (per NCEC NCG No. 4) as peer modules.
- Trended NEWS2: track slope over trailing 4h; alert on slope > threshold.
- Smart debouncer: suppresses only if score stable or decreasing; fires immediately on increase.
- Escalation acknowledgement with SBAR capture.
- CCOT dispatch integration.

### 1.18 Discharge Lounge (app_21, :8221)

**Current state.** Discharge planning, follow-up, readmission prevention.

**Gaps.**
- No integration with **community services**: Public Health Nurses, Community Intervention Teams, GP (Healthlink).
- No **pharmacy reconciliation** — medication at discharge is the #1 harm vector.
- No **transport scheduling** (NAS or private).
- No **accessibility planning** (home modifications, OT referrals).

**Irish impact.** Sláintecare is explicitly about integrated care at discharge. A discharge module that stops at hospital walls undermines the programme's strategic intent.

**Improvements.**
- Medication reconciliation integrated with Pharmacy microservice (see §3).
- GP discharge summary via Healthlink (HL7 v2).
- Public Health Nurse referral via HSE community portal (stub in simulation).
- NAS transport booking adapter.

---

## 2. Cross-Module Data Flow Integrity

Walking the four documented flows and checking for seamlessness, failure-mode handling, and ordering guarantees.

### 2.1 Digital Twin admission pipeline (§4.2)

**Flow:** ED Triage → ED Flow → Bed Mgmt (predict + allocate + notify) → [Oncology if cancer] → Deterioration → Clinical Scribe.

**Seamless?** Partially.

**Issues identified.**
- **Sequential coupling.** Each step awaits the previous. If ED Triage (8201) takes 300ms and Bed Mgmt (8208) takes 500ms and Deterioration (8220) takes 200ms, *every* admission costs 1s+ of serial latency. At Irish ED arrival rates (~30/sim-day, but real hospitals peak at 10+/hour), this is acceptable; at scale, it's not.
- **No compensation on partial failure.** If Step 3 (Bed Mgmt allocate) succeeds but Step 4 (Oncology) fails, the bed is allocated but downstream state is inconsistent. No Saga pattern.
- **No idempotency keys.** Retries on circuit-breaker half-open can double-allocate beds.
- **No explicit ordering guarantee across patients.** Two simultaneous admissions could race on last-available ICU bed.
- **`map_department()` at backend boundary is good** but there's no check that *every* downstream consumer applies it — risk of MIMIC names leaking through an unmapped path (e.g., a new endpoint added without the mapping import).

**Recommendations.**
- Introduce a lightweight **Saga coordinator** in `shared/integration/`: each step declares a compensating action.
- **Idempotency key** (`patient_id + admission_id + step_name + timestamp-hour`) on every HTTP POST; receiving service deduplicates.
- Bed Mgmt: **optimistic locking** with retry on conflict (Mongo `findOneAndUpdate` with version field).
- CI check: **lint rule** that every service calling `/notify-*` must import `map_department`.

### 2.2 Bed Management ↔ Hospital Ops loop (§5.1)

**Flow:** Bed Mgmt polls → notify-census → Hospital Ops DES steps → staffing recommendations → returned to Bed Mgmt → used in allocation scoring.

**Seamless?** Fragile.

**Issues.**
- **Circular dependency.** Bed Mgmt's allocation depends on Hospital Ops staffing; Hospital Ops census depends on Bed Mgmt. Both services going down simultaneously = total operational blindness.
- **Polling.** Documented as polling at fixed intervals — latency between a bed freeing and MARL observing it is O(poll-interval).
- **No back-pressure.** If Hospital Ops DES is slow, Bed Mgmt keeps pushing notifications — queue builds in memory.

**Recommendations.**
- Break the cycle: publish `census_updated` events to a durable broker (see §3.2); both services subscribe.
- Replace polling with **event-driven**: Bed Mgmt publishes `bed_state_changed`; Hospital Ops consumes.
- **Back-pressure**: Hospital Ops exposes `/health/readiness` with queue depth; Bed Mgmt circuit-breaker opens on high depth.

### 2.3 Deterioration cascade (§5.3)

**Flow:** Vitals → Deterioration `/screen` → NEWS2 ≥ 7 → trigger ICU transfer via DT → Bed Mgmt pre-allocate → Clinical Scribe auto-note.

**Seamless?** Partially.

**Issues.**
- **ICU transfer via DT** — but DT is the orchestrator, not a transfer executor. Who actually performs the move? Is this advisory-only or does it change bed state?
- **Pre-allocate an ICU bed** — what if no ICU bed is available? Does the system escalate to transfer-out request?
- **Clinical Scribe auto-generates escalation note** — sign-off workflow?

**Recommendations.**
- Distinguish **advisory** escalation (recommendation to team) from **action** (bed state change) — current design conflates.
- ICU-full handling: trigger Surge Protocol, escalate to site manager, log as capacity event.
- Escalation note is draft → requires clinician acknowledgement before becoming part of record.

### 2.4 Simulation reset (§5.4)

**Flow:** 7-step reset cascade.

**Seamless?** Mostly — but:

**Issues.**
- **No transactional reset.** If step 5 (reset ED Flow) fails, the system is partially reset — some services have old state, some new.
- **Race condition** between "clear MIMIC_SIM collections" and "reinitialize patient pool" — if sim engine restarts mid-reset, chaos.

**Recommendations.**
- Two-phase reset: (a) pause all services, (b) clear state, (c) verify empty, (d) reinitialise, (e) resume.
- Reset idempotency: `/reset` with reset-id that downstream services deduplicate.

### 2.5 Cross-cutting data flow concerns

- **No distributed tracing.** When an admission fails at step 4 of 6, the only way to debug is cross-service log correlation via correlation IDs (present via structlog) — no visual trace, no latency breakdown. **Add OpenTelemetry + Jaeger.**
- **No schema contract testing.** Service A publishes `{patient_id, acuity}`; Service B consumes `{patient_id, acuity_level}`. Silent breakage. **Add Pact or similar contract tests.**
- **No event replay.** If a consumer is down for 1h, events during that window are lost. **Durable broker with retention.**

---

## 3. Structural Gaps (Shared Infra & Missing Modules)

### 3.1 Missing modules blocking clinical value

| Module | Why needed | HSE integration touchpoint |
|---|---|---|
| **Pharmacy / Medication Reconciliation** | #1 discharge harm vector | ePMA, HPRA MURS reporting |
| **Lab / Radiology Ordering** | Currently consume lab results, can't order | iSOFT, NIMIS |
| **NIMIS Integration** | National radiology | DICOM + HL7 v2 |
| **Healthlink Gateway** | GP messaging | HL7 v2 over Healthlink MPLS |
| **Individual Health Identifier (IHI) Service** | Legally mandatory | HSE IHI directory service |
| **Consent Management** | Article 9(2)(h) is necessity; explicit consent needed for research/AI training | DPC-aligned |
| **Paediatric Early Warning (PEWS)** | NCEC NCG No. 1 | Mandatory in all Irish paediatric admissions |
| **Obstetric Early Warning (IMEWS)** | NCEC NCG No. 4 | Mandatory for all pregnant/post-partum |
| **Infection Prevention & Control** | HIQA Standard 3 | Isolation bed tracking, AMR surveillance |
| **Adverse Event Reporting** | NIMS (National Incident Management System) | STARSWeb reporting |

### 3.2 Shared integration layer gaps

**Current:** `digital_twin.py, event_bus.py (in-process), circuit_breaker.py, service_client.py, sim_clock.py, debouncer.py, conversation_buffer.py`.

**Missing:**

1. **Durable message broker adapter** — Kafka (or NATS, or RabbitMQ). The in-process EventBus is a critical limiter. Introduce `shared/integration/broker.py` with publish/subscribe/replay.
2. **Saga coordinator** — for multi-step workflows with compensation.
3. **Idempotency middleware** — FastAPI dependency that records+deduplicates request IDs.
4. **Schema registry client** — Confluent Schema Registry or JSON Schema store.
5. **Distributed tracing** — OpenTelemetry SDK integration at service factory (`shared/api/base.py`).
6. **Feature flag service** — LaunchDarkly-style local implementation; MARL on/off, new models, deployment modes.
7. **Secrets manager adapter** — Azure Key Vault, HashiCorp Vault.
8. **Metrics aggregator** — Prometheus exporter in every service (currently no mention).

### 3.3 MLOps gaps

1. **Drift detection** — no monitoring of feature distributions vs training distribution.
2. **Model versioning in production** — `ModelRegistry` exists (joblib + JSON metadata) but no A/B, no shadow mode, no canary deployment.
3. **Feedback loop** — clinician overrides aren't fed back to retraining.
4. **Calibration monitoring** — AUROC alone is insufficient for high-risk AI Act systems; need Brier score, ECE, reliability diagrams.
5. **Bias monitoring** — no subgroup analysis by age band, sex, deprivation index (Pobal HP Index for Ireland).

### 3.4 Observability & ops gaps

- No centralised logging (ELK/Loki).
- No Prometheus/Grafana.
- No PagerDuty-style alerting.
- No SLO/SLI definitions.
- No runbook per service.
- No chaos-engineering tests.
- No blue/green or canary deploy documented.

### 3.5 Security gaps

- **JWT rotation not specified.**
- **No secrets manager.**
- **No mTLS between services** (only external mTLS in prod mode).
- **No zero-trust posture** (e.g., SPIRE/SPIFFE for service identity).
- **No WAF on dashboard edge.**
- **No dependency scanning** (Snyk, Trivy).
- **No SBOM** — required for any HSE procurement.
- **No container image signing** (Cosign).

### 3.6 Compliance & clinical safety gaps

1. **Clinical Safety Case** — Hazard Log, Safety Requirements, Clinical Risk Management File. HIQA-aligned (DCB 0129/0160 equivalent).
2. **FRIA** (Fundamental Rights Impact Assessment, EU AI Act Art. 27) — mandatory for HSE as public authority deploying high-risk AI.
3. **Post-market monitoring** (Art. 72) — continuous performance + incident reporting pipeline.
4. **Conformity assessment** (Art. 43) — notified body engagement or internal conformity per annex VII.
5. **EU AI Act database registration** (Art. 49).
6. **CE marking** workflow.
7. **HIQA themes alignment** — Standards for Safer Better Healthcare (8 themes); current design touches Theme 2 (Safe Care) and Theme 3 (Effective Care) partially.
8. **ISO 27001 / 27799** — no mention.
9. **Medical Device Regulation (MDR)** applicability — any diagnostic/triage AI is likely a Class IIa device. No MDR pathway described.

---

## 4. Irish Healthcare Industry Impact Map

This section maps each major capability onto the Irish healthcare landscape — who cares, why they'd buy, what they need.

| Capability | Primary stakeholder | Why they'd fund | Block for adoption |
|---|---|---|---|
| ED Triage + Flow + PET tracking | HSE Acute Hospitals Division | PET compliance is ministerially reported | MTS alignment, paediatric/obstetric coverage, real-time escalation |
| Sepsis ICU | NCEC, Sepsis Programme lead | AUROC 0.998 = potential lives saved if real | External validation on non-MIMIC data; Sepsis Six bundle tracker |
| MARL staffing | HSE CFO, Chief Operations Officer | Staffing cost is ~70% of hospital budget | EWTD-aware action masking, locum cost in reward, explainability |
| Oncology pathways | NCCP | Rapid Access Clinic routing lives-saved | NCCP pathway alignment, NCRI integration |
| Bed Management | Site manager, COO | Trolley crisis reduction | State persistence, isolation bed taxonomy, surge protocols |
| FHIR Gateway | HSE eHealth Ireland | Sláintecare integrated care vision | Resource expansion, IHI handling, Healthlink adapter |
| Deterioration (NEWS2 + PEWS + IMEWS) | CNO (Chief Nursing Officer), NCEC | NCEC NCG #1 and #4 compliance | PEWS, IMEWS, CCOT integration |
| GDPR compliance | DPO, DPC | DPC audit readiness | Cross-module DPIA, DPO console, consent management |
| XAI | Clinicians, EU AI Act | Art. 13 + clinician trust | Narrative generation, counterfactuals |
| Clinical Scribe | HIPE coders, consultant time | Revenue cycle + admin time | ICD-10-AM dual output, SNOMED, clinician sign-off |
| Waiting List | SDU, NTPF | Scheduled-Care Transformation | NTPF feed, P1/P2/P3, chronological validation |
| Discharge Lounge | Community services, Sláintecare | Readmission reduction | Healthlink integration, pharmacy, transport |

### 4.1 Market positioning

For Enthiram.AI's D2D platform, HSE Pulse sits between three Irish markets:

1. **HSE direct procurement** — slow (12–24 months), requires HIQA registration, DPIA acceptance, clinical safety case, public tender. High ticket, low velocity.
2. **Voluntary hospitals** (St. James's, Mater, Beaumont, St. Vincent's) — faster (6–12 months), still need HIQA-aligned evidence. Each has a CIO with local authority.
3. **Private providers** (Bon Secours, Blackrock Health, Mater Private, UPMC) — fastest (3–6 months), less regulatory friction but different operational model (higher elective, lower ED).

**Recommended go-to-market sequencing:** voluntary hospital pilot → validated Irish dataset → HIQA registration → HSE tender. Private is a parallel revenue path but doesn't credentialise for HSE.

---

## 5. Prioritised Issue Register

Priorities: **P0 = safety / compliance blocker**, **P1 = adoption blocker**, **P2 = functional gap**, **P3 = nice-to-have**.

### P0 — Safety & compliance (blockers for any production deployment)

1. Clinical Safety Case absent — no Hazard Log, no Safety Officer sign-off.
2. FRIA absent (EU AI Act Art. 27, mandatory for HSE public body).
3. Cross-module DPIA absent.
4. Sepsis AUROC 0.998 unvalidated externally — risk of overfit claim surviving into clinical use.
5. Medication reconciliation / e-prescribing absent — #1 harm vector at discharge.
6. PEWS (paediatric) and IMEWS (obstetric) absent — NCEC NCGs #1 and #4 mandatory.
7. In-memory state loss on restart across 6+ modules.
8. MIMIC-IV distributional mismatch with Irish hospital data — no transfer-learning pathway.
9. EventBus in-process only — no durable event delivery, no replay.
10. Clinical Scribe ICD-10-CM vs HIPE ICD-10-AM mismatch.

### P1 — Adoption blockers

11. FHIR Gateway thin (4 resources) — expansion + SMART-on-FHIR + IHI.
12. MARL action space not EWTD-constrained.
13. MTS not trained natively in ED Triage (ESI only).
14. NTPF waiting-list integration absent.
15. Healthlink integration absent.
16. Hospital ERP master data in static Python — no audit.
17. No observability stack (Prometheus, tracing, centralised logs).
18. Trolley count methodology vs INMO not reconciled.
19. XAI explanations not clinician-readable.
20. No clinician override feedback loop into model retraining.

### P2 — Functional gaps

21. No saga coordinator / compensation.
22. No idempotency keys on HTTP POSTs.
23. No contract testing between services.
24. No drift detection / calibration monitoring / bias monitoring.
25. No paediatric sub-model in ED Triage.
26. Bed Mgmt forecast rule-based placeholder.
27. LWBS prediction absent.
28. Clinical Chat LLM provider not declared; no guardrails.
29. No consent management service.
30. No DPO console.

### P3 — Nice-to-have

31. Patient portal.
32. Irish-language (Gaeilge) dashboard variant.
33. Mobile/tablet role-specific views.
34. Offline mode for critical-care scenarios.
35. WCAG 2.1 AA accessibility audit.

---

## 6. Phased Implementation Plan

Organised in three phases × four workstreams (Foundation, Modules, Compliance, MLOps). Sequencing targets: **Phase 1 ends at "can run a clean voluntary-hospital pilot"**; **Phase 2 ends at "HIQA registration submission-ready"**; **Phase 3 ends at "HSE tender-ready with FRIA, CE marking, EU AI Act conformity"**.

### Phase 1 — Stabilise (Months 0–3)

**Foundation**
- Durable broker: Kafka or NATS adapter in `shared/integration/broker.py`. Migrate EventBus topics (15 listed) to broker with in-process fallback.
- State persistence: Bed Mgmt, ED Flow, Deterioration, Discharge Lounge allocations → MongoDB with CDC.
- OpenTelemetry + Jaeger. Prometheus exporter in every service via `create_app()` factory.
- Saga coordinator + idempotency middleware in `shared/integration/`.
- Secrets manager adapter (Azure Key Vault stub).

**Modules**
- ED Triage: native MTS classifier with agreement score vs ESI.
- Deterioration: PEWS + IMEWS sibling services (NCEC NCG compliance).
- Clinical Scribe: ICD-10-CM → ICD-10-AM crosswalk; mandatory clinician sign-off.
- Hospital ERP: move master data to MongoDB with audit.
- FHIR Gateway: add Condition, MedicationRequest, DiagnosticReport, AllergyIntolerance, DocumentReference.

**Compliance**
- Clinical Safety Case v1: Hazard Log template, initial hazards enumerated per module.
- Cross-module DPIA v1.
- Consent Management service (19th microservice).

**MLOps**
- External validation of Sepsis model on eICU-CRD or similar; publish honest AUROC.
- Drift monitoring with Evidently AI or Whylogs.
- Calibration curves on all prediction models.

**Exit criterion Phase 1.** The platform survives a full pod restart with zero state loss; every ML model has published external validation metrics; Clinical Safety Case is in review with a Clinical Safety Officer.

### Phase 2 — Clinicalise (Months 3–6)

**Foundation**
- Schema registry + contract tests (Pact) between every service pair that exchanges data.
- mTLS between all services (SPIRE).
- SBOM generation in CI.
- Blue/green deployment.

**Modules**
- Pharmacy / Medication Reconciliation service (new).
- Healthlink adapter (HL7 v2).
- IHI service.
- SMART-on-FHIR auth layer.
- MARL: action masking for EWTD and safe-staffing; cost-aware reward.
- Clinical Chat: on-prem LLM enforcement + RAG over NCEC NCGs + guardrails.
- XAI: counterfactual explanations + LLM-generated narrative.
- Waiting List: P1/P2/P3 + NTPF CSV import stub.

**Compliance**
- FRIA v1 (EU AI Act Art. 27).
- DPO console.
- Retention policy enforcement with TTL indices.
- DPC breach reporting automation.
- Post-market monitoring pipeline scaffold (Art. 72).

**MLOps**
- Feedback loop: clinician override collection → retraining pipeline with human-in-the-loop.
- Shadow deployment for new models.
- Bias monitoring with Pobal HP Index as proxy for deprivation.

**Exit criterion Phase 2.** A voluntary hospital can run a clinical pilot with Clinical Safety Officer sign-off. HIQA pre-submission engagement is possible.

### Phase 3 — Scale (Months 6–12)

**Foundation**
- Multi-hospital tenancy (tenant isolation per HSE Hospital Group).
- Disaster recovery: RPO 15 min, RTO 1 hour.
- Chaos engineering (Litmus or Chaos Mesh).
- Full observability SLO/SLI dashboards per service.

**Modules**
- NIMIS radiology adapter.
- NAS transport booking.
- Lab/Radiology ordering service.
- Infection Prevention & Control module.
- Adverse Event Reporting (NIMS / STARSWeb).
- Paediatric triage sub-models.
- Patient portal (read-only, SAR-aligned).

**Compliance**
- CE marking process initiated (Class IIa MDR).
- EU AI Act database registration.
- Conformity assessment — Annex VII internal or notified body.
- HIQA registration submission.
- ISO 27001 certification path.

**MLOps**
- A/B testing framework.
- Automated retraining cadence (quarterly).
- Model card publication per service (EU AI Act Art. 13).
- Population-calibrated models using NCRI / HIPE distributions where available.

**Exit criterion Phase 3.** The platform is procurable by HSE via formal tender with HIQA registration, EU AI Act conformity, CE marking, and an operational post-market monitoring pipeline.

---

## 7. Data-Flow Seamlessness — Concise Verdict

| Concern | Status | Blocker? |
|---|---|---|
| Admission pipeline end-to-end | Works, but sequential + no compensation | P1 |
| Vital event propagation | Works via DT; no durable broker | P0 |
| Transfer event state consistency | Fragile (substring ID match) | P0 |
| Discharge event cascade | Works; missing pharmacy + community handoff | P1 |
| Bed Mgmt ↔ Hospital Ops loop | Circular coupling, polling-based | P1 |
| Deterioration → ICU transfer | Advisory vs actioned unclear | P0 |
| Simulation reset | Not transactional | P2 |
| Cross-service schema contracts | None | P1 |
| Distributed tracing | None | P1 |
| Event replay capability | None (in-process bus) | P0 |
| MIMIC → Irish name mapping | Works at boundary; no CI lint | P2 |

**Overall:** Data flows are designed coherently and work under happy-path simulation. Under production conditions — restarts, partial failures, cross-service latency, concurrent admissions — multiple invariants break silently. The fixes are additive (broker, saga, idempotency, persistence, tracing), not architectural rewrites — the existing service decomposition is sound.

---

## 8. One-page Decision Brief

If forced to act on three things in the next 30 days to maximise both clinical safety and commercial credibility:

1. **Externally validate the Sepsis model** on eICU-CRD and publish honest metrics. AUROC 0.998 is the single biggest credibility risk in the current design; any clinician-reviewer will notice it, and recovering from a published "too good to be true" number is hard.
2. **Implement PEWS + IMEWS** alongside NEWS2. These are NCEC mandatory. Their absence is a conversation-ending finding in any Irish clinical review.
3. **Introduce durable-broker event delivery** and **persist Bed Mgmt / Deterioration state**. A system that loses operational truth on restart cannot be piloted in any real setting; this is the fastest single change that unlocks clinical testability.

Everything else in this review sequences after these three.

---

*End of review. This document is intended as a working backlog source; each P0/P1 item can be expanded into a design doc on request.*
