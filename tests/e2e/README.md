# End-to-End Smoke Tests

Nine tests that exercise the full live platform:

```
test_all_services_healthy                        — every :820x /health returns 200
test_kafka_broker_active                         — DT orchestrator initialised
test_mongo_event_log_reachable                   — durable broker tier alive
test_reset_clears_event_log_and_state            — /reset fans out, wipes census, wl, beds, event_log
test_start_honours_speed_param                   — /start?speed=25 actually applies 25×
test_admission_cascades_to_all_kafka_subscribers — 1 admission lands in all 16 subscribing services' ring buffers
test_hospital_ops_mirrors_data_ingestion_not_parallel_poisson
                                                 — hops DES population stays bounded by DT admissions
test_discharge_fires_action_handlers             — forced discharge → waiting_list marks entry completed via Kafka
test_waiting_list_live_only_hides_demo_seeds     — live_only filter works + purge-demo clears only demo entries
```

These are the tests you wish you'd had in the prior engineering sessions.
Every one of them maps to at least one bug that shipped and had to be
caught manually.

## Running

Requires the live stack — all 17 services + MongoDB + Redpanda (Kafka):

```bash
# 1. Start Redpanda
docker compose -f docker-compose.kafka.yml up -d

# 2. Start all services (with Kafka wired)
KAFKA_BOOTSTRAP=localhost:19092 python scripts/start_all_services.py --no-dashboard

# 3. Run the smoke tests
pytest tests/e2e/ -v
```

If `data_ingestion` (:8207) isn't reachable, the whole suite skips with a
clear message — safe to run in CI before infra is ready.

Typical run time: **~2.5 minutes** (dominated by waiting for the first
admission at 60× speed + discharge cascade).

## What each test proves

**Infra (3):** every service up, Kafka broker live, Mongo reachable. If
these fail everything else is noise.

**Reset flow (1):** `/reset` on data_ingestion propagates to bed_mgmt,
waiting_list, hospital_ops, and wipes the Mongo event_log. Regressions
here leave stale state that poisons the next run.

**Sim control (1):** `/start?speed=N` honoured. A silent default is
worse than an error.

**Event bus (1) — headline test:** an admission fires, the DT cascade
publishes `admission_complete`, and within ~10 real seconds every one of
the 16 subscribing services has the same `hadm_id` in its ring buffer.

**Architectural (1):** hospital_ops DES mirrors DT admissions rather
than running a parallel Poisson arrival stream. Catches a class of bug
where a synthetic population silently diverges from the real sim.

**Action handler (1):** a forced discharge reaches `waiting_list` via
Kafka and the handler marks the entry `completed`. Proves the consumer-
group dispatch actually runs the local callback.

**Data provenance (1):** waiting_list live-only filter works end-to-end;
demo seeds don't leak into the UI's clinical view.

## CI guidance

The suite is designed to run against a docker-compose-managed stack. A
starter GitHub Actions workflow would:

```yaml
services:
  mongo:   { image: mongo:7 }
  redpanda: { image: docker.redpanda.com/redpandadata/redpanda:v24.3.1 }

steps:
  - uses: actions/checkout@v4
  - setup-python 3.10
  - pip install -e . + test deps
  - python scripts/start_all_services.py --no-dashboard &
  - wait for :8207/health (30s timeout)
  - pytest tests/e2e/ -v --timeout=400
```
