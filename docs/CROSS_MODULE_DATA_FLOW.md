# Cross-Module Data Flow Architecture
## Consistent Interoperability Between All 11 Modules

---

## 1. Complete Module Registry

| Port | Module | Type | Data Role |
|------|--------|------|-----------|
| 8201 | ED Triage (App 01) | Existing | Producer: acuity, disposition prediction |
| 8202 | Sepsis ICU (App 02) | Existing | Producer: sepsis risk, NEWS2 scores |
| 8203 | Hospital Ops (App 03) | Existing | Producer: department census, simulation |
| 8204 | Oncology AI (App 04) | Existing | Producer: cancer risk, pathway recommendations |
| 8205 | Patient Journey (App 05) | Existing | Hub: patient demographics, vitals, labs, meds, timeline |
| 8206 | Clinical Chat (App 06) | Existing | Consumer: queries all modules for conversational AI |
| 8207 | Data Ingestion (App 07) | Existing | Producer: synthetic patient events |
| **8208** | **Bed Management** | **New** | **Hub: bed state, discharge predictions, capacity forecasts** |
| **8209** | **Waiting List** | **New** | **Hub: priority scores, schedules, deterioration alerts** |
| **8210** | **Clinical Scribe** | **New** | **Producer: structured notes, ICD codes, entities** |
| **8214** | **ED Flow Optimizer** | **New** | **Hub: patient flow predictions, surge forecasts, bottlenecks** |

---

## 2. Data Flow Matrix

Shows which modules produce data consumed by which other modules.

```
                    CONSUMERS →
                    08    09    10    14    01    02    03    04    05    06
PRODUCERS ↓         Bed   Wait  Scri  Flow  Tria  Sep   Ops   Onc   Jrny  Chat
─────────────────────────────────────────────────────────────────────────────
08 Bed Management    -     ●     ○     ●     ○     ○     ●     ○     ○     ●
09 Waiting List      ●     -     ○     ○     ○     ○     ●     ●     ○     ●
10 Clinical Scribe   ○     ●     -     ○     ○     ○     ○     ●     ●     ●
14 ED Flow           ●     ○     ○     -     ○     ○     ●     ○     ○     ●
01 ED Triage         ●     ●     ●     ●     -     ○     ○     ○     ○     ●
02 Sepsis ICU        ●     ○     ●     ●     ○     -     ○     ○     ○     ●
03 Hospital Ops      ●     ○     ○     ●     ○     ○     -     ○     ○     ●
04 Oncology AI       ○     ●     ●     ○     ○     ○     ○     -     ○     ●
05 Patient Journey   ●     ●     ●     ●     ●     ●     ○     ●     -     ●

● = Active data flow    ○ = No direct flow
```

---

## 3. Specific Data Flows Between Modules

### 3.1 ED Triage (8201) → ED Flow (8214)
```
Data: acuity_level, disposition_prediction, risk_factors, ed_los_estimate
Trigger: Every new triage prediction
Method: ED Flow subscribes to ED Triage via ServiceClient.ed_triage.post("/predict")
Purpose: Feed flow predictions with acuity data; determine patient track assignment
```

### 3.2 ED Flow (8214) → Bed Management (8208)
```
Data: admission_predicted event {patient_id, probability, acuity, department_preference}
Trigger: When admission probability > 0.7 for an ED patient
Method: EventBus publish("admission_predicted") → Bed Management subscribes
Purpose: Pre-allocate beds before formal admission decision to reduce boarding
```

### 3.3 Bed Management (8208) → ED Flow (8214)
```
Data: bed_allocated event {bed_id, department, patient_id}
       bed_released event {bed_id, department}
Trigger: Bed status changes
Method: EventBus publish → ED Flow subscribes
Purpose: Update ED boarding status; clear bottleneck flags when beds become available
```

### 3.4 Patient Journey (8205) → Bed Management (8208)
```
Data: patient vitals, labs, medications, current department, admission details
Trigger: Bed Management queries on-demand for discharge prediction
Method: ServiceClient.patient_journey.get("/patient/{id}/admission/{hadm}/vitals")
Purpose: Provide clinical data for discharge readiness scoring and TFT prediction
```

### 3.5 Patient Journey (8205) → ED Flow (8214)
```
Data: patient history, comorbidities, prior ED visits
Trigger: On patient arrival at ED (for returning patients)
Method: ServiceClient.patient_journey.get("/patient/{id}/summary")
Purpose: Improve disposition prediction with patient history context
```

### 3.6 Patient Journey (8205) → Clinical Scribe (8210)
```
Data: demographics, medications, vitals, labs, active diagnoses
Trigger: When generating a clinical note (context enrichment)
Method: ServiceClient.patient_journey.get("/patient/{id}/admission/{hadm}/...")
Purpose: Enrich AI-generated notes with current patient data
```

### 3.7 Oncology AI (8204) → Waiting List (8209)
```
Data: cancer_risk_score, treatment_pathway, recommended_urgency
Trigger: When scoring priority for oncology patients
Method: ServiceClient.oncology_ai.post("/predict-risk", patient_data)
Purpose: Cancer-specific urgency feeds into waiting list prioritization
```

### 3.8 Bed Management (8208) → Waiting List (8209)
```
Data: capacity_forecast, available_beds_by_department
Trigger: When generating weekly schedule (need bed availability)
Method: ServiceClient.bed_management.get("/forecast/{department}")
Purpose: Schedule elective admissions only when beds predicted to be available
```

### 3.9 Waiting List (8209) → Bed Management (8208)
```
Data: scheduled_admissions {date, specialty, patient_count}
Trigger: When new schedule is generated
Method: EventBus publish("schedule_generated") → Bed Management subscribes
Purpose: Include elective admission demand in capacity forecasting
```

### 3.10 Clinical Scribe (8210) → Patient Journey (8205)
```
Data: generated_note (FHIR DocumentReference), ICD codes, NER entities
Trigger: When a note is approved by clinician
Method: EventBus publish("note_approved") → Patient Journey subscribes
Purpose: Add structured clinical documentation to patient timeline
```

### 3.11 Sepsis ICU (8202) → Bed Management (8208)
```
Data: sepsis_risk_score, ICU_transfer_prediction
Trigger: When sepsis risk exceeds threshold
Method: EventBus / ServiceClient
Purpose: Predict ICU bed demand; trigger early ICU bed allocation
```

### 3.12 Sepsis ICU (8202) → ED Flow (8214)
```
Data: sepsis_risk for ED patients with suspected infection
Trigger: On sepsis screening in ED
Method: ServiceClient.sepsis_icu.post("/predict", patient_data)
Purpose: Identify high-risk patients needing expedited care and ICU preparation
```

### 3.13 All Modules → Clinical Chat (8206)
```
Data: Any module can be queried via Clinical Chat
Trigger: User asks a question in the chat interface
Method: Clinical Chat routes to appropriate module via ServiceClient
Purpose: Unified conversational interface for all platform capabilities
```

---

## 4. Event Bus Topics & Subscribers

```python
# Module 08: Bed Management publishes
"bed_allocated"        → [ED Flow, Hospital Ops]
"bed_released"         → [ED Flow, Hospital Ops]
"discharge_predicted"  → [Hospital Ops, Clinical Chat]
"capacity_alert"       → [ED Flow, Hospital Ops, Clinical Chat]
"trolley_alert"        → [ED Flow, Clinical Chat]

# Module 09: Waiting List publishes
"priority_updated"     → [Clinical Chat]
"schedule_generated"   → [Bed Management, Hospital Ops]
"deterioration_alert"  → [Clinical Chat, Patient Journey]
"referral_triaged"     → [Clinical Chat]

# Module 10: Clinical Scribe publishes
"note_generated"       → [Patient Journey]
"note_approved"        → [Patient Journey, Clinical Audit]
"coding_suggested"     → [Patient Journey]

# Module 14: ED Flow publishes
"pet_breach_risk"      → [Clinical Chat]
"lwbs_risk"            → [Clinical Chat]
"surge_alert"          → [Hospital Ops, Bed Management, Clinical Chat]
"bottleneck_detected"  → [Clinical Chat]
"admission_predicted"  → [Bed Management]

# Existing Module events (new subscriptions)
"patient_admitted"     → [Bed Management, ED Flow, Hospital Ops]
"patient_discharged"   → [Bed Management, ED Flow, Hospital Ops, Waiting List]
"patient_transferred"  → [Bed Management, Hospital Ops]
```

---

## 5. Data Consistency Guarantees

### 5.1 Shared Data Models
All modules use these shared data contracts:

```python
# shared/integration/data_contracts.py

class PatientIdentifier:
    """Canonical patient identifier used across all modules."""
    subject_id: int          # MIMIC subject_id
    hadm_id: Optional[int]   # Hospital admission ID
    patient_name: Optional[str]

class VitalSigns:
    """Standardized vital signs used across modules."""
    heart_rate: Optional[float]
    respiratory_rate: Optional[float]
    spo2: Optional[float]
    sbp: Optional[float]
    dbp: Optional[float]
    temperature: Optional[float]
    news2_score: Optional[float]
    timestamp: datetime

class ClinicalPriority:
    """Standardized priority used by ED Triage, Waiting List, Bed Management."""
    acuity_level: int        # 1-5 (ESI/MTS mapped)
    priority_score: float    # 0-1 composite
    priority_label: str      # immediate, very_urgent, urgent, standard, non_urgent

class DepartmentState:
    """Standardized department state used by Bed Mgmt, Hospital Ops, ED Flow."""
    department: str
    census: int
    capacity: int
    occupancy_rate: float
    alert_level: str
```

### 5.2 Data Freshness Rules
| Data Type | Max Staleness | Refresh Strategy |
|-----------|---------------|------------------|
| Bed state | 1 minute | Event-driven (bed_allocated/released) |
| ED patient flow | 5 minutes | Event-driven + periodic refresh |
| Discharge predictions | 15 minutes | Periodic batch re-prediction |
| Capacity forecasts | 30 minutes | Periodic re-forecast |
| Waiting list priority | 24 hours | On-demand re-scoring |
| Clinical notes | Immediate | Event-driven (note_generated) |
| Surge forecast | 1 hour | Periodic re-forecast |

### 5.3 Failure Handling
- **Module unavailable:** ServiceClient returns `{status: "error"}` gracefully
- **No cascade failures:** Each module operates independently; degraded but functional without peers
- **Fallback data:** Each module has rule-based fallbacks when ML models or peer modules are unavailable
- **Event replay:** EventBus maintains last 10,000 events for late-subscribing modules

---

## 6. Integration with Existing Modules

### Required Changes to Existing Modules

#### App 01: ED Triage
- **No code changes needed** — ED Flow calls its API as a consumer
- **Optional enhancement:** Publish triage events to EventBus for real-time notification

#### App 02: Sepsis ICU
- **Activate API on port 8202** (models already trained)
- **Add EventBus integration:** Publish sepsis alerts

#### App 03: Hospital Ops
- **Subscribe to Bed Management events** for real-time capacity data
- **Subscribe to Waiting List events** for elective demand forecasting

#### App 04: Oncology AI
- **No code changes needed** — Waiting List calls its API as a consumer

#### App 05: Patient Journey
- **Subscribe to Clinical Scribe events** to receive generated notes
- **No API changes needed** — all modules already consume its API

#### App 06: Clinical Chat
- **Add new module routes** to ServiceClient for Bed Management, Waiting List, Scribe, ED Flow
- **Subscribe to alert events** (PET breach, trolley, surge, deterioration) for proactive alerts

#### App 07: Data Ingestion
- **Extend synthetic events** to include bed state changes and ED flow events

---

## 7. Startup Order

For full inter-module communication, services should start in this order:

```
1. MongoDB                          (prerequisite)
2. Patient Journey (8205)           (data provider for all)
3. ED Triage (8201)                 (core prediction)
4. Sepsis ICU (8202)                (core prediction)
5. Oncology AI (8204)               (core prediction)
6. Hospital Ops (8203)              (simulation)
7. Bed Management (8208)            (depends on Patient Journey)
8. ED Flow Optimizer (8214)         (depends on ED Triage, Bed Management)
9. Waiting List (8209)              (depends on Oncology, Bed Management)
10. Clinical Scribe (8210)          (depends on Patient Journey, all clinical)
11. Clinical Chat (8206)            (depends on all modules)
12. Data Ingestion (8207)           (optional)
13. Dashboard (3000)                (frontend)
```

Each module gracefully handles missing dependencies (returns fallback data).
