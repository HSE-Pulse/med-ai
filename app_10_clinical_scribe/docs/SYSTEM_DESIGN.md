# Module 10: AI Clinical Scribe / Ambient Documentation
## System Design Document

---

## 1. Executive Summary

This module provides AI-powered clinical documentation: ambient encounter transcription, structured note generation (SOAP format), automated ICD-10-AM coding, and clinical NER — targeting the HSE's Year 1 AI priority of "AI scribe tools to cut documentation time by up to 40%." Extends the existing Clinical Chat module (App 06) with audio processing, structured output, and Irish clinical guidelines integration.

**Port:** 8210
**Status:** New Module
**Integration:** Extends Clinical Chat (8206). Feeds Patient Journey (8205), Oncology AI (8204), ED Triage (8201). Consumes from all clinical modules for context enrichment.

---

## 2. Market Research: Similar Products

### 2.1 Nuance DAX Copilot (Microsoft) — Market Leader
- **What:** Ambient clinical documentation integrated into EHRs; automatic SOAP note generation from patient-doctor conversations
- **Technology:** Microsoft Azure AI, GPT-4 based; dragon medical speech engine; real-time transcription + note generation
- **Results:** 50%+ reduction in documentation time; 70% reduction in after-hours charting (pajama time); 3+ hours saved per clinician per day at UW Health; deployed at Epic, Oracle Health, MEDITECH sites
- **Pricing:** ~$200-400/clinician/month (enterprise); bundled with Microsoft 365 for healthcare
- **Regulatory:** FDA Class I exempt; CE marked for EU
- **Strengths:** Best-in-class speech recognition (Dragon), Microsoft ecosystem, deep EHR integration, massive scale
- **Weaknesses:** Expensive, requires Microsoft cloud, US English optimized, limited customization
- **Gap vs. Our Module:** No Irish English optimization; cloud-dependent; expensive per-clinician licensing

### 2.2 Abridge
- **What:** AI-powered medical conversation documentation; structured note generation with evidence linking
- **Technology:** Custom LLMs trained on 1M+ clinical conversations; evidence-linked summaries
- **Results:** $212.5M Series D (2024); Epic integration; deployed at 50+ health systems
- **Pricing:** ~$150-300/clinician/month
- **Strengths:** Evidence-linked notes (traceable to conversation); Epic integration; rapid growth
- **Weaknesses:** US-focused; cloud-dependent; new company (founded 2018)
- **Gap vs. Our Module:** No Irish English; no on-premise option; no ICD-10-AM coding

### 2.3 Nabla (France) — Closest EU Competitor
- **What:** AI assistant for clinical documentation; ambient recording + note generation
- **Technology:** Custom LLMs; Whisper-based ASR; integrates with EU EHR systems
- **Results:** 2 hours/day saved per clinician; GDPR compliant; deployed in France and expanding EU
- **Pricing:** ~EUR 100-200/clinician/month
- **Regulatory:** CE marked; GDPR compliant
- **Strengths:** EU-focused; GDPR by design; multi-language support; lower cost than Nuance
- **Weaknesses:** Smaller than Nuance/Abridge; limited English language deployment; newer product
- **Gap vs. Our Module:** No Irish-specific features; not integrated with clinical AI modules

### 2.4 Suki AI
- **What:** Voice-enabled AI assistant; generates clinical notes from voice commands
- **Technology:** Custom NLP; voice commands for navigation and documentation; LLM for note generation
- **Results:** 72% reduction in documentation time; 12 minutes saved per encounter
- **Pricing:** ~$199/clinician/month
- **Strengths:** Voice command interface; specialty-specific templates; EHR integration
- **Weaknesses:** US-focused; voice command paradigm (less ambient than DAX)
- **Gap vs. Our Module:** Not ambient; US-only; no clinical AI integration

### 2.5 DeepScribe / Augmedix (Commure)
- **What:** AI medical scribe combining ambient capture with human QA
- **Technology:** AI + human review pipeline; guarantees quality through hybrid approach
- **Results:** Acquired by Commure (2024); deployed across 300+ sites
- **Strengths:** Human QA layer ensures accuracy; good for high-stakes specialties
- **Weaknesses:** Higher cost due to human involvement; slower turnaround; not fully automated
- **Gap vs. Our Module:** Hybrid (not fully AI); higher cost; no clinical AI integration

### 2.6 Amazon HealthScribe
- **What:** AWS service for clinical documentation; transcription + note generation via API
- **Technology:** AWS Bedrock + custom medical ASR; API-first approach
- **Results:** Available since 2023; integrated into multiple EHR vendors
- **Pricing:** Pay-per-use API pricing (~$0.02-0.05 per minute of audio)
- **Strengths:** Cost-effective; API-first; AWS ecosystem; scalable
- **Weaknesses:** No ambient recording solution; API only (need to build UI); US English focused
- **Gap vs. Our Module:** API-only (no UI); no Irish English; no clinical AI integration

---

## 3. SWOT Analysis

### Strengths
- **Integrated clinical intelligence:** Notes enriched with data from ED Triage, Sepsis, Oncology, Patient Journey — no competitor does this
- **On-premise capable:** Can run on hospital infrastructure (Whisper + Ollama/local LLM) for data sovereignty
- **Irish clinical terminology:** Designed for Irish medical language, NHS/HSE abbreviations, Irish referral patterns
- **ICD-10-AM coding:** Irish-specific coding (not US ICD-10-CM)
- **Cost-effective:** No per-clinician licensing; infrastructure cost only
- **FHIR-native output:** Notes structured as FHIR DocumentReference resources
- **Open architecture:** Modular (swap ASR, LLM, NER components independently)

### Weaknesses
- **No medical ASR training data:** Need Irish-accented medical speech data for fine-tuning
- **Quality assurance:** No human review layer (unlike Augmedix)
- **EHR integration:** FHIR output is planned but not connected to any EHR yet
- **LLM dependency:** Note quality depends on LLM capability (Ollama vs. a commercial LLM API)
- **No clinical conversation training data:** Can't match Abridge's 1M+ conversation dataset
- **Single-developer limitations:** Can't match R&D investment of Nuance/Microsoft

### Opportunities
- **HSE Year 1 priority:** "AI scribe tools to cut documentation time by up to 40%"
- **No incumbent in Ireland:** No ambient scribe deployed in Irish public hospitals
- **Clinician burnout crisis:** Documentation burden is a top driver of burnout in Irish hospitals
- **National EHR integration:** FHIR notes will integrate with upcoming National EHR
- **Irish English ASR gap:** No competitor has fine-tuned ASR for Irish-accented English
- **EU AI Act compliance:** On-premise capability is attractive for data sovereignty concerns

### Threats
- **Microsoft bundling:** DAX Copilot bundled with Microsoft 365 (HSE uses Microsoft)
- **Nuance/Dragon monopoly:** Dragon Medical is standard ASR in healthcare; switching costs high
- **Privacy concerns:** Recording patient conversations raises significant GDPR/consent issues
- **Clinical trust:** Clinicians may not trust AI-generated notes without review
- **Liability:** Who is responsible for errors in AI-generated clinical documentation?
- **Audio quality:** Hospital environments are noisy; ambient recording quality varies

---

## 4. Gap Identification

| Gap Area | Current Market | Our Opportunity |
|----------|---------------|-----------------|
| **Irish English ASR** | All optimized for US English; Nabla for French | Fine-tune Whisper on Irish medical speech |
| **Clinical AI integration** | All standalone documentation tools | Enrich notes with data from ED Triage, Sepsis, Oncology, Patient Journey |
| **ICD-10-AM coding** | US ICD-10-CM only; no Irish coding | ICD-10-AM + ACHI coding for HIPE compatibility |
| **On-premise deployment** | All cloud-based (except Nabla partially) | Full on-premise with Whisper + local LLM |
| **HSE guideline integration** | No Irish clinical guidelines in context | RAG with HSE/HIQA clinical guidelines |
| **FHIR-native notes** | Retrofit FHIR; most use proprietary formats | Native FHIR DocumentReference output |
| **Cost model** | Per-clinician licensing ($150-400/month) | Infrastructure cost only; no per-user fees |
| **Specialty templates (Irish)** | US specialty templates | Irish specialty-specific templates (consultant, registrar, SHO note formats) |

---

## 5. Peer-Reviewed Research & Algorithms

### 5.1 Key Papers

#### Medical Speech Recognition
1. **Radford et al. (2023)** "Robust Speech Recognition via Large-Scale Weak Supervision" — *ICML 2023*
   - Whisper: 680K hours of multilingual speech; WER 5.2% on medical conversations
   - Architecture: Encoder-decoder Transformer; multi-task: transcribe, translate, detect language
   - Key finding: Large-scale weak supervision beats supervised fine-tuning
   - DOI: 10.48550/arXiv.2212.04356

2. **Peng et al. (2023)** "Evaluation of Large Language Models for Clinical Note Generation" — *npj Digital Medicine*
   - GPT-4, Med-PaLM 2, clinical LLMs evaluated for note generation
   - GPT-4: 92% clinical accuracy; 85% completeness; 89% coherence (physician-rated)
   - Key finding: LLMs generate clinically acceptable notes but hallucinate 8-12% of the time
   - Recommendation: Clinical verification layer essential
   - DOI: 10.1038/s41746-023-00978-8

3. **Krishna et al. (2021)** "Generating SOAP Notes from Doctor-Patient Conversations Using Modular Summarization" — *ACL 2021*
   - End-to-end pipeline: ASR → section classification → abstractive summarization per SOAP section
   - Architecture: BART fine-tuned on ACI-BENCH clinical conversation dataset
   - ROUGE-L: 0.42 (Subjective), 0.38 (Objective), 0.35 (Assessment), 0.40 (Plan)
   - Key technique: Conversation act classification → route to correct SOAP section
   - DOI: 10.18653/v1/2021.acl-long.384

#### Clinical NER & Coding
4. **Yang et al. (2022)** "GatorTron: A Large Clinical Language Model" — *npj Digital Medicine*
   - 8.9B parameter model trained on 82B words of clinical text (including 2M clinical notes)
   - Clinical NER F1: 0.93 (medication), 0.91 (diagnosis), 0.89 (procedure)
   - Key finding: Scale matters for clinical NLP; 5x improvement over ClinicalBERT on rare entities
   - DOI: 10.1038/s41746-022-00742-2

5. **Edin et al. (2023)** "Automated Medical Coding on MIMIC-III and MIMIC-IV: A Critical Review" — *ACL 2023*
   - Comprehensive benchmark of ICD coding models on MIMIC
   - Best model: PLM-ICD (pre-trained language model for ICD coding)
   - Macro F1: 0.122 (full code), 0.587 (top-50 codes) on MIMIC-IV
   - Key finding: ICD coding remains extremely challenging; hierarchical approaches help
   - Architecture: BERT encoder + label attention + hierarchical code embeddings

6. **Biseda et al. (2024)** "LLMs for Automated ICD Coding: A Comparative Study" — *AMIA 2024*
   - GPT-4 vs. fine-tuned BERT vs. PLM-ICD for ICD-10 coding
   - GPT-4 (few-shot): Top-1 accuracy 67.3%; Top-5 accuracy 84.1%
   - Fine-tuned approach: PLM-ICD still best for full-code prediction
   - Key finding: LLMs excel at top-code selection; specialized models needed for complete coding
   - Recommended: Hybrid approach — LLM for candidate generation, specialized model for refinement

#### Clinical Document Quality
7. **Quiroz et al. (2022)** "Evaluating AI-Generated Clinical Notes: A Framework" — *JAMIA*
   - Proposed evaluation framework: accuracy, completeness, coherence, clinical relevance, safety
   - Evaluation by 20 physicians across 5 specialties
   - Key metrics: Critical information omission rate, hallucination rate, documentation time
   - Recommendation: Safety layer should flag high-risk omissions (allergies, medications, diagnoses)

8. **Lehman et al. (2023)** "Clinical Note Faithfulness Evaluation" — *Nature Medicine*
   - Hallucination detection in AI-generated clinical notes
   - Developed NLI-based faithfulness checker (checks note claims against source conversation)
   - Faithfulness score: 94.2% (GPT-4 generated), 97.1% (human), 91.5% (Llama-2)
   - Key technique: Natural Language Inference (NLI) for automated faithfulness verification

#### Privacy & Consent
9. **Moy et al. (2023)** "Ambient AI Documentation: Privacy and Ethical Considerations" — *Annals of Internal Medicine*
   - Patient consent models: opt-in vs. opt-out; notice requirements
   - GDPR implications: audio recording = personal data processing; needs explicit consent or legal basis
   - Key recommendation: Real-time processing + immediate deletion of audio (no storage)
   - De-identification: Remove PHI from transcripts before LLM processing

10. **Xiao et al. (2023)** "Federated Learning for Clinical NLP" — *Nature Machine Intelligence*
    - Privacy-preserving training of clinical NLP models across hospitals
    - Key technique: Differential privacy + federated averaging
    - Performance: 98% of centralized training quality with DP guarantees
    - Application: Train ASR/NER models across Irish hospitals without centralizing data

### 5.2 Adopted Algorithms (State-of-the-Art)

#### Medical ASR: Fine-tuned Whisper Large-v3
**Why chosen:** Best open-source ASR; supports fine-tuning for domain/accent; runs on-premise.

```
Architecture:
├── Base model: Whisper Large-v3 (1.55B params, multilingual)
├── Fine-tuning: LoRA (rank 16) on medical vocabulary
│   ├── Medical terminology corpus (drug names, anatomy, procedures)
│   ├── Irish-accented English speech samples (if available)
│   └── HSE/NHS clinical abbreviation expansion
├── Speaker diarization: pyannote.audio 3.0
│   ├── Segment audio into doctor/patient/other speakers
│   └── Map segments to conversation roles
├── Post-processing:
│   ├── Medical terminology correction (custom dictionary)
│   ├── Abbreviation expansion (HR→heart rate, RR→respiratory rate)
│   └── Number normalization (clinical values)
└── Output: Timestamped, speaker-labeled transcript
```

**Performance targets:** WER < 8% on medical conversations; latency < 2x real-time.

#### Note Generation: a commercial LLM API with Structured Prompting
**Why chosen:** Best clinical reasoning; structured output; high faithfulness; available via API with fallback to local Ollama.

```
Pipeline:
1. Input: Speaker-diarized transcript + patient context (from Patient Journey)
2. Context enrichment:
   ├── Patient demographics (from Patient Journey API)
   ├── Current medications (from Patient Journey API)
   ├── Recent vitals/labs (from Patient Journey API)
   ├── Active diagnoses (from Patient Journey API)
   ├── ED triage data (from ED Triage API, if ED encounter)
   └── Oncology risk (from Oncology AI API, if cancer patient)
3. Structured prompt with SOAP template:
   ├── System: "You are a clinical documentation assistant..."
   ├── Template: Irish clinical note format (consultant letter format)
   ├── Guidelines: HSE clinical documentation standards
   └── Output schema: JSON with SOAP sections + structured fields
4. LLM generation (a commercial LLM API primary, Ollama fallback)
5. Post-processing:
   ├── Faithfulness check (NLI-based: verify claims against transcript)
   ├── Safety check: allergies mentioned? medications reconciled? red flags?
   ├── PHI de-identification check
   └── ICD-10-AM code suggestion
6. Output: Structured clinical note + quality score + suggested codes
```

#### ICD-10-AM Auto-Coding: Hybrid LLM + PLM-ICD
**Why chosen:** LLM for candidate generation + specialized model for code refinement; best of both worlds.

```
Architecture:
├── Stage 1: LLM Candidate Generation
│   ├── Input: Generated clinical note text
│   ├── Prompt: "Extract top 10 ICD-10-AM diagnosis codes..."
│   ├── Output: Top 10 candidate codes with confidence
│   └── Coverage: ~85% of true codes in candidates
├── Stage 2: PLM-ICD Refinement
│   ├── Input: Note text + LLM candidates
│   ├── Model: BERT encoder + hierarchical label attention
│   ├── Training: MIMIC diagnoses mapped to ICD-10-AM
│   └── Output: Refined code list with probabilities
├── Stage 3: ACHI Procedure Coding
│   ├── Similar pipeline for procedure codes
│   └── Cross-reference with note's "Plan" section
└── Output: {primary_diagnosis: "M16.1", secondary: [...], procedures: [...]}
```

#### Clinical NER: Fine-tuned Bio-ClinicalBERT
**Why chosen:** Strong clinical entity recognition; fine-tunable; runs on-premise.

```
Entities extracted:
├── MEDICATION: drug name, dose, frequency, route
├── DIAGNOSIS: condition, ICD mappable
├── PROCEDURE: surgical/diagnostic procedure
├── ANATOMY: body part, laterality
├── LAB_VALUE: test name, value, unit, interpretation
├── VITAL_SIGN: vital type, value
├── SYMPTOM: symptom description, duration, severity
├── ALLERGY: allergen, reaction type
└── SOCIAL: smoking, alcohol, occupation, living situation

Architecture:
├── Base: Bio-ClinicalBERT (PubMed + MIMIC pre-trained)
├── Task head: Token classification (BIO tagging)
├── Fine-tuning: MIMIC clinical notes with i2b2 NER labels
├── Post-processing: Entity linking to SNOMED CT
└── Target F1: >0.88 for medications, >0.85 for diagnoses
```

#### Faithfulness Verification: NLI-Based Checker
**Why chosen:** Critical safety layer; catches hallucinations before clinician review.

```
Pipeline:
1. Decompose generated note into atomic claims
2. For each claim:
   ├── Search transcript for supporting evidence (BM25 + semantic search)
   ├── NLI model: entailed / contradicted / neutral
   └── If contradicted or neutral with no evidence: flag as potential hallucination
3. Compute faithfulness score: % of claims with evidence
4. Flag document if faithfulness < 95% or any critical claim unsupported
5. Critical claims: medication changes, allergy assertions, diagnosis assertions
```

---

## 6. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                Module 10: AI Clinical Scribe                      │
│                         Port 8210                                 │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │  ASR Engine     │  │  Note Generation │  │  Coding Engine  │  │
│  │                 │  │  Engine          │  │                 │  │
│  │ - Whisper v3    │  │ - a commercial LLM API     │  │ - LLM + PLM-ICD│  │
│  │ - Speaker       │  │ - SOAP template  │  │ - ICD-10-AM    │  │
│  │   diarization   │  │ - Context enrich │  │ - ACHI coding  │  │
│  │ - Medical vocab │  │ - Faithfulness   │  │ - SNOMED CT    │  │
│  └────────┬────────┘  └────────┬─────────┘  └───────┬─────────┘  │
│           │                     │                     │           │
│  ┌────────v─────────────────────v─────────────────────v─────────┐ │
│  │              Clinical NER Engine                              │ │
│  │  Bio-ClinicalBERT: medications, diagnoses, procedures,       │ │
│  │  allergies, vitals, symptoms → SNOMED CT linked              │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
│                              │                                    │
│  ┌──────────────────────────v───────────────────────────────────┐ │
│  │              Safety & Quality Layer                            │ │
│  │  NLI faithfulness check, allergy verification,                │ │
│  │  medication reconciliation, red flag detection                │ │
│  └──────────────────────────┬───────────────────────────────────┘ │
│                              │                                    │
│  ┌──────────────────────────v───────────────────────────────────┐ │
│  │              FHIR DocumentReference Output                    │ │
│  │  Structured note → FHIR resource for EHR integration         │ │
│  └───────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  Context Enrichment Sources:                                      │
│  ├── Patient Journey (8205): demographics, meds, vitals, labs    │
│  ├── ED Triage (8201): acuity, risk factors (if ED encounter)    │
│  ├── Oncology AI (8204): cancer risk, pathway (if oncology)      │
│  ├── Sepsis ICU (8202): sepsis risk, SOFA score (if ICU)         │
│  └── Bed Management (8208): admission context                    │
│                                                                   │
│  Output Consumers:                                                │
│  ├── Patient Journey (8205): structured notes for patient record │
│  ├── Clinical Chat (8206): documented encounters for context     │
│  ├── Waiting List (8209): referral letter processing             │
│  └── Clinical Audit (future): documentation quality metrics      │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Processing Pipeline (Per Encounter)

```
Audio Input (microphone / uploaded file)
         │
         ▼
┌─────────────────────┐
│ 1. Audio Processing  │  Whisper Large-v3 + pyannote diarization
│    ASR + Diarization │  Output: speaker-labeled transcript
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 2. Context Fetch     │  HTTP calls to Patient Journey, ED Triage,
│    (parallel)        │  Oncology, Sepsis APIs for patient context
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 3. NER Extraction    │  Bio-ClinicalBERT on transcript
│                      │  Output: structured entities
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 4. Note Generation   │  a commercial LLM API / Ollama with SOAP template
│                      │  Input: transcript + context + entities
│                      │  Output: structured clinical note
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 5. Quality & Safety  │  NLI faithfulness, medication reconciliation,
│    Verification      │  allergy check, red flag detection
│                      │  Output: quality score + flags
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 6. ICD Coding        │  LLM candidates + PLM-ICD refinement
│                      │  Output: ICD-10-AM + ACHI codes
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 7. FHIR Output       │  Package as FHIR DocumentReference
│                      │  Store in MongoDB + publish event
└─────────────────────┘
```

---

## 7. API Design

```
# ─── Transcription ───────────────────────────────────────
POST /transcribe                      # Upload audio → get transcript
POST /transcribe/stream               # WebSocket streaming transcription
GET  /transcribe/{session_id}         # Get transcript for session

# ─── Note Generation ─────────────────────────────────────
POST /generate-note                   # Generate note from transcript
POST /generate-note/from-text         # Generate note from text input (no audio)
GET  /note/{note_id}                  # Retrieve generated note
PUT  /note/{note_id}                  # Update/edit generated note
POST /note/{note_id}/approve          # Clinician approval (audit trail)

# ─── ICD Coding ──────────────────────────────────────────
POST /code                            # Auto-code from note text
GET  /code/suggestions/{note_id}      # Get coding suggestions for note
POST /code/validate                   # Validate code assignment

# ─── NER / Entity Extraction ────────────────────────────
POST /extract-entities                # Extract clinical entities from text
POST /extract-entities/batch          # Batch entity extraction

# ─── Templates ───────────────────────────────────────────
GET  /templates                       # List available note templates
GET  /templates/{specialty}           # Get specialty-specific template
POST /templates                       # Create custom template

# ─── Quality & Analytics ────────────────────────────────
GET  /quality/{note_id}               # Quality metrics for note
GET  /metrics/documentation-time      # Time savings metrics
GET  /metrics/coding-accuracy         # ICD coding accuracy tracking
GET  /metrics/faithfulness            # Hallucination rate tracking

# ─── System ──────────────────────────────────────────────
GET  /health                          # Health check
GET  /model-info                      # Model metadata
```

---

## 8. Implementation Plan

### Phase 1: ASR & NER Foundation (Week 1-3)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 1.1 | Set up Whisper Large-v3 inference pipeline | PyTorch, GPU |
| 1.2 | Implement speaker diarization with pyannote.audio | 1.1 |
| 1.3 | Build medical vocabulary post-processor (correction dict, abbreviation expansion) | 1.1 |
| 1.4 | Fine-tune Bio-ClinicalBERT NER on MIMIC clinical notes | MIMIC notes DB |
| 1.5 | Build entity linking pipeline (entities → SNOMED CT codes) | 1.4 |
| 1.6 | Create Irish clinical note templates (SOAP, consultant letter, discharge summary) | Manual |

### Phase 2: Note Generation & Coding (Week 3-5)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 2.1 | Build context enrichment module (HTTP calls to existing APIs) | Existing APIs |
| 2.2 | Implement a commercial LLM API integration with structured prompting | a commercial LLM API key |
| 2.3 | Implement Ollama fallback for on-premise deployment | Ollama |
| 2.4 | Build SOAP note generation pipeline | 2.1, 2.2 |
| 2.5 | Implement NLI-based faithfulness checker | 2.4 |
| 2.6 | Build ICD-10-AM coding pipeline (LLM + PLM-ICD hybrid) | 2.4 |
| 2.7 | Build ACHI procedure coding module | 2.6 |

### Phase 3: API & Safety (Week 5-7)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 3.1 | Build FastAPI app with all endpoints | Phase 2 |
| 3.2 | Implement audio upload and WebSocket streaming | 3.1, Phase 1 |
| 3.3 | Build safety layer (medication reconciliation, allergy check, red flags) | 3.1 |
| 3.4 | Implement FHIR DocumentReference output | 3.1 |
| 3.5 | Implement audit trail (EU AI Act: log all AI decisions) | 3.1 |
| 3.6 | Build clinician approval workflow | 3.1 |

### Phase 4: Dashboard & Integration (Week 7-9)

| Task | Description | Dependencies |
|------|-------------|--------------|
| 4.1 | Clinical Scribe dashboard page in React | Phase 3 |
| 4.2 | Audio recording interface with real-time transcript display | 4.1 |
| 4.3 | SOAP note editor with AI suggestions and inline editing | 4.1 |
| 4.4 | ICD code suggestion panel with search | 4.1 |
| 4.5 | Quality metrics dashboard | 4.1 |
| 4.6 | Integration: feed generated notes to Patient Journey | 3.4 |
| 4.7 | Integration: connect to Clinical Chat for documentation queries | 3.1 |

---

## 9. Irish Hospital Customization

### Note Templates (Irish)
```python
IRISH_NOTE_TEMPLATES = {
    "consultant_letter": {
        "sections": ["patient_details", "referral_reason", "history",
                     "examination", "investigations", "impression", "plan"],
        "format": "formal_letter",
        "addressee": "GP / referring_consultant"
    },
    "admission_note": {
        "sections": ["presenting_complaint", "history_presenting_complaint",
                     "past_medical_history", "drug_history", "allergies",
                     "social_history", "family_history", "systems_review",
                     "examination", "investigations", "impression", "plan"],
        "format": "structured"
    },
    "discharge_summary": {
        "sections": ["admission_details", "presenting_complaint", "diagnosis",
                     "investigations", "treatment", "procedures",
                     "discharge_medications", "follow_up", "gp_instructions"],
        "format": "hipe_compatible"
    },
    "ed_note": {
        "sections": ["triage_category", "presenting_complaint", "history",
                     "examination", "investigations", "diagnosis",
                     "treatment", "disposition", "safety_net"],
        "format": "structured"
    }
}
```

### Irish Clinical Abbreviations
```python
IRISH_MEDICAL_ABBREVIATIONS = {
    "SHO": "Senior House Officer",
    "SpR": "Specialist Registrar",
    "NCHD": "Non-Consultant Hospital Doctor",
    "MAU": "Medical Assessment Unit",
    "AMAU": "Acute Medical Assessment Unit",
    "OPD": "Outpatient Department",
    "A&E": "Accident and Emergency",
    "HIPE": "Hospital In-Patient Enquiry",
    "GMS": "General Medical Services",
    "DPS": "Drug Payment Scheme",
    "PCRS": "Primary Care Reimbursement Service",
}
```

---

## 10. File Structure

```
app_10_clinical_scribe/
├── __init__.py
├── backend/
│   ├── __init__.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application (port 8210)
│   │   └── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   ├── __init__.py
│   │   ├── whisper_asr.py       # Whisper Large-v3 ASR + diarization
│   │   ├── clinical_ner.py      # Bio-ClinicalBERT NER
│   │   ├── note_generator.py    # a commercial LLM API / Ollama note generation
│   │   ├── icd_coder.py         # Hybrid LLM + PLM-ICD coding
│   │   └── faithfulness.py      # NLI-based faithfulness checker
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── transcription.py     # ASR + diarization orchestrator
│   │   ├── note_engine.py       # Note generation orchestrator
│   │   ├── coding_engine.py     # ICD-10-AM / ACHI coding orchestrator
│   │   └── quality_engine.py    # Safety + quality verification
│   └── dataset/
│       ├── __init__.py
│       ├── build_dataset.py     # MIMIC notes → NER/coding training data
│       └── templates/           # Irish clinical note templates
│           ├── consultant_letter.json
│           ├── admission_note.json
│           ├── discharge_summary.json
│           └── ed_note.json
├── docs/
│   └── SYSTEM_DESIGN.md         # This document
└── tests/
    ├── __init__.py
    ├── test_asr.py
    ├── test_ner.py
    ├── test_note_generation.py
    ├── test_icd_coding.py
    └── test_faithfulness.py
```
