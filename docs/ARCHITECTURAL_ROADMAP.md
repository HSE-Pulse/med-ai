# MedAI Platform — Architectural Roadmap

**Date:** 2026-04-23
**Scope:** frameworks/tools remaining after the durable event broker (Kafka/Redpanda + MongoDB event_log) and per-service state persistence landed.

This document is a prioritised backlog. Each item names the gap it closes, the concrete framework to adopt, the code touch-points, and the value unlock (clinical-safety, HSE procurement, operational robustness, MLOps, developer experience).

Priorities:

- **P0** — safety, compliance or durability blocker before any production pilot.
- **P1** — adoption blocker for HSE / voluntary-hospital procurement.
- **P2** — operational robustness / developer velocity.
- **P3** — long-horizon / nice-to-have.

---

## P0 — Before any production deployment

### 1. Distributed tracing — OpenTelemetry + Jaeger / Tempo
**Gap:** Every admission now fans out through 6+ services (Digital Twin cascade); when something goes wrong, the only debugging signal is cross-service log correlation via structlog.
**Framework:** OpenTelemetry SDK (Python) + OTLP exporter, Jaeger or Grafana Tempo as the backend.
**Touch-points:**
- Instrument `shared/api/base.py::create_app()` to install the FastAPI OTel middleware by default.
- Instrument `shared/integration/service_client.py` to propagate trace context via headers on every cross-service POST.
- Instrument `shared/integration/digital_twin.py::_safe_call` to create a span per module call.
- Add `ServiceClient._inject_trace_headers()` for W3C Trace Context.
**Unlock:** visual per-request traces, latency breakdown per module, root-cause of failed admissions without grep.

### 2. Prometheus metrics + Grafana dashboards
**Gap:** `/sim/metrics-history` is per-service and in-memory. No unified SLI/SLO view across the platform.
**Framework:** `prometheus-client` Python SDK → scraped by Prometheus → Grafana for dashboards.
**Touch-points:**
- `shared/api/base.py::create_app()` mounts a `/metrics` endpoint with default FastAPI counters (requests-total, request-duration-seconds, in-progress).
- Per-module custom counters: NEWS2 alerts fired, bed allocations, PET breaches, MARL actions.
- Scrape config + Grafana dashboards live in `ops/prometheus/` and `ops/grafana/`.
**Unlock:** SLOs like "p95 admission-pipeline latency < 2 s" become measurable and alertable.

### 3. Schema registry + contract tests
**Gap:** Service A publishes `{patient_id, acuity}`; Service B consumes `{patient_id, acuity_level}`. Silent breakage.
**Framework:** JSON Schema registry (Redpanda Schema Registry is already exposed at :18081 in the Kafka compose) + Pact contract tests.
**Touch-points:**
- Generate schemas from the Pydantic models in `shared/integration/event_bus.py::TOPICS`.
- Publish each schema to the registry on CI.
- Pact: every service declares consumer expectations; Pact broker validates on PR.
**Unlock:** event-shape drift caught at CI rather than production.

### 4. Kafka / broker operational hardening
**Gap:** Current `docker-compose.kafka.yml` is a single-node dev Redpanda. Production needs a 3-node cluster, ACLs, TLS.
**Framework:** Redpanda Kubernetes Operator or Strimzi for Apache Kafka on Kubernetes.
**Touch-points:**
- Helm chart in `ops/helm/redpanda/` with 3 brokers, PVCs, mTLS certs.
- Principal-based ACLs (one principal per service).
- Consumer-group-lag alerts wired to Prometheus.
**Unlock:** broker loses a node, platform keeps running.

### 5. Clinical Safety Case + Hazard Log
**Gap:** No HIQA-aligned risk management file (DCB 0129 / 0160 equivalents). Review flagged as P0.
**Framework:** ISO 14971 template + custom Hazard Log spreadsheet/tool.
**Touch-points:**
- `docs/CLINICAL_SAFETY/` — Hazard Log, Safety Requirements, Safety Officer sign-off flow.
- One hazard per high-risk AI module (ED Triage, Sepsis ICU, Oncology AI, Deterioration).
- Periodic review cadence tied to model drift metrics.
**Unlock:** required for HIQA registration and CSO sign-off.

### 6. FRIA (Fundamental Rights Impact Assessment, EU AI Act Art. 27)
**Gap:** Required for HSE as a public authority deploying high-risk AI. Missing entirely.
**Framework:** EU AI Act FRIA template + DPIA cross-reference.
**Touch-points:**
- `docs/COMPLIANCE/FRIA.md` covering: data protection, non-discrimination, transparency, human oversight, remedies.
- Per-module fundamental-rights analysis (paediatric, maternity, deprivation-index subgroups).
**Unlock:** mandatory artefact before HSE tender submission.

### 7. Model drift + calibration monitoring
**Gap:** AUROC is reported at training time; no monitoring of production drift (feature distribution, score calibration, subgroup performance).
**Framework:** Evidently AI or WhyLabs/WhyLogs; Brier score + Expected Calibration Error for calibration.
**Touch-points:**
- `shared/ml/monitoring/` — drift detector wraps every `predict()`.
- Reliability diagrams per model, per quarter.
- Alerts to `#medai-mlops` on drift exceeding threshold.
**Unlock:** meets EU AI Act Art. 72 (post-market monitoring) + closes the "0.998 AUROC" credibility gap.

---

## P1 — Adoption blockers for Irish procurement

### 8. Service mesh — Istio or Linkerd
**Gap:** No mTLS between services in simulation mode. Service-to-service identity is implicit (trust via localhost). Review flagged as P1.
**Framework:** Linkerd (simpler, smaller blast radius) or Istio (more features).
**Touch-points:**
- Sidecar injection on every service deployment.
- mTLS by default, SPIFFE-style SVID identity per service.
- Traffic policies (retry, circuit-break, rate-limit) moved from code into sidecar config.
**Unlock:** zero-trust security posture required by HSE.

### 9. Secrets management — HashiCorp Vault (or Azure Key Vault)
**Gap:** `FERNET_KEY`, `MONGO_URI`, `KAFKA_BOOTSTRAP` all read from env vars. Production needs rotation + audit.
**Framework:** Vault Agent sidecar for service-side, Vault API for bootstrap.
**Touch-points:**
- `shared/utils/secrets.py` — abstraction over env-var / Vault / Azure Key Vault.
- Automatic rotation hook for Fernet keys (with key-ID in payload for backwards decrypt).
**Unlock:** HIQA security evidence + credential rotation SLO.

### 10. Saga coordinator for multi-step workflows
**Gap:** Digital Twin admission cascade has 6 steps; if step 4 fails after step 3 allocated a bed, no compensation.
**Framework:** Temporal.io or AWS Step Functions (Camunda for on-prem).
**Touch-points:**
- `shared/integration/saga.py` — declare each cascade step + compensating action.
- Migrate `DigitalTwinOrchestrator.process_admission` to a Temporal workflow.
- Retries, timeouts, and deterministic replay come for free.
**Unlock:** reliable multi-service workflows under partial failure; audit trail of every step.

### 11. SMART-on-FHIR auth on the FHIR Gateway
**Gap:** `app_19_fhir` has no auth. HSE national messaging requires SMART-on-FHIR launch.
**Framework:** `fhirclient` (SMART Python client) + Keycloak as the OAuth2/OIDC server.
**Touch-points:**
- `app_19_fhir/backend/app/auth.py` — SMART standalone launch + EHR launch flows.
- Keycloak realm in `ops/keycloak/medai-realm.json`.
- Scopes: `patient/*.read`, `user/*.read`, `launch/patient`, …
**Unlock:** HSE national Shared Care Record integration.

### 12. IHI (Individual Health Identifier) service
**Gap:** Health Identifiers Act 2014 makes IHI mandatory for cross-provider exchange. Missing.
**Framework:** IHE PIX/PDQ profile over FHIR; new 22nd microservice.
**Touch-points:**
- `app_22_ihi/` — PIX query/response, PDQ patient demographic lookup.
- Integration with HSE IHI directory service (stubbed in simulation).
- Consent Management integration before any IHI is stored.
**Unlock:** legally required for HSE integration.

### 13. Healthlink adapter (HL7 v2 over MPLS)
**Gap:** Irish GP messaging still runs on HL7 v2. Missing.
**Framework:** Mirth Connect (or NextGen Connect Integration Engine) as the v2↔FHIR bridge.
**Touch-points:**
- `ops/mirth/channels/` — Healthlink inbound/outbound channels.
- `app_19_fhir` exposes the v2-to-FHIR converted MedicationRequest/Condition.
**Unlock:** GP discharge summaries + referrals; Sláintecare integrated care.

### 14. Consent Management Service (the missing 22nd / 23rd service)
**Gap:** Implied consent under Art. 9(2)(h) is declared globally. No mechanism for explicit consent (research, AI training, cross-border transfer).
**Framework:** Custom microservice + FHIR `Consent` resource.
**Touch-points:**
- New service at port 8222 — `app_22_consent/`.
- Every prediction endpoint checks consent before returning a result.
- DPO console (task 16) consumes its audit trail.
**Unlock:** DPC audit readiness; unlocks research-grade data sharing.

### 15. MLOps — drift detection, A/B testing, shadow deploy
**Gap:** `ModelRegistry` exists; no A/B, no shadow mode, no canary, no automated retraining cadence.
**Framework:** MLflow (model registry) + Seldon Core / KServe (inference), Evidently (drift).
**Touch-points:**
- Every prediction service wraps model in a MLflow-served proxy.
- `shadow: true` flag routes a copy of traffic to a candidate model for silent comparison.
- Quarterly retraining pipeline in Kubeflow.
**Unlock:** safe continuous model improvement; post-market monitoring evidence.

### 16. DPO Console + cross-module DPIA
**Gap:** Per-module DPIA exists (Art. 35); no unified cross-module DPIA and no DPO dashboard.
**Framework:** Dedicated React page consuming `/gdpr/*` endpoints from app_17.
**Touch-points:**
- `dashboard/src/pages/DPOConsole.tsx` — SAR queue, breach queue, DPIA dashboard, erasure request tracker.
- `app_17_gdpr` new endpoint: `GET /gdpr/dpia/cross-module` — joins all per-module DPIAs.
- 72-hour DPC breach countdown clock automation.
**Unlock:** first DPC audit pass.

### 17. Waiting List NTPF integration
**Gap:** `app_09_waiting_list` isn't wired to National Treatment Purchase Fund.
**Framework:** CSV import + future REST API when NTPF publishes one.
**Touch-points:**
- `app_09_waiting_list/backend/adapters/ntpf.py` — scheduled NTPF ingest.
- P1/P2/P3 surgical priority codes on every waiting-list entry.
- SDU chronological validation workflow.
**Unlock:** Scheduled-Care Transformation Programme alignment.

---

## P2 — Operational robustness & developer velocity

### 18. Centralised logging — Loki or ELK
**Gap:** Logs are per-service stdout. Cross-service incident analysis requires grep.
**Framework:** Grafana Loki (lighter, Prometheus-native) or Elastic Stack.
**Touch-points:**
- `shared/utils/logging.py` already uses structlog — add a JSON formatter + Promtail shipper.
- One Grafana dashboard per service domain (clinical, operations, compliance).
**Unlock:** mean-time-to-diagnose drops by 10×.

### 19. Feature flag service
**Gap:** MARL on/off, model versions, deployment modes are env-var-gated. No runtime toggle without redeploy.
**Framework:** Unleash (OSS) or LaunchDarkly.
**Touch-points:**
- `shared/integration/feature_flags.py` — client SDK wrapper.
- Digital Twin pipeline modules already toggleable via `enable_module`/`disable_module`; promote to feature flags with audit trail.
**Unlock:** safe progressive rollout; dark launch for new models.

### 20. Clinician override feedback loop → retraining
**Gap:** Clinician overrides on AI predictions are not captured as training signal.
**Framework:** Event-driven — every dashboard override emits an `override_submitted` event.
**Touch-points:**
- New topic `override_submitted` in the event broker.
- `app_18_xai` subscribes + logs to `overrides` collection.
- Quarterly retraining pipeline consumes overrides as weighted samples.
**Unlock:** model accuracy improves from real-world deployment; AI Act post-market requirement.

### 21. Bias monitoring (subgroup analysis)
**Gap:** No performance tracking by age band, sex, or Pobal HP Index (Irish deprivation proxy).
**Framework:** Fairlearn + Evidently subgroup reports.
**Touch-points:**
- Monthly per-subgroup AUROC / Brier score calibration reports.
- Pobal HP Index lookup from patient address.
**Unlock:** closes AI Act Art. 15 fairness requirement.

### 22. SBOM + container signing
**Gap:** No Software Bill of Materials. HSE procurement requires SBOM.
**Framework:** Syft (SBOM generation) + Cosign (signing) + Grype (vuln scan).
**Touch-points:**
- CI pipeline step in `.github/workflows/build.yml`.
- Publish SBOM attestation alongside each container image.
**Unlock:** HSE procurement gate.

### 23. Data medallion + quality — bronze/silver/gold
**Gap:** `app_07_data_ingestion` has no bronze/silver/gold structure. Data quality not monitored.
**Framework:** Great Expectations or Soda Core; Delta Lake storage.
**Touch-points:**
- `data/bronze/` (raw MIMIC + Irish), `data/silver/` (cleaned + PHI-scrubbed), `data/gold/` (feature store).
- Great Expectations suites on every silver → gold transform.
**Unlock:** data quality dashboard; HIQA-grade ingestion pipeline.

### 24. Chaos engineering
**Gap:** No testing of service-failure resilience.
**Framework:** Chaos Mesh (Kubernetes) or Litmus.
**Touch-points:**
- Weekly chaos experiments: kill Bed Management during peak census sync; drop Kafka messages 1% of the time.
- Resilience SLO: full admission pipeline must succeed despite any one service being down.
**Unlock:** discovers the review's remaining concurrency bugs before production does.

### 25. Blue/green + canary deployment
**Gap:** Deployment story is manual.
**Framework:** Argo Rollouts on Kubernetes.
**Touch-points:**
- Progressive rollout: 5% → 25% → 100% over 30 min; automatic rollback on SLO violation.
**Unlock:** zero-downtime + safe model promotion.

### 26. Centralised alerting — PagerDuty / Opsgenie
**Gap:** Alerts fire into logs, not a human.
**Framework:** Alertmanager routes to PagerDuty / Opsgenie; on-call rota.
**Touch-points:**
- Page on: platform-wide SLO breach, critical deterioration unacknowledged > 5 min, model drift alert.

---

## P3 — Long-horizon / nice-to-have

### 27. Patient portal (read-only, consent-gated, IHI-keyed)
**Framework:** Next.js + SMART-on-FHIR app.
**Unlock:** Sláintecare patient-centric vision.

### 28. Mobile / tablet role-specific views
**Framework:** React Native (existing dashboard has Recharts which is web-only).
**Unlock:** point-of-care clinical use.

### 29. Irish-language (Gaeilge) dashboard variant
**Framework:** i18next or FormatJS.
**Unlock:** Irish language requirements in public services (Official Languages Act).

### 30. Offline mode for critical-care scenarios
**Framework:** Service Workers + IndexedDB mirror of the patient's chart.
**Unlock:** resilience to network outages.

### 31. WCAG 2.1 AA accessibility audit
**Framework:** axe-core + Pa11y in CI.
**Unlock:** public-sector accessibility compliance (EU Web Accessibility Directive).

### 32. NIMIS radiology adapter
**Framework:** DICOM + HL7 v2 bridge, likely via Mirth.
**Unlock:** radiology ordering + image review inside the dashboard.

### 33. NAS transport booking adapter
**Framework:** REST client + structured request ticket.
**Unlock:** discharge planning completeness.

### 34. Adverse Event Reporting (NIMS / STARSWeb)
**Framework:** Event subscriber → STARSWeb web-form automation (Puppeteer/Playwright).
**Unlock:** HIQA reporting requirement.

### 35. Pharmacy / e-prescribing integration (HPRA)
**Framework:** FHIR `MedicationRequest` + HPRA MURS reporting hook.
**Unlock:** medication-reconciliation safety (P0 clinical harm vector at discharge).

---

## Suggested 12-month sequencing

**Quarter 1 (months 0–3) — Durability & Observability (P0 foundation)**
1. Distributed tracing (OTel + Jaeger)
2. Prometheus metrics + Grafana dashboards
3. Centralised logging (Loki)
4. Drift + calibration monitoring
5. Clinical Safety Case v1

**Quarter 2 (months 3–6) — Compliance & Adoption (P0/P1)**
6. FRIA v1
7. Schema registry + Pact contract tests
8. Service mesh (Linkerd) + mTLS
9. Vault secrets management
10. DPO Console + cross-module DPIA
11. Consent Management service

**Quarter 3 (months 6–9) — Clinical Integration (P1)**
12. SMART-on-FHIR auth
13. IHI service
14. Healthlink HL7 v2 adapter
15. NTPF integration
16. Saga coordinator (Temporal) for admission cascade
17. Pharmacy / medication reconciliation (new service)

**Quarter 4 (months 9–12) — Scale & Safety (P2/P3)**
18. Broker hardening (3-node Redpanda cluster)
19. MLOps (MLflow + Seldon, shadow deploy, A/B)
20. Bias monitoring
21. SBOM + container signing
22. Chaos engineering
23. Blue/green + canary with Argo Rollouts
24. PagerDuty alerting + SLOs
25. Data medallion (bronze/silver/gold) + Great Expectations
26. Feature flag service (Unleash)
27. Clinician override feedback loop

---

## What's already landed in the current codebase

- Durable event broker: Kafka (Redpanda compose) + MongoDB `event_log` fallback.
- Per-service state persistence: Bed Management, ED Flow, Discharge Lounge, Hospital Ops action_log, Deterioration (snapshots + replay-since-sim-start).
- Circuit breakers on every inter-service call (closed / open / half-open).
- Score-aware debouncer (rises fire immediately, stable / declining suppressed).
- Idempotency middleware + `Idempotency-Key` header propagation through Digital Twin.
- Bed category taxonomy (isolation / paediatric / bariatric / stroke / maternity / …).
- PEWS + IMEWS alongside NEWS2 (NCEC NCG #1 + #4).
- SBAR-captured escalation acknowledgement loop + time-to-ack metric.
- FHIR R4 gateway expanded to 11 resource types.

Everything on the roadmap above builds on those primitives — no rewrites needed, only additive integration.
