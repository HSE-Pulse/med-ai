# MedAI Deployment — Helm + Linkerd

This directory contains everything needed to deploy the MedAI platform
to a Kubernetes cluster with Linkerd as the service mesh. Structured as:

```
deploy/
├── helm/
│   ├── medai-service/          # Parametric chart — one per microservice
│   │   ├── Chart.yaml
│   │   ├── values.yaml         # Defaults
│   │   └── templates/
│   │       ├── deployment.yaml
│   │       ├── service.yaml
│   │       ├── configmap.yaml
│   │       ├── hpa.yaml
│   │       └── pdb.yaml
│   └── medai-platform/         # Umbrella chart — all 17 services
│       ├── Chart.yaml          # Depends on medai-service × 17 aliases
│       └── values.yaml         # Per-service resource/HPA overrides
└── linkerd/
    ├── 00-namespace.yaml                  # medai namespace + auto-inject
    ├── 10-service-profiles.yaml           # per-route retries + timeouts
    ├── 20-authorization-policies.yaml     # zero-trust east-west
    └── 30-circuit-breaker-policies.yaml   # native outbound breakers
```

## Prereqs

- Kubernetes 1.27+ cluster
- Linkerd 2.15+ (`linkerd install --crds | kubectl apply -f -`)
- Metrics Server (for HPA)
- MongoDB operator or external Mongo URI
- Redpanda operator or external Kafka URL
- Redis (deploy via `helm install redis bitnami/redis` or external)

## Deploy

```bash
# 1. Install Linkerd control plane
linkerd install --crds | kubectl apply -f -
linkerd install | kubectl apply -f -
linkerd viz install | kubectl apply -f -                 # optional UI

# 2. Create namespace + base policies
kubectl apply -f deploy/linkerd/00-namespace.yaml

# 3. Create secrets (NOT via Helm — use SealedSecrets or external-secrets)
kubectl create secret generic medai-mongo-credentials \
  --from-literal=MONGO_URI='mongodb://...'             -n medai
kubectl create secret generic medai-kafka-credentials \
  --from-literal=KAFKA_SASL_USER=medai                 -n medai

# 4. Deploy all 17 services
helm dep update deploy/helm/medai-platform
helm install medai deploy/helm/medai-platform -n medai

# 5. Apply Linkerd policies after pods are up
kubectl apply -f deploy/linkerd/10-service-profiles.yaml
kubectl apply -f deploy/linkerd/20-authorization-policies.yaml
kubectl apply -f deploy/linkerd/30-circuit-breaker-policies.yaml
```

## Per-service characteristics

| Service | Scaling | Replicas | Rationale |
|---|---|---|---|
| ed-triage | HPA | 2–6 | Stateless, inference-heavy |
| sepsis-icu | HPA | 2–4 | LGBM/LSTM models |
| hospital-ops | fixed | 1 | DES + MARL singleton, in-memory state |
| oncology-ai | HPA | 1–3 | Sparse workload |
| patient-journey | HPA | 2–6 | Read-heavy (Mongo indexed queries) |
| clinical-chat | HPA | 2–8 | Ollama LLM — burst capacity needed |
| data-ingestion | fixed | 1 | Sim engine + DT orchestrator singleton |
| bed-management | fixed | 1 | In-memory bed state (until CDC) |
| waiting-list | HPA | 2–4 | NTPF priority queries |
| clinical-scribe | HPA | 2–6 | NER + SOAP generation |
| ed-flow | HPA | 2–6 | Real-time patient tracking |
| erp | HPA | 1–3 | Config + finance lookups |
| trolley-watch | HPA | 1–3 | Low-volume alerting |
| gdpr | HPA | 2–4 | Consumes all topics (audit trail) |
| xai | HPA | 1–4 | SHAP compute is CPU-intensive |
| fhir | HPA | 2–6 | FHIR R4 gateway |
| deterioration | HPA | 2–6 | NEWS2/PEWS/IMEWS scoring |
| discharge-lounge | HPA | 1–3 | Low-volume coordinator |

## Service-mesh features active after applying Linkerd manifests

- **mTLS**: all pod-to-pod traffic encrypted automatically (namespace has `linkerd.io/inject: enabled`)
- **Zero-trust east-west**: only SA identities listed in `MeshTLSAuthentication` can reach each Service (see `20-authorization-policies.yaml`)
- **Per-route timeouts**: 1s (deterioration scoring) → 120s (chat streaming). Calls exceeding timeout fail fast at the mesh.
- **Retries on idempotent GETs**: with retry budgets (cap 20% of traffic, minimum 10/s) to prevent retry storms
- **Circuit breakers**: consecutive-failures eviction with exponential backoff (2s → 30s per service)
- **Golden metrics**: success rate, latency p50/p95/p99 per route, scrapable by Prometheus (via `linkerd viz`)

## Not yet covered (roadmap)

- CDC operator (Debezium) for Mongo → Kafka changelog — needed for bed_management and hospital_ops to go horizontally scalable (see per-service table above, "fixed" rows)
- External Secrets Operator for vault-backed secrets (current manifests expect SealedSecrets or manual `kubectl create secret`)
- NetworkPolicies as a second layer under Linkerd auth (belt-and-braces for CIS benchmark)
- GPU node pool scheduling (for clinical-chat LLM inference via Ollama/vLLM)
- Flagger or Argo Rollouts for automated canary deploys

## Local smoke test (kind)

```bash
# 1-line smoke test in kind — verifies the charts + Linkerd install
kind create cluster --name medai
linkerd install --crds | kubectl apply -f -
linkerd install | kubectl apply -f -
kubectl apply -f deploy/linkerd/00-namespace.yaml

# Lint + template-render all 17 services
helm lint deploy/helm/medai-platform
helm template medai deploy/helm/medai-platform -n medai \
    --dry-run=server | kubectl apply -n medai -f -

# Verify
kubectl get pods,svc,hpa -n medai
linkerd viz stat deployments -n medai
```
