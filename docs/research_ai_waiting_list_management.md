# Deep Research: AI-Powered Hospital Waiting List Management & Surgical Scheduling Optimization

**Research Date:** April 2026
**Scope:** Peer-reviewed literature (2021-2026), market landscape, state-of-the-art algorithms

---

## SECTION 1: PEER-REVIEWED PAPERS

### Paper 1: Dynamic Surgical Prioritization with ML and XAI

| Field | Detail |
|-------|--------|
| **Title** | Dynamic Surgical Prioritization: A Machine Learning and XAI-Based Strategy |
| **Authors** | (Multiple authors) |
| **Journal** | Technologies (MDPI), Vol. 13, Issue 2 |
| **Year** | 2025 |
| **DOI/URL** | https://www.mdpi.com/2227-7080/13/2/72 |
| **Algorithms** | LightGBM for predictive modeling; stochastic simulations for dynamic variable evolution; SHAP (SHapley Additive exPlanations) for local and global interpretability |
| **Key Results** | Integrated framework captures temporal evolution of dynamic prioritization scores. XAI layer ensures transparency for clinicians. Demonstrated that dynamic scoring outperforms static priority assignment by accounting for competitive interactions between patients on the same list. |
| **Relevance** | Directly addresses ML-based surgical waiting list prioritization with explainability -- core to clinical adoption. |

---

### Paper 2: Managing Surgical Waiting Lists Through Dynamic Priority Scoring

| Field | Detail |
|-------|--------|
| **Title** | Managing surgical waiting lists through dynamic priority scoring |
| **Authors** | Jack Powers, James M. McGree, David Grieve, Ratna Aseervatham, Suzanne Ryan, Paul Corry |
| **Journal** | Health Care Management Science, Vol. 26, pp. 533-557 |
| **Year** | 2023 |
| **DOI** | 10.1007/s10729-023-09648-1 |
| **Algorithms** | Dynamic Priority Scoring (DPS) system combining waiting time with clinical urgency factors; mathematical modeling of priority evolution over time |
| **Key Results** | DPS enables patients to progress on waiting list at a rate relative to clinical need, improving equity. More transparent and objective than static clinical urgency categorization. Demonstrates that combining time-based and clinical factors produces fairer prioritization than either alone. |
| **Relevance** | Foundation paper for equitable, dynamic waiting list management -- directly applicable to priority scoring engines. |

---

### Paper 3: ML Predict-Then-Optimize for Elective Orthopedic Surgery Scheduling

| Field | Detail |
|-------|--------|
| **Title** | Using Machine Learning to Predict-Then-Optimize Elective Orthopedic Surgery Scheduling to Improve Operating Room Utilization: Retrospective Study |
| **Authors** | (Multiple authors) |
| **Journal** | JMIR Medical Informatics, 2025;1:e70857 |
| **Year** | 2025 |
| **DOI/URL** | https://medinform.jmir.org/2025/1/e70857 |
| **Algorithms** | Two-stage approach: (1) ML prediction of surgical duration, (2) scheduling optimization using predicted durations. Trained on 302,490 total knee arthroplasty and 196,942 total hip arthroplasty cases. |
| **Key Results** | ML-based duration prediction significantly outperforms historical averages. Two-stage predict-then-optimize paradigm improves OR utilization by reducing idle time and overtime. |
| **Relevance** | Exemplifies the predict-then-optimize paradigm that is becoming standard for surgical scheduling. |

---

### Paper 4: AI in Medical Referrals Triage Based on Clinical Prioritization Criteria

| Field | Detail |
|-------|--------|
| **Title** | Artificial intelligence in medical referrals triage based on Clinical Prioritization Criteria |
| **Authors** | Ahmad Abdel-Hafez, Melanie Jones, Maziiar Ebrahimabadi, Cathi Ryan, Steve Graham, Nicola Slee, Bernard Whitfield |
| **Journal** | Frontiers in Digital Health |
| **Year** | 2023 |
| **DOI** | 10.3389/fdgth.2023.1192975 |
| **Algorithms** | NLP pipeline: Amazon Comprehend Medical for entity extraction, BiLSTM for named entity recognition, classical ML classifiers for prioritization. Text preprocessing with gensim, NLTK. |
| **Dataset** | 17,378 ENT referrals (5,688 pediatric, 11,690 adult) from two Queensland hospitals (2019-2022). Only 9.6% included specified Clinical Prioritization Criteria. |
| **Key Results** | 53.8% agreement between referral categories and predictions. Positioned as clinical decision support rather than autonomous triage. Highlighted the challenge of inconsistent referral letter quality. |
| **Relevance** | Key paper for NLP-based referral triage; reveals both potential and current limitations. |

---

### Paper 5: Improving Musculoskeletal Care with AI-Enhanced Triage of Referral Letters

| Field | Detail |
|-------|--------|
| **Title** | Improving musculoskeletal care with AI enhanced triage through data driven screening of referral letters |
| **Authors** | (Multiple authors) |
| **Journal** | npj Digital Medicine (Nature) |
| **Year** | 2025 |
| **DOI/URL** | https://www.nature.com/articles/s41746-025-01495-4 |
| **Algorithms** | ML pipeline for referral letter screening; identifies rheumatoid arthritis, osteoarthritis, fibromyalgia, and long-term care needs from unstructured text |
| **Dataset** | 8,044 referral letters from 5,728 patients across 12 clinics |
| **Key Results** | Demonstrated effective automated prioritization of musculoskeletal referrals, reducing clinician triage burden. Multi-condition classification from free-text referrals. |
| **Relevance** | High-impact journal publication showing real-world NLP triage at scale across multiple clinics. |

---

### Paper 6: ML-Based Integrated Scheduling for Elective and Emergency Patients

| Field | Detail |
|-------|--------|
| **Title** | Machine learning based integrated scheduling and rescheduling for elective and emergency patients in the operating theatre |
| **Authors** | (Multiple authors) |
| **Journal** | Annals of Operations Research (Springer) |
| **Year** | 2023 (online) / 2024 (print) |
| **DOI/URL** | https://link.springer.com/article/10.1007/s10479-023-05168-x |
| **Algorithms** | Genetic Algorithm (GA) and Particle Swarm Optimization (PSO) for scheduling; ML for case duration prediction; integrated rescheduling for emergency disruptions |
| **Key Results** | Demonstrated that integrated approach handling both elective and emergency cases outperforms sequential scheduling. Handles real-time rescheduling when emergency cases arrive. |
| **Relevance** | Addresses the critical real-world challenge of scheduling disruption from emergency cases. |

---

### Paper 7: Statistical Models vs. Machine Learning for Competing Risks

| Field | Detail |
|-------|--------|
| **Title** | Statistical models versus machine learning for competing risks: development and validation of prognostic models |
| **Authors** | (Multiple authors) |
| **Journal** | BMC Medical Research Methodology |
| **Year** | 2023 |
| **DOI/URL** | https://link.springer.com/article/10.1186/s12874-023-01866-z |
| **Algorithms** | Cause-specific Cox regression, Fine-Gray subdistribution hazard model, Random Survival Forest for competing risks, DeepHit |
| **Key Results** | Compared traditional statistical and ML approaches for competing risk scenarios. ML models (particularly RSF) showed comparable or slightly better discrimination but traditional models maintained better calibration. Trade-offs between interpretability and flexibility discussed. |
| **Relevance** | Essential methodology paper for modeling deterioration during surgical wait (where competing events include: surgery performed, condition deterioration, death, self-discharge). |

---

### Paper 8: Dynamic Operation Room Scheduling with Explainable AI and Fuzzy Inference

| Field | Detail |
|-------|--------|
| **Title** | A dynamic operation room scheduling (DORS) strategy based on explainable AI and fuzzy interface engine |
| **Authors** | (Multiple authors) |
| **Journal** | Artificial Intelligence Review (Springer) |
| **Year** | 2025 |
| **DOI/URL** | https://link.springer.com/article/10.1007/s10462-025-11366-9 |
| **Algorithms** | Two-layer architecture: (1) Explainable AI for feature selection and importance ranking, (2) Fuzzy Inference Engine for dynamic intraday scheduling decisions |
| **Key Results** | Provides intraday rescheduling capability. XAI layer ensures clinician trust. Fuzzy logic handles uncertainty in case durations and resource availability. |
| **Relevance** | Combines XAI with fuzzy decision-making for real-time OR scheduling -- addresses the "last mile" problem of intraday adjustments. |

---

### Paper 9: FAIM -- Fairness-Aware Interpretable Modeling for Healthcare ML

| Field | Detail |
|-------|--------|
| **Title** | FAIM: Fairness-aware interpretable modeling for trustworthy machine learning in healthcare |
| **Authors** | (Multiple authors) |
| **Journal** | Patterns (Cell Press) |
| **Year** | 2024 |
| **DOI/URL** | https://www.sciencedirect.com/science/article/pii/S2666389924002095 |
| **Algorithms** | Fairness-aware model selection framework; adversarial debiasing; fairness metrics (demographic parity, equalized odds, calibration across groups); interpretable model architectures |
| **Key Results** | Significant improvement in fairness metrics without compromising predictive performance. Interpretability enables domain experts to participate in fairness assessment. Framework is generalizable across clinical use cases. |
| **Relevance** | Directly applicable to ensuring equitable waiting list prioritization across demographic groups. |

---

### Paper 10: Multi-Agent Reinforcement Learning for OR Scheduling Under Uncertainty

| Field | Detail |
|-------|--------|
| **Title** | Multi-Agent Reinforcement Learning for Intraday Operating Rooms Scheduling under Uncertainty |
| **Authors** | (Multiple authors) |
| **Journal** | arXiv preprint (December 2025) |
| **DOI/URL** | https://arxiv.org/html/2512.04918 |
| **Algorithms** | Cooperative Markov Game formulation; MARL with centralized training, decentralized execution (CTDE); each OR modeled as an independent agent |
| **Key Results** | Handles stochastic case durations and emergency arrivals. Decentralized execution enables real-time decision-making per OR. Outperforms rule-based and single-agent RL baselines in utilization and patient throughput. |
| **Relevance** | Cutting-edge approach to real-time OR scheduling; represents the frontier of RL-based surgical scheduling. |

---

### Paper 11 (Bonus): NLP Systematic Review for Outpatient Referral Triage

| Field | Detail |
|-------|--------|
| **Title** | Systematic literature review and narrative synthesis of the use of natural language processing to triage outpatient referrals |
| **Authors** | (Multiple authors) |
| **Journal** | Frontiers in Health Services |
| **Year** | 2026 |
| **DOI/URL** | https://www.frontiersin.org/journals/health-services/articles/10.3389/frhs.2026.1797583/full |
| **Scope** | Systematic review of NLP-based models for urgency prioritization and referral classification; articles up to February 2024. Covers deep learning, traditional ML, and rule-based approaches. |
| **Relevance** | Comprehensive landscape view of NLP for referral triage -- essential for understanding which approaches work in which clinical contexts. |

---

### Paper 12 (Bonus): Enhancing Surgery Scheduling with Metaheuristic Optimization

| Field | Detail |
|-------|--------|
| **Title** | Enhancing Surgery Scheduling in Health Care Settings With Metaheuristic Optimization Models: Algorithm Validation Study |
| **Authors** | (Multiple authors) |
| **Journal** | JMIR Medical Informatics, 2025;1:e57231 |
| **Year** | 2025 |
| **DOI/URL** | https://medinform.jmir.org/2025/1/e57231 |
| **Algorithms** | Metaheuristic optimization (genetic algorithms, simulated annealing, tabu search) for multi-objective surgical scheduling |
| **Key Results** | Validated across real hospital data; demonstrated practical feasibility of metaheuristic approaches for complex multi-constraint scheduling. |

---

## SECTION 2: MARKET RESEARCH -- SIMILAR PRODUCTS

### 2.1 LeanTaaS iQueue

| Attribute | Detail |
|-----------|--------|
| **Company** | LeanTaaS (Santa Clara, CA) |
| **Products** | iQueue for Operating Rooms, iQueue for Infusion Centers, iQueue for Inpatient Beds, iQueue for Surgical Clinics |
| **Technology** | AI/ML-powered predictive analytics and prescriptive recommendations; generative AI via "iQueue Autopilot" (launched 2023) |
| **Key Features** | Smart Match (surgeon-to-slot optimization based on practice patterns, hospital goals, case info); Predictive Nudges (alerts for unlikely-to-fill blocks); Case Length Accuracy forecasting; Real-Time View (mobile-first perioperative dashboard, 2024); Block and open time management |
| **Outcomes** | Inova Health: 46% fill rate improvement; general: reduced block waste, improved OR utilization by 5-15% |
| **Pricing** | Enterprise SaaS, custom pricing (not publicly disclosed). Estimated $200K-$1M+ annually depending on hospital size. GenAI features included at no additional cost for existing customers. |
| **Market Position** | Market leader in OR scheduling optimization. 1,500+ hospitals. Strong brand recognition in US market. |
| **URL** | https://leantaas.com/products/operating-rooms/ |

---

### 2.2 Qventus Perioperative Solution

| Attribute | Detail |
|-----------|--------|
| **Company** | Qventus (Mountain View, CA) |
| **Products** | Perioperative Solution, Patient Flow, Discharge |
| **Technology** | AI, ML, and behavioral science; predictive analytics for scheduling and resource allocation; "TimeFinder" for real-time OR time discovery |
| **Key Features** | ML-based slot prioritization based on surgeon performance history; unused block identification; automated case scheduling workflows; real-time OR availability dashboard |
| **Outcomes** | Banner Health (33 hospitals, 6 states): +13 cases per surgical robot per month in 6-month pilot; idle time reduction up to 34.8%; HonorHealth: improved patient flow |
| **Pricing** | Enterprise SaaS, custom pricing. Not publicly disclosed. |
| **Market Position** | Strong challenger to LeanTaaS; differentiates on behavioral science integration. Growing rapidly with major health system deployments (2024-2025). |
| **URL** | https://www.qventus.com/ |

---

### 2.3 Optum / UnitedHealth Group -- Crimson AI

| Attribute | Detail |
|-----------|--------|
| **Company** | Optum (subsidiary of UnitedHealth Group) |
| **Products** | Crimson AI (predictive analytics for OR), Crimson AI IQ Surgical Cost module, AI Marketplace |
| **Technology** | Predictive analytics for OR scheduling optimization; identifies underused block time; flags cases likely to run long; suggests OR time reassignment |
| **Key Features** | OR utilization analytics; surgical case duration prediction; block time optimization; cost analytics via Surgical Cost module; broader AI Marketplace (launched 2024) for payer/provider AI apps |
| **Outcomes** | Children's National Hospital (Washington DC): scheduling lead time shortened by >1 week, case volume increased by 7% |
| **Pricing** | Enterprise, part of broader Optum analytics suite. Pricing not publicly disclosed. |
| **Market Position** | Leverages UnitedHealth's massive data assets (2,000+ AI engineers, 1,000+ AI use cases). Strong in the US payer-provider integrated space. |
| **URL** | https://business.optum.com/en/data-analytics/performance/surgical-cost.html |

---

### 2.4 NHS England Waiting List AI Initiatives

| Attribute | Detail |
|-----------|--------|
| **Initiative** | NHS AI Expansion (March 2024); GIRFT Further Faster 20; NHS Federated Data Platform |
| **AI Tools Deployed** | (1) A&E admission prediction tool (3-week forecast, used by ~50 NHS organizations); (2) Smart Centre (ML-based DNA prediction using age, deprivation, attendance history, demographics, appointment timing); (3) DrDoctor AI system (30% reduction in missed appointments); (4) RPA for waiting list administration |
| **GIRFT FF20 Results** | Trusts in FF20 programme: 4.2% waiting list reduction vs. 1.4% nationally (Oct 2024-Oct 2025). Applied to outpatient follow-ups and urgent/emergency care. |
| **Policy** | NHS Federated Data Platform works with GIRFT to embed best practice. AI tools endorsed by GIRFT for deployment. |
| **Estimated Savings** | DrDoctor AI: potential savings of 300M GBP/year from reduced missed appointments |
| **URL** | https://www.england.nhs.uk/2024/03/nhs-ai-expansion-to-help-tackle-missed-appointments-and-improve-waiting-times/ |

---

### 2.5 Huma (formerly Medopad)

| Attribute | Detail |
|-----------|--------|
| **Company** | Huma Therapeutics (London, UK) |
| **Products** | Huma Cloud Platform, Huma Workspace, Hospital at Home (RPM) |
| **Technology** | Cloud platform with GenAI integrations; remote patient monitoring; virtual wards; eConsult (acquired Oct 2024) |
| **Waiting List Features** | Manages elective waitlists to help patients "wait well"; monitors patients on cardiac and orthopaedic surgery waiting lists at home; symptom tracking, deterioration alerts, remote vital signs |
| **Outcomes** | 77 enterprise customers signed up within 48 hours of Huma Cloud launch; deployed across NHS for surgical waitlist monitoring; Series D: $80M raised (July 2024) |
| **Pricing** | Enterprise SaaS; available on NHS Digital Marketplace (G-Cloud) |
| **Market Position** | Strong in remote patient monitoring for surgical waiting lists. Unique positioning in "wait well" programmes. Integrated into NHS via eConsult acquisition. |
| **URL** | https://www.huma.com/ |

---

### 2.6 DrDoctor

| Attribute | Detail |
|-----------|--------|
| **Company** | DrDoctor (London, UK) |
| **Products** | HybridOS platform: Patient Engagement Platform, Patient-Led Booking, Accessing Care (PIFU), Waiting List Validation |
| **Technology** | ML-based DNA prediction (Smart Centre); automated patient communication; two-way scheduling workflows; NHS App integration |
| **Key Features** | Waiting list validation, triage, and stratification at scale; clinical risk understanding for prioritization; patient-initiated follow-up (PIFU); digital correspondence; partial booking with two-way automation; 91% patient recommendation rate |
| **Outcomes** | Guy's and St Thomas': 17.2% DNA rate reduction, 2.6M GBP savings in year 1; live in NHS App with 30+ NHS Trusts; acute, mental health, and community services; potential 300M GBP/year savings nationally |
| **Pricing** | NHS-focused SaaS; available on NHS frameworks. Pricing via direct engagement. |
| **Market Position** | UK market leader in patient engagement for waiting list management. Strong NHS trust base. GIRFT best-practice endorsed. |
| **URL** | https://www.drdoctor.co.uk/ |

---

### 2.7 Patients Know Best (PKB)

| Attribute | Detail |
|-----------|--------|
| **Company** | Patients Know Best (social enterprise, UK) |
| **Products** | Personal Health Record (PHR) platform |
| **Technology** | Patient-controlled health record; NHS App integration (first PHR to integrate); customizable questionnaires/PROMs with scoring/branching logic; collaborative care plans |
| **Waiting List Features** | Self-management tools (symptom tracking, journals, measurements); patient-reported outcome measures while waiting; educational resources for self-care; alerts for when to contact healthcare team |
| **Scale** | ~4 million registered patients (April 2024); ~100,000 new registrations/month |
| **Pricing** | Available on NHS Digital Marketplace |
| **Market Position** | Leading PHR in UK; strong in patient empowerment and self-management during wait periods. GDPR compliant, NHS login integrated. |
| **URL** | https://patientsknowbest.com/ |

---

### 2.8 Intouch with Health (VitalHub UK)

| Attribute | Detail |
|-----------|--------|
| **Company** | Intouch with Health (part of VitalHub Corp, est. 1999) |
| **Products** | Intouch Platform, Flow Manager |
| **Technology** | Digital patient flow management; check-in kiosks; real-time dashboard; EPR/PAS integration |
| **Key Features** | Flow Manager (digital dashboard for patient journey management); self-check-in kiosks (80% self-check-in rate); pre-appointment activity scheduling (reduces appointment time by 35 min); supports face-to-face, virtual, and remote appointments |
| **Scale** | Processes ~56% of all outpatient attendances in the UK |
| **Outcomes** | Saves patients 4.5 minutes per check-in; 35-minute reduction in average appointment time; significant administrative burden reduction |
| **Market Position** | Dominant in UK outpatient flow management. Partnered with DrDoctor for integrated digital outpatient solution. |
| **URL** | https://www.intouchwithhealth.co.uk/ |

---

### 2.9 Irish and EU Solutions

| Attribute | Detail |
|-----------|--------|
| **Initiative** | Ireland "AI for Care" Strategy (2026-2030); HSE AI and Automation Centre of Excellence; 2024 Waiting List Action Plan |
| **Technology Deployed** | Robotic Process Automation (RPA) for waiting list admin across 20 hospitals; automated DNA reporting, SMS processing, appointment booking, batch cancellations |
| **Policy Framework** | Aligned to EU Artificial Intelligence Act; governance framework for clinical AI. National Shared Care Record, virtual wards, and HSE App in development. |
| **Key Vendors** | Microsoft Dragon Copilot (clinical documentation, available in Ireland Oct 2025); domestic RPA deployments via HSE |
| **Challenges** | Over 900,000 patients on waiting lists (as of 2024); digital infrastructure still being built; no single AI-native waiting list platform yet deployed nationally |
| **URL** | https://www.gov.ie/en/department-of-health/publications/ai-for-care-the-artificial-intelligence-ai-strategy-for-healthcare-in-ireland-2026-2030/ |

---

### 2.10 Other Notable Vendors

| Vendor | Focus | Notes |
|--------|-------|-------|
| **Surgical Information Systems (SIS)** | ASC management software | Leading ASC platform (scheduling, charting, billing, documentation). Not AI-native for optimization. URL: https://www.sisfirst.com/ |
| **Leap Rail** | Surgical scheduling | AI algorithms for case duration prediction (70%+ accuracy improvement). URL: https://www.leaprail.com/ |
| **TAGNOS** | OR orchestration | Real-time OR planning, block scheduling management, workflow intelligence. URL: https://www.tagnos.com/ |
| **LiveData** | PeriOp planning | Surgical calendar optimization, curated interface for surgical schedules. URL: https://www.livedata.com/ |
| **OpMed.ai** | OR scheduling AI | AI-driven surgical scheduling optimization. URL: https://www.opmed.ai/ |

---

## SECTION 3: STATE-OF-THE-ART ALGORITHMS

### 3.1 Clinical Priority Scoring with ML

**Problem:** Rank patients on elective surgical waiting lists balancing clinical urgency, equity, wait duration, and resource constraints.

#### Best Current Approaches:

**A. Dynamic Priority Scoring (DPS) with Gradient Boosting**
- **Architecture:** LightGBM or XGBoost for priority score prediction; stochastic simulation for temporal score evolution
- **Input Features:** Clinical urgency category, wait time, comorbidity burden (Charlson/Elixhauser), age, pain scores, functional status (PROMs), social deprivation index, procedure complexity
- **Training:** Supervised learning on historical outcomes (time-to-surgery, deterioration events, cancellations). Labels derived from expert panel consensus on "correct" priority ordering.
- **Key Innovation:** Priority score is not static -- it evolves over time via simulation of competing risks (deterioration, improvement, death)
- **Interpretability:** SHAP values at patient level enable clinicians to understand why a patient is ranked where they are
- **Metrics:** Concordance index (C-index) for ranking quality; Kendall's tau for agreement with expert rankings; fairness metrics across demographic groups

**B. Multi-Criteria Decision Analysis (MCDA) + ML Hybrid**
- **Architecture:** AHP/ANP for criteria weighting (urgency, equity, resource fit) + ML model for individual criterion scoring
- **Innovation:** Structured elicitation of clinical values (how much weight to give urgency vs. wait time vs. equity) combined with data-driven individual risk scoring
- **Methods:** Fuzzy TOPSIS for handling uncertainty; Group AHP for consensus among clinicians
- **Metrics:** Spearman correlation with expert rankings; sensitivity analysis across weight configurations

**C. Competing Risks Survival Models**
- **Architecture:** Fine-Gray subdistribution hazard model or Random Survival Forest with competing risks
- **Events:** Surgery performed (target), clinical deterioration, patient cancellation, death
- **Innovation:** Models probability of each competing event over time, enabling prediction of "who will deteriorate if they wait longer"
- **Training Data:** Historical waiting list data with event timestamps and types
- **Metrics:** Time-dependent AUROC; Brier score; calibration plots per event type

---

### 3.2 Surgical Scheduling Optimization

**Problem:** Assign surgical cases to ORs, time slots, and surgical teams while minimizing overtime, idle time, cancellations, and maximizing throughput.

#### Best Current Approaches:

**A. Predict-Then-Optimize (Two-Stage)**
- **Stage 1 (Predict):** ML model (gradient boosting, neural network) predicts surgical case duration from: procedure code, surgeon ID, patient comorbidities, anesthesia type, ASA score, time of day
  - Best models: XGBoost, LightGBM (outperform historical surgeon averages by 15-25% in MAE)
  - Training data: 100K-300K+ historical cases
- **Stage 2 (Optimize):** Mixed Integer Linear Programming (MILP) or Constraint Programming (CP) for slot assignment
  - Objective: minimize weighted sum of overtime + idle time + patient waiting
  - Constraints: OR availability, surgeon availability, equipment, ICU bed availability, staff ratios, case sequencing preferences
  - Solvers: IBM CPLEX, Google OR-Tools, Gurobi
- **Metrics:** OR utilization (target: 80-85%), overtime hours, first-case start time compliance, turnover time, cancellation rate

**B. Robust / Distributionally Robust Optimization**
- **Architecture:** Two-Stage Robust Surgery Scheduling Problem (2SRSSP)
- **Innovation:** Models uncertainty in case durations as an ambiguity set rather than point estimates. Seeks scheduling that performs well under worst-case duration realizations.
- **Formulation:** MILP with uncertainty sets; Column and Constraint Generation (C&CG) algorithm
- **Recent advance:** Direct MILP formulations now outperform C&CG for moderate-sized problems

**C. Multi-Agent Reinforcement Learning (MARL)**
- **Architecture:** Cooperative Markov Game; each OR is an agent
- **Training:** Centralized Training, Decentralized Execution (CTDE)
  - Centralized critic observes global state (all OR statuses, pending cases)
  - Decentralized actors make local scheduling decisions per OR
- **State space:** Current case progress, queue of pending cases, staff availability, time remaining
- **Action space:** Accept/delay next case, swap cases between ORs
- **Reward:** Negative of (overtime + idle time + patient wait time + cancellation penalty)
- **Innovation:** Handles stochastic case durations and emergency arrivals in real-time
- **Metrics:** Total utilization, patient throughput, overtime hours vs. rule-based baselines

**D. Metaheuristic Approaches**
- **Algorithms:** Genetic Algorithm (GA), Particle Swarm Optimization (PSO), Simulated Annealing, Tabu Search
- **Use case:** Large-scale scheduling (many ORs, many cases) where exact optimization is intractable
- **Innovation:** Integrated scheduling + rescheduling for elective + emergency patients
- **Metrics:** Makespan, weighted tardiness, resource utilization

---

### 3.3 Deterioration Risk Prediction During Wait

**Problem:** Predict which patients on a surgical waiting list will deteriorate, enabling proactive intervention or priority escalation.

#### Best Current Approaches:

**A. Deep Survival Models**
- **DeepSurv:** Neural network extension of Cox proportional hazards; handles high-dimensional features
- **DeepHit:** Directly learns the joint distribution of survival times and competing events; no proportional hazards assumption
- **DySurv (2024):** Dynamic deep learning model with conditional variational inference; handles time-varying covariates
- **Architecture:** Input layer (clinical features, imaging features, lab trends) -> hidden layers (128-256 units, ReLU) -> output (hazard function or CIF)
- **Training:** Partial likelihood loss (DeepSurv) or ranking loss + calibration loss (DeepHit)
- **Metrics:** C-index (discrimination), integrated Brier score (calibration), time-dependent AUROC

**B. Random Survival Forest for Competing Risks**
- **Architecture:** Ensemble of survival trees; each tree splits on features that maximize survival difference
- **Competing risk extension:** Separate cause-specific hazard forests, or integrated competing risk splitting criteria
- **Advantages:** Non-parametric, handles interactions and non-linearities, feature importance via permutation
- **Compared to deep models:** Similar discrimination, better calibration, more interpretable
- **Metrics:** Cause-specific C-index, cumulative incidence function (CIF) calibration

**C. Fine-Gray Subdistribution Hazard Model**
- **Architecture:** Semi-parametric regression directly modeling subdistribution hazard for the event of interest (deterioration) while accounting for competing events
- **Advantages:** Well-understood statistical properties; straightforward covariate effect interpretation
- **Limitations:** Proportional subdistribution hazards assumption; may miss complex interactions
- **Use case:** Baseline model and interpretable benchmark for regulatory/clinical acceptance

---

### 3.4 NLP for Referral Letter Parsing and Triage

**Problem:** Automatically extract clinical information from unstructured referral letters and classify urgency/priority.

#### Best Current Approaches:

**A. Transformer-Based Models (BERT Family)**
- **Recommended models:** ClinicalBERT (pretrained on MIMIC-III clinical notes), BioBERT (pretrained on PubMed + PMC), PubMedBERT, Bio-Clinical-BERT
- **Architecture:** BERT encoder (12 layers, 768-dim hidden) fine-tuned for:
  - Named Entity Recognition (NER): extract diagnoses, symptoms, medications, procedures
  - Text Classification: urgency category (routine/soon/urgent)
  - Relation Extraction: link symptoms to diagnoses
- **Training approach:**
  1. Start with domain-pretrained BERT (ClinicalBERT or BioBERT)
  2. Fine-tune on labeled referral letters (typically 1,000-10,000 annotated examples)
  3. Use active learning to efficiently expand training set
- **Performance:** Bio-Clinical-BERT achieves AUROC 0.82-0.85 for hospitalization prediction from triage notes (superior to BOW-LR-TF-IDF baselines)
- **Metrics:** Micro/Macro F1, AUROC, precision/recall per urgency class

**B. LLM-Based Approaches (2024-2026)**
- **Architecture:** GPT-4, a commercial LLM, or open-source LLMs (Llama, Mistral) with in-context learning or fine-tuning
- **Approach:** Zero-shot or few-shot prompting for referral classification; structured output extraction
- **Advantages:** No labeled training data needed for zero-shot; handles diverse referral formats
- **Challenges:** Hallucination risk; cost at scale; latency; data privacy (on-premise deployment needed)
- **Emerging pattern:** LLM for initial extraction -> traditional ML classifier for final prioritization (hybrid approach)

**C. Entity Extraction Pipeline**
- **Step 1:** Medical NER (Amazon Comprehend Medical, SciSpacy, or fine-tuned BERT NER)
- **Step 2:** Negation detection (NegEx, NegBERT)
- **Step 3:** Temporal reasoning (when did symptoms start)
- **Step 4:** Structured feature vector from extracted entities
- **Step 5:** Classification model (LightGBM, logistic regression) on structured features
- **Advantage:** More interpretable; each extraction step can be validated clinically

---

### 3.5 Fairness-Aware ML in Healthcare Scheduling

**Problem:** Ensure AI-based prioritization does not systematically disadvantage patients based on race, ethnicity, gender, socioeconomic status, or geography.

#### Best Current Approaches:

**A. Fairness Metrics for Healthcare Scheduling**
| Metric | Definition | Application |
|--------|-----------|-------------|
| Demographic Parity | P(high priority) equal across groups | Ensure no group systematically deprioritized |
| Equalized Odds | TPR and FPR equal across groups | Equal accuracy of urgency classification |
| Calibration Across Groups | Predicted risk = observed risk within each group | Priority scores mean the same thing for all groups |
| Counterfactual Fairness | Prediction unchanged if sensitive attribute flipped | Individual-level fairness test |

**B. Algorithmic Debiasing Techniques**
- **Pre-processing:** Resampling, reweighting training data to balance representation
- **In-processing:**
  - Adversarial debiasing: Train adversary to predict protected attribute from model output; penalize main model if adversary succeeds
  - Fairness constraints in optimization: Add demographic parity or equalized odds as constraints to the scheduling optimization MILP
  - FAIM framework: Fairness-aware interpretable model selection with domain expert involvement
- **Post-processing:** Threshold calibration per group; reject option classification

**C. The FAIM Framework (State of the Art, 2024)**
- **Architecture:** Model selection framework that jointly optimizes predictive performance and fairness
- **Process:**
  1. Define fairness criteria with clinical stakeholders
  2. Train multiple candidate models with varying fairness-performance trade-offs
  3. Visualize Pareto frontier of fairness vs. performance
  4. Domain experts select acceptable operating point
  5. Interpretability layer (SHAP/LIME) enables ongoing monitoring
- **Key insight:** No single "fair" model exists -- fairness is context-dependent and requires clinical judgment

**D. Practical Considerations**
- Protected attributes (race, ethnicity) may be proxied by zip code, insurance status, or hospital site -- must audit for proxy discrimination
- Obermeyer et al. (2019) showed healthcare cost as a proxy for need disadvantaged Black patients -- lesson: choose prediction targets carefully (predict clinical need, not cost)
- Regular fairness audits with stratified performance reporting are essential
- Federated learning can help train on diverse populations without centralizing sensitive data

---

## SECTION 4: SUMMARY COMPARISON TABLE -- PRODUCTS

| Product | Geography | AI/ML | Scheduling | Waiting List | Patient Self-Management | NHS/Public Health |
|---------|-----------|-------|------------|-------------|------------------------|-------------------|
| LeanTaaS iQueue | US | Yes (deep) | Yes (core) | Partial | No | No |
| Qventus | US | Yes (deep) | Yes (core) | Partial | No | No |
| Optum Crimson AI | US | Yes | Yes | Analytics | No | No |
| DrDoctor | UK | Yes (ML) | Yes | Yes (core) | Yes | Yes (30+ Trusts) |
| Huma | UK/Global | Yes | No | Yes (monitoring) | Yes (core) | Yes (NHS) |
| PKB | UK | Minimal | No | Partial | Yes (core) | Yes (NHS App) |
| Intouch/VitalHub | UK | Minimal | Partial | Partial | Partial | Yes (56% OP) |
| SIS | US | Minimal | Yes | No | No | No |
| TAGNOS | US | Yes | Yes | No | No | No |
| Ireland HSE | Ireland | RPA only | No | RPA automation | No | Yes (public) |

---

## SECTION 5: KEY GAPS AND OPPORTUNITIES

1. **No integrated platform** exists that combines: (a) NLP referral triage, (b) dynamic priority scoring, (c) deterioration prediction, (d) scheduling optimization, and (e) fairness monitoring in a single system.

2. **Deterioration prediction during wait** is severely under-researched. Competing risk survival models exist in oncology and transplant contexts but have not been widely applied to general surgical waiting lists.

3. **Fairness in surgical prioritization** is discussed theoretically but few deployed systems include fairness auditing or debiasing.

4. **Ireland and EU** are early in the journey -- no AI-native waiting list optimization tool has been deployed nationally. The "AI for Care" strategy (2026-2030) creates an opening.

5. **The "wait well" concept** (monitoring patients during their wait, predicting deterioration, enabling self-management) is a growing area where Huma and PKB have early positions but lack AI sophistication.

6. **Real-time rescheduling** combining MARL or RL with traditional optimization is at the research frontier but not yet in commercial products.

---

## Sources

### Peer-Reviewed Papers
- [Dynamic Surgical Prioritization: ML and XAI-Based Strategy](https://www.mdpi.com/2227-7080/13/2/72)
- [Managing surgical waiting lists through dynamic priority scoring](https://link.springer.com/article/10.1007/s10729-023-09648-1)
- [ML Predict-Then-Optimize for Elective Orthopedic Surgery Scheduling](https://medinform.jmir.org/2025/1/e70857)
- [AI in medical referrals triage based on Clinical Prioritization Criteria](https://www.frontiersin.org/journals/digital-health/articles/10.3389/fdgth.2023.1192975/full)
- [Improving musculoskeletal care with AI enhanced triage of referral letters](https://www.nature.com/articles/s41746-025-01495-4)
- [ML-based integrated scheduling for elective and emergency patients](https://link.springer.com/article/10.1007/s10479-023-05168-x)
- [Statistical models vs ML for competing risks](https://link.springer.com/article/10.1186/s12874-023-01866-z)
- [Dynamic OR scheduling with explainable AI and fuzzy inference](https://link.springer.com/article/10.1007/s10462-025-11366-9)
- [FAIM: Fairness-aware interpretable modeling for healthcare](https://www.sciencedirect.com/science/article/pii/S2666389924002095)
- [Multi-Agent RL for Intraday OR Scheduling](https://arxiv.org/html/2512.04918)
- [NLP for outpatient referral triage: Systematic review](https://www.frontiersin.org/journals/health-services/articles/10.3389/frhs.2026.1797583/full)
- [Metaheuristic Optimization for Surgery Scheduling](https://medinform.jmir.org/2025/1/e57231)
- [NLP of Referral Letters for Low Back Pain Triage](https://www.jmir.org/2024/1/e46857)
- [Task-Specific Transformer-Based Language Models in Health Care](https://pmc.ncbi.nlm.nih.gov/articles/PMC11612605/)
- [AI-driven healthcare: Fairness survey](https://pmc.ncbi.nlm.nih.gov/articles/PMC12091740/)
- [Algorithm fairness in AI for medicine](https://pmc.ncbi.nlm.nih.gov/articles/PMC10632090/)
- [DySurv: Dynamic deep learning for survival analysis](https://academic.oup.com/jamia/advance-article/doi/10.1093/jamia/ocae271/7906103)
- [Deep learning for survival analysis: A review](https://link.springer.com/article/10.1007/s10462-023-10681-3)
- [Mixed Integer Programming for Robust Surgery Scheduling](https://optimization-online.org/2025/01/mixed-integer-linear-programming-formulations-for-robust-surgery-scheduling/)
- [Multi-resource constrained elective surgical scheduling with Nash equilibrium](https://www.nature.com/articles/s41598-025-87867-y)

### Market Research
- [LeanTaaS iQueue for Operating Rooms](https://leantaas.com/products/operating-rooms/)
- [LeanTaaS iQueue Autopilot (GenAI)](https://www.businesswire.com/news/home/20230605005284/en/LeanTaaS-Announces-iQueue-Autopilot-First-Ever-Generative-AI-Hospital-Operations-Solution)
- [Qventus Perioperative Solution](https://www.qventus.com/)
- [Banner Health + Qventus](https://hitconsultant.net/2024/04/15/banner-health-optimizes-surgery-scheduling-with-qventus/)
- [Optum Crimson AI for OR](https://www.beckershospitalreview.com/healthcare-information-technology/ai/how-optums-ai-tool-helped-boost-or-use-by-7/)
- [NHS AI Expansion for Waiting Times](https://www.england.nhs.uk/2024/03/nhs-ai-expansion-to-help-tackle-missed-appointments-and-improve-waiting-times/)
- [GIRFT Further Faster 20 Results](https://gettingitrightfirsttime.co.uk/report-shows-waiting-lists-reduced-three-times-faster-during-the-first-year-of-girfts-further-faster-20-programme/)
- [DrDoctor Platform](https://www.drdoctor.co.uk/)
- [Guy's and St Thomas' + DrDoctor](https://www.drdoctor.co.uk/resources/case-studies/guys-and-st-thomas-improves-waiting-list-management-with-drdoctor-full)
- [Huma Digital Health](https://www.huma.com/)
- [Huma Cloud Platform Launch](https://medcitynews.com/2024/08/humas-evolution-from-patient-monitoring-apps-to-cloud-platform-with-ai-chops/)
- [Patients Know Best](https://patientsknowbest.com/)
- [Intouch with Health / VitalHub](https://www.intouchwithhealth.co.uk/)
- [Ireland AI for Care Strategy 2026-2030](https://www.gov.ie/en/department-of-health/publications/ai-for-care-the-artificial-intelligence-ai-strategy-for-healthcare-in-ireland-2026-2030/)
- [Ireland Waiting List RPA Rollout](https://www.pulseit.news/irish-digital-health/waiting-list-robotic-process-automation-being-rolled-out-across-health-regions/)
- [Surgical Information Systems](https://www.sisfirst.com/)
- [Leap Rail Surgical Scheduling](https://www.leaprail.com/)
- [TAGNOS OR Orchestration](https://www.tagnos.com/)
