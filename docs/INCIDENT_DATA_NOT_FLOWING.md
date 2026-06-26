# Incident report — dashboard pages not picking up data from sim start

**Date:** 2026-04-23
**Pages affected:** `/ed-flow`, `/hospital-ops`, `/bed-management`
**Symptom:** dashboard shows no admitted patients, no bed occupancy, no staffing
activity even though simulation reports 4 active patients, 468 vitals, 5 transfers.

---

## Root cause: SSL_CERT_FILE env var points at a file that doesn't exist

In the conda `mitiosis` environment, `SSL_CERT_FILE` is set to

    C:\Users\Harishankar\miniconda3\envs\mitiosis/ssl/cacert.pem

but the actual cert bundle for that env lives at

    C:\Users\Harishankar\miniconda3\envs\mitiosis\Library\ssl\cacert.pem

The `/ssl/` path (missing `Library/`) doesn't exist. `httpx.AsyncClient()` — which
`shared.integration.service_client` uses for every cross-service call — reads
`SSL_CERT_FILE` at client-construction time and calls
`ssl.create_default_context(cafile=...)`. When the file is missing, that raises
`FileNotFoundError` before a single HTTP byte leaves the process.

The Digital Twin's `process_admission` pipeline calls 6+ services per admission.
Every single call crashed at client construction, so:

1. `ed_flow` / `bed_management` / `ed_triage` / `sepsis_icu` / etc never saw any
   Digital Twin payload
2. Each failure counted against the per-service circuit breaker
3. Breakers opened after 5 consecutive failures
4. Breakers half-opened after 30 s, each half-open retry hit the same SSL error,
   re-opened the breaker — permanent open state
5. Dashboard polled `/ed-state`, `/beds/summary`, `/staffing-recommendations` —
   all returned "empty" responses because no downstream state had ever been
   populated
6. Meanwhile, raw MIMIC-style data (admissions, transfers, chartevents) was
   being written directly to MongoDB by the sim engine — which is why the
   simulation *looked* alive, but the AI modules had no idea anything was
   happening.

### Why it was invisible

- `_safe_call` in the Digital Twin swallows failures to DEBUG-level logging so
  the cascade continues on partial failure. This is the right behaviour for
  production — but in dev, it hides root-cause exceptions.
- `cross_service_call_failed` warnings went to stderr but didn't include the
  exception type/message by default — just a `service=...` field.
- The initial 4 admissions DID populate `MIMIC_SIM.admissions` etc, which is
  what I saw when I inspected collections and said "sim is producing data".
  The data was just going into raw MongoDB, not into the downstream services'
  in-memory state.

---

## Fix applied

**1. `shared/integration/service_client.py` — trust_env=False on httpx client**

```python
async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
    ...
```

`trust_env=False` tells httpx to ignore `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`,
`HTTP(S)_PROXY`, and any other ambient env vars. All our inter-service calls are
plain `http://localhost:8xxx/` so we don't need a CA bundle at all. This closes
the root cause.

**2. New endpoint `POST /sim/circuit-breaker-reset` (data_ingestion)**

Forces every breaker in the Digital Twin's ServiceClient back to CLOSED. Useful
after a transient infrastructure issue (late startup, DNS hiccup, misconfigured
env var that's been fixed) so you don't wait 30 s per service for half-open.

```bash
curl -X POST http://localhost:8207/sim/circuit-breaker-reset
```

**3. `ServiceClient.reset_breakers()` method**

Public API the endpoint wraps. Usable by tests and scripts.

**4. Datetime timezone-awareness in bed_management `/predict-discharge` and `/allocate`**

A secondary bug surfaced once events started flowing: `req.admission_time` from
the Digital Twin is a timezone-naive `datetime`, while `_sim_now()` returns an
aware one. Their subtraction raised `TypeError: can't subtract offset-naive and
offset-aware datetimes` and returned HTTP 500, which again fed the circuit
breaker.

Added a `_to_aware()` helper that coerces naive datetimes to UTC-aware before
subtraction, and ensures `best_bed.admission_time` is always stored tz-aware.

---

## Validation

After the SSL fix + restart:

```text
t=14:27:37  sim_adm=2  act=2  ED_patients=2  bed_occupied=2  events=7
t=14:30:00  sim_adm=4  act=4  ED_patients=4  bed_occupied=4  events=12
```

All three pages (`/ed-flow`, `/bed-management`, `/hospital-ops`) now populate as
the Digital Twin cascades each admission through the full 6-service pipeline
and the events land in `MIMIC_SIM.event_log` for replay.

---

## Follow-up items (P1/P2 for the roadmap)

1. **Make `cross_service_call_failed` log line carry the exception type + first
   200 chars of the message.** Current log just says "service=X endpoint=Y" with
   the error as a field that isn't in the default formatter. A one-line
   `%(message)s` that includes `err=TypeName: detail` would have surfaced the
   SSL_CERT_FILE error in the first observation.

2. **Fail fast on broken env vars at service boot.** Add a sentinel call in
   `shared/api/base.py::create_app()` that constructs one `httpx.AsyncClient`
   and performs a self-ping to `/health`. If that fails at startup, log an
   explicit FATAL rather than deferring to the first real cascade failure.

3. **Contract test between Digital Twin and every downstream service** so a
   schema-level mismatch (like `admission_time` naive vs aware) is caught by
   CI rather than at runtime.

4. **Standardise on timezone-aware UTC everywhere.** Audit every `datetime`
   field in every Pydantic schema; add a root validator that coerces naive
   datetimes to UTC. Pair with a lint rule that flags raw `datetime.now()` in
   favour of `datetime.now(timezone.utc)`.

5. **Split `_safe_call` logging to a dedicated WARNING/ERROR stream** so
   production can silence it but dev sees the full traceback once per unique
   failure.
