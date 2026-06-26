# OpenTelemetry Distributed Tracing

**What it is**: every request, every DT cascade, every Kafka event lands
as spans in Jaeger, threaded together by `trace_id`. One click in the
Jaeger UI shows every service a single admission touched, how long each
took, and where the time went.

## Start

```bash
# 1. Infra
docker compose -f docker-compose.otel.yml up -d

# 2. Set endpoint before launching services
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
python scripts/start_all_services.py

# 3. Drive traffic
curl -X POST "http://localhost:8207/start?speed=60"
```

## Access

| Thing | URL |
|---|---|
| Jaeger UI | http://localhost:16686 |
| OTel Collector health | http://localhost:13133/health/status |
| Collector zpages | http://localhost:55679 |
| Collector metrics | http://localhost:8888/metrics |

## What you can do in Jaeger UI

Open `http://localhost:16686`:

- **Search by service** → `data_ingestion`, operation `dt.process_admission`,
  click any trace → see the full 18-span cascade: ed_triage → ed_flow →
  bed_management → oncology_ai (if cancer) → clinical_scribe →
  waiting_list → sepsis_icu → deterioration, plus every Mongo write
- **System architecture** tab → auto-generated service graph from trace
  data showing who calls whom with success rate + latency
- **Compare traces** → regression testing: grab a "good" admission
  trace + a slow one, view side-by-side to find the extra hop
- **Dependency graph** → one-click visualisation of cross-service
  dependencies

## What's instrumented

| Layer | How |
|---|---|
| Inbound HTTP (FastAPI) | Auto — `FastAPIInstrumentor.instrument_app` in `setup_tracing` |
| Outbound HTTP (httpx) | Auto — `HTTPXClientInstrumentor` + W3C traceparent header propagation |
| MongoDB (pymongo) | Auto — `PymongoInstrumentor`; each query becomes a span |
| Kafka producer | Manual — `inject_kafka_headers()` in `KafkaBroker.produce` |
| Kafka consumer | Manual — `extract_kafka_context()` in `consume_forever` creates a child span linked to the producer's |
| Log correlation | Auto — `LoggingInstrumentor.instrument(set_logging_format=True)` injects `trace_id`/`span_id` into every log record |
| DT cascade | Manual — `dt.process_admission` parent span wraps the whole pipeline |

## Wiring into a service

Every service needs one line in its startup hook:

```python
from shared.integration.tracing import setup_tracing

@app.on_event("startup")
async def startup():
    setup_tracing(app, service_name="bed_management")
    # ... rest of startup
```

Currently wired: `data_ingestion`, `hospital_ops`, `bed_management`,
`waiting_list`, `ed_flow`, `clinical_chat`, `ed_triage`, `sepsis_icu`,
`deterioration`. Remaining services inherit trace context automatically
through httpx (inbound FastAPI spans still appear when OTEL endpoint
is set, even without explicit setup — but the service-name resource
attribute defaults to `unknown_service`).

## Opt-out

Unset `OTEL_EXPORTER_OTLP_ENDPOINT`. `setup_tracing` becomes a no-op,
all `get_tracer().start_as_current_span(...)` calls return null spans,
zero overhead.

## Production upgrade path

The dev config writes traces to Jaeger's in-memory storage (100k
traces, lost on restart). For production:

1. Swap Jaeger all-in-one for Tempo with S3 backend:
   ```yaml
   exporters:
     otlp/tempo:
       endpoint: tempo:4317
   ```
2. Add tail sampling to the Collector — keep all error traces + 10%
   of normal:
   ```yaml
   processors:
     tail_sampling:
       policies:
         - name: errors
           type: status_code
           status_code: { status_codes: [ERROR] }
         - name: slow
           type: latency
           latency: { threshold_ms: 1000 }
         - name: sample
           type: probabilistic
           probabilistic: { sampling_percentage: 10 }
   ```
3. TLS between services and Collector (`tls.insecure: false`)
4. Grafana dashboard linked to Tempo — one click from a Prometheus
   alert to the offending trace

## Troubleshooting

- **No services in Jaeger** → check `docker logs medai-otel-collector`;
  verify `OTEL_EXPORTER_OTLP_ENDPOINT` is set on each service
- **Traces have `unknown_service`** → the service didn't call
  `setup_tracing(app, service_name=...)` in its lifespan
- **Cascade breaks at Kafka boundary** → aiokafka headers arg may be
  stripped by a middleware; grep for `kafka_produce_failed` in logs
- **Trace_id in logs is `0`** → `LoggingInstrumentor` wasn't called, or
  logging was configured before tracing; ensure `setup_tracing` runs
  first in the startup hook
