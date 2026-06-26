# AI Platform Strategy Report: Irish Hospital Setup
## Deep Research & Impact Analysis | April 2026

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Platform Module Analysis](#2-current-platform-module-analysis)
3. [Irish Healthcare Context & Urgent Needs](#3-irish-healthcare-context--urgent-needs)
4. [Competitive Landscape: AI Vendors in Irish Hospitals](#4-competitive-landscape-ai-vendors-in-irish-hospitals)
5. [Recommended New Modules (High Impact)](#5-recommended-new-modules-high-impact)
6. [Existing Module Refinements for Irish Setup](#6-existing-module-refinements-for-irish-setup)
7. [Interoperability Architecture](#7-interoperability-architecture)
8. [Impact Factor Matrix](#8-impact-factor-matrix)
9. [Regulatory Compliance Roadmap](#9-regulatory-compliance-roadmap)
10. [Go-To-Market via HIHI & HSE](#10-go-to-market-via-hihi--hse)

---

## 1. Executive Summary

Ireland's public health system is in crisis: **25,290+ patients on trolleys** in the first two months of 2025, **750,000 on hospital waiting lists**, and **700+ extra daily ED attendees** compared to prior years. The HSE has allocated **EUR 263 million for digital health in 2026** and published Ireland's first **"AI for Care" national strategy (2026-2030)** explicitly prioritising AI for clinical care, hospital operations, and demand/capacity management.

**Key finding:** There is **no deployed integrated AI hospital operations platform** in Ireland covering ED triage, bed management, oncology risk, sepsis detection, and patient flow together. Current deployments are point solutions (Aidoc for radiology, eAltra for chemotherapy, UiPath for RPA). This represents a significant gap that your platform is uniquely positioned to fill.

Your platform currently has **7 modules** built on MIMIC-IV data. This report recommends **8 new high-impact modules** and **6 refinements to existing modules**, all designed for interoperability and aligned with HSE priorities, HIQA standards, and EU AI Act compliance.

---

## 2. Current Platform Module Analysis

### Existing 7 Modules

| # | Module | Status | Strengths | Irish Relevance |
|---|--------|--------|-----------|-----------------|
| 1 | **ED Triage AI** (XGBoost, F1=0.653) | Running | Acuity scoring 1-5, disposition prediction, 59 features | **Critical** - Ireland's #1 crisis is ED overcrowding |
| 2 | **Sepsis & ICU** (LSTM AUROC=0.998) | Models trained, API not served | Best-in-class temporal prediction, 6-hour early warning | **High** - ICU bed scarcity, early intervention saves lives & beds |
| 3 | **Hospital Ops DES-MARL** | Client-side simulation only | Multi-agent staffing optimization, 8 dept simulation | **Critical** - Bed management is HSE's top operational priority |
| 4 | **Oncology AI** (XGBoost AUROC=0.897) | Running | Cancer risk prediction, treatment pathway optimization | **High** - Ireland's National Cancer Strategy needs AI support |
| 5 | **Patient Journey** | Running (live queries) | Full timeline, vitals, labs, medications, care path | **High** - Supports clinical decision-making and audit |
| 6 | **Clinical Chat** (Ollama/LLM) | Running | Multi-module integration, session management | **Medium** - AI scribe tools are Year 1 HSE AI priority |
| 7 | **Data Ingestion Simulator** | Functional, unused | Synthetic patient generation, real-time event simulation | **Medium** - Useful for training and demo environments |

### Gap Analysis Against Irish Priorities

| HSE Priority (AI for Care 2026-2030) | Your Current Coverage | Gap |
|---------------------------------------|----------------------|-----|
| Demand & Capacity Visualization | Partial (Hospital Ops) | No real-time bed board, no predictive discharge |
| ED Overcrowding / Trolley Crisis | ED Triage (prediction only) | No patient flow optimization, no wait time prediction |
| Radiology AI | None | Major gap -- Aidoc already deployed at Mater Hospital |
| AI Scribe / Documentation | Clinical Chat (basic) | No ambient scribe, no structured note generation |
| Waiting List Management | None | 750,000 patients waiting -- huge unmet need |
| Virtual Care / Remote Monitoring | None | EUR 2.7M allocated for virtual wards in 2026 |
| e-Prescribing Intelligence | None | National e-prescribing procurement underway |
| Mental Health Digital | None | EUR 1M initial budget, "Sharing the Vision" strategy |

---

## 3. Irish Healthcare Context & Urgent Needs

### System Structure
- **HSE** (Health Service Executive) manages ~50 public acute hospitals across **6 Health Regions**
- **Slaaintecare** (2017-2027): 10-year reform programme driving universal healthcare + digital transformation
- **"Digital for Care" Framework 2024-2030**: Overarching digital health strategy
- **"AI for Care" Strategy 2026-2030**: First national AI strategy for healthcare (published March 2026)

### The Crisis in Numbers

| Metric | Value | Source |
|--------|-------|--------|
| Patients on trolleys (Jan-Feb 2025) | 25,290+ | INMO |
| Single-day trolley record | 501 patients (Aug 2025) | HSE |
| Hospital waiting list | 750,000 patients | HSE |
| Waiting longer than target | 64% | HSE |
| Extra daily ED attendees vs prior year | 700+ | HSE |
| 2026 Digital Health Budget | EUR 263 million | Dept of Health |

### Year 1 HSE AI Priorities (2026-2027)
1. **Radiology AI** -- faster image reading, stroke/cancer/fracture detection
2. **AI Scribe tools** -- cut documentation time by 40%
3. **Demand & Capacity Management** -- real-time analytics across ED, outpatient, surgery, diagnostics, beds
4. **Contact centre automation**
5. **Support function automation** (HR, finance, procurement)

### National EHR Procurement
- **Largest digital health project in Irish history**
- Government approved procurement February 2026
- Vendor shortlisting underway (Epic, Oracle Health, InterSystems are likely contenders)
- Your platform must be **EHR-agnostic** and integrate via **HL7 FHIR** to remain relevant regardless of vendor selection

---

## 4. Competitive Landscape: AI Vendors in Irish Hospitals

### Currently Deployed in Irish Healthcare

| Company | Domain | Irish Deployment | Threat Level |
|---------|--------|-----------------|--------------|
| **Aidoc** (Israel) | Radiology AI triage | Mater Hospital -- 15,600+ scans, 90%+ accuracy | High (established) |
| **Oneview Healthcare** (Ireland) | Patient engagement + "Ovie" GenAI | CHI -- 600+ hospital locations, 7-year deal | Medium |
| **SilverCloud / Amwell** (Irish-founded) | Digital mental health CBT | HSE -- 45,000+ referrals, 12,300+ users | Low (different domain) |
| **Wellola** (Ireland) | Patient portal / telehealth | HSE COVID portal; secure messaging | Low |
| **Epic Systems** (US) | EHR | CHI (Project Ogham), all NI trusts (encompass) | Integration partner |
| **UiPath** | RPA automation | HSE -- 50+ processes, 800K hours saved, EUR 30M value | Low (complementary) |
| **eAltra** (Ireland) | Pre-chemo remote assessment | St James's Hospital pilot -- 25% fewer chemo cancellations | Medium (oncology overlap) |
| **S3 Connected Health** (Dublin) | Remote monitoring / digital therapeutics | Multiple pharma partnerships, Frost & Sullivan 2025 winner | Medium |
| **Clanwilliam Health** (Ireland) | GP systems, pharmacy, hospital IT | HSE, NHS, 1.5M+ healthcare professionals | Low (legacy IT) |

### HIHI.AI 2025 Winners (Direct Competitors to Watch)

| Company | Domain | Overlap with Your Platform |
|---------|--------|---------------------------|
| **Yellow Schedule** (Triage Link) | AI-powered triage digitization (OCR + NLP) | **Direct** -- ED triage |
| **Katana Healthcare** | Real-time medication error prevention | **Adjacent** -- patient safety |
| **CommPAL** | Specialist care coordination decision support | **Adjacent** -- clinical decision support |
| **Alto Health** | AI referral workflow automation | **Adjacent** -- patient flow |
| **Deciphex** (Diagnexia) | AI digital pathology | **Adjacent** -- oncology diagnostics |
| **CergenX** | AI newborn brain screening (FDA breakthrough) | **None** -- neonatal |
| **Samsa Ltd** (Samsa Bloom) | AI referral-to-case conversion | **Adjacent** -- intake optimization |

### International Competitors (Not Yet in Ireland)

| Company | Domain | Why They Matter |
|---------|--------|-----------------|
| **Qventus** (US) | AI hospital operations -- reduces excess days 20-35%, cuts LOS by 1 day | **Most direct competitor** to your Hospital Ops module |
| **TeleTracking** (US/UK) | Real-time bed management -- 1,000+ hospitals, deployed in NHS | **Direct competitor** to bed management capability |
| **GE Healthcare Command Centre** | Hospital "nerve center" -- reduced ED wait 35% at Johns Hopkins, deployed at Bradford NHS | **Direct competitor** to operations dashboard |
| **Viz.ai** (US/EU) | 50+ FDA-cleared clinical AI algorithms, care coordination, 1,700+ hospitals | **Broad competitor** across clinical AI |
| **C the Signs** (UK) | AI early cancer detection in primary care, NHS-evaluated | **Direct competitor** to oncology risk module |
| **Eolas Medical** (Belfast) | Clinical knowledge AI, $12M Series A, 85% of NHS acute trusts | **Adjacent** -- could expand to Ireland quickly |

### Competitive Assessment

**Your Advantages:**
1. **Integrated platform** -- No competitor offers ED triage + sepsis + bed management + oncology + patient journey in one system
2. **Open architecture** -- FastAPI microservices vs. monolithic vendor lock-in
3. **Multi-modal AI** -- XGBoost, LSTM, Transformers, DES-MARL, LLM vs. single-algorithm competitors
4. **Price point** -- Irish-built vs. enterprise US pricing (Qventus, GE Command Centre)

**Your Vulnerabilities:**
1. No Irish hospital validation data (MIMIC-IV is US data)
2. No HIQA/EU AI Act compliance documentation
3. No HL7 FHIR interoperability layer
4. No radiology AI (Aidoc is already entrenched)
5. Hospital Ops and Sepsis modules not fully operational

---

## 5. Recommended New Modules (High Impact)

### Module 8: Real-Time Bed Management & Discharge Prediction

**Impact Factor: 10/10 -- CRITICAL for Irish Setup**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | Directly addresses trolley crisis (25,290 patients in 2 months) and "Demand & Capacity Visualization" priority |
| **What It Does** | Real-time bed occupancy dashboard, predicted discharge times (ML), automated bed allocation, trolley wait tracking, capacity forecasting (24h/48h/7d) |
| **ML Approach** | Gradient boosting for discharge prediction (features: diagnosis, LOS so far, lab trends, vitals trajectory, department); time-series forecasting for demand; optimization for bed allocation |
| **Key Metrics** | Predicted discharge accuracy, bed turnaround time, trolley hours saved, occupancy rate optimization |
| **Competitors** | Qventus (US, not in Ireland), TeleTracking (UK/NHS), GE Command Centre (UK/NHS). **None deployed in Irish public hospitals.** |
| **Interoperability** | Consumes data from: ED Triage (incoming patients), Hospital Ops (department census), Patient Journey (current admissions), Sepsis ICU (ICU bed demand). Publishes to: Hospital Ops (real-time capacity), Clinical Chat (bed queries) |
| **Revenue Model** | Per-hospital license; HSE Demand & Capacity platform integration |
| **Irish Data Needed** | HSE BIU (Business Intelligence Unit) bed occupancy data, HIPE (Hospital In-Patient Enquiry) discharge data |

**Why This Is #1 Priority:** The trolley crisis is Ireland's most visible healthcare failure, generating daily media coverage and political pressure. Any solution that demonstrably reduces trolley hours will get immediate HSE attention. The EUR 263M digital budget specifically includes "Demand and Capacity Visualization Platform" -- this is a funded procurement opportunity.

---

### Module 9: Waiting List Intelligence & Prioritization

**Impact Factor: 9.5/10 -- CRITICAL**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | 750,000 patients on waiting lists; AI for Care strategy explicitly names waiting list management |
| **What It Does** | ML-based clinical priority scoring, predicted wait time estimation, deterioration risk during wait, automated scheduling optimization, capacity-demand matching |
| **ML Approach** | Survival analysis for wait-time prediction; classification for deterioration risk; constraint optimization for scheduling; NLP for referral letter triage |
| **Key Metrics** | Reduction in patients waiting beyond target, clinical deterioration events prevented, scheduling efficiency gains |
| **Competitors** | **No AI-powered waiting list solution deployed in Ireland.** NHS England has piloted some solutions but nothing comprehensive. |
| **Interoperability** | Feeds into: Bed Management (scheduled admissions), Hospital Ops (predicted demand), Oncology AI (cancer pathway prioritization). Receives from: ED Triage (emergency vs. elective classification), Patient Journey (clinical history) |
| **Irish Data Needed** | National Treatment Purchase Fund (NTPF) waiting list data, specialty-specific demand patterns |

**Why This Matters:** 64% of patients wait longer than target times. This is the second most politically sensitive health issue in Ireland. The NTPF publishes waiting list data monthly -- your module could provide the predictive intelligence layer that transforms reactive management into proactive scheduling.

---

### Module 10: AI Clinical Documentation / Ambient Scribe

**Impact Factor: 9/10 -- HIGH (Year 1 HSE AI Priority)**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | "AI for Care" Year 1 priority: "AI scribe tools to cut documentation time by up to 40%" |
| **What It Does** | Real-time clinical encounter transcription, structured note generation (SOAP format), auto-coding (ICD-10, SNOMED CT), integration with Clinical Chat for conversational documentation, specialty-specific templates |
| **Technical Approach** | Whisper/local ASR for transcription; LLM (Ollama or a commercial LLM API) for structuring; NER for entity extraction; rule-based for ICD-10 coding |
| **Key Metrics** | Documentation time reduction (target: 40%), coding accuracy, clinician satisfaction |
| **Competitors** | Nabla (France), Nuance DAX (Microsoft), Suki AI. **None deployed in Irish public hospitals.** |
| **Interoperability** | Extends: Clinical Chat (App 06) with audio input and structured output. Feeds: Patient Journey (structured clinical notes), Oncology AI (clinical note analysis), ED Triage (chief complaint extraction) |
| **Regulatory** | Requires EU AI Act compliance as high-risk system; GDPR DPIA for audio processing of patient data |

**Why This Matters:** Clinician burnout is driving workforce shortages across Irish hospitals. Documentation consumes 30-40% of physician time. The HSE has explicitly named this as a Year 1 deliverable -- building this now positions you for early adoption.

---

### Module 11: Virtual Ward / Remote Patient Monitoring

**Impact Factor: 8.5/10 -- HIGH**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | EUR 2.7M allocated for virtual care in 2026; target: 100 virtual ward beds per health region; first acute virtual ward went live Q1 2026 |
| **What It Does** | Remote vital sign monitoring (wearable integration), AI-driven deterioration alerts, virtual ward capacity management, patient self-reporting dashboard, escalation protocols |
| **ML Approach** | Anomaly detection on vital sign streams (isolation forest, autoencoders); trend prediction (LSTM from Sepsis module); risk stratification for safe discharge to virtual ward |
| **Key Metrics** | Readmission rate from virtual ward, early deterioration detection rate, physical bed days saved, patient satisfaction |
| **Competitors** | S3 Connected Health (Ireland, device connectivity), Current Health (acquired by BestBuy Health). **No integrated AI virtual ward platform in Irish hospitals.** |
| **Interoperability** | Receives from: Bed Management (discharge candidates), Sepsis ICU (deterioration models), Patient Journey (baseline vitals). Feeds: Bed Management (freed bed capacity), Clinical Chat (patient queries), Hospital Ops (demand reduction) |
| **Standards** | IEEE 11073 for device data, FHIR Observation resources for vitals |

**Why This Matters:** Virtual wards directly address the bed crisis by allowing safe earlier discharge. Each virtual ward bed frees a physical bed. With 100 beds targeted per region (600 total across Ireland), this is a high-volume, high-impact opportunity.

---

### Module 12: Radiology AI Triage & Reporting Support

**Impact Factor: 8/10 -- HIGH (Year 1 HSE AI Priority)**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | "AI for Care" Year 1 priority: "Certified AI for radiology -- faster image reading, earlier detection of strokes, cancers, fractures" |
| **What It Does** | AI-assisted prioritization of imaging worklists, preliminary finding detection (chest X-ray, CT head), reporting time estimation, integration with PACS systems, quality assurance flagging |
| **Technical Approach** | Pre-trained models (TorchXRayVision, MONAI) fine-tuned on available datasets; anomaly detection for worklist prioritization; NLP for report analysis |
| **Key Metrics** | Reporting turnaround time, critical finding detection sensitivity, worklist prioritization accuracy |
| **Competitors** | **Aidoc is already deployed at Mater Hospital** with 15,600+ scans analyzed. Behold.ai in NHS. Lunit in EU. |
| **Strategy** | Don't compete directly with Aidoc on detection. Instead, build **complementary worklist intelligence** -- prioritize the queue, estimate reporting times, flag discordant findings. Position as the operations layer that works alongside Aidoc's detection layer. |
| **Interoperability** | Feeds: ED Triage (imaging results integration), Oncology AI (staging imaging), Patient Journey (imaging timeline). Consumes: Waiting List (imaging backlog prioritization) |

**Why This Matters:** Radiology reporting backlogs are a major bottleneck in Irish hospitals. Rather than competing with established detection AI (Aidoc), positioning as the workflow/operations layer is a blue-ocean strategy that makes your platform complementary to existing deployments.

---

### Module 13: Antimicrobial Stewardship & Pharmacy Intelligence

**Impact Factor: 8/10 -- HIGH**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | National e-prescribing procurement underway; Hospital Medicines Management going live across 12 sites in 2026; AMR is a top HSE patient safety priority |
| **What It Does** | AI-powered antibiotic recommendation based on patient culture data, resistance patterns, and clinical context; drug interaction checking; dose optimization for renal/hepatic impairment; pharmacy workload prediction |
| **ML Approach** | Classification for optimal antibiotic selection; regression for dose optimization; time-series for resistance pattern tracking; NLP for microbiology report parsing |
| **Key Metrics** | Appropriate antibiotic use rate, de-escalation timeliness, drug interaction prevention rate, pharmacy cost savings |
| **Competitors** | **Katana Healthcare** (HIHI.AI winner) focuses on medication error prevention but not full stewardship. No comprehensive AI pharmacy platform in Irish hospitals. |
| **Interoperability** | Consumes from: Patient Journey (medications, labs, cultures), Sepsis ICU (infection detection), Clinical Chat (prescribing queries). Feeds: Bed Management (infection control isolation needs), Oncology AI (chemo-drug interactions) |
| **Data Available** | MIMIC prescriptions (15.4M records), labevents (microbiology), emar (26.7M medication administration records) |

**Why This Matters:** AMR (antimicrobial resistance) is one of the biggest global health threats. Irish hospitals report rising resistance rates. With 12 sites going live on hospital medicines management in 2026, there's a clear integration opportunity. This module also has strong revenue potential through pharmacy cost savings (measurable ROI).

---

### Module 14: Emergency Department Patient Flow Optimizer

**Impact Factor: 9/10 -- CRITICAL**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | Directly addresses ED overcrowding; complements Demand & Capacity Visualization Platform |
| **What It Does** | Real-time ED patient tracking (arrival to disposition), predicted ED LOS per patient, bottleneck identification (labs, imaging, beds, consults), automated escalation when wait times exceed thresholds, ambulance diversion recommendations |
| **ML Approach** | Survival analysis for time-to-disposition; queue simulation (extends Hospital Ops DES); reinforcement learning for resource allocation; anomaly detection for surge identification |
| **Key Metrics** | ED LOS reduction, left-without-being-seen rate, door-to-doctor time, 6-hour target compliance (Irish ED target) |
| **Competitors** | KATE AI/Mednition (US, not in Ireland), Qventus (US, not in Ireland). **Yellow Schedule's Triage Link** (HIHI.AI winner) focuses on triage digitization but not full flow optimization. |
| **Interoperability** | Extends: ED Triage (acuity prediction feeds flow priority). Feeds: Bed Management (admission predictions), Hospital Ops (real-time census). Consumes: Radiology AI (imaging wait times), Patient Journey (patient history for disposition prediction) |

**Why This Matters:** Ireland uses a **6-hour ED target** (patients should be admitted or discharged within 6 hours). Compliance is below 70% at most hospitals. This module extends your existing ED Triage from prediction-only to full operational optimization -- moving from "what acuity is this patient" to "how do we move all patients through efficiently."

---

### Module 15: Clinical Audit & Quality Intelligence

**Impact Factor: 7.5/10 -- HIGH**

| Attribute | Detail |
|-----------|--------|
| **HSE Alignment** | HIQA mandates quality reporting; supports hospital accreditation and clinical governance |
| **What It Does** | Automated KPI tracking (mortality rates, readmission rates, infection rates, LOS benchmarks), variance detection from clinical pathways, automated HIQA-format reporting, clinical outcome benchmarking across departments/hospitals |
| **ML Approach** | Statistical process control for outcome monitoring; anomaly detection for quality outliers; NLP for incident report analysis; causal inference for intervention effectiveness |
| **Key Metrics** | Reporting time savings, variance detection lead time, clinical outcome improvements identified |
| **Competitors** | No AI-powered clinical audit platform deployed in Irish hospitals. Traditional audit is manual and retrospective. |
| **Interoperability** | Consumes from: ALL modules (Patient Journey for outcomes, ED Triage for process metrics, Sepsis ICU for infection rates, Oncology AI for cancer pathway compliance, Hospital Ops for operational KPIs, Bed Management for flow metrics). This is the **analytics aggregation layer.** |

**Why This Matters:** HIQA inspections drive hospital behaviour. Automating quality reporting and providing real-time variance detection transforms audit from a retrospective burden to a proactive safety net. This module also serves as the **value demonstration layer** -- proving the impact of all other modules.

---

## 6. Existing Module Refinements for Irish Setup

### Refinement 1: ED Triage -- Irish ESI + Manchester Triage Alignment

**Impact Factor: 9/10**

| What to Change | Why | How |
|----------------|-----|-----|
| Replace US ESI framework with **Manchester Triage System (MTS)** | Irish EDs use MTS, not ESI. Your acuity 1-5 must map to MTS categories (Red/Orange/Yellow/Green/Blue) | Retrain classifier with MTS-aligned labels; add chief complaint flowcharts; output MTS-compatible categories |
| Add **Irish ED 6-hour target** tracking | Ireland measures performance against 6-hour ED target (Patient Experience Time) | Add PET prediction as output; flag patients at risk of breaching 6-hour target |
| Integrate **Irish clinical coding** (ICD-10-AM) | Ireland uses ICD-10-AM (Australian Modification) via HIPE system | Update ICD mapping tables; align diagnosis categories |
| Add **ambulance pre-alert** integration | Irish ambulance service (NAS) sends pre-alerts to EDs | Add pre-arrival prediction endpoint; integrate with NAS data format |

**Interoperability:** MTS-aligned output feeds into ED Flow Optimizer and Bed Management modules.

---

### Refinement 2: Sepsis ICU -- Activate API + Add NEWS2 Score

**Impact Factor: 8.5/10**

| What to Change | Why | How |
|----------------|-----|-----|
| **Activate API** (currently models trained but not served) | LSTM AUROC=0.998 is production-ready; wasted asset if not served | Deploy on port 8202; add to dashboard with live predictions |
| Add **NEWS2 (National Early Warning Score)** | NEWS2 is the standard deterioration scoring system used in all Irish hospitals | Add NEWS2 calculation alongside SOFA; map to escalation protocols |
| Add **ward-level deterioration** (not just ICU) | Most deterioration happens on general wards before ICU transfer | Extend models to predict ward-to-ICU transfer; add ward monitoring dashboard |
| Integrate **Irish antimicrobial guidelines** | HSE publishes specific antibiotic protocols for sepsis | Add Irish-specific antibiotic recommendation engine |

**Interoperability:** NEWS2 scores feed into Bed Management (escalation predictions), Virtual Ward (safe discharge criteria), and Clinical Audit (deterioration tracking).

---

### Refinement 3: Hospital Ops -- Complete MARL + Irish Hospital Profiles

**Impact Factor: 9/10**

| What to Change | Why | How |
|----------------|-----|-----|
| **Train MARL model** and serve via API | Client-side simulation is demo-only; need server-side for production | Complete MADDPG training; deploy on port 8203 |
| Replace MIMIC department profiles with **Irish hospital profiles** | Irish hospitals have different department structures (MAU, AMAU, SAU, CDU) | Add: Medical Assessment Unit (MAU), Acute Medical Assessment Unit (AMAU), Surgical Assessment Unit (SAU), Clinical Decision Unit (CDU), Day Ward |
| Add **staff rostering** patterns | Irish hospitals use specific shift patterns (8h/12h rotations, consultant on-call rotas) | Model Irish staffing constraints; integrate with shift planning |
| Add **HSE performance metrics** | HSE tracks specific KPIs (PET, trolley count, ALOS, delayed discharges) | Output HSE-compatible metrics; enable benchmark comparisons |

**Interoperability:** Feeds Bed Management (predicted capacity), receives from ED Flow (incoming demand), publishes to Clinical Audit (operational KPIs).

---

### Refinement 4: Oncology AI -- Irish Cancer Strategy Alignment

**Impact Factor: 8/10**

| What to Change | Why | How |
|----------------|-----|-----|
| Align pathways with **National Cancer Strategy 2017-2026** | Ireland has 8 designated cancer centres with specific referral pathways | Map treatment pathways to Irish NCCP (National Cancer Control Programme) protocols |
| Add **Rapid Access Clinic** integration | Irish cancer diagnosis uses rapid access clinics (breast, lung, prostate) | Add rapid access pathway tracking; measure referral-to-diagnosis time |
| Integrate **NCRI (National Cancer Registry Ireland)** data format | NCRI tracks all cancers diagnosed in Ireland | Output NCRI-compatible data; enable registry-quality reporting |
| Add **MDT (Multidisciplinary Team)** meeting support | Irish cancer care mandates MDT discussion for all cases | Generate MDT meeting summaries; track MDT recommendations vs. actual treatment |
| Enhance NLP for **Irish clinical notes** | Irish clinical terminology differs from US (e.g., "theatre" not "OR", "registrar" not "resident") | Fine-tune NLP models on Irish clinical vocabulary |

**Interoperability:** Connects to Waiting List (cancer pathway prioritization), Radiology AI (staging imaging), Clinical Audit (NCCP pathway compliance), Pharmacy (chemo protocols).

---

### Refinement 5: Clinical Chat -- Upgrade to Production LLM

**Impact Factor: 7.5/10**

| What to Change | Why | How |
|----------------|-----|-----|
| Replace Ollama with **a commercial LLM API** or production LLM | Ollama is local/experimental; Irish hospital deployment needs enterprise-grade LLM | Integrate a commercial LLM API via the vendor SDK; add fallback chain |
| Add **Irish clinical guidelines** as RAG context | HSE publishes clinical guidelines; clinicians need guideline-aware responses | Build RAG pipeline with HSE/HIQA/NCCP guidelines |
| Add **Irish formulary** integration | Irish hospitals use specific formulary (HSE-approved medications) | Index Irish formulary for medication queries |
| Add **role-based access** | Different clinical roles need different information levels | Implement RBAC: consultant, registrar, SHO, nurse, pharmacist |
| Add **audit trail** for EU AI Act | High-risk AI requires transparency and human oversight | Log all queries, responses, and clinical decisions with timestamps |

**Interoperability:** Hub module connecting all other modules; RAG context from Clinical Audit, Pharmacy, Oncology, and ED Triage.

---

### Refinement 6: Patient Journey -- FHIR Output + Irish Coding

**Impact Factor: 8/10**

| What to Change | Why | How |
|----------------|-----|-----|
| Add **HL7 FHIR R4** output format | National EHR procurement requires FHIR interoperability; NSCR (National Shared Care Record) is FHIR-based | Wrap all API responses in FHIR resources (Patient, Encounter, Observation, MedicationRequest) |
| Add **ICD-10-AM** and **ACHI** coding | Ireland uses ICD-10-AM (diagnoses) and ACHI (procedures) via HIPE | Map existing ICD codes to ICD-10-AM; add ACHI procedure coding |
| Add **HIPE discharge summary** format | HIPE (Hospital In-Patient Enquiry) is the national discharge coding system | Generate HIPE-compatible discharge summaries |
| Add **cross-hospital journey** tracking | Patients move between Irish hospitals (transfers between hospital groups) | Support multi-facility patient journeys; aggregate cross-site data |

**Interoperability:** FHIR output enables integration with ANY future National EHR vendor (Epic, Oracle, InterSystems). This is the **interoperability cornerstone** of the entire platform.

---

## 7. Interoperability Architecture

### Module Interaction Map (All 15 Modules)

```
                              +-------------------+
                              | Clinical Audit &  |
                              | Quality Intel (15)|
                              | [Aggregation Hub] |
                              +--------+----------+
                                       |
          Consumes KPIs from ALL modules below
                                       |
     +--+------+-------+-------+------+------+-------+------+--+
     |  |      |       |       |      |      |       |      |  |
+----v--v+ +---v---+ +-v------+v+ +--v----+ +v------v+ +---v--v----+
|ED Triage| |Sepsis | |Hosp Ops | |Oncology| |Patient | |Clinical   |
|  (01)   | |ICU(02)| | (03)    | |AI (04) | |Journey | |Chat (06)  |
|+MTS     | |+NEWS2 | |+MARL    | |+NCCP   | |(05)    | |+LLM API|
|+6hr PET | |+Wards | |+Irish   | |+MDT    | |+FHIR   | |+RAG+RBAC  |
+---------+ +-------+ +---------+ +--------+ |+HIPE   | +-----------+
     |          |          |          |       +--------+       |
     |          |          |          |           |            |
+----v----------v----------v----------v-----------v------------v----+
|                    HL7 FHIR R4 Integration Layer                   |
|     (Interoperability backbone for National EHR & NSCR)           |
+---+----------+----------+----------+-----------+----------+-------+
    |          |          |          |           |          |
+---v---+ +---v----+ +---v-----+ +-v--------+ +v-------+ +v--------+
|Bed    | |Waiting | |Virtual  | |Radiology | |Pharmacy| |ED Flow   |
|Mgmt   | |List    | |Ward     | |AI        | |Intel   | |Optimizer |
|(08)   | |Intel(09| |(11)     | |(12)      | |(13)    | |(14)      |
+-------+ +--------+ +---------+ +----------+ +--------+ +----------+

NEW MODULES (08-15) ^^^^                    ^^^^ EXISTING REFINED (01-07)
```

### Integration Standards

| Standard | Purpose | Modules |
|----------|---------|---------|
| **HL7 FHIR R4** | Data exchange with National EHR, NSCR, and external systems | ALL modules (via Patient Journey FHIR layer) |
| **ICD-10-AM** | Irish diagnosis coding | ED Triage, Oncology, Patient Journey, Clinical Audit |
| **ACHI** | Irish procedure coding | Oncology, Patient Journey, Surgical modules |
| **SNOMED CT** | Clinical terminology | Clinical Chat, Documentation, NLP modules |
| **DICOM** | Medical imaging | Radiology AI |
| **IEEE 11073** | Medical device data | Virtual Ward, Sepsis ICU |
| **NEWS2** | Deterioration scoring | Sepsis ICU, Virtual Ward, Bed Management |
| **MTS** | ED triage categorization | ED Triage, ED Flow Optimizer |

### Data Flow Architecture

```
External Systems (EHR, PACS, Lab, Pharmacy)
                |
                v
    +-------------------+
    | FHIR Integration  |  <-- New integration layer
    | Gateway           |
    +-------------------+
                |
    +-----------+-----------+
    |                       |
    v                       v
+--------+          +-----------+
|MongoDB |          |Event Bus  |  <-- New pub/sub for real-time
|(MIMIC/ |          |(Redis/    |      inter-module communication
| Irish) |          | RabbitMQ) |
+--------+          +-----------+
    |                       |
    v                       v
+----------------------------+
| FastAPI Microservices      |
| (Ports 8201-8215)          |
+----------------------------+
                |
                v
    +-------------------+
    | React Dashboard   |  <-- Extended to 15+ pages
    | (Port 3000)       |
    +-------------------+
```

---

## 8. Impact Factor Matrix

### Scoring Criteria
- **Clinical Impact** (1-10): Direct patient outcome improvement
- **Operational Impact** (1-10): Hospital efficiency and cost savings
- **HSE Alignment** (1-10): Match with stated HSE/government priorities
- **Competitive Advantage** (1-10): Differentiation from existing market solutions
- **Implementation Feasibility** (1-10): Technical readiness given current platform
- **Revenue Potential** (1-10): Procurement likelihood and commercial viability

### New Modules

| Module | Clinical | Operational | HSE Align | Competitive | Feasibility | Revenue | **TOTAL /60** | **Priority** |
|--------|----------|-------------|-----------|-------------|-------------|---------|---------------|--------------|
| **Bed Management (08)** | 8 | 10 | 10 | 9 | 7 | 10 | **54** | **#1** |
| **Waiting List Intel (09)** | 9 | 9 | 10 | 10 | 6 | 9 | **53** | **#2** |
| **ED Flow Optimizer (14)** | 9 | 10 | 9 | 8 | 8 | 9 | **53** | **#2** |
| **AI Clinical Scribe (10)** | 7 | 9 | 10 | 8 | 7 | 9 | **50** | **#4** |
| **Virtual Ward (11)** | 8 | 9 | 9 | 8 | 6 | 8 | **48** | **#5** |
| **Pharmacy Intel (13)** | 9 | 8 | 8 | 8 | 7 | 8 | **48** | **#5** |
| **Radiology AI (12)** | 8 | 8 | 9 | 5 | 5 | 8 | **43** | **#7** |
| **Clinical Audit (15)** | 7 | 8 | 8 | 9 | 8 | 7 | **47** | **#8** |

### Existing Module Refinements

| Refinement | Clinical | Operational | HSE Align | Competitive | Feasibility | Revenue | **TOTAL /60** | **Priority** |
|------------|----------|-------------|-----------|-------------|-------------|---------|---------------|--------------|
| **ED Triage + MTS (01)** | 9 | 9 | 10 | 9 | 8 | 9 | **54** | **#1** |
| **Hospital Ops + MARL (03)** | 7 | 10 | 9 | 9 | 6 | 9 | **50** | **#2** |
| **Sepsis ICU Activation (02)** | 10 | 8 | 8 | 8 | 9 | 7 | **50** | **#2** |
| **Patient Journey + FHIR (05)** | 6 | 8 | 10 | 10 | 7 | 8 | **49** | **#4** |
| **Oncology + NCCP (04)** | 9 | 7 | 8 | 8 | 7 | 8 | **47** | **#5** |
| **Clinical Chat + LLM (06)** | 7 | 8 | 8 | 7 | 8 | 7 | **45** | **#6** |

### Recommended Implementation Phases

**Phase 1 (Months 1-3): Foundation & Quick Wins**
1. ED Triage + MTS alignment (Refinement #1)
2. Sepsis ICU API activation (Refinement #2)
3. Patient Journey + FHIR output (Refinement #4)
4. Begin Bed Management module (New #8)

**Phase 2 (Months 3-6): Core Operational Modules**
5. Bed Management completion + ED Flow Optimizer (New #8, #14)
6. Hospital Ops MARL completion (Refinement #3)
7. Clinical Chat upgrade to a commercial LLM API (Refinement #6)
8. Begin Waiting List Intelligence (New #9)

**Phase 3 (Months 6-9): Clinical Intelligence**
9. Waiting List Intelligence completion (New #9)
10. AI Clinical Scribe (New #10)
11. Oncology + NCCP alignment (Refinement #5)
12. Pharmacy Intelligence (New #13)

**Phase 4 (Months 9-12): Advanced Capabilities**
13. Virtual Ward / Remote Monitoring (New #11)
14. Radiology AI workflow (New #12)
15. Clinical Audit & Quality Intelligence (New #15)

---

## 9. Regulatory Compliance Roadmap

### EU AI Act (Fully applicable 2 August 2026)

| Requirement | Impact on Your Platform | Action Required |
|-------------|------------------------|-----------------|
| **High-risk classification** | ED Triage, Sepsis, Oncology are all high-risk (medical device safety components, emergency triage) | Register in EU AI database; complete conformity assessment |
| **Risk management system** | Continuous risk identification and mitigation | Implement risk management framework across all modules |
| **Data governance** | Training data quality, bias testing, representativeness | Document MIMIC-IV limitations; plan Irish data validation |
| **Technical documentation** | Detailed system description, design choices, performance metrics | Create per-module technical dossiers |
| **Record-keeping** | Automatic logging of all AI decisions | Implement audit trail across all prediction endpoints |
| **Transparency** | Users must know they're interacting with AI; understand outputs | Add AI decision explanations (SHAP/LIME) to all predictions |
| **Human oversight** | Meaningful human control over AI outputs | Ensure all predictions are advisory, not autonomous; add override mechanisms |
| **Accuracy & robustness** | Validated performance, resilience to errors | Establish validation framework; plan Irish data benchmarking |

### HIQA Compliance

| HIQA Standard | Relevance | Action |
|---------------|-----------|--------|
| **National Standards for Safer Better Healthcare** | All modules | Map each module to relevant HIQA standards |
| **AI Guidance (expected 2026)** | All modules | Based on four principles: accountability, human rights, safety, responsiveness |
| **Information Management Standards** | Data handling | Ensure data quality, security, access controls |
| **Draft AI principles**: Accountability | All clinical AI | Clear responsibility chains for AI-assisted decisions |
| **Draft AI principles**: Safety & Wellbeing | Clinical modules | Clinical validation, fail-safe modes, human override |

### GDPR / Health Data

| Requirement | Action |
|-------------|--------|
| **Lawful basis** | Identify lawful basis for each data processing activity (likely: public interest in healthcare) |
| **DPIA** | Complete Data Protection Impact Assessment for each module |
| **Data minimization** | Ensure only necessary data is collected and processed |
| **Patient consent** | Implement consent management where required |
| **Right to explanation** | Provide meaningful explanations of AI decisions (links to EU AI Act transparency) |
| **Data Processing Agreements** | DPAs with HSE and each hospital |

### Medical Device Regulation (MDR 2017/745)

| Module | MDR Classification | Action |
|--------|-------------------|--------|
| ED Triage | Class IIa (clinical decision support) | CE marking required; clinical evaluation |
| Sepsis ICU | Class IIa (monitoring & alerting) | CE marking required; clinical evaluation |
| Oncology AI | Class IIa (risk prediction) | CE marking required; clinical evaluation |
| Bed Management | Not a medical device (operational) | Exempt from MDR |
| Waiting List | Not a medical device (administrative) | Exempt from MDR |
| Clinical Chat | Depends on claims made | Legal review needed |

---

## 10. Go-To-Market via HIHI & HSE

### Health Innovation Hub Ireland (HIHI) Pathway

HIHI is the **primary gateway** for AI companies to pilot in Irish hospitals. Recommended approach:

1. **Apply to HIHI.AI 2026 Call** (next call expected Q3-Q4 2026)
   - Submit 2-3 modules with strongest Irish relevance (ED Triage + MTS, Bed Management, ED Flow)
   - HIHI provides: clinical site access, mentorship, regulatory guidance, 12-month pilot support

2. **Target Pilot Sites**
   - **Mater Hospital** -- already using AI (Aidoc); innovation-friendly leadership
   - **St James's Hospital** -- largest hospital in Ireland; strong research/innovation culture; eAltra pilot site
   - **UH Galway** -- deployed "Ruadhan" RPA bot; open to AI innovation
   - **Beaumont Hospital** -- neuroscience centre; strong ED pressure

3. **Enterprise Ireland Support**
   - Innovation vouchers (EUR 5,000) for feasibility studies
   - Commercialisation Fund for R&D
   - HPSU (High Potential Start-Up) programme for scaling

### HSE Procurement Entry Points

| Entry Point | Module Fit | Timeline |
|-------------|-----------|----------|
| **Demand & Capacity Visualization Platform** | Bed Management, ED Flow, Hospital Ops | 2026-2027 (optimization phase) |
| **National EHR Integration** | Patient Journey (FHIR), all modules via FHIR gateway | 2027+ (post vendor selection) |
| **AI and Automation CoE** | All modules (via innovation pipeline) | Ongoing |
| **Virtual Care Programme** | Virtual Ward module | 2026 (EUR 2.7M allocated) |
| **Hospital Medicines Management** | Pharmacy Intelligence | 2026 (12-site go-live) |

### Key Stakeholders to Engage

| Role | Organization | Why |
|------|-------------|-----|
| Chief Clinical Information Officer | HSE | Drives clinical AI adoption decisions |
| Head of AI & Automation CoE | HSE | Manages AI evaluation and deployment |
| Clinical Director | Target hospital | Champions innovation at site level |
| HIHI Programme Manager | HIHI | Gateway to hospital pilot access |
| eHealth Ireland Director | HSE | Owns digital health strategy |
| HIQA Health Information team | HIQA | Influences standards and compliance |

---

## Appendix A: Vendor Comparison Matrix

| Capability | Your Platform | Qventus | TeleTracking | GE Command Centre | Aidoc | eAltra |
|------------|--------------|---------|-------------|-------------------|-------|--------|
| ED Triage AI | Yes | No | No | Partial | No | No |
| Sepsis Detection | Yes (AUROC 0.998) | No | No | No | No | No |
| Bed Management | Planned | Yes | Yes | Yes | No | No |
| Patient Flow | Yes (DES-MARL) | Yes | Yes | Yes | No | No |
| Oncology AI | Yes | No | No | No | No | Partial |
| Patient Journey | Yes | No | No | No | No | No |
| Clinical Chat | Yes | No | No | No | No | No |
| Waiting List AI | Planned | No | No | No | No | No |
| Radiology AI | Planned | No | No | No | Yes | No |
| Pharmacy AI | Planned | No | No | No | No | No |
| Virtual Ward | Planned | No | No | No | No | No |
| Clinical Audit | Planned | Partial | Partial | Partial | No | No |
| **Integrated Platform** | **Yes** | Partial | Partial | Partial | No | No |
| **Irish Deployment** | **Planned** | No | No | No | **Yes** | **Yes** |
| **FHIR Support** | Planned | Yes | Yes | Yes | Yes | Unknown |
| **Pricing** | Competitive | Enterprise | Enterprise | Enterprise | Per-scan | Per-site |

---

## Appendix B: Summary of All 15 Modules (Final Architecture)

| # | Module | Type | Status | Impact Score /60 |
|---|--------|------|--------|-----------------|
| 01 | ED Triage + MTS | Refined | Existing + MTS alignment | 54 |
| 02 | Sepsis ICU + NEWS2 | Refined | Activate API + NEWS2 | 50 |
| 03 | Hospital Ops + MARL | Refined | Complete MARL + Irish profiles | 50 |
| 04 | Oncology AI + NCCP | Refined | Align with Irish cancer strategy | 47 |
| 05 | Patient Journey + FHIR | Refined | Add FHIR output + ICD-10-AM | 49 |
| 06 | Clinical Chat + LLM | Refined | Upgrade LLM + RAG + RBAC | 45 |
| 07 | Data Ingestion Sim | Existing | Unchanged (training/demo use) | - |
| **08** | **Bed Management** | **New** | Real-time beds + discharge prediction | **54** |
| **09** | **Waiting List Intel** | **New** | ML prioritization + scheduling | **53** |
| **10** | **AI Clinical Scribe** | **New** | Ambient documentation + coding | **50** |
| **11** | **Virtual Ward** | **New** | Remote monitoring + alerts | **48** |
| **12** | **Radiology AI Workflow** | **New** | Worklist intelligence + PACS | **43** |
| **13** | **Pharmacy Intelligence** | **New** | AMR stewardship + dose optimization | **48** |
| **14** | **ED Flow Optimizer** | **New** | Full ED patient flow management | **53** |
| **15** | **Clinical Audit** | **New** | Automated quality reporting | **47** |

**Total: 15 interoperable modules forming Ireland's first integrated AI hospital operations platform.**

---

*Report prepared: 17 April 2026*
*Data sources: HSE publications, HIQA, Department of Health, Enterprise Ireland, HIHI, Silicon Republic, company websites, HSE Digital Health Roadmap, AI for Care Strategy 2026-2030*
